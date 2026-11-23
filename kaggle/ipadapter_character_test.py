"""Kaggle worker: AnythingV5 SD1.5 + IP-Adapter SD15 Light character reference test.

Requires model files already cached under /kaggle/working/hf_cache.
This is an experimental 512x512 identity-consistency test, not a 1080p video pipeline.
"""
from pathlib import Path
import glob
import time
import torch
from PIL import Image, ImageDraw
from diffusers import StableDiffusionPipeline

HF_HOME = Path('/kaggle/working/hf_cache')
BASE_REPO = HF_HOME / 'models--Yntec--AnythingV5' / 'snapshots'
ADAPTER_REPO = HF_HOME / 'models--h94--IP-Adapter' / 'snapshots'
base = Path(sorted(glob.glob(str(BASE_REPO / '*')))[-1])
adapter = Path(sorted(glob.glob(str(ADAPTER_REPO / '*')))[-1])
reference_path = Path('/kaggle/working/heroine_reference_seed20261123.png')
output_dir = Path('/kaggle/working')

if not torch.cuda.is_available():
    raise RuntimeError('CUDA is not available; select a Kaggle GPU session first.')
if not reference_path.is_file():
    raise FileNotFoundError(reference_path)

started = time.time()
pipe = StableDiffusionPipeline.from_pretrained(
    str(base),
    torch_dtype=torch.float16,
    variant='fp16',
    safety_checker=None,
    requires_safety_checker=False,
    local_files_only=True,
)
pipe = pipe.to('cuda')
pipe.enable_vae_slicing()
# Keep attention slicing disabled while loading IP-Adapter; it caused a processor
# constructor mismatch with the tested Diffusers 0.35.2 stack.
pipe.disable_attention_slicing()
pipe.load_ip_adapter(
    str(adapter),
    subfolder='models',
    weight_name='ip-adapter_sd15_light.safetensors',
    image_encoder_folder='image_encoder',
)
pipe.set_ip_adapter_scale(0.6)
reference = Image.open(reference_path).convert('RGB')
negative = 'blurry, low quality, distorted face, extra fingers, text, watermark'

views = [
    ('front', 'front view, centered portrait, neutral expression'),
    ('side', 'strict right-facing side profile, centered single character'),
    ('full', 'full body head to toe, standing alone, centered single character'),
]
images = []
for index, (name, view_prompt) in enumerate(views):
    generator = torch.Generator(device='cuda').manual_seed(20261124 + index)
    prompt = (
        'same young adult anime fantasy heroine as the reference image, '
        'long glossy black hair, violet eyes, delicate face, same dark navy '
        'and silver fantasy outfit, ' + view_prompt + ', clean light background'
    )
    image = pipe(
        prompt,
        ip_adapter_image=reference,
        negative_prompt=negative,
        num_inference_steps=16,
        guidance_scale=6.5,
        height=512,
        width=512,
        generator=generator,
    ).images[0]
    image.save(output_dir / f'ipadapter_view_{name}.png')
    images.append(image)
    print(f'VIEW_SAVED {name}')

sheet = Image.new('RGB', (1536, 540), 'white')
for index, image in enumerate(images):
    sheet.paste(image.resize((512, 512)), (512 * index, 0))
draw = ImageDraw.Draw(sheet)
for index, label in enumerate(('FRONT', 'SIDE', 'FULL BODY')):
    draw.text((512 * index + 15, 515), label, fill='black')
sheet.save(output_dir / 'ipadapter_consistency_contact_sheet.png')
print('CONTACT_SHEET_SAVED', output_dir / 'ipadapter_consistency_contact_sheet.png')
print('TOTAL_SECONDS', round(time.time() - started, 1))
print('GPU_FREE_GB', [round(torch.cuda.mem_get_info(i)[0] / 1024**3, 2) for i in range(torch.cuda.device_count())])
[exit status 0]