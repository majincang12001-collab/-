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
DATASET_HANDLE = os.environ.get("MANGA_MODEL_DATASET", "jincangma/ai-manga-model-bundle")
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


# Prefer the private Kaggle Dataset: it survives Notebook sessions and is
# mounted by kagglehub into /kaggle/input. Fall back to the legacy HF cache.
model_bundle = None
try:
    import kagglehub
    mounted = Path(kagglehub.dataset_download(DATASET_HANDLE))
    if (mounted / "manifest.json").is_file() and (mounted / "AnythingV5" / "model_index.json").is_file():
        # The Dataset mount is durable but random tensor reads from its FUSE
        # filesystem are very slow. Keep the base model mounted read-only and
        # stage only the IP-Adapter folder (including its 2.53GB image encoder)
        # on the local working disk for fast safetensors loading.
        local = WORK / "manga_model_bundle"
        adapter_source = mounted / "IP-Adapter"
        adapter_target = local / "IP-Adapter"
        base_target = local / "AnythingV5"
        if not base_target.exists() and not base_target.is_symlink():
            local.mkdir(parents=True, exist_ok=True)
            base_target.symlink_to(mounted / "AnythingV5", target_is_directory=True)
        if not adapter_target.is_dir():
            adapter_bytes = sum(x.stat().st_size for x in adapter_source.rglob("*") if x.is_file())
            free_bytes = shutil.disk_usage(WORK).free
            if free_bytes < adapter_bytes + 512 * 1024**2:
                raise RuntimeError(f"Not enough working space to stage IP-Adapter: need {adapter_bytes / 1024**3:.2f} GiB, free {free_bytes / 1024**3:.2f} GiB")
            print("STAGING_IP_ADAPTER", adapter_bytes, "bytes", flush=True)
            shutil.copytree(adapter_source, adapter_target)
        manifest_target = local / "manifest.json"
        if not manifest_target.exists():
            shutil.copy2(mounted / "manifest.json", manifest_target)
        model_bundle = local
        os.environ["MANGA_MODEL_BUNDLE"] = str(model_bundle)
        print("MODEL_DATASET_READY", DATASET_HANDLE, "MOUNT", mounted, "LOCAL_BUNDLE", model_bundle, flush=True)
    else:
        print("MODEL_DATASET_INCOMPLETE", mounted, flush=True)
except Exception as exc:
    print("MODEL_DATASET_MOUNT_FAILED", type(exc).__name__, str(exc)[:500], flush=True)

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
print("MODEL_BUNDLE", os.environ.get("MANGA_MODEL_BUNDLE", "not mounted; legacy cache fallback"))
print("WORK_FREE_GB", round(shutil.disk_usage(WORK).free / 1024**3, 2))
print("SCRATCH_FREE_GB", round(shutil.disk_usage(SCRATCH).free / 1024**3, 2))
