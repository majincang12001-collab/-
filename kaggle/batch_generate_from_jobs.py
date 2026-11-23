1	"""Generate storyboard keyframes from workbench JSON on a Kaggle GPU.
     2	Upload manga-generation-jobs.json and a reference image named character_reference.png
     3	into /kaggle/working, then run this script. Models must already be cached in hf_cache.
     4	"""
     5	from pathlib import Path
     6	import glob, json, time, os, shutil
     7	import torch
     8	from PIL import Image
     9	from diffusers import StableDiffusionPipeline
    10	ROOT=Path('/kaggle/working'); JOBS=ROOT/'manga-generation-jobs.json'; REFERENCE=ROOT/'character_reference.png'; OUT=ROOT/'manga_keyframes'; HF_HOME=ROOT/'hf_cache'
    11	if not JOBS.is_file(): raise FileNotFoundError(JOBS)
    12	if not REFERENCE.is_file(): raise FileNotFoundError(f'Missing {REFERENCE}')
    13	if not torch.cuda.is_available(): raise RuntimeError('Select a Kaggle GPU session first')
    14	base_candidates=sorted(glob.glob(str(HF_HOME/'models--Yntec--AnythingV5'/'snapshots'/'*'))); adapter_candidates=sorted(glob.glob(str(HF_HOME/'models--h94--IP-Adapter'/'snapshots'/'*')))
    15	if not base_candidates: raise FileNotFoundError('AnythingV5 snapshot missing under '+str(HF_HOME))
    16	if not adapter_candidates: raise FileNotFoundError('IP-Adapter snapshot missing under '+str(HF_HOME))
    17	BASE=Path(base_candidates[-1]); ADAPTER=Path(adapter_candidates[-1])
    18	data=json.loads(JOBS.read_text(encoding='utf-8')); characters=data.get('characters',[]); shots=data.get('storyboard',[])
    19	if not shots: raise ValueError('No storyboard shots in JSON')
    20	limit=max(1,int(os.environ.get('MAX_SHOTS','3'))); shots=shots[:limit]
    21	print('GPU',torch.cuda.get_device_name(0),'GPU_COUNT',torch.cuda.device_count(),'BASE',BASE,'ADAPTER',ADAPTER,'SHOTS',len(shots),flush=True)
    22	character_text='; '.join(' '.join(str(c.get(k,'')) for k in ('name','appearance','costume','personality')) for c in characters)
    23	reference=Image.open(REFERENCE).convert('RGB'); OUT.mkdir(parents=True,exist_ok=True)
    24	pipe=StableDiffusionPipeline.from_pretrained(str(BASE),torch_dtype=torch.float16,variant='fp16',safety_checker=None,requires_safety_checker=False,local_files_only=True).to('cuda')
    25	pipe.disable_attention_slicing(); pipe.enable_vae_slicing()
    26	pipe.load_ip_adapter(str(ADAPTER),subfolder='models',weight_name='ip-adapter_sd15_light.safetensors',image_encoder_folder='image_encoder'); pipe.set_ip_adapter_scale(0.75)
    27	negative='duplicate person, two people, extra limbs, extra fingers, deformed hands, blurry, low quality, text, watermark'
    28	manifest=[]; started=time.time()
    29	for i,shot in enumerate(shots,1):
    30	 sid=int(shot.get('id',i)); prompt=f"{data.get('style','anime cinematic manhua')}, single character, {character_text}, {shot.get('visual','')}, {shot.get('action','')}, {shot.get('shot_type','medium shot')}, {shot.get('prompt','')}, detailed clean composition"
    31	 generator=torch.Generator(device='cuda').manual_seed(20261124+sid)
    32	 image=pipe(prompt,ip_adapter_image=reference,negative_prompt=negative,num_inference_steps=18,guidance_scale=6.5,width=512,height=512,generator=generator).images[0]
    33	 filename=f'shot_{sid:04d}.png'; image.save(OUT/filename); manifest.append({'id':sid,'file':filename,'dialogue':shot.get('dialogue',''),'prompt':prompt}); print('SAVED',filename,flush=True)
    34	(OUT/'manifest.json').write_text(json.dumps({'project_name':data.get('project_name',''),'shots':manifest},ensure_ascii=False,indent=2),encoding='utf-8')
    35	archive=shutil.make_archive(str(ROOT/'manga_keyframes'),'zip',root_dir=OUT)
    36	print('ZIP',archive,flush=True)
    37	print('DONE',len(manifest),'shots; seconds',round(time.time()-started,1))