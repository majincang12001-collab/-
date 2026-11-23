# AI Manga Studio

Free-first AI manga production workbench prototype.

## Contents
- `index.html`: responsive Chinese web UI.
- `server.py`: local project/file/production metadata API.
- `OPEN_SOURCE_STACK.md`: architecture, tested stages and open-source choices.
- `kaggle/`: reproducible GPU environment and IP-Adapter experiments.

## Run the control server
1. Create `data/access.key` locally with a long random secret (never commit it).
2. Run `python3 server.py`.
3. Open `http://127.0.0.1:8766`.

The `data/` directory, uploads, credentials, model weights and generated images are intentionally excluded from Git. Keep backups of those assets separately.

## Safety
No paid GPU, API, or hosted inference is enabled by this repository. Kaggle sessions are temporary; save outputs outside the session.
