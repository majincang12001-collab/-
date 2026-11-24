#!/usr/bin/env python3
"""Persist/restore the manga Hugging Face model cache through Kaggle Notebook outputs.

Kaggle's /kaggle/tmp is scratch and disappears with the session. This helper packs
that cache into /kaggle/working so a Kaggle Quick Save with output files enabled
can preserve it (Kaggle notebook output limit: 20 GB). No GPU is needed to pack
or restore. The saved notebook output can later be attached as an Input source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import time

DEFAULT_CACHE = Path("/kaggle/tmp/hf_cache")
DEFAULT_ARCHIVE = Path("/kaggle/working/manga-model-cache.tar.gz")
REQUIRED = (
    "models--Yntec--AnythingV5/snapshots",
    "models--h94--IP-Adapter/snapshots",
)


def inventory(root: Path) -> dict:
    rows = []
    total = 0
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                size = p.stat().st_size
            except OSError:
                continue
            total += size
            rows.append({"path": str(p.relative_to(root)), "bytes": size})
    return {"root": str(root), "file_count": len(rows), "total_bytes": total, "files": rows}


def validate_cache(root: Path) -> None:
    missing = [x for x in REQUIRED if not (root / x).is_dir() or not any((root / x).iterdir())]
    if missing:
        raise SystemExit("CACHE_INCOMPLETE: " + ", ".join(missing))


def pack(cache: Path, archive: Path) -> None:
    validate_cache(cache)
    archive.parent.mkdir(parents=True, exist_ok=True)
    info = inventory(cache)
    free = shutil.disk_usage(archive.parent).free
    print("CACHE_FILES", info["file_count"], "CACHE_GB", round(info["total_bytes"] / 1024**3, 3))
    print("OUTPUT_FREE_GB", round(free / 1024**3, 3))
    if info["total_bytes"] > 18 * 1024**3:
        raise SystemExit("REFUSE_PACK: cache exceeds safe 18 GiB output budget")
    tmp = archive.with_suffix(archive.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()
    print("PACK_START", flush=True)
    with tarfile.open(tmp, "w:gz", compresslevel=1) as tf:
        tf.add(cache, arcname="hf_cache", recursive=True)
    os.replace(tmp, archive)
    check = tarfile.open(archive, "r:gz")
    try:
        names = set(check.getnames())
        for required in REQUIRED:
            if not any(n.startswith("hf_cache/" + required + "/") for n in names):
                raise RuntimeError("Archive validation failed: " + required)
    finally:
        check.close()
    print("PACK_OK", archive, archive.stat().st_size, "bytes")


def restore(archive: Path, dest: Path) -> None:
    if not archive.is_file():
        raise SystemExit("ARCHIVE_NOT_FOUND: " + str(archive))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".restore-tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    print("RESTORE_START", archive, "->", dest, flush=True)
    with tarfile.open(archive, "r:gz") as tf:
        # Validate all paths before extraction.
        base = tmp.resolve()
        for member in tf.getmembers():
            target = (tmp / member.name).resolve()
            if target != base and base not in target.parents:
                raise RuntimeError("Unsafe archive path: " + member.name)
        tf.extractall(tmp)
    restored = tmp / "hf_cache"
    validate_cache(restored)
    if dest.exists():
        raise SystemExit("REFUSE_OVERWRITE: destination already exists: " + str(dest))
    restored.rename(dest)
    shutil.rmtree(tmp)
    print("RESTORE_OK", dest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("pack", "restore", "verify"))
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    ap.add_argument("--dest", type=Path, default=DEFAULT_CACHE)
    args = ap.parse_args()
    if args.mode == "pack":
        pack(args.cache, args.archive)
    elif args.mode == "restore":
        restore(args.archive, args.dest)
    else:
        if not args.archive.is_file():
            raise SystemExit("ARCHIVE_NOT_FOUND")
        with tarfile.open(args.archive, "r:gz") as tf:
            names = set(tf.getnames())
            missing = [x for x in REQUIRED if not any(n.startswith("hf_cache/" + x + "/") for n in names)]
            if missing:
                raise SystemExit("ARCHIVE_INCOMPLETE: " + ", ".join(missing))
        print("ARCHIVE_OK", args.archive, args.archive.stat().st_size, "bytes")


if __name__ == "__main__":
    main()