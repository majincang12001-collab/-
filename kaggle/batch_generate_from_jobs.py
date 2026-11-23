"""Generate storyboard keyframes from workbench JSON on a Kaggle GPU.
Upload manga-generation-jobs.json and a reference image named character_reference.png
into /kaggle/working, then run this script. Models must already be cached in hf_cache.
"""
from pathlib import Path
import glob, json, time
import torch
from PIL import Image
from diffusers import StableDiffusionPipeline
ROOT=Path('/kaggle/working'); JOBS=ROOT/'manga-generation-jobs.json'; REFERENCE=ROOT/'character_reference.png'; OUT=ROOT/'manga_keyframes'; HF_HOME=ROOT/'hf_cache'
if not JOBS.is_file(): raise FileNotFoundError(JOBS)
if not REFERENCE.is_file(): raise FileNotFoundError(f'Missing {REFERENCE}')
if not torch.cuda.is_available(): raise RuntimeError('Select a Kaggle GPU session first')
BASE=Path(sorted(glob.glob(str(HF_HOME/'models--Yntec--AnythingV5'/'snapshots'/'*')))[-1]); ADAPTER=Path(sorted(glob.glob(str(HF_HOME/'models--h94--IP-Adapter'/'snapshots'/'*')))[-1])
data=json.loads(JOBS.read_text(encoding='utf-8')); characters=data.get('characters',[]); shots=data.get('storyboard',[])
if not shots: raise ValueError('No storyboard shots in JSON')
character_text='; '.join(' '.join(str(c.get(k,'')) for k in ('name','appearance','costume','personality')) for c in characters)
reference=Image.open(REFERENCE).convert('RGB'); OUT.mkdir(parents=True,exist_ok=True)
pipe=StableDiffusionPipeline.from_pretrained(str(BASE),torch_dtype=torch.float16,variant='fp16',safety_checker=None,requires_safety_checker=False,local_files_only=True).to('cuda')
pipe.disable_attention_slicing(); pipe.enable_vae_slicing()
pipe.load_ip_adapter(str(ADAPTER),subfolder='models',weight_name='ip-adapter_sd15_light.safetensors',image_encoder_folder='image_encoder'); pipe.set_ip_adapter_scale(0.75)
negative='duplicate person, two people, extra limbs, extra fingers, deformed hands, blurry, low quality, text, watermark'
manifest=[]; started=time.time()
for i,shot in enumerate(shots,1):
 sid=int(shot.get('id',i)); prompt=f"{data.get('style','anime cinematic manhua')}, single character, {character_text}, {shot.get('visual','')}, {shot.get('action','')}, {shot.get('shot_type','medium shot')}, {shot.get('prompt','')}, detailed clean composition"
 generator=torch.Generator(device='cuda').manual_seed(20261124+sid)
 image=pipe(prompt,ip_adapter_image=reference,negative_prompt=negative,num_inference_steps=18,guidance_scale=6.5,width=512,height=512,generator=generator).images[0]
 filename=f'shot_{sid:04d}.png'; image.save(OUT/filename); manifest.append({'id':sid,'file':filename,'dialogue':shot.get('dialogue',''),'prompt':prompt}); print('SAVED',filename,flush=True)
(OUT/'manifest.json').write_text(json.dumps({'project_name':data.get('project_name',''),'shots':manifest},ensure_ascii=False,indent=2),encoding='utf-8')
import shutil
archive=shutil.make_archive(str(ROOT/'manga_keyframes'),'zip',root_dir=OUT)
print('ZIP',archive,flush=True)
print('DONE',len(manifest),'shots; seconds',round(time.time()-started,1))
[exit status 0]