#!/usr/bin/env python3
"""Read-only Hygon DCU preflight for SCNet Slurm compute allocations.
No model download, no queue claim, no GPU generation. Reports sanitized facts only.
"""
import json, os, platform, shutil, subprocess, sys, urllib.request, urllib.error

def run(cmd, timeout=8):
    exe=shutil.which(cmd[0])
    if not exe: return f"{cmd[0]}: not installed/on PATH"
    try:
        p=subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out=(p.stdout+'\n'+p.stderr).strip()
        return out[:3500] if out else f"exit={p.returncode} (no output)"
    except Exception as e: return f"{type(e).__name__}: {e}"

def main():
    base=(os.environ.get('MANGA_BOX_URL') or '').rstrip('/')
    key=os.environ.get('MANGA_WORKER_KEY') or ''
    host=platform.node()
    node_id=os.environ.get('SLURMD_NODENAME') or host
    gpu=run(['hy-smi'])
    if 'not installed' in gpu: gpu=run(['rocm-smi'])
    py=f"{sys.version.split()[0]} ({sys.executable})"
    torch_info='not installed'
    try:
        import torch
        torch_info=f"version={torch.__version__}; cuda_available={torch.cuda.is_available()}; device_count={torch.cuda.device_count()}"
        if torch.cuda.is_available() and torch.cuda.device_count():
            torch_info += '; device0='+str(torch.cuda.get_device_name(0))
    except Exception as e: torch_info=f"import_error={type(e).__name__}: {e}"[:500]
    dtk=bool(os.environ.get('DTK_PATH') or os.environ.get('DTK_ROOT') or shutil.which('hy-smi'))
    report={"node_id":node_id,"hostname":host,"partition":os.environ.get('SLURM_JOB_PARTITION',''),
            "job_id":os.environ.get('SLURM_JOB_ID',''),"gpu_summary":gpu,"python":py,"torch":torch_info,"dtk_detected":dtk}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not base or not key:
        print('HEARTBEAT_SKIPPED: set MANGA_BOX_URL and MANGA_WORKER_KEY in the protected SCNet job environment')
        return 0
    body=json.dumps(report).encode()
    req=urllib.request.Request(base+'/api/worker/scnet/heartbeat',data=body,headers={'Content-Type':'application/json','X-Worker-Key':key,'Accept':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=15) as r: print('HEARTBEAT',r.read(2000).decode('utf-8','replace'))
    except Exception as e:
        print('HEARTBEAT_FAILED',type(e).__name__,str(e)[:300])
        return 2
    return 0
if __name__=='__main__': raise SystemExit(main())
