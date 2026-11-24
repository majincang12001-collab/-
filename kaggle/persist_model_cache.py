#!/usr/bin/env python3
"""Persist/restore the manga Hugging Face model cache through Kaggle Notebook outputs."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tarfile

DEFAULT_CACHE = Path("/kaggle/tmp/hf_cache")
DEFAULT_ARCHIVE = Path("/kaggle/working/manga-model-cache.tar.gz")
REQUIRED = (
    "models--Yntec--AnythingV5/snapshots",
    "models--h94--IP-Adapter/snapshots",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> dict:
    rows = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_file():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            total += size
            rows.append({
                "path": str(path.relative_to(root)),
                "bytes": size,
                "sha256": sha256_file(path),
            })
    return {"file_count": len(rows), "total_bytes": total, "files": rows}


def validate_cache(root: Path) -> None:
    if not root.is_dir():
        raise SystemExit("CACHE_NOT_FOUND: " + str(root))
    missing = [item for item in REQUIRED if not (root / item).is_dir() or not any((root / item).iterdir())]
    if missing:
        raise SystemExit("CACHE_INCOMPLETE: " + ", ".join(missing))


def add_manifest(tf: tarfile.TarFile, manifest: dict) -> None:
    data = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    info = tarfile.TarInfo("hf_cache_manifest.json")
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))


def verify_manifest(root: Path, manifest: dict) -> None:
    files = manifest.get("files", [])
    if len(files) != manifest.get("file_count"):
        raise RuntimeError("Manifest file_count mismatch")
    for row in files:
        rel = Path(row["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise RuntimeError("Unsafe manifest path: " + str(rel))
        path = root / rel
        if not path.is_file():
            raise RuntimeError("Missing cached file: " + str(rel))
        if path.stat().st_size != row["bytes"] or sha256_file(path) != row["sha256"]:
            raise RuntimeError("Checksum mismatch: " + str(rel))


def pack(cache: Path, archive: Path) -> None:
    validate_cache(cache)
    archive.parent.mkdir(parents=True, exist_ok=True)
    manifest = inventory(cache)
    free = shutil.disk_usage(archive.parent).free
    print("CACHE_FILES", manifest["file_count"], "CACHE_GB", round(manifest["total_bytes"] / 1024**3, 3))
    print("OUTPUT_FREE_GB", round(free / 1024**3, 3))
    if manifest["total_bytes"] > 18 * 1024**3:
        raise SystemExit("REFUSE_PACK: cache exceeds safe 18 GiB output budget")
    tmp = archive.with_suffix(archive.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()
    print("PACK_START", flush=True)
    with tarfile.open(tmp, "w:gz", compresslevel=1) as tf:
        tf.add(cache, arcname="hf_cache", recursive=True)
        add_manifest(tf, manifest)
    with tarfile.open(tmp, "r:gz") as tf:
        names = set(tf.getnames())
        for required in REQUIRED:
            if not any(name.startswith("hf_cache/" + required + "/") for name in names):
                raise RuntimeError("Archive validation failed: " + required)
        raw = tf.extractfile("hf_cache_manifest.json")
        if raw is None:
            raise RuntimeError("Archive manifest missing")
        verify_manifest_from_tar(tf, json.load(raw))
    os.replace(tmp, archive)
    print("PACK_OK", archive, archive.stat().st_size, "bytes")


def verify_manifest_from_tar(tf: tarfile.TarFile, manifest: dict) -> None:
    files = manifest.get("files", [])
    if len(files) != manifest.get("file_count"):
        raise RuntimeError("Manifest file_count mismatch")
    for row in files:
        rel = Path(row["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise RuntimeError("Unsafe manifest path: " + str(rel))
        member = tf.getmember("hf_cache/" + str(rel))
        stream = tf.extractfile(member)
        if stream is None:
            raise RuntimeError("Missing archive member: " + str(rel))
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
        if size != row["bytes"] or digest.hexdigest() != row["sha256"]:
            raise RuntimeError("Archive checksum mismatch: " + str(rel))


def safe_extract(tf: tarfile.TarFile, destination: Path) -> None:
    base = destination.resolve()
    for member in tf.getmembers():
        target = (destination / member.name).resolve()
        if target != base and base not in target.parents:
            raise RuntimeError("Unsafe archive path: " + member.name)
        if member.issym() or member.islnk():
            raise RuntimeError("Links are not allowed in model cache archive: " + member.name)
    tf.extractall(destination, filter="data")


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
        safe_extract(tf, tmp)
    restored = tmp / "hf_cache"
    validate_cache(restored)
    manifest_path = tmp / "hf_cache_manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("Archive manifest missing")
    verify_manifest(restored, json.loads(manifest_path.read_text(encoding="utf-8")))
    if dest.exists():
        raise SystemExit("REFUSE_OVERWRITE: destination already exists: " + str(dest))
    restored.rename(dest)
    shutil.rmtree(tmp)
    print("RESTORE_OK", dest)


def verify_archive(archive: Path) -> None:
    if not archive.is_file():
        raise SystemExit("ARCHIVE_NOT_FOUND: " + str(archive))
    with tarfile.open(archive, "r:gz") as tf:
        names = set(tf.getnames())
        for required in REQUIRED:
            if not any(name.startswith("hf_cache/" + required + "/") for name in names):
                raise RuntimeError("ARCHIVE_INCOMPLETE: " + required)
        raw = tf.extractfile("hf_cache_manifest.json")
        if raw is None:
            raise RuntimeError("ARCHIVE_MANIFEST_MISSING")
        verify_manifest_from_tar(tf, json.load(raw))
    print("ARCHIVE_OK", archive, archive.stat().st_size, "bytes")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("pack", "restore", "verify"))
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--dest", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    if args.mode == "pack":
        pack(args.cache, args.archive)
    elif args.mode == "restore":
        restore(args.archive, args.dest)
    else:
        verify_archive(args.archive)


if __name__ == "__main__":
    main()