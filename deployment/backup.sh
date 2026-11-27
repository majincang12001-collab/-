#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$ROOT/../backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
ARCHIVE="$DEST/manga-console-portable-$STAMP.tar.gz"
cd "$(dirname "$ROOT")"
tar -czf "$ARCHIVE" --exclude='manga-console/data/access.key' --exclude='manga-console/data/test_access.key' --exclude='manga-console/.venv' --exclude='manga-console/__pycache__' --exclude='manga-console/**/__pycache__' --exclude='manga-console/.git' --exclude='manga-console/*.log' --exclude='manga-console/backups' --exclude='manga-console/data/gpu_jobs' manga-console
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"
tar -tzf "$ARCHIVE" >/dev/null
echo "BACKUP_OK $ARCHIVE"
