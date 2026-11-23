"""Resumable Kaggle GPU worker for storyboard keyframes.

Required files in /kaggle/working:
  manga-generation-jobs.json
  character_reference.png (or HEROINE_REFERENCE env override)
Models must already exist in hf_cache; this worker never downloads weights.
Run: MAX_SHOTS=1 python kaggle/batch_generate_from_jobs.py
"""
from pathlib import Path
import argparse, glob, json, os, shutil, time, gc
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

    base_candidates = sorted(glob.glob(str(HF_HOME / 'models--Yntec--AnythingV5' / 'snapshots' / '*')))
    adapter_candidates = sorted(glob.glob(str(HF_HOME / 'models--h94--IP-Adapter' / 'snapshots' / '*')))
    if not base_candidates:
        raise FileNotFoundError(f'AnythingV5 snapshot missing under {HF_HOME}')
    if not adapter_candidates:
        raise FileNotFoundError(f'IP-Adapter snapshot missing under {HF_HOME}')
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
    ref = Image.open(REFERENCE).convert('RGB')
    character_text = '; '.join(
        ' '.join(str(c.get(k, '')) for k in ('name', 'age', 'appearance', 'costume', 'personality'))
        for c in characters
    )
    print('GPU', torch.cuda.get_device_name(0), 'GPU_COUNT', torch.cuda.device_count(),
          'BASE', BASE, 'ADAPTER', ADAPTER, 'SHOTS', len(shots), 'REFERENCE', REFERENCE, flush=True)

    pipe = StableDiffusionPipeline.from_pretrained(
        str(BASE), torch_dtype=torch.float16, variant='fp16', safety_checker=None,
        requires_safety_checker=False, local_files_only=True
    ).to('cuda')
    pipe.disable_attention_slicing()
    pipe.enable_vae_slicing()
    pipe.load_ip_adapter(str(ADAPTER), subfolder='models',
                         weight_name='ip-adapter_sd15_light.safetensors',
                         image_encoder_folder='image_encoder')
    adapter_scale = float(os.environ.get('MANGA_IP_ADAPTER_SCALE', '0.45'))
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
    started = time.time()
    for index, shot in enumerate(shots, 1):
        sid = int(shot.get('id', index))
        filename = f'shot_{sid:04d}.png'
        target = OUT / filename
        if args.resume and sid in done_ids and target.is_file() and target.stat().st_size > 10000:
            print('SKIP', filename, 'already complete', flush=True)
            continue
        framing = shot.get('shot_type', 'medium shot')
        if any(k in str(framing) for k in ('远景', '全景', 'wide', 'establishing')):
            camera = 'wide establishing shot, full body small in frame, environment dominant, character occupies only 15 percent of image, detailed ancient city architecture visible'
        elif any(k in str(framing) for k in ('中景', 'medium')):
            camera = 'medium shot, waist-up character, clear readable background environment'
        else:
            camera = 'close-up portrait, clean facial features'
        prompt = ', '.join(x for x in [
            data.get('style', 'Chinese anime donghua, cinematic manhua'),
            shot.get('visual', ''), shot.get('action', ''), camera,
            'one heroine only', character_text, shot.get('prompt', ''),
            'coherent costume design, same heroine identity, environment clearly visible, detailed clean composition'
        ] if x)
        seed = int(data.get('seed', 20261124)) + sid
        generator = torch.Generator(device='cuda').manual_seed(seed)
        t0 = time.time()
        image = pipe(prompt, ip_adapter_image=ref, negative_prompt=negative,
                     num_inference_steps=steps, guidance_scale=6.5, width=width, height=height,
                     generator=generator).images[0]
        image.save(target, format='PNG')
        if target.stat().st_size < 10000:
            raise RuntimeError(f'Generated image looks incomplete: {target}')
        record = {'id': sid, 'file': filename, 'status': 'done', 'seed': seed,
                  'width': image.width, 'height': image.height, 'bytes': target.stat().st_size,
                  'seconds': round(time.time() - t0, 2), 'dialogue': shot.get('dialogue', ''),
                  'prompt': prompt}
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
