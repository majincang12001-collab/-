#!/usr/bin/env bash
set -euo pipefail
sudo apt-get update
sudo apt-get install -y ffmpeg espeak-ng python3-pil fonts-noto-cjk
python3 -m py_compile server.py render_episode.py kaggle/batch_generate_from_jobs.py
printf '\nDependencies installed. Start with: python3 server.py\n'
printf 'Health check: curl http://127.0.0.1:8766/api/health\n'
[exit status 0]