#!/usr/bin/env python3
"""Read-only preflight for free GPU video-model experiments; downloads nothing."""
import json, os, platform, shutil, subprocess, sys
from pathlib import Path

def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        return (p.stdout or p.stderr).strip()[:2000]
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"

report = {
    "python": sys.version.split()[0],
    "platform": platform.platform(),
    "disk": {},
    "cuda_available": False,
    "gpus": [],
    "packages": {},
    "network": {},
}
for path in ["/kaggle/working", "/workspace", "/tmp"]:
    if os.path.exists(path):
        u = shutil.disk_usage(path)
        report["disk"][path] = {"total_gb": round(u.total/1024**3,2), "free_gb": round(u.free/1024**3,2)}
try:
    import torch
    report["torch"] = torch.__version__
    report["cuda_available"] = torch.cuda.is_available()
    report["gpu_count"] = torch.cuda.device_count()
    for i in range(torch.cuda.device_count()):
        p = torch.cuda.get_device_properties(i)
        report["gpus"].append({"index":i,"name":p.name,"vram_gb":round(p.total_memory/1024**3,2)})
except Exception as e:
    report["torch_error"] = f"{type(e).__name__}: {e}"
for name in ["diffusers","transformers","accelerate","huggingface_hub","safetensors","PIL"]:
    try:
        mod = __import__(name)
        report["packages"][name] = getattr(mod,"__version__","installed")
    except Exception as e:
        report["packages"][name] = f"MISSING: {type(e).__name__}"
report["network"]["github"] = run(["bash","-lc","curl -ILs --max-time 8 https://github.com | head -1"])
report["network"]["huggingface"] = run(["bash","-lc","curl -ILs --max-time 8 https://huggingface.co | head -1"])
print("VIDEO_MODEL_PREFLIGHT_JSON_BEGIN")
print(json.dumps(report,ensure_ascii=False,indent=2))
print("VIDEO_MODEL_PREFLIGHT_JSON_END")
