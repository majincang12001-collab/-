"""Resumable Kaggle GPU worker for storyboard keyframes.

Required files in /kaggle/working:
  manga-generation-jobs.json
  character_reference.png (or HEROINE_REFERENCE env override)
Models must already exist in hf_cache; this worker never downloads weights.
Run: MAX_SHOTS=1 python kaggle/batch_generate_from_jobs.py
"""
from pathlib import Path
import argparse, glob, json, os, shutil, time, gc, subprocess


def select_gpu_before_torch():
    """Prefer the physical GPU with the most free VRAM; respect explicit overrides."""
    if os.environ.get('CUDA_VISIBLE_DEVICES'):
        return 'CUDA_VISIBLE_DEVICES already set: ' + os.environ['CUDA_VISIBLE_DEVICES']
    requested = os.environ.get('MANGA_GPU_INDEX', 'auto').strip().lower()
    if requested != 'auto':
        os.environ['CUDA_VISIBLE_DEVICES'] = requested
        return 'manual physical GPU ' + requested
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,memory.free', '--format=csv,noheader,nounits'],
            check=True, capture_output=True, text=True, timeout=10
        )
        candidates = []
        for line in result.stdout.splitlines():
            parts = [x.strip() for x in line.split(',')]
            if len(parts) == 2:
                candidates.append((int(parts[1]), parts[0]))
        if candidates:
            free_mb, physical_index = max(candidates)
            os.environ['CUDA_VISIBLE_DEVICES'] = physical_index
            return f'auto-selected physical GPU {physical_index} ({free_mb} MiB free)'
    except Exception as exc:
        return 'GPU auto-selection unavailable; using runtime default: ' + str(exc)
    return 'GPU auto-selection found no devices; using runtime default'


GPU_SELECTION = select_gpu_before_torch()
import torch
from PIL import Image
from diffusers import StableDiffusionPipeline

ROOT = Path('/kaggle/working')
JOBS = ROOT / 'manga-generation-jobs.json'
REFERENCE = Path(os.environ.get('HEROINE_REFERENCE', str(ROOT / 'character_reference.png')))
OUT = Path(os.environ.get('MANGA_OUTPUT_DIR', str(ROOT / 'manga_keyframes')))
HF_HOME = Path(os.environ.get('MANGA_MODEL_CACHE', '/kaggle/tmp/hf_cache'))
if not HF_HOME.exists() and (ROOT / 'hf_cache').exists():
    HF_HOME = ROOT / 'hf_cache'


