1	"""Resumable Kaggle GPU worker for storyboard keyframes.
     2	
     3	Required files in /kaggle/working:
     4	  manga-generation-jobs.json
     5	  character_reference.png (or HEROINE_REFERENCE env override)
     6	Models must already exist in hf_cache; this worker never downloads weights.
     7	Run: MAX_SHOTS=1 python kaggle/batch_generate_from_jobs.py
     8	"""
     9	from pathlib import Path
    10	import argparse, glob, json, os, shutil, time, gc, subprocess
    11	
    12	
    13	def select_gpu_before_torch():
    14	    """Prefer the physical GPU with the most free VRAM; respect explicit overrides."""
    15	    if os.environ.get('CUDA_VISIBLE_DEVICES'):
    16	        return 'CUDA_VISIBLE_DEVICES already set: ' + os.environ['CUDA_VISIBLE_DEVICES']
    17	    requested = os.environ.get('MANGA_GPU_INDEX', 'auto').strip().lower()
    18	    if requested != 'auto':
    19	        os.environ['CUDA_VISIBLE_DEVICES'] = requested
    20	        return 'manual physical GPU ' + requested
    21	    try:
    22	        result = subprocess.run(
    23	            ['nvidia-smi', '--query-gpu=index,memory.free', '--format=csv,noheader,nounits'],
    24	            check=True, capture_output=True, text=True, timeout=10
    25	        )
    26	        candidates = []
    27	        for line in result.stdout.splitlines():
    28	            parts = [x.strip() for x in line.split(',')]
    29	            if len(parts) == 2:
    30	                candidates.append((int(parts[1]), parts[0]))
    31	        if candidates:
    32	            free_mb, physical_index = max(candidates)
    33	            os.environ['CUDA_VISIBLE_DEVICES'] = physical_index
    34	            return f'auto-selected physical GPU {physical_index} ({free_mb} MiB free)'
    35	    except Exception as exc:
    36	        return 'GPU auto-selection unavailable; using runtime default: ' + str(exc)
    37	    return 'GPU auto-selection found no devices; using runtime default'
    38	
    39	
    40	GPU_SELECTION = select_gpu_before_torch()
    41	import torch
    42	from PIL import Image
    43	from diffusers import StableDiffusionPipeline
    44	
    45	ROOT = Path('/kaggle/working')
    46	JOBS = ROOT / 'manga-generation-jobs.json'
    47	REFERENCE = Path(os.environ.get('HEROINE_REFERENCE', str(ROOT / 'character_reference.png')))
    48	OUT = Path(os.environ.get('MANGA_OUTPUT_DIR', str(ROOT / 'manga_keyframes')))
    49	HF_HOME = Path(os.environ.get('MANGA_MODEL_CACHE', '/kaggle/tmp/hf_cache'))
    50	if not HF_HOME.exists() and (ROOT / 'hf_cache').exists():
    51	    HF_HOME = ROOT / 'hf_cache'
    52	
    53	
    54	def atomic_json(path: Path, value):
    55	    tmp = path.with_suffix(path.suffix + '.tmp')
    56	    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    57	    tmp.replace(path)
    58	
    59	
    60	def main():
    61	    parser = argparse.ArgumentParser()
    62	    parser.add_argument('--max-shots', type=int, default=int(os.environ.get('MAX_SHOTS', '3')))
    63	    parser.add_argument('--resume', action='store_true', help='skip existing valid keyframes')
    64	    args = parser.parse_args()
    65	    if not JOBS.is_file():
    66	        raise FileNotFoundError(f'Missing jobs JSON: {JOBS}')
    67	    if not REFERENCE.is_file():
    68	        raise FileNotFoundError(f'Missing character reference: {REFERENCE}')
    69	    if not torch.cuda.is_available():
    70	        raise RuntimeError('Kaggle GPU is unavailable; select a free GPU session')
    71	
    72	    base_candidates = sorted(glob.glob(str(HF_HOME / 'models--Yntec--AnythingV5' / 'snapshots' / '*')))
    73	    adapter_candidates = sorted(glob.glob(str(HF_HOME / 'models--h94--IP-Adapter' / 'snapshots' / '*')))
    74	    if not base_candidates:
    75	        raise FileNotFoundError(f'AnythingV5 snapshot missing under {HF_HOME}')
    76	    if not adapter_candidates:
    77	        raise FileNotFoundError(f'IP-Adapter snapshot missing under {HF_HOME}')
    78	    BASE, ADAPTER = Path(base_candidates[-1]), Path(adapter_candidates[-1])
    79	    required = [BASE / 'model_index.json', BASE / 'unet' / 'diffusion_pytorch_model.fp16.safetensors',
    80	                ADAPTER / 'models' / 'ip-adapter_sd15_light.safetensors']
    81	    missing = [str(p) for p in required if not p.is_file()]
    82	    if missing:
    83	        raise FileNotFoundError('Incomplete local model cache: ' + '; '.join(missing))
    84	
    85	    data = json.loads(JOBS.read_text(encoding='utf-8'))
    86	    characters = data.get('characters', [])
    87	    shots = data.get('storyboard', [])
    88	    if not shots:
    89	        raise ValueError('No storyboard shots in JSON')
    90	    if args.max_shots < 1:
    91	        raise ValueError('--max-shots must be >= 1')
    92	    shots = shots[:args.max_shots]
    93	    OUT.mkdir(parents=True, exist_ok=True)
    94	    ref = Image.open(REFERENCE).convert('RGB')
    95	    # The original character sheet is a head-and-shoulders portrait. Cropping to
    96	    # the face/hair region reduces the adapter's tendency to copy portrait framing
    97	    # into wide establishing shots. Set MANGA_REFERENCE_CROP=face to enable.
    98	    crop_mode = os.environ.get('MANGA_REFERENCE_CROP', 'none').lower()
    99	    if crop_mode == 'face':
   100	        w, h = ref.size
   101	        box = (round(w * 0.20), round(h * 0.10), round(w * 0.80), round(h * 0.72))
   102	        ref = ref.crop(box)
   103	    elif crop_mode not in ('none', ''):
   104	        raise ValueError('MANGA_REFERENCE_CROP must be none or face')
   105	    character_text = '; '.join(
   106	        ' '.join(str(c.get(k, '')) for k in ('name', 'age', 'appearance', 'costume', 'personality'))
   107	        for c in characters
   108	    )
   109	    print('GPU_SELECTION', GPU_SELECTION, 'GPU', torch.cuda.get_device_name(0), 'GPU_COUNT', torch.cuda.device_count(),
   110	          'BASE', BASE, 'ADAPTER', ADAPTER, 'SHOTS', len(shots), 'REFERENCE', REFERENCE, 'REF_MODE', crop_mode, 'REF_SIZE', ref.size, flush=True)
   111	
   112	    pipe = StableDiffusionPipeline.from_pretrained(
   113	        str(BASE), torch_dtype=torch.float16, variant='fp16', safety_checker=None,
   114	        requires_safety_checker=False, local_files_only=True
   115	    ).to('cuda')
   116	    pipe.disable_attention_slicing()
   117	    pipe.enable_vae_slicing()
   118	    pipe.load_ip_adapter(str(ADAPTER), subfolder='models',
   119	                         weight_name='ip-adapter_sd15_light.safetensors',
   120	                         image_encoder_folder='image_encoder')
   121	    adapter_scale = float(os.environ.get('MANGA_IP_ADAPTER_SCALE', '0.45'))
   122	    width = int(os.environ.get('MANGA_WIDTH', '768'))
   123	    height = int(os.environ.get('MANGA_HEIGHT', '432'))
   124	    steps = int(os.environ.get('MANGA_STEPS', '22'))
   125	    pipe.set_ip_adapter_scale(adapter_scale)
   126	    print('RENDER_CONFIG', width, height, steps, 'IP_ADAPTER_SCALE', adapter_scale, flush=True)
   127	
   128	    manifest_path = OUT / 'manifest.json'
   129	    manifest = {'project_name': data.get('project_name', ''), 'style': data.get('style', ''),
   130	                'reference': REFERENCE.name, 'model': 'Yntec/AnythingV5 fp16 + IP-Adapter SD15 Light',
   131	                'shots': []}
   132	    if args.resume and manifest_path.is_file():
   133	        try:
   134	            old = json.loads(manifest_path.read_text(encoding='utf-8'))
   135	            manifest['shots'] = old.get('shots', [])
   136	        except Exception:
   137	            pass
   138	    done_ids = {int(x['id']) for x in manifest['shots'] if x.get('status') == 'done'}
   139	    negative = 'duplicate person, two people, extra limbs, extra fingers, deformed hands, blurry, low quality, text, watermark'
   140	    started = time.time()
   141	    for index, shot in enumerate(shots, 1):
   142	        sid = int(shot.get('id', index))
   143	        filename = f'shot_{sid:04d}.png'
   144	        target = OUT / filename
   145	        if args.resume and sid in done_ids and target.is_file() and target.stat().st_size > 10000:
   146	            print('SKIP', filename, 'already complete', flush=True)
   147	            continue
   148	        framing = shot.get('shot_type', 'medium shot')
   149	        if any(k in str(framing) for k in ('远景', '全景', 'wide', 'establishing')):
   150	            camera = 'wide establishing shot, full body small in frame, environment dominant, character occupies only 15 percent of image, detailed ancient city architecture visible'
   151	        elif any(k in str(framing) for k in ('中景', 'medium')):
   152	            camera = 'medium shot, waist-up character, clear readable background environment'
   153	        else:
   154	            camera = 'close-up portrait, clean facial features'
   155	        prompt = ', '.join(x for x in [
   156	            data.get('style', 'Chinese anime donghua, cinematic manhua'),
   157	            shot.get('visual', ''), shot.get('action', ''), camera,
   158	            'one heroine only', character_text, shot.get('prompt', ''),
   159	            'coherent costume design, same heroine identity, environment clearly visible, detailed clean composition'
   160	        ] if x)
   161	        seed = int(data.get('seed', 20261124)) + sid
   162	        generator = torch.Generator(device='cuda').manual_seed(seed)
   163	        t0 = time.time()
   164	        image = pipe(prompt, ip_adapter_image=ref, negative_prompt=negative,
   165	                     num_inference_steps=steps, guidance_scale=6.5, width=width, height=height,
   166	                     generator=generator).images[0]
   167	        image.save(target, format='PNG')
   168	        if target.stat().st_size < 10000:
   169	            raise RuntimeError(f'Generated image looks incomplete: {target}')
   170	        record = {'id': sid, 'file': filename, 'status': 'done', 'seed': seed,
   171	                  'width': image.width, 'height': image.height, 'bytes': target.stat().st_size,
   172	                  'seconds': round(time.time() - t0, 2), 'dialogue': shot.get('dialogue', ''),
   173	                  'prompt': prompt}
   174	        manifest['shots'] = [x for x in manifest['shots'] if int(x.get('id', -1)) != sid] + [record]
   175	        atomic_json(manifest_path, manifest)
   176	        print('SAVED', filename, record['bytes'], 'bytes', record['seconds'], 'sec', flush=True)
   177	        del image, generator
   178	        gc.collect()
   179	        torch.cuda.empty_cache()
   180	
   181	    archive = shutil.make_archive(str(ROOT / 'manga_keyframes'), 'zip', root_dir=OUT)
   182	    print('ZIP', archive, Path(archive).stat().st_size, 'bytes', flush=True)
   183	    print('DONE', len([x for x in manifest['shots'] if x.get('status') == 'done']),
   184	          'shots; seconds', round(time.time() - started, 1), flush=True)
   185	
   186	
   187	if __name__ == '__main__':
   188	    main()