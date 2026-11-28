# SCNet Hygon DCU adapter (preflight stage)

This is a separate Slurm adapter for SCNet Hygon DCU. It does not reuse the NVIDIA/Kaggle worker and does not install CUDA.

## Current scope

- Slurm submission template requests one `dcu` for 10 minutes.
- `dcu_preflight.py` records the Slurm node/partition/job, `hy-smi` or `rocm-smi` diagnostics, Python, DTK environment presence, PyTorch/HIP versions, visible devices and memory.
- When a Hygon-compatible PyTorch runtime is present, it performs a tiny FP32 matrix-multiplication smoke test to verify allocation, kernel launch and synchronization. This is not a model-generation test.
- Optional heartbeat POST: `/api/worker/scnet/heartbeat`, authenticated by `X-Worker-Key`.
- Node report endpoint: `GET /api/worker/scnet/status` (Worker-key protected).
- No production task queue is claimed; no image/video generation is run.

## Before first submission

1. Deploy the matching control-plane heartbeat/status routes to the workbench host.
2. Copy this directory to your SCNet home directory.
3. Run `bash inspect_allocation.sh` on the login node. It only reads partition, queue, storage and account quota information; it does not submit a job.
4. Set `MANGA_BOX_URL` and `MANGA_WORKER_KEY` through SCNet's protected job environment/secret facility; never paste the key into chat or commit it into a script.
5. Submit only after identifying the exact DCU partition and confirming the already allocated card-hour quota. Use `sbatch --partition=<verified-dcu-partition> submit_preflight.sbatch`. No automatic retry or paid fallback.
6. SCNet documents that login nodes can access the internet while compute nodes generally cannot. Prepare Python wheels and model files on the login node or approved storage before running a compute job.

## Not yet production-ready

The current image/video pipeline is not validated on Hygon DCU. The DCU torch wheel, driver, DTK, model kernels, attention implementation, custom nodes and memory behavior must match the SCNet image. Passing the matrix-multiplication smoke test proves only that basic device computation works. Next gates are a single 512/768 keyframe, reference-image conditioning, then a 2–4 second video. Only after these pass may the DCU worker claim production jobs.
