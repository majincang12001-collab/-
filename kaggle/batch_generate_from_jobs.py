"""Resumable Kaggle GPU worker for storyboard keyframes.

Required files in /kaggle/working:
  manga-generation-jobs.json
  character_reference.png (or HEROINE_REFERENCE env override)
Models must already exist in hf_cache; this worker never downloads weights.
Run: MAX_SHOTS=1 python kaggle/batch_generate_from_jobs.py
"""
from pathlib import Path
import argparse, glob, json, os, re, shutil, time, gc, subprocess


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
from diffusers.image_processor import IPAdapterMaskProcessor

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
    """Framing-only helper. Subject/action must come from the actual storyboard shot."""
    framing = str(shot.get('shot_type', '') or '').strip().lower()
    if any(k in framing for k in ('手部', '微距', 'macro', 'detail')):
        return 'macro detail framing'
    if any(k in framing for k in ('特写', '近景', 'close-up', 'close up')):
        return 'close-up framing, keep the specified subject clear'
    if any(k in framing for k in ('中景', 'medium')):
        return 'medium framing, clear subject and action'
    if any(k in framing for k in ('远景', '全景', '全身', 'wide', 'establishing')):
        return 'wide cinematic framing, preserve the scene layout'
    return 'cinematic framing faithful to the described scene'


def camera_prompt_for_shot(shot):
    """Retain authored camera movement/composition and append only neutral framing guidance."""
    return ', '.join(x for x in (str(shot.get('camera_prompt_en') or shot.get('camera_prompt') or '').strip(), camera_for_shot(shot)) if x)



def shot_cast_names(shot):
    raw = shot.get('character_names') or shot.get('characters') or shot.get('character') or shot.get('character_name') or []
    if isinstance(raw, str):
        return list(dict.fromkeys(x.strip() for x in re.split(r'[、，,；;|]', raw) if x.strip()))
    if isinstance(raw, list):
        return list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))
    return []


def renderable_cast_names(shot, characters):
    """Remove memorial-only names from the visual cast, even in legacy queued jobs."""
    memorial = {str(c.get('name') or '').strip() for c in characters
                if str(c.get('entity_type') or '') == 'memorial_only'
                or '墓碑' in str(c.get('visual_lock') or '')
                or '不生成实体' in str(c.get('negative_constraints') or '')}
    return [name for name in shot_cast_names(shot) if name not in memorial]


def character_anchor_text(character):
    """Prioritize structured identity locks; retain free-form prompt anchor last."""
    fields = ('prompt_name', 'costume_en', 'visual_lock_en', 'appearance_en', 'age_en', 'prompt_anchor')
    parts, seen = [], set()
    for key in fields:
        value = str(character.get(key, '') or '').strip()
        if value and value not in seen:
            parts.append(value)
            seen.add(value)
    return ', '.join(parts)



def should_use_text_only_for_missing_reference(character_names, explicit_text_only=False):
    # Named cast without a dedicated approved reference must never borrow the hero image.
    return bool(explicit_text_only or character_names)


def shot_uses_character_adapter(shot):
    # Identity adapters are driven only by an explicit, validated cast list.
    # A name in a prop inscription or prose must never activate a person reference.
    cast = shot.get('character_names') or shot.get('characters') or shot.get('character') or shot.get('character_name')
    if isinstance(cast, str):
        return bool(cast.strip(' 、，,；;|'))
    return bool(cast)




def shot_needs_full_reference(shot):
    """Use the full character sheet for full-body shots; face crop for tighter shots."""
    framing = ' '.join(str(shot.get(k, '')) for k in ('shot_type', 'camera_prompt_en', 'camera_prompt')).lower()
    return any(k in framing for k in ('全身', 'full body', 'full-body', 'full length', 'full-length', 'head to toe', 'full figure'))

def adapter_scale_for_shot(shot, base_scale, full_body_scale=0.68, closeup_scale=0.38):
    """Balance identity against scene composition; wide scene shots need less adapter weight."""
    if shot.get('text_only_character') or not shot_uses_character_adapter(shot):
        return 0.0
    if shot.get('scene_background') and shot_needs_full_reference(shot):
        return min(float(base_scale), 0.25)
    return max(float(base_scale), float(full_body_scale)) if shot_needs_full_reference(shot) else max(float(base_scale), float(closeup_scale))