def atomic_json(path: Path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def build_prompt(tokenizer, segments, max_tokens=75):
    """Fit prioritized prompt segments without dropping an overlong scene block."""
    chosen = []
    remaining = max_tokens
    for segment in segments:
        segment = str(segment or '').strip()
        if not segment or remaining <= 0:
            continue
        prefix = ', ' if chosen else ''
        prefix_tokens = len(tokenizer.encode(prefix, add_special_tokens=False))
        available = max(0, remaining - prefix_tokens)
        if available <= 0:
            break
        ids = tokenizer.encode(segment, add_special_tokens=False)
        if len(ids) > available:
            segment = tokenizer.decode(ids[:available], skip_special_tokens=True).strip()
            ids = tokenizer.encode(segment, add_special_tokens=False)
        if segment and ids:
            chosen.append(segment)
            remaining -= prefix_tokens + len(ids)
    return ', '.join(chosen)


def camera_for_shot(shot):
    """Choose framing from the actual subject/action, not a generic portrait fallback."""
    framing = str(shot.get('shot_type', '')).strip()
    visual = str(shot.get('visual', '')).strip()
    text = framing + ' ' + visual
    birds = any(k in text for k in ('乌鸦', '鸟', '羽毛', '枝头'))
    reveal = any(k in text for k in ('下摇', '向下摇', '落到树下', '越过弯曲枝干'))
    wide = any(k in framing for k in ('远景', '全景', 'wide', 'establishing'))
    medium = any(k in framing for k in ('中景', 'medium'))
    character = bool(shot.get('character') or shot.get('character_name'))
    if birds and ('眼睛' in visual or '抬起头' in visual):
        return 'cinematic close-up of a single black crow on a bare twisted branch, head and glowing red eye clearly visible, bird fills frame, no human, natural anatomy'
    if birds and ('张开嘴' in visual or '嘶哑叫声' in visual):
        return 'cinematic close-up side profile of one black crow perched on a dead twisted persimmon tree branch in an autumn graveyard, beak wide open calling, clearly visible curved black avian beak, ruffled black neck feathers, single bird only, muted sepia brown dusk background, natural crow anatomy, bare branches behind, no wires'
    if birds and ('振翅' in visual or '翅膀' in visual):
        return 'medium shot of three black crows on a dead persimmon tree branch, two birds spreading wings, full wings visible, no human'
    if reveal:
        return 'wide vertical establishing shot, camera looks down from bare tree branches to a lone slender teenage boy standing on a dirt path among graves, full body visible, environment dominant'
    if wide and character:
        return 'vertical wide establishing shot, one character small in lower third, full environment visible, head-to-toe silhouette, natural landscape composition'
    if wide:
        return 'wide vertical establishing shot of an autumn graveyard, dead twisted persimmon tree and three crows visible, environment dominant, no people'
    if medium and character:
        return 'STRICT MEDIUM SHOT, crop from waist to just above head, character occupies 55 to 70 percent of frame height, face and upper torso clearly visible, gravestone remains beside shoulder in background, do not show knees or feet, do not use full body framing'
    if medium:
        return 'medium environmental shot, main subject fills central frame, crop from waist up, surrounding location visible behind subject'
    return 'cinematic detail shot of the specific subject described, natural proportions, environment consistent with scene'


def shot_uses_character_adapter(shot):
    text = str(shot.get('visual', ''))
    return bool(shot.get('character')) or any(k in text for k in ('韩渐离', '少年', '人物', '站在坟包之间'))

def shot_needs_full_reference(shot):
    """Use the full character sheet for full-body shots; face crop for tighter shots."""
    framing = ' '.join(str(shot.get(k, '')) for k in ('shot_type', 'camera_prompt')).lower()
    return any(k in framing for k in ('全身', 'full body', 'full-body', 'full length', 'full-length', 'head to toe', 'full figure'))

def adapter_scale_for_shot(shot, base_scale, full_body_scale=0.68, closeup_scale=0.38):
    """Balance identity against scene composition; wide scene shots need less adapter weight."""
    if shot.get('text_only_character') or not shot_uses_character_adapter(shot):
        return 0.0
    if shot.get('scene_background') and shot_needs_full_reference(shot):
        return min(float(base_scale), 0.25)
    return max(float(base_scale), float(full_body_scale)) if shot_needs_full_reference(shot) else max(float(base_scale), float(closeup_scale))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-shots', type=int, default=int(os.environ.get('MAX_SHOTS', '3')))
    parser.add_argument('--resume', action='store_true', help='skip existing valid keyframes')
    args = parser.parse_args()
    if not JOBS.is_file():
        raise FileNotFoundError(f'Missing jobs JSON: {JOBS}')
    if not REFERENCE.is_file():
        raise FileNotFoundError(f'Missing character reference: {REFERENCE}')
    if not torch.cuda.is_available():
        raise RuntimeError('Kaggle GPU is unavailable; select a free GPU session')

    bundle_candidates = [
        Path(os.environ.get('MANGA_MODEL_BUNDLE', '/kaggle/input/datasets/jincangma/ai-manga-model-bundle')),
        ROOT / 'manga_model_bundle',
    ]
    bundle = next((p for p in bundle_candidates if (p / 'AnythingV5' / 'model_index.json').is_file()), None)
    if bundle is not None:
        BASE, ADAPTER = bundle / 'AnythingV5', bundle / 'IP-Adapter'
    else:
        base_candidates = sorted(glob.glob(str(HF_HOME / 'models--Yntec--AnythingV5' / 'snapshots' / '*')))
        adapter_candidates = sorted(glob.glob(str(HF_HOME / 'models--h94--IP-Adapter' / 'snapshots' / '*')))
        if not base_candidates:
            raise FileNotFoundError(f'AnythingV5 missing in model bundle or HF cache {HF_HOME}')
        if not adapter_candidates:
            raise FileNotFoundError(f'IP-Adapter missing in model bundle or HF cache {HF_HOME}')
        BASE, ADAPTER = Path(base_candidates[-1]), Path(adapter_candidates[-1])
    required = [BASE / 'model_index.json', BASE / 'unet' / 'diffusion_pytorch_model.fp16.safetensors',
                ADAPTER / 'models' / 'ip-adapter_sd15_light.safetensors']
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise FileNotFoundError('Incomplete local model cache: ' + '; '.join(missing))

    data = json.loads(JOBS.read_text(encoding='utf-8'))
    characters = data.get('characters', [])
    shots = data.get('storyboard', [])
    if not shots:
        raise ValueError('No storyboard shots in JSON')
    if args.max_shots < 1:
        raise ValueError('--max-shots must be >= 1')
    shots = shots[:args.max_shots]
    OUT.mkdir(parents=True, exist_ok=True)
    full_ref = Image.open(REFERENCE).convert('RGB')
    # Keep both identity views: close/medium shots benefit from face detail,
    # while full-body shots need the complete costume and silhouette reference.
    face_ref = full_ref
    crop_mode = os.environ.get('MANGA_REFERENCE_CROP', 'face').lower()
    if crop_mode == 'face':
        w, h = full_ref.size
        face_ref = full_ref.crop((round(w * 0.25), round(h * 0.05), round(w * 0.75), round(h * 0.55)))
    elif crop_mode == 'upper':
        w, h = full_ref.size
        face_ref = full_ref.crop((round(w * 0.12), round(h * 0.03), round(w * 0.88), round(h * 0.76)))
    elif crop_mode not in ('none', ''):
        raise ValueError('MANGA_REFERENCE_CROP must be none, face or upper')
    # Build identity anchors from the project's actual character sheet rather
    # than hard-coding one heroine; reuse the same reference image for every shot.
    if characters:
        identity = characters[0]
        character_text = str(identity.get('prompt_anchor', '')).strip() or ', '.join(str(identity.get(k, '')).strip() for k in ('name', 'age', 'costume', 'appearance') if str(identity.get(k, '')).strip())
        character_text += ', same character, same face, same hairstyle, same costume'
        print('CHARACTER_IDENTITY_ANCHOR', identity.get('name', 'heroine'), character_text, flush=True)
    else:
        character_text = 'same main character, consistent face, hairstyle and costume'
    print('GPU_SELECTION', GPU_SELECTION, 'GPU', torch.cuda.get_device_name(0), 'GPU_COUNT', torch.cuda.device_count(),
          'BASE', BASE, 'ADAPTER', ADAPTER, 'SHOTS', len(shots), 'REFERENCE', REFERENCE, 'REF_MODE', crop_mode, 'REF_SIZE', full_ref.size, 'FACE_REF_SIZE', face_ref.size, flush=True)

    pipe = StableDiffusionPipeline.from_pretrained(
        str(BASE), torch_dtype=torch.float16, variant='fp16', safety_checker=None,
        requires_safety_checker=False, local_files_only=True
    ).to('cuda')
    pipe.disable_attention_slicing()
    pipe.vae.enable_slicing()
    pipe.load_ip_adapter(str(ADAPTER), subfolder='models',
                         weight_name='ip-adapter_sd15_light.safetensors',
                         image_encoder_folder='image_encoder')
    adapter_scale = float(os.environ.get('MANGA_IP_ADAPTER_SCALE', '0.65'))
    width = int(os.environ.get('MANGA_WIDTH', '768'))
    height = int(os.environ.get('MANGA_HEIGHT', '432'))
    steps = int(os.environ.get('MANGA_STEPS', '22'))
    pipe.set_ip_adapter_scale(adapter_scale)
    print('RENDER_CONFIG', width, height, steps, 'IP_ADAPTER_SCALE', adapter_scale, flush=True)

    manifest_path = OUT / 'manifest.json'
    manifest = {'project_name': data.get('project_name', ''), 'style': data.get('style', ''),
                'reference': REFERENCE.name, 'model': 'Yntec/AnythingV5 fp16 + IP-Adapter SD15 Light',
                'shots': []}
    if args.resume and manifest_path.is_file():
        try:
            old = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['shots'] = old.get('shots', [])
        except Exception:
            pass
    done_ids = {int(x['id']) for x in manifest['shots'] if x.get('status') == 'done'}
    negative = 'duplicate person, two people, extra limbs, extra fingers, deformed hands, blurry, low quality, text, watermark'
    bird_negative = negative + ', human face, person, woman, man, anime portrait, human skin, human hair, humanoid body, human eyes, multiple birds, flock, power lines, electrical wires, white studio background, plain white background'
    reference_negative = negative + ', western business suit, blazer, necktie, dress shirt, school uniform, modern office clothes, buttons, white robe, white shirt, red robe, red sash, crimson clothes, shorts, skirt, bare legs, bare thighs, exposed legs, sandals, bare feet, barefoot, exposed toes, sneakers, hoodie, tracksuit, sweatshirt, logo, streetwear, red tattoo visible, armor, female, woman, girl, feminine face, breasts, cleavage, staff, sword, spear, weapon, text, calligraphy, lettering, signature, barefoot, sandals, naked feet, exposed toes'
    started = time.time()
    for index, shot in enumerate(shots, 1):
        sid = int(shot.get('id', index))
        filename = f'shot_{sid:04d}.png'
        target = OUT / filename
        if args.resume and sid in done_ids and target.is_file() and target.stat().st_size > 10000:
            print('SKIP', filename, 'already complete', flush=True)
            continue
        framing_type = str(shot.get('shot_type',''))
        strict_framing = any(k in framing_type for k in ('中景','远景','全景','全身','特写'))
        camera = camera_for_shot(shot) if strict_framing else (shot.get('camera_prompt') or camera_for_shot(shot))
        shot_character_name = str(shot.get('character') or shot.get('character_name') or '').strip()
        shot_character = next((c for c in characters if str(c.get('name','')).strip()==shot_character_name), None)
        uses_character = shot_uses_character_adapter(shot) and not bool(data.get('text_only_reference', False)) and not bool(shot.get('text_only_character', False))
        if shot_character:
            shot_identity = str(shot_character.get('prompt_anchor','')).strip() or ', '.join(str(shot_character.get(k,'')).strip() for k in ('name','age','costume','appearance') if str(shot_character.get(k,'')).strip())
            shot_identity += ', same character identity, same face, same hairstyle, same costume'
        else:
            shot_identity = character_text if uses_character else ''
        # CLIP's SD1.5 text encoder accepts only 77 tokens. Put camera and
        # scene-composition instructions first, then add identity/style details
        # only while they fit; never let long Chinese prose push the wide-shot
        # instruction beyond CLIP's truncation boundary.
        scene = shot.get('visual', '')
        # Identity/costume must precede descriptive scene prose: SD1.5's 77-token
        # encoder otherwise truncates trousers/shoes from long character sheets.
        prompt = build_prompt(pipe.tokenizer, [
            camera,
            shot_identity,
            shot.get('prompt', ''),
            scene,
            shot.get('action', ''),
            'cinematic Chinese 2D animation film, Chinese manhua visual development, natural East Asian facial proportions, refined hand-drawn linework, coherent composition',
            'detailed layered Chinese environment, painterly color, coherent cinematic lighting'
        ], max_tokens=75)
        seed = int(data.get('seed', 20261124)) + sid
        generator = torch.Generator(device='cuda').manual_seed(seed)
        t0 = time.time()
        reference_path = shot.get('reference_path')
        if reference_path and Path(reference_path).is_file():
            shot_full_ref = Image.open(reference_path).convert('RGB')
            sw, sh = shot_full_ref.size
            shot_face_ref = shot_full_ref.crop((round(sw * 0.25), round(sh * 0.05), round(sw * 0.75), round(sh * 0.55)))
        else:
            shot_full_ref, shot_face_ref = full_ref, face_ref
        shot_ref = shot_full_ref if shot_needs_full_reference(shot) else shot_face_ref
        shot_adapter_scale = adapter_scale_for_shot(shot, adapter_scale,
            float(os.environ.get('MANGA_FULL_BODY_ADAPTER_SCALE', '0.68')),
            float(os.environ.get('MANGA_FACE_ADAPTER_SCALE', '0.38')))
        pipe.set_ip_adapter_scale(shot_adapter_scale)
        shot_negative = (bird_negative if any(k in str(shot.get('visual', '')) for k in ('乌鸦', '鸟', '羽毛')) else (reference_negative if (data.get('text_only_reference') or data.get('reference_file')) else negative))
        if shot.get('negative_prompt'):
            shot_negative += ', ' + str(shot.get('negative_prompt'))
        shot_negative += ', Japanese anime face, oversized anime eyes, moe, school anime, cel-shaded anime, manga screentone, chibi, glossy 3D plastic skin'
        if shot.get('scene_background'):
            shot_negative += ', ornamental frame, decorative border, circular halo, magic circle, pedestal, platform, floating platform, character showcase, character design sheet, plain studio background, isolated gray background'
        if '中景' in str(shot.get('shot_type','')) and shot.get('character'):
            shot_negative += ', full body, full length, knees, legs, feet, shoes visible, long shot'
        image = pipe(prompt, ip_adapter_image=shot_ref, negative_prompt=shot_negative,
                     num_inference_steps=steps, guidance_scale=6.5, width=width, height=height,
                     generator=generator).images[0]
        image.save(target, format='PNG')
        if target.stat().st_size < 10000:
            raise RuntimeError(f'Generated image looks incomplete: {target}')
        record = {'id': sid, 'file': filename, 'status': 'done', 'seed': seed,
                  'width': image.width, 'height': image.height, 'bytes': target.stat().st_size,
                  'seconds': round(time.time() - t0, 2), 'dialogue': shot.get('dialogue', ''),
                  'prompt': prompt, 'reference_view': ('full' if shot_ref is shot_full_ref else 'face'), 'reference_character': shot_character_name, 'adapter_scale': shot_adapter_scale}
        manifest['shots'] = [x for x in manifest['shots'] if int(x.get('id', -1)) != sid] + [record]
        atomic_json(manifest_path, manifest)
        print('SAVED', filename, record['bytes'], 'bytes', record['seconds'], 'sec', flush=True)
        del image, generator
        gc.collect()
        torch.cuda.empty_cache()

    archive = shutil.make_archive(str(ROOT / 'manga_keyframes'), 'zip', root_dir=OUT)
    print('ZIP', archive, Path(archive).stat().st_size, 'bytes', flush=True)
    print('DONE', len([x for x in manifest['shots'] if x.get('status') == 'done']),
          'shots; seconds', round(time.time() - started, 1), flush=True)


if __name__ == '__main__':
    main()
