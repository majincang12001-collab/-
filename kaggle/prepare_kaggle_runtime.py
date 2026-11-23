"""Prepare a Kaggle session to keep large model files off the 20GB working disk.

Run this at the start of a GPU session, before loading models:
    !python /kaggle/working/prepare_kaggle_runtime.py
or copy this file into /kaggle/working and run it.

This relocates existing caches without deleting them. Scratch storage is temporary;
keep final images/videos and manifests under /kaggle/working.
"""
from pathlib import Path
import os
import shutil

WORK = Path("/kaggle/working")
SCRATCH = Path("/kaggle/tmp")
SCRATCH.mkdir(parents=True, exist_ok=True)


def relocate(source: Path, destination: Path):
    if source.is_symlink():
        print("ALREADY_LINKED", source, "->", source.resolve())
        return source.resolve()
    if not source.exists():
        print("NOT_FOUND", source)
        return None
    if destination.exists():
        raise RuntimeError(f"Refusing to overwrite existing scratch data: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    print("MOVING", source, "->", destination, flush=True)
    shutil.move(str(source), str(destination))
    source.symlink_to(destination, target_is_directory=True)
    print("LINKED", source, "->", destination, flush=True)
    return destination


cache = relocate(WORK / "hf_cache", SCRATCH / "hf_cache")
mimic_models = relocate(
    WORK / "MimicMotion" / "models",
    SCRATCH / "MimicMotion_models",
)

model_cache = cache or (SCRATCH / "hf_cache")
os.environ["MANGA_MODEL_CACHE"] = str(model_cache)
os.environ["HF_HOME"] = str(model_cache)
os.environ["HUGGINGFACE_HUB_CACHE"] = str(model_cache / "hub")
os.environ["TRANSFORMERS_CACHE"] = str(model_cache / "hub")

print("MODEL_CACHE", model_cache)
print("WORK_FREE_GB", round(shutil.disk_usage(WORK).free / 1024**3, 2))
print("SCRATCH_FREE_GB", round(shutil.disk_usage(SCRATCH).free / 1024**3, 2))
