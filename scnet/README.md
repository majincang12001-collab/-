# SCNet Hygon DCU adapter (preflight stage)

This is a separate Slurm adapter for SCNet Hygon DCU. It does not reuse the NVIDIA Kaggle worker and does not install CUDA.

## Current scope
- Slurm submission template requests one `dcu` for 10 minutes.
- `dcu_preflight.py` runs only inside a compute allocation and reports hostname, Slurm partition/job id, `hy-smi`/`rocm-smi`, Python and PyTorch import/device facts.
- Optional heartbeat POST: `/api/worker/scnet/heartbeat`, authenticated by `X-Worker-Key`.
- Node report endpoint: `GET /api/worker/scnet/status` (Worker-key protected).
- No task queue is claimed; no image/video generation is run.

## Before first submission
1. Deploy the updated `server.py` and `deployment/providers.json` to the workbench host.
2. Copy this directory to your SCNet home directory.
3. Set `MANGA_BOX_URL` and `MANGA_WORKER_KEY` through SCNet's protected job environment/secret facility; never paste the key into chat or commit it into a script.
4. Change `/path/to/dcu_preflight.py` in `submit_preflight.sbatch` to the real absolute path.
5. Confirm account/card-hour quota with SCNet. Submit only after the user explicitly approves the 1-DCU, 10-minute allocation.

## Not yet production-ready
The current image-generation pipeline is NVIDIA/CUDA-specific (`nvidia-smi`, CUDA-oriented dependencies and model kernels). Hygon DTK/PyTorch compatibility, model availability, image output and identity-consistency behavior must be separately validated before enabling SCNet to claim production keyframe jobs.