def character_region_boxes(count):
    """Deterministic non-overlapping image regions for masked cast references."""
    if count < 2 or count > 4:
        raise ValueError('masked multi-character conditioning supports 2-4 characters')
    if count <= 3:
        edges = [i / count for i in range(count + 1)]
        return [(edges[i], 0.04, edges[i + 1], 0.96) for i in range(count)]
    return [(0.03, 0.03, 0.49, 0.49), (0.51, 0.03, 0.97, 0.49),
            (0.03, 0.51, 0.49, 0.97), (0.51, 0.51, 0.97, 0.97)]


def make_character_masks(count, width, height):
    """Create one binary IP-Adapter mask per cast member in stable reading order."""
    from PIL import Image, ImageDraw
    masks = []
    for box in character_region_boxes(count):
        mask = Image.new('L', (width, height), 0)
        draw = ImageDraw.Draw(mask)
        draw.rectangle((round(box[0] * width), round(box[1] * height),
                        round(box[2] * width), round(box[3] * height)), fill=255)
        masks.append(mask)
    return masks

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
    BASE = Path(os.environ.get('MANGA_BASE_MODEL_DIR', str(BASE)))
    ADAPTER = Path(os.environ.get('MANGA_IP_ADAPTER_DIR', str(ADAPTER)))
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
    # No episode-wide "first character" identity fallback: a shot receives identity
    # anchors only from its explicit cast and that character's own reference file.
    print('GPU_SELECTION', GPU_SELECTION, 'GPU', torch.cuda.get_device_name(0), 'GPU_COUNT', torch.cuda.device_count(),
          'BASE', BASE, 'ADAPTER', ADAPTER, 'SHOTS', len(shots), 'REFERENCE', REFERENCE, 'REF_MODE', crop_mode, 'REF_SIZE', full_ref.size, 'FACE_REF_SIZE', face_ref.size, flush=True)

    pipe = StableDiffusionPipeline.from_pretrained(
        str(BASE), torch_dtype=torch.float16, variant='fp16', safety_checker=None,
        requires_safety_checker=False, local_files_only=True
    ).to('cuda')
    pipe.disable_attention_slicing()
    pipe.vae.enable_slicing()
    print('IP_ADAPTER_LOAD_BEGIN', ADAPTER, flush=True)
    pipe.load_ip_adapter(str(ADAPTER), subfolder='models',
                         weight_name='ip-adapter_sd15_light.safetensors',
                         image_encoder_folder='image_encoder')
    print('IP_ADAPTER_READY', flush=True)
    adapter_scale = float(os.environ.get('MANGA_IP_ADAPTER_SCALE', '0.65'))
    width = int(os.environ.get('MANGA_WIDTH', '768'))
    height = int(os.environ.get('MANGA_HEIGHT', '432'))
    steps = int(os.environ.get('MANGA_STEPS', '22'))
    pipe.set_ip_adapter_scale(adapter_scale)
    print('RENDER_CONFIG', width, height, steps, 'IP_ADAPTER_SCALE', adapter_scale, flush=True)

    manifest_path = OUT / 'manifest.json'
    manifest = {'project_name': data.get('project_name', ''), 'style': data.get('style', ''),
                'reference': REFERENCE.name, 'model': f'{BASE.name} fp16 + IP-Adapter SD15 Light',
                'shots': []}
    if args.resume and manifest_path.is_file():
        try:
            old = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['shots'] = old.get('shots', [])
        except Exception:
            pass
    done_ids = {int(x['id']) for x in manifest['shots'] if x.get('status') == 'done'}
    negative = 'duplicate person, two people, extra limbs, extra fingers, deformed hands, blurry, low quality, text, watermark'
    bird_negative = 'human, person, woman, girl, female, human face, human body, humanoid, human hair, human hands, multiple birds, flock, flowers, blossoms, text, watermark, duplicate subject, blurry'
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
        camera = camera_prompt_for_shot(shot)
        shot_character_names = renderable_cast_names(shot, characters)
        shot_character_name = shot_character_names[0] if len(shot_character_names) == 1 else ''
        shot_character = next((c for c in characters if str(c.get('name','')).strip()==shot_character_name), None)
        cast_characters = [c for c in characters if str(c.get('name', '')).strip() in shot_character_names]
        uses_character = shot_uses_character_adapter(shot) and not bool(data.get('text_only_reference', False)) and not bool(shot.get('text_only_character', False))
        if data.get('task_type') == 'character_candidates':
            # Put the candidate's locked age, face, hair and costume first so the
            # SD1.5 CLIP 77-token limit cannot truncate identity details.
            shot_identity = str(data.get('candidate_identity_prompt') or data.get('character_description') or '')
        elif len(shot_character_names) > 1:
            # Each cast member gets an independent image embedding and a matching
            # spatial mask; the prompt fixes the same left-to-right reading order.
            positions = ('left', 'center', 'right') if len(shot_character_names) == 3 else ('upper left', 'upper right', 'lower left', 'lower right')
            if len(shot_character_names) == 2:
                positions = ('left', 'right')
            shot_identity = '; '.join(f"{next((c.get('prompt_name') for c in cast_characters if str(c.get('name','')).strip()==name and c.get('prompt_name')), name)} ({positions[i]}): {character_anchor_text(next((c for c in cast_characters if str(c.get('name','')).strip()==name), {'prompt_name':name}))}" for i, name in enumerate(shot_character_names))
            shot_identity += ', distinct faces, no blending'
        elif shot_character:
            shot_identity = character_anchor_text(shot_character)
            if str(shot_character.get('entity_type') or '') == 'animal':
                shot_identity += ', same bird, same black feathers and red eye'
            else:
                shot_identity += ', same face, same hair and outfit'
        elif cast_characters:
            shot_identity = character_anchor_text(cast_characters[0]) + ', same character identity, same face, same hairstyle, same costume'
        else:
            shot_identity = ''
        # CLIP's SD1.5 text encoder accepts only 77 tokens. Put camera and
        # scene-composition instructions first, then add identity/style details
        # only while they fit; never let long Chinese prose push the wide-shot
        # instruction beyond CLIP's truncation boundary.
        scene = shot.get('visual_en') or shot.get('visual', '')
        # Identity/costume must precede descriptive scene prose: SD1.5's 77-token
        # encoder otherwise truncates trousers/shoes from long character sheets.
        style_anchor = 'Chinese 2D hand-painted donghua, semi-realistic anatomy, cinematic lighting'
        continuity = ', '.join(x for x in ([str(shot.get('scene_anchor_en') or shot.get('scene_anchor') or '')] + list(shot.get('prop_anchors_en') or shot.get('prop_anchors') or [])) if x)
        if data.get('task_type') == 'character_candidates':
            # Candidate sheets have their own compact prompt path. Do not repeat the
            # long base prompt in `visual`: it used to consume CLIP's budget and
            # truncate the four different view-angle instructions.
            prompt = build_prompt(pipe.tokenizer, [
                shot_identity,
                shot.get('candidate_view_prompt') or shot.get('action', ''),
                'Chinese 2D hand-painted donghua character concept art, semi-realistic anatomy, full body, neutral dark gray studio background, centered single subject'
            ], max_tokens=75)
        else:
            # Do not feed the already-expanded `prompt` and identity_guard_prompt
            # back into CLIP: that duplicated identity text and pushed scene/prop
            # continuity beyond SD1.5's 77-token window. Build once, in priority order.
            prompt = build_prompt(pipe.tokenizer, [
                shot_identity,
                style_anchor,
                camera,
                shot.get('scene_anchor_en') or shot.get('scene_anchor', ''),
                ', '.join(shot.get('prop_anchors_en') or shot.get('prop_anchors') or []),
                scene,
                shot.get('action_en') or shot.get('action', '')
            ], max_tokens=75)
        seed = int(data.get('seed', 20261124)) + sid
        generator = torch.Generator(device='cuda').manual_seed(seed)
        t0 = time.time()
        reference_paths = shot.get('reference_paths') if len(shot_character_names) > 1 else None
        multi_reference_images = []
        if len(shot_character_names) > 1:
            # Fail closed: never silently replace a missing cast reference with
            # the project's default/first character image.
            refs_valid = bool(reference_paths and len(reference_paths) == len(shot_character_names)
                              and all(Path(x).is_file() for x in reference_paths))
            if refs_valid:
                for ref_path in reference_paths:
                    ref_image = Image.open(ref_path).convert('RGB')
                    rw, rh = ref_image.size
                    if crop_mode == 'face':
                        multi_reference_images.append(ref_image.crop((round(rw * 0.25), round(rh * 0.05), round(rw * 0.75), round(rh * 0.55))))
                    elif crop_mode == 'upper':
                        multi_reference_images.append(ref_image.crop((round(rw * 0.12), round(rh * 0.03), round(rw * 0.88), round(rh * 0.76))))
                    else:
                        multi_reference_images.append(ref_image)
                shot_full_ref, shot_face_ref = full_ref, face_ref
            else:
                shot['text_only_character'] = True
                shot['multi_character_reference_error'] = 'one or more independent character reference files are missing'
                shot_full_ref, shot_face_ref = full_ref, face_ref
                print('MULTI_CHARACTER_REFERENCE_MISSING_TEXT_ONLY', shot_character_names, flush=True)
        else:
            reference_path = shot.get('reference_path')
            if reference_path and Path(reference_path).is_file():
                shot_full_ref = Image.open(reference_path).convert('RGB')
                sw, sh = shot_full_ref.size
                shot_face_ref = shot_full_ref.crop((round(sw * 0.25), round(sh * 0.05), round(sw * 0.75), round(sh * 0.55)))
            else:
                if should_use_text_only_for_missing_reference(shot_character_names, shot.get('text_only_character', False)):
                    # Fail closed for a named character without their own approved
                    # reference. Never borrow the protagonist's face sheet for Grandpa,
                    # the crow, or any other cast member.
                    shot['text_only_character'] = True
                    shot_full_ref = Image.new('RGB', (512, 512), (127, 127, 127))
                    shot_face_ref = shot_full_ref
                    if shot_character_names:
                        print('CHARACTER_REFERENCE_MISSING_TEXT_ONLY', shot_character_names, flush=True)
                else:
                    shot_full_ref, shot_face_ref = full_ref, face_ref
        shot_ref = shot_full_ref if shot_needs_full_reference(shot) else shot_face_ref
        is_bird = any(str(c.get('entity_type') or '') == 'animal' for c in cast_characters)
        shot_adapter_scale = (float(os.environ.get('MANGA_BIRD_ADAPTER_SCALE', '0.72')) if is_bird else adapter_scale_for_shot(shot, adapter_scale,
            float(os.environ.get('MANGA_FULL_BODY_ADAPTER_SCALE', '0.68')),
            float(os.environ.get('MANGA_FACE_ADAPTER_SCALE', '0.38'))))
        pipe.set_ip_adapter_scale(shot_adapter_scale)
        # Keep all negative constraints inside CLIP's 77-token limit. Shot-specific
        # exclusions and identity guards take priority over generic style exclusions.
        negative_segments = [str(shot.get('negative_prompt') or '')]
        negative_segments.append(bird_negative if is_bird else (reference_negative if (data.get('text_only_reference') or data.get('reference_file')) else negative))
        if not shot_character_names:
            negative_segments.append('no people, no human, no person, no human face, no human body, no ghost, no spirit, no humanoid silhouette, no extra character, no crowd')
        if len(shot_character_names) == 1 and shot_character_names[0] not in ('黑色乌鸦',):
            negative_segments.append('extra people, additional person, crowd, duplicate face, twins, background people, female, woman, girl')
        if is_bird:
            negative_segments.append('human portrait, humanoid, character lineup, collage')
        if len(shot_character_names) > 1:
            negative_segments.append('merged faces, blended identities, duplicate same person, extra person, extra head, fused bodies, character collage, split screen')
        if shot.get('scene_background'):
            negative_segments.append('ornamental frame, decorative border, magic circle, pedestal, floating platform, character showcase, character design sheet, isolated studio background')
        if '中景' in str(shot.get('shot_type','')) and shot_character_names:
            negative_segments.append('extreme close-up, cropped face, cut off head')
        negative_segments.append('Japanese anime face, oversized anime eyes, moe, school anime, cel-shaded anime, chibi, glossy 3D plastic skin')
        shot_negative = build_prompt(pipe.tokenizer, negative_segments, max_tokens=75)
        call_kwargs = {}
        if len(multi_reference_images) >= 2 and not shot.get('text_only_character'):
            if len(multi_reference_images) > 4:
                raise ValueError('当前多人参考图区域遮罩最多支持4名角色；请拆分镜头或使用文本模式')
            masks = IPAdapterMaskProcessor().preprocess(make_character_masks(len(multi_reference_images), width, height), height=height, width=width)
            masks = masks.reshape(1, masks.shape[0], masks.shape[2], masks.shape[3])
            pipe.set_ip_adapter_scale([[shot_adapter_scale] * len(multi_reference_images)])
            call_kwargs = {'ip_adapter_image': [multi_reference_images],
                           'cross_attention_kwargs': {'ip_adapter_masks': [masks]}}
            print('MULTI_CHARACTER_MASKS', shot_character_names, 'regions', len(multi_reference_images), flush=True)
        else:
            pipe.set_ip_adapter_scale(shot_adapter_scale)
            call_kwargs = {'ip_adapter_image': shot_ref}
        guidance = 8.0 if is_bird else 7.5
        image = pipe(prompt, negative_prompt=shot_negative,
                     num_inference_steps=steps, guidance_scale=guidance, width=width, height=height,
                     generator=generator, **call_kwargs).images[0]
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
