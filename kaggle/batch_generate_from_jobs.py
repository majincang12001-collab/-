1	"""Resumable Kaggle GPU worker for storyboard keyframes.
     2	
     3	Required files in /kaggle/working:
     4	  manga-generation-jobs.json
     5	  character_reference.png (or HEROINE_REFERENCE env override)
     6	Models must already exist in hf_cache; this worker never downloads weights.
     7	Run: MAX_SHOTS=1 python kaggle/batch_generate_from_jobs.py
     8	"""
     9	from pathlib import Path
    10	import argparse, glob, json, os, shutil, time, gc
    11	import torch
    12	from PIL import Image
    13	from diffusers import StableDiffusionPipeline
    14	
    15	ROOT = Path('/kaggle/working')
    16	JOBS = ROOT / 'manga-generation-jobs.json'
    17	REFERENCE = Path(os.environ.get('HEROINE_REFERENCE', str(ROOT / 'character_reference.png')))
    18	OUT = Path(os.environ.get('MANGA_OUTPUT_DIR', str(ROOT / 'manga_keyframes')))
    19	HF_HOME = Path(os.environ.get('MANGA_MODEL_CACHE', '/kaggle/tmp/hf_cache'))
    20	if not HF_HOME.exists() and (ROOT / 'hf_cache').exists():
    21	    HF_HOME = ROOT / 'hf_cache'
    22	
    23	
    24	def atomic_json(path: Path, value):
    25	    tmp = path.with_suffix(path.suffix + '.tmp')
    26	    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    27	    tmp.replace(path)
    28	
    29	
    30	def main():
    31	    parser = argparse.ArgumentParser()
    32	    parser.add_argument('--max-shots', type=int, default=int(os.environ.get('MAX_SHOTS', '3')))
    33	    parser.add_argument('--resume', action='store_true', help='skip existing valid keyframes')
    34	    args = parser.parse_args()
    35	    if not JOBS.is_file():
    36	        raise FileNotFoundError(f'Missing jobs JSON: {JOBS}')
    37	    if not REFERENCE.is_file():
    38	        raise FileNotFoundError(f'Missing character reference: {REFERENCE}')
    39	    if not torch.cuda.is_available():
    40	        raise RuntimeError('Kaggle GPU is unavailable; select a free GPU session')
    41	
    42	    base_candidates = sorted(glob.glob(str(HF_HOME / 'models--Yntec--AnythingV5' / 'snapshots' / '*')))
    43	    adapter_candidates = sorted(glob.glob(str(HF_HOME / 'models--h94--IP-Adapter' / 'snapshots' / '*')))
    44	    if not base_candidates:
    45	        raise FileNotFoundError(f'AnythingV5 snapshot missing under {HF_HOME}')
    46	    if not adapter_candidates:
    47	        raise FileNotFoundError(f'IP-Adapter snapshot missing under {HF_HOME}')
    48	    BASE, ADAPTER = Path(base_candidates[-1]), Path(adapter_candidates[-1])
    49	    required = [BASE / 'model_index.json', BASE / 'unet' / 'diffusion_pytorch_model.fp16.safetensors',
    50	                ADAPTER / 'models' / 'ip-adapter_sd15_light.safetensors']
    51	    missing = [str(p) for p in required if not p.is_file()]
    52	    if missing:
    53	        raise FileNotFoundError('Incomplete local model cache: ' + '; '.join(missing))
    54	
    55	    data = json.loads(JOBS.read_text(encoding='utf-8'))
    56	    characters = data.get('characters', [])
    57	    shots = data.get('storyboard', [])
    58	    if not shots:
    59	        raise ValueError('No storyboard shots in JSON')
    60	    if args.max_shots < 1:
    61	        raise ValueError('--max-shots must be >= 1')
    62	    shots = shots[:args.max_shots]
    63	    OUT.mkdir(parents=True, exist_ok=True)
    64	    ref = Image.open(REFERENCE).convert('RGB')
    65	    # The original character sheet is a head-and-shoulders portrait. Cropping to
    66	    # the face/hair region reduces the adapter's tendency to copy portrait framing
    67	    # into wide establishing shots. Set MANGA_REFERENCE_CROP=face to enable.
    68	    crop_mode = os.environ.get('MANGA_REFERENCE_CROP', 'none').lower()
    69	    if crop_mode == 'face':
    70	        w, h = ref.size
    71	        box = (round(w * 0.20), round(h * 0.10), round(w * 0.80), round(h * 0.72))
    72	        ref = ref.crop(box)
    73	    elif crop_mode not in ('none', ''):
    74	        raise ValueError('MANGA_REFERENCE_CROP must be none or face')
    75	    character_text = '; '.join(
    76	        ' '.join(str(c.get(k, '')) for k in ('name', 'age', 'appearance', 'costume', 'personality'))
    77	        for c in characters
    78	    )
    79	    print('GPU', torch.cuda.get_device_name(0), 'GPU_COUNT', torch.cuda.device_count(),
    80	          'BASE', BASE, 'ADAPTER', ADAPTER, 'SHOTS', len(shots), 'REFERENCE', REFERENCE, 'REF_MODE', crop_mode, 'REF_SIZE', ref.size, flush=True)
    81	
    82	    pipe = StableDiffusionPipeline.from_pretrained(
    83	        str(BASE), torch_dtype=torch.float16, variant='fp16', safety_checker=None,
    84	        requires_safety_checker=False, local_files_only=True
    85	    ).to('cuda')
    86	    pipe.disable_attention_slicing()
    87	    pipe.enable_vae_slicing()
    88	    pipe.load_ip_adapter(str(ADAPTER), subfolder='models',
    89	                         weight_name='ip-adapter_sd15_light.safetensors',
    90	                         image_encoder_folder='image_encoder')
    91	    adapter_scale = float(os.environ.get('MANGA_IP_ADAPTER_SCALE', '0.45'))
    92	    width = int(os.environ.get('MANGA_WIDTH', '768'))
    93	    height = int(os.environ.get('MANGA_HEIGHT', '432'))
    94	    steps = int(os.environ.get('MANGA_STEPS', '22'))
    95	    pipe.set_ip_adapter_scale(adapter_scale)
    96	    print('RENDER_CONFIG', width, height, steps, 'IP_ADAPTER_SCALE', adapter_scale, flush=True)
    97	
    98	    manifest_path = OUT / 'manifest.json'
    99	    manifest = {'project_name': data.get('project_name', ''), 'style': data.get('style', ''),
   100	                'reference': REFERENCE.name, 'model': 'Yntec/AnythingV5 fp16 + IP-Adapter SD15 Light',
   101	                'shots': []}
   102	    if args.resume and manifest_path.is_file():
   103	        try:
   104	            old = json.loads(manifest_path.read_text(encoding='utf-8'))
   105	            manifest['shots'] = old.get('shots', [])
   106	        except Exception:
   107	            pass
   108	    done_ids = {int(x['id']) for x in manifest['shots'] if x.get('status') == 'done'}
   109	    negative = 'duplicate person, two people, extra limbs, extra fingers, deformed hands, blurry, low quality, text, watermark'
   110	    started = time.time()
   111	    for index, shot in enumerate(shots, 1):
   112	        sid = int(shot.get('id', index))
   113	        filename = f'shot_{sid:04d}.png'
   114	        target = OUT / filename
   115	        if args.resume and sid in done_ids and target.is_file() and target.stat().st_size > 10000:
   116	            print('SKIP', filename, 'already complete', flush=True)
   117	            continue
   118	        framing = shot.get('shot_type', 'medium shot')
   119	        if any(k in str(framing) for k in ('远景', '全景', 'wide', 'establishing')):
   120	            camera = 'wide establishing shot, full body small in frame, environment dominant, character occupies only 15 percent of image, detailed ancient city architecture visible'
   121	        elif any(k in str(framing) for k in ('中景', 'medium')):
   122	            camera = 'medium shot, waist-up character, clear readable background environment'
   123	        else:
   124	            camera = 'close-up portrait, clean facial features'
   125	        prompt = ', '.join(x for x in [
   126	            data.get('style', 'Chinese anime donghua, cinematic manhua'),
   127	            shot.get('visual', ''), shot.get('action', ''), camera,
   128	            'one heroine only', character_text, shot.get('prompt', ''),
   129	            'coherent costume design, same heroine identity, environment clearly visible, detailed clean composition'
   130	        ] if x)
   131	        seed = int(data.get('seed', 20261124)) + sid
   132	        generator = torch.Generator(device='cuda').manual_seed(seed)
   133	        t0 = time.time()
   134	        image = pipe(prompt, ip_adapter_image=ref, negative_prompt=negative,
   135	                     num_inference_steps=steps, guidance_scale=6.5, width=width, height=height,
   136	                     generator=generator).images[0]
   137	        image.save(target, format='PNG')
   138	        if target.stat().st_size < 10000:
   139	            raise RuntimeError(f'Generated image looks incomplete: {target}')
   140	        record = {'id': sid, 'file': filename, 'status': 'done', 'seed': seed,
   141	                  'width': image.width, 'height': image.height, 'bytes': target.stat().st_size,
   142	                  'seconds': round(time.time() - t0, 2), 'dialogue': shot.get('dialogue', ''),
   143	                  'prompt': prompt}
   144	        manifest['shots'] = [x for x in manifest['shots'] if int(x.get('id', -1)) != sid] + [record]
   145	        atomic_json(manifest_path, manifest)
   146	        print('SAVED', filename, record['bytes'], 'bytes', record['seconds'], 'sec', flush=True)
   147	        del image, generator
   148	        gc.collect()
   149	        torch.cuda.empty_cache()
   150	
   151	    archive = shutil.make_archive(str(ROOT / 'manga_keyframes'), 'zip', root_dir=OUT)
   152	    print('ZIP', archive, Path(archive).stat().st_size, 'bytes', flush=True)
   153	    print('DONE', len([x for x in manifest['shots'] if x.get('status') == 'done']),
   154	          'shots; seconds', round(time.time() - started, 1), flush=True)
   155	
   156	
   157	if __name__ == '__main__':
   158	    main()