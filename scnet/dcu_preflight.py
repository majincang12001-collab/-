#!/usr/bin/env python3
"""Read-only Hygon DCU preflight + tiny compute smoke test for SCNet Slurm.

This script does not install software, download weights, claim production jobs,
or start paid resources. It reports sanitized runtime facts and optionally
sends a heartbeat to the manga control plane using a protected environment key.
"""
from __future__ import annotations
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request


def run(cmd: list[str], timeout: int = 8, limit: int = 4500) -> str:
    if not shutil.which(cmd[0]):
        return f"{cmd[0]}: not installed/on PATH"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout + "\n" + p.stderr).strip()
        return out[:limit] if out else f"exit={p.returncode} (no output)"
    except Exception as exc:  # diagnostics must never print credentials
        return f"{type(exc).__name__}: {exc}"


def torch_probe() -> dict:
    result = {
        "installed": False,
        "version": None,
        "hip_version": None,
        "cuda_build_version": None,
        "cuda_available": False,
        "device_count": 0,
        "devices": [],
        "compute_smoke": {"status": "skipped", "reason": "PyTorch unavailable"},
    }
    try:
        import torch
    except Exception as exc:
        result["import_error"] = f"{type(exc).__name__}: {exc}"[:400]
        return result

    result.update({
        "installed": True,
        "version": str(torch.__version__),
        "hip_version": getattr(getattr(torch, "version", None), "hip", None),
        "cuda_build_version": getattr(getattr(torch, "version", None), "cuda", None),
    })
    try:
        result["cuda_available"] = bool(torch.cuda.is_available())
        if not result["cuda_available"]:
            result["compute_smoke"] = {"status": "blocked", "reason": "torch.cuda.is_available() is false"}
            return result
        count = int(torch.cuda.device_count())
        result["device_count"] = count
        for index in range(count):
            props = torch.cuda.get_device_properties(index)
            row = {"index": index, "name": str(getattr(props, "name", "unknown"))}
            for attr in ("total_memory", "multi_processor_count", "major", "minor"):
                value = getattr(props, attr, None)
                if value is not None:
                    row[attr] = int(value) if isinstance(value, (int, float)) else str(value)
            result["devices"].append(row)
        if count < 1:
            result["compute_smoke"] = {"status": "blocked", "reason": "device_count is zero"}
            return result

        # Tiny FP32 matrix multiply: proves allocation, kernel launch and sync.
        started = time.monotonic()
        device = torch.device("cuda:0")
        a = torch.randn((1024, 1024), device=device, dtype=torch.float32)
        b = torch.randn((1024, 1024), device=device, dtype=torch.float32)
        c = torch.matmul(a, b)
        torch.cuda.synchronize(device)
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        result["compute_smoke"] = {
            "status": "passed" if tuple(c.shape) == (1024, 1024) else "failed",
            "operation": "fp32_matmul_1024",
            "elapsed_ms": elapsed_ms,
            "output_shape": list(c.shape),
        }
        try:
            free_bytes, total_bytes = torch.cuda.mem_get_info(device)
            result["memory"] = {"free_bytes": int(free_bytes), "total_bytes": int(total_bytes)}
        except Exception:
            pass
        del a, b, c
        return result
    except Exception as exc:
        result["compute_smoke"] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:400]}
        return result


def main() -> int:
    host = platform.node()
    node_id = os.environ.get("SLURMD_NODENAME") or host
    gpu_summary = run(["hy-smi"])
    if "not installed" in gpu_summary:
        gpu_summary = run(["rocm-smi"])
    if "not installed" in gpu_summary:
        gpu_summary = run(["lspci", "-nn"])

    dtk_root = os.environ.get("DTK_ROOT") or os.environ.get("DTK_PATH") or "/opt/dtk"
    dtk_env = os.path.join(dtk_root, "env.sh")
    torch_info = torch_probe()
    dtk_detected = os.path.isfile(dtk_env) or bool(os.environ.get("ROCM_PATH")) or shutil.which("hy-smi") is not None
    report = {
        "node_id": node_id,
        "hostname": host,
        "partition": os.environ.get("SLURM_JOB_PARTITION", ""),
        "job_id": os.environ.get("SLURM_JOB_ID", ""),
        "gpu_summary": gpu_summary,
        "python": f"{sys.version.split()[0]} ({sys.executable})",
        "torch": torch_info,
        "dtk_detected": dtk_detected,
        "dtk_env_file_present": os.path.isfile(dtk_env),
        "rocm_path_present": bool(os.environ.get("ROCM_PATH")),
        "preflight_state": "ready_for_model_compatibility_test" if torch_info["compute_smoke"]["status"] == "passed" else "blocked_or_incomplete",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    base = (os.environ.get("MANGA_BOX_URL") or "").rstrip("/")
    key = os.environ.get("MANGA_WORKER_KEY") or ""
    if not base or not key:
        print("HEARTBEAT_SKIPPED: protected MANGA_BOX_URL/MANGA_WORKER_KEY not configured")
        return 0 if torch_info["compute_smoke"]["status"] == "passed" else 2

    body = json.dumps(report).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/worker/scnet/heartbeat",
        data=body,
        headers={"Content-Type": "application/json", "X-Worker-Key": key, "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            print("HEARTBEAT", response.read(2000).decode("utf-8", "replace"))
    except Exception as exc:
        print("HEARTBEAT_FAILED", type(exc).__name__, str(exc)[:250])
        return 3
    return 0 if torch_info["compute_smoke"]["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
