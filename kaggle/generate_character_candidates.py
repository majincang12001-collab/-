import os, time, zipfile, torch
from pathlib import Path
from PIL import Image, ImageDraw
from IPython.display import display, FileLink
from diffusers import StableDiffusionPipeline
BASE='/kaggle/input/ai-manga-model-bundle/AnythingV5'
OUT=Path('/kaggle/working/jiehun_character_candidates'); OUT.mkdir(parents=True,exist_ok=True)
assert os.path.isfile(BASE+'/model_index.json'), 'AnythingV5模型目录不完整'
assert torch.cuda.is_available(), '没有检测到GPU'
print('GPU',torch.cuda.get_device_name(0),'VRAM_GB',round(torch.cuda.get_device_properties(0).total_memory/1024**3,2),flush=True)
print('MODEL_LOAD_BEGIN',flush=True); t0=time.time()
pipe=StableDiffusionPipeline.from_pretrained(BASE,torch_dtype=torch.float16,variant='fp16',safety_checker=None,requires_safety_checker=False,local_files_only=True).to('cuda')
pipe.enable_vae_slicing(); pipe.set_progress_bar_config(disable=True)
print('MODEL_READY_SEC',round(time.time()-t0,1),flush=True)
style='Chinese 2D donghua film character concept art, semi-realistic Chinese animation proportions, refined hand-painted linework, painterly shading, detailed fabric texture, cinematic soft studio lighting, neutral charcoal gray background, single subject, full body head to toe, centered composition'
negative='Japanese anime, huge eyes, chibi, school anime, cel shading, 3D plastic, photorealistic, modern clothes, modern accessories, armor, weapon, cape, extra people, duplicate person, collage, multiple panels, text, watermark, cropped head, cropped feet, malformed hands, extra fingers, blurry'
characters=[('han','韩','one 17-year-old Chinese teenage boy, youthful clean face, clearly adolescent not adult, natural simple black hair, calm restrained expression, modest dark worn traditional Chinese robe, plain old fabric, no jewelry'),('grandfather','爷爷','one thin Chinese elderly man around 70 years old, deeply wrinkled face, white-gray hair, kind tired eyes, pale gray tattered Taoist robe with naturally frayed edges, no elaborate beard, humble old cloth shoes'),('raven','黑色乌鸦','one single anatomically accurate black crow, glossy layered black feathers, distinct crimson red eyes, realistic crow beak wings tail and feet, perched naturally on one dry branch, no clothes')]
files=[]
for ci,(slug,name,desc) in enumerate(characters):
 folder=OUT/slug; folder.mkdir(exist_ok=True); prompt=f'{style}, {desc}, front three-quarter view, natural neutral pose, complete silhouette visible'; thumbs=[]
 for k in range(4):
  seed=20261126+ci*100+k; gen=torch.Generator(device='cuda').manual_seed(seed); t=time.time()
  img=pipe(prompt,negative_prompt=negative,num_inference_steps=22,guidance_scale=7.0,height=768,width=512,generator=gen).images[0]
  path=folder/f'candidate_{k+1}.png'; img.save(path); files.append(path); thumbs.append(img.resize((256,384)))
  print('SAVED',name,k+1,'seed',seed,'sec',round(time.time()-t,1),flush=True); del img,gen; torch.cuda.empty_cache()
 sheet=Image.new('RGB',(520,800),(35,35,35)); draw=ImageDraw.Draw(sheet)
 for j,im in enumerate(thumbs):
  x=4+(j%2)*260; y=4+(j//2)*398; sheet.paste(im,(x,y)); draw.text((x+8,y+386),f'{name} 候选{j+1}',fill='white')
 sp=OUT/f'{slug}_contact_sheet.jpg'; sheet.save(sp,quality=92); files.append(sp); display(sheet)
zip_path='/kaggle/working/jiehun_character_candidates_20261126.zip'
with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
 for f in files: z.write(f,f.relative_to(OUT))
print('ALL_CANDIDATES_READY',len(files),'ZIP',zip_path,'SIZE_MB',round(os.path.getsize(zip_path)/1024**2,1),flush=True); display(FileLink(zip_path))
