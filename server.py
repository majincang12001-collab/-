#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import json, uuid, datetime, re, subprocess, sys, time, threading, secrets, os, urllib.request, urllib.error

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
PROJECTS = DATA / "projects.json"
PRODUCTION = DATA / "production"
RENDERS = DATA / "renders"
WORKSPACES = DATA / "project_workspaces"
USAGE_FILE = DATA / "usage" / "ledger.json"
SECRET_DIR = Path("/workspace/manga-secrets")
PROVIDER_SECRET_FILE = SECRET_DIR / "provider-keys.json"
SECRET_DIR.mkdir(parents=True, exist_ok=True)
try: SECRET_DIR.chmod(0o700)
except OSError: pass
WORKSPACES.mkdir(parents=True, exist_ok=True)
USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
PRODUCTION.mkdir(exist_ok=True); RENDERS.mkdir(exist_ok=True)
GPU_JOBS = DATA / "gpu_jobs"
GPU_JOBS.mkdir(exist_ok=True)
TTS_JOBS = DATA / "tts_jobs"
TTS_JOBS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True); UPLOADS.mkdir(exist_ok=True)
MAX_UPLOAD = 40 * 1024 * 1024
ACCESS_KEY = (DATA / "access.key").read_text("utf-8").strip()
# Keep the existing GPU worker credential separate so changing the human login
# password never silently disconnects a running Kaggle worker.
WORKER_ACCESS_KEY_FILE = SECRET_DIR / "worker-access.key"
if not WORKER_ACCESS_KEY_FILE.exists():
    fd=os.open(str(WORKER_ACCESS_KEY_FILE),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f: f.write(ACCESS_KEY)
    try: WORKER_ACCESS_KEY_FILE.chmod(0o600)
    except OSError: pass
WORKER_ACCESS_KEY = WORKER_ACCESS_KEY_FILE.read_text("utf-8").strip()
VALID_ACCESS_KEYS = {ACCESS_KEY} - {""}
VALID_WORKER_KEYS = {WORKER_ACCESS_KEY} - {""}
WORKER_ACCESS_HASH_FILE = SECRET_DIR / "worker-access.sha256"
WORKER_ACCESS_HASH = WORKER_ACCESS_HASH_FILE.read_text("utf-8").strip() if WORKER_ACCESS_HASH_FILE.exists() else os.environ.get("MANGA_WORKER_ACCESS_HASH", "").strip()
def valid_worker_key(value):
    value=str(value or "")
    return bool(value) and (value in VALID_WORKER_KEYS or secrets.compare_digest(__import__("hashlib").sha256(value.encode()).hexdigest(), WORKER_ACCESS_HASH))
MEDIA_TOKENS = {}
MEDIA_TOKEN_LOCK = threading.Lock()



def load_provider_secrets():
    try: return json.loads(PROVIDER_SECRET_FILE.read_text("utf-8"))
    except Exception: return {}

def save_provider_secrets(data):
    SECRET_DIR.mkdir(parents=True,exist_ok=True)
    tmp=PROVIDER_SECRET_FILE.with_suffix(".tmp")
    fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)
    os.replace(tmp,PROVIDER_SECRET_FILE)
    try: PROVIDER_SECRET_FILE.chmod(0o600)
    except OSError: pass

def apply_provider_secret(provider_id):
    row=load_provider_secrets().get(provider_id,{})
    if provider_id=="nvidia-nim":
        if row.get("api_key"): os.environ["NVIDIA_API_KEY"]=row["api_key"]
        if row.get("base_url"): os.environ["NVIDIA_BASE_URL"]=row["base_url"]
        if row.get("model"): os.environ["NVIDIA_MODEL"]=row["model"]
    return row

def project_workspace(project_id):
    if not re.fullmatch(r"[a-f0-9]{12}", str(project_id)): raise ValueError("invalid project id")
    base=WORKSPACES/project_id
    for name in ("story", "characters", "references", "episodes", "audio", "exports", "logs"):
        (base/name).mkdir(parents=True, exist_ok=True)
    (base/"episodes"/"episode-01").mkdir(parents=True,exist_ok=True)
    return base

def production_file(project_id, episode_id="episode-01"):
    if not re.fullmatch(r"episode-\d{2,3}", str(episode_id)): raise ValueError("invalid episode id")
    project_workspace(project_id)
    episode_root=WORKSPACES/project_id/"episodes"/episode_id
    for sub in ("keyframes","video_clips","timeline","audio","renders"): (episode_root/sub).mkdir(parents=True,exist_ok=True)
    folder=PRODUCTION/project_id
    folder.mkdir(parents=True,exist_ok=True)
    dest=folder/(episode_id+".json")
    legacy=PRODUCTION/(project_id+".json")
    if episode_id=="episode-01" and not dest.exists() and legacy.exists():
        import shutil
        shutil.copy2(legacy,dest)
    return dest

def usage_data():
    try: data=json.loads(USAGE_FILE.read_text("utf-8"))
    except Exception: data={"providers":[],"events":[]}
    data.setdefault("providers",[]); data.setdefault("events",[])
    defaults=[
        {"name":"Kaggle GPU","model":"Tesla T4 / ComfyUI","unit":"GPU任务/镜头","quota_limit":None,"quota_source":"未接入Kaggle实时配额接口；只统计本控制台任务"},
        {"name":"ComfyUI关键帧","model":"AnythingV5 + IP-Adapter","unit":"张关键帧","quota_limit":None,"quota_source":"按本控制台任务记录，不代表云平台剩余额度"},
        {"name":"普通话TTS","model":"Qwen3-TTS / CosyVoice（待部署）","unit":"音频段","quota_limit":None,"quota_source":"尚未部署语音服务"},
        {"name":"本地渲染","model":"FFmpeg","unit":"镜头","quota_limit":None,"quota_source":"本地CPU处理，不计第三方额度"}
    ]
    for d in defaults:
        if not any(x.get("name")==d["name"] for x in data["providers"]): data["providers"].append(d)
    return data

def save_usage(data):
    tmp=USAGE_FILE.with_suffix(".tmp"); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),"utf-8"); tmp.replace(USAGE_FILE)

def usage_event(project_id, provider, task, units=1, unit="次", model="", status="completed", detail="", job_id=""):
    data=usage_data(); data.setdefault("events",[])
    data["events"].append({"id":uuid.uuid4().hex[:12],"project_id":project_id,"provider":provider,"task":task,"model":model,"units":units,"unit":unit,"status":status,"detail":detail,"job_id":job_id,"created_at":datetime.datetime.now(datetime.timezone.utc).isoformat()})
    data["events"]=data["events"][-5000:]; save_usage(data)

def update_usage_event(job_id,status,detail=""):
    if not job_id:return
    data=usage_data(); changed=False
    for e in reversed(data.get("events",[])):
        if e.get("job_id")==job_id:
            e["status"]=status
            if detail:e["detail"]=detail
            changed=True; break
    if changed:save_usage(data)

def load_projects():
    try: return json.loads(PROJECTS.read_text("utf-8"))
    except Exception: return []

def save_projects(items):
    tmp = PROJECTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(PROJECTS)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def send_json(self, obj, status=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        p=urlparse(self.path)
        if p.path == "/media/render":
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]; token=(q.get("token") or [""])[0]
            with MEDIA_TOKEN_LOCK:
                expiry=MEDIA_TOKENS.get(token, 0)
                if expiry < time.time(): MEDIA_TOKENS.pop(token, None); expiry=0
            if not expiry or not re.fullmatch(r"[a-f0-9]{16}",job_id):
                self.send_error(403, "media token expired"); return
            matches=list(RENDERS.glob(f"*/{job_id}/episode_preview.mp4"))
            if not matches:
                self.send_error(404, "video not found"); return
            f=matches[0]; size=f.stat().st_size; start=0; end=size-1; status=200
            rng=self.headers.get("Range", "")
            if rng:
                m=re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
                if not m:
                    self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
                a,b=m.groups()
                if not a:
                    length=int(b or 0); start=max(0,size-length)
                else:
                    start=int(a); end=min(end,int(b)) if b else end
                if start >= size or start > end:
                    self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
                status=206
            download=(q.get("download") or [""])[0] == "1"
            self.send_response(status); self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes"); self.send_header("Content-Length", str(end-start+1))
            if status==206: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Disposition", ('attachment' if download else 'inline')+'; filename="manga-episode-preview.mp4"')
            self.send_header("Cache-Control", "private, no-store"); self.end_headers()
            try:
                with f.open("rb") as stream:
                    stream.seek(start); remaining=end-start+1
                    while remaining:
                        chunk=stream.read(min(1024*1024,remaining))
                        if not chunk: break
                        self.wfile.write(chunk); remaining-=len(chunk)
            except (BrokenPipeError, ConnectionResetError): pass
            return
        if p.path == "/worker-bootstrap.py":
            f=ROOT/"kaggle"/"box_gpu_worker.py"
            raw=f.read_bytes(); self.send_response(200); self.send_header("Content-Type","text/x-python; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        worker_path = p.path.startswith("/api/worker/") or p.path in ("/api/tts/next",)
        if p.path.startswith("/api/") and p.path not in ("/api/health", "/api/worker/auth-check"):
            valid = valid_worker_key(self.headers.get("X-Worker-Key")) if worker_path else self.headers.get("X-Access-Key") in VALID_ACCESS_KEYS
            if not valid: return self.send_json({"error":"访问凭据无效，请检查登录或Worker配置"},401)
        if p.path == "/api/ai/storyboard/draft":
            q=parse_qs(urlparse(self.path).query); project_id=(q.get("project_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{12}",project_id): return self.send_json({"error":"项目编号无效"},400)
            draft_path=PRODUCTION/(project_id+"_pipeline")/"ai_dual_model_review_draft.json"
            if not draft_path.is_file(): return self.send_json({"error":"当前项目还没有双模型草稿"},404)
            try:
                draft=json.loads(draft_path.read_text("utf-8"))
                return self.send_json({"ok":True,"shots":draft.get("shots",[]),"models":draft.get("models",{}),"note":draft.get("note","")})
            except Exception as e: return self.send_json({"error":"草稿读取失败："+str(e)},500)
        if p.path == "/api/health":
            return self.send_json({"ok":True,"service":"manga-console","time":datetime.datetime.now(datetime.timezone.utc).isoformat()})
        if p.path == "/api/gpu/jobs":
            q=parse_qs(p.query); project_id=(q.get("project_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{12}",project_id): return self.send_json({"error":"invalid project"},400)
            jobs=[]
            for jf in sorted(GPU_JOBS.glob("*.json"),key=lambda x:x.stat().st_mtime,reverse=True):
                try: j=json.loads(jf.read_text("utf-8"))
                except Exception: continue
                if j.get("project_id")==project_id:
                    jobs.append({k:j.get(k) for k in ("job_id","episode_id","status","created_at","claimed_at","completed_at","selected_shot_ids","max_shots","keyframes","error")})
            return self.send_json({"jobs":jobs[:20]})
        if p.path == "/api/gpu/status":
            try:
                from gpu_worker_client import ComfyUIWorker, GPUWorkerError
                worker = ComfyUIWorker(timeout=5.0)
                if not worker.configured:
                    return self.send_json({"configured":False,"online":False,"message":"尚未配置云端 GPU 地址"})
                try:
                    status = worker.health()
                    return self.send_json(status)
                except GPUWorkerError as exc:
                    return self.send_json({"configured":True,"online":False,"message":str(exc)},502)
            except Exception:
                return self.send_json({"configured":False,"online":False,"message":"GPU 状态检查暂不可用"},503)
        if p.path == "/api/worker/auth-check":
            supplied=self.headers.get("X-Worker-Key","")
            return self.send_json({"worker_key_matches": valid_worker_key(supplied), "received": bool(supplied), "received_length": len(supplied)})
        if p.path == "/api/tts/next":
            if not valid_worker_key(self.headers.get("X-Worker-Key")): return self.send_json({"error":"worker unauthorized"},401)
            for f in sorted(TTS_JOBS.glob("*.json"),key=lambda x:x.stat().st_mtime):
                try: job=json.loads(f.read_text("utf-8"))
                except Exception: continue
                if job.get("status")=="queued":
                    job.update(status="running",claimed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
                    f.write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8")
                    return self.send_json({"job":job})
            return self.send_json({"job":None})
        if p.path == "/api/tts/audio":
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{16}",job_id): return self.send_json({"error":"job id invalid"},400)
            jf=TTS_JOBS/(job_id+".json")
            if not jf.exists(): return self.send_json({"error":"任务不存在"},404)
            job=json.loads(jf.read_text("utf-8"))
            if job.get("status")!="completed": return self.send_json({"error":"音频尚未生成"},409)
            f=ROOT/job.get("audio_file","")
            if not f.is_file() or ROOT not in f.resolve().parents: return self.send_json({"error":"音频文件不存在"},404)
            raw=f.read_bytes(); self.send_response(200); self.send_header("Content-Type","audio/wav"); self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(raw); return
        if p.path == "/api/tts/status":
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{16}",job_id): return self.send_json({"error":"job id invalid"},400)
            f=TTS_JOBS/(job_id+".json")
            if not f.exists(): return self.send_json({"error":"任务不存在"},404)
            return self.send_json({"job":json.loads(f.read_text("utf-8"))})
        if p.path == "/api/worker/next":
            # Worker authentication uses the private access key; never expose it to the browser.
            if not valid_worker_key(self.headers.get("X-Worker-Key")):
                return self.send_json({"error":"worker unauthorized"},401)
            pending = sorted(GPU_JOBS.glob("*.json"), key=lambda x: x.stat().st_mtime)
            for job_file in pending:
                try: job = json.loads(job_file.read_text("utf-8"))
                except Exception: continue
                if job.get("status") == "queued":
                    job.update(status="running", claimed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
                    job_file.write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8")
                    return self.send_json({"job":job})
            return self.send_json({"job":None})
        if p.path == "/api/worker/script":
            if not valid_worker_key(self.headers.get("X-Worker-Key")): return self.send_json({"error":"worker unauthorized"},401)
            f=ROOT/"kaggle"/"box_gpu_worker.py"
            raw=f.read_bytes(); self.send_response(200); self.send_header("Content-Type","text/x-python; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if p.path == "/api/worker/batch-script":
            if not valid_worker_key(self.headers.get("X-Worker-Key")): return self.send_json({"error":"worker unauthorized"},401)
            f=ROOT/"kaggle"/"batch_generate_from_jobs.py"
            raw=f.read_bytes(); self.send_response(200); self.send_header("Content-Type","text/x-python; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if p.path == "/api/worker/reference":
            if not valid_worker_key(self.headers.get("X-Worker-Key")): return self.send_json({"error":"worker unauthorized"},401)
            q=parse_qs(p.query); project_id=(q.get("project_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{12}",project_id): return self.send_json({"error":"invalid project"},400)
            job_id=(q.get("job_id") or [""])[0]
            if job_id:
                if not re.fullmatch(r"[a-f0-9]{16,32}",job_id): return self.send_json({"error":"invalid job id"},400)
                job_path=GPU_JOBS/f"{job_id}.json"
                if not job_path.is_file(): return self.send_json({"error":"job not found"},404)
                try:
                    job=json.loads(job_path.read_text("utf-8"))
                    if job.get("project_id") != project_id: return self.send_json({"error":"job project mismatch"},403)
                    qname=(q.get("character_name") or [""])[0].strip()
                    requested=job.get("reference_file") or next((c.get("reference_file") for c in job.get("characters",[]) if c.get("reference_file")),None)
                    if qname:
                        character=next((c for c in job.get("characters",[]) if str(c.get("name","")).strip()==qname),None)
                        requested=(character or {}).get("reference_file")
                    if requested:
                        candidate=(UPLOADS/Path(requested).name).resolve()
                        if Path(requested).name != requested or not requested.startswith(project_id+"_") or not candidate.is_file() or UPLOADS.resolve() not in candidate.parents or candidate.suffix.lower() not in (".png",".jpg",".jpeg"):
                            return self.send_json({"error":"invalid job reference"},400)
                        f=candidate
                        self.send_response(200); self.send_header("Content-Type","application/octet-stream"); self.send_header("Content-Length",str(f.stat().st_size)); self.end_headers()
                        with f.open("rb") as stream:
                            while chunk:=stream.read(1024*1024): self.wfile.write(chunk)
                        return
                except Exception as e: return self.send_json({"error":"job reference lookup failed"},500)
            # Prefer the exact approved character base image saved by the episode.
            # Uploaded filenames are opaque hashes, so filename-only discovery is unsafe.
            production_path=production_file(project_id,"episode-01")
            approved=None
            if production_path.is_file():
                try:
                    episode=json.loads(production_path.read_text("utf-8"))
                    if episode.get("approved_base_image") and episode.get("base_image_file"):
                        candidate=(UPLOADS/Path(episode["base_image_file"]).name).resolve()
                        if candidate.is_file() and UPLOADS.resolve() in candidate.parents and candidate.suffix.lower() in (".png",".jpg",".jpeg"):
                            approved=candidate
                except Exception: pass
            if approved is None:
                refs=list(UPLOADS.glob(project_id+"_*heroine_reference*")) + list(UPLOADS.glob(project_id+"_*reference*"))
                refs=[f for f in refs if f.suffix.lower() in (".png",".jpg",".jpeg")]
                if not refs: return self.send_json({"error":"reference not found; please approve a character base image first"},404)
                approved=max(refs,key=lambda x:x.stat().st_size)
            f=approved
            self.send_response(200); self.send_header("Content-Type","application/octet-stream"); self.send_header("Content-Length",str(f.stat().st_size)); self.end_headers()
            with f.open("rb") as stream:
                while chunk:=stream.read(1024*1024): self.wfile.write(chunk)
            return
        if p.path == "/api/projects":
            projects=load_projects()
            for item in projects:
                try:
                    base=project_workspace(item["id"])
                    item["workspace"]={"path":str(base.relative_to(ROOT)),"folders":[{"name":"故事剧本","path":"story"},{"name":"角色资产","path":"characters"},{"name":"参考素材","path":"references"},{"name":"分集制作","path":"episodes"},{"name":"配音音频","path":"audio"},{"name":"成片导出","path":"exports"}]}
                except Exception: pass
            return self.send_json({"projects":projects})
        if p.path == "/api/providers/catalog":
            catalog=ROOT/"deployment"/"providers.json"
            return self.send_json(json.loads(catalog.read_text("utf-8")))
        if p.path == "/api/nvidia/status":
            row=apply_provider_secret("nvidia-nim")
            return self.send_json({"configured":bool(row.get("api_key")),"base_url":row.get("base_url","https://integrate.api.nvidia.com/v1"),"model":row.get("model") or "nvidia/nemotron-3.5-bolt-e4b","masked_key":("••••"+row.get("api_key","")[-4:]) if row.get("api_key") else "","key_value_exposed":False})
        if p.path == "/api/providers/keys":
            rows=load_provider_secrets(); safe={k:{"configured":bool(v.get("api_key")),"masked_key":("••••"+v.get("api_key","")[-4:]) if v.get("api_key") else "","base_url":v.get("base_url",""),"model":v.get("model","")} for k,v in rows.items()}
            return self.send_json({"providers":safe})
        if p.path == "/api/usage":
            data=usage_data(); providers=data.get("providers",[]); events=data.get("events",[])
            q=parse_qs(p.query); project_filter=(q.get("project_id") or [""])[0]
            if project_filter: events=[e for e in events if e.get("project_id")==project_filter]
            summary={}
            for e in events:
                k=e.get("provider","unknown"); row=summary.setdefault(k,{"calls":0,"success":0,"failed":0,"units":0})
                row["calls"]+=1; row["success"]+=int(e.get("status")=="completed"); row["failed"]+=int(e.get("status")=="failed"); row["units"]+=float(e.get("units",0) or 0)
            return self.send_json({"providers":providers,"events":events[-100:],"summary":summary,"note":"调用统计来自本控制台记录；第三方剩余额度需平台授权接口或手动填写上限，未接入的平台不会伪造实时额度。"})
        if p.path == "/api/episodes":
            q=parse_qs(p.query); project_id=(q.get("project_id") or [""])[0]
            if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
            base=project_workspace(project_id); meta=base/"episodes.json"
            if not meta.exists():
                meta.write_text(json.dumps([{"id":"episode-01","name":"第一集","created_at":datetime.datetime.now(datetime.timezone.utc).isoformat()}],ensure_ascii=False,indent=2),"utf-8")
            return self.send_json({"episodes":json.loads(meta.read_text("utf-8"))})
        if p.path == "/api/project/assets":
            q=parse_qs(urlparse(self.path).query); project_id=(q.get("project_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{12}",project_id) or not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
            base=project_workspace(project_id); found=[]
            roots=[UPLOADS,base]
            for root in roots:
                for f in root.rglob('*'):
                    if not f.is_file() or f.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.gif','.mp4','.webm','.wav','.mp3','.m4a'):
                        continue
                    if root==UPLOADS and not f.name.startswith(project_id+'_'): continue
                    rel=str(f.resolve().relative_to(ROOT.resolve()))
                    found.append({"name":f.name,"size":f.stat().st_size,"kind":"video" if f.suffix.lower() in ('.mp4','.webm') else "audio" if f.suffix.lower() in ('.wav','.mp3','.m4a') else "image","url":"/api/media/file?path="+quote(rel)})
            found.sort(key=lambda x:x['name'])
            return self.send_json({"assets":found[-200:]})
        if p.path == "/api/media/file":
            q=parse_qs(urlparse(self.path).query); rel=(q.get("path") or [""])[0]
            try: f=(ROOT/rel).resolve()
            except Exception: return self.send_json({"error":"文件路径无效"},400)
            allowed=[UPLOADS.resolve(),WORKSPACES.resolve(),RENDERS.resolve()]
            if not f.is_file() or not any(root==f or root in f.parents for root in allowed): return self.send_json({"error":"文件不存在或无权访问"},404)
            import mimetypes
            mime=mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
            self.send_response(200); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(f.stat().st_size)); self.send_header('Cache-Control','private, no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers()
            try:
                with f.open('rb') as stream:
                    while chunk:=stream.read(1024*1024): self.wfile.write(chunk)
            except (BrokenPipeError,ConnectionResetError): pass
            return
        if p.path == "/api/project/files":
            q=parse_qs(self.path and urlparse(self.path).query); project_id=(q.get("project_id") or [""])[0]
            if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
            base=project_workspace(project_id); items=[]
            for folder in ("story","characters","references","episodes","audio","exports","logs"):
                path=base/folder
                files=[{"name":f.name,"size":f.stat().st_size,"modified":datetime.datetime.fromtimestamp(f.stat().st_mtime).astimezone().isoformat()} for f in path.iterdir() if f.is_file()]
                if folder=="episodes":
                    for sub in sorted(path.iterdir()):
                        if sub.is_dir():
                            files.append({"name":sub.name+"/","size":0,"modified":"分集文件夹"})
                            files.extend({"name":str(f.relative_to(path)),"size":f.stat().st_size,"modified":datetime.datetime.fromtimestamp(f.stat().st_mtime).astimezone().isoformat()} for f in sub.rglob('*') if f.is_file())
                items.append({"name":folder,"files":files})
            return self.send_json({"project_id":project_id,"folders":items})
        if p.path == "/api/render/status":
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{16}",job_id): return self.send_json({"error":"job id invalid"},400)
            matches=list(RENDERS.glob(f"*/{job_id}.json"))
            if not matches: return self.send_json({"error":"任务不存在"},404)
            return self.send_json({"job":json.loads(matches[0].read_text("utf-8"))})
        if p.path == "/api/render/access":
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{16}",job_id): return self.send_json({"error":"job id invalid"},400)
            matches=list(RENDERS.glob(f"*/{job_id}/episode_preview.mp4"))
            if not matches: return self.send_json({"error":"视频尚未生成"},404)
            token=secrets.token_urlsafe(32)
            with MEDIA_TOKEN_LOCK: MEDIA_TOKENS[token]=time.time()+6*3600
            return self.send_json({"token":token,"expires_in":21600,"size":matches[0].stat().st_size})
        if p.path == "/api/render/download":
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{16}",job_id): return self.send_json({"error":"job id invalid"},400)
            matches=list(RENDERS.glob(f"*/{job_id}/episode_preview.mp4"))
            if not matches: return self.send_json({"error":"视频尚未生成"},404)
            f=matches[0]; self.send_response(200); self.send_header("Content-Type","video/mp4"); self.send_header("Content-Length",str(f.stat().st_size)); self.send_header("Content-Disposition",'attachment; filename="manga-episode-preview.mp4"'); self.end_headers()
            with f.open("rb") as stream:
                while chunk:=stream.read(1024*1024): self.wfile.write(chunk)
            return
        if p.path == "/api/pipeline":
            q=parse_qs(p.query); project_id=(q.get("project_id") or [""])[0]
            project=next((x for x in load_projects() if x.get("id")==project_id),None)
            if not project: return self.send_json({"error":"项目不存在"},404)
            episode_id=(parse_qs(p.query).get("episode_id") or ["episode-01"])[0]
            prod_path=production_file(project_id,episode_id)
            if not prod_path.exists(): return self.send_json({"error":"请先保存制作资料"},404)
            try:
                from production_pipeline import compile_production
                production=json.loads(prod_path.read_text("utf-8"))
                artifacts=compile_production(production,project,project.get("files",[]),UPLOADS)
                out=PRODUCTION/(project_id+"_pipeline"); out.mkdir(parents=True,exist_ok=True)
                for name,obj in artifacts.items():
                    target=out/(name+".json"); tmp=target.with_suffix(".tmp")
                    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),"utf-8"); tmp.replace(target)
                qc=artifacts["qc_report"]
                return self.send_json({"ok":True,"project_id":project_id,"qc_status":qc["status"],
                    "summary":qc["summary"],"shots":qc["shots"],"regeneration_jobs":len(artifacts["regeneration_brief"]["jobs"]),
                    "timeline_duration_seconds":artifacts["canonical_timeline"]["duration_seconds"],
                    "artifacts":list(artifacts)})
            except Exception as e: return self.send_json({"error":"制作流水线编译失败："+str(e)},500)
        if p.path == "/api/production":
            q=parse_qs(p.query); project_id=(q.get("project_id") or [""])[0]; episode_id=(q.get("episode_id") or ["episode-01"])[0]
            if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
            f=production_file(project_id,episode_id)
            try: data=json.loads(f.read_text("utf-8")) if f.exists() else {"project_id":project_id,"episode_id":episode_id,"story":"","characters":[],"storyboard":[]}
            except Exception: data={"project_id":project_id,"episode_id":episode_id,"story":"","characters":[],"storyboard":[]}
            return self.send_json({"production":data})
        if p.path.startswith("/api/files/"):
            name=Path(p.path.removeprefix("/api/files/")).name
            f=UPLOADS/name
            if not f.is_file(): return self.send_json({"error":"not found"},404)
            self.send_response(200); self.send_header("Content-Type","application/octet-stream"); self.send_header("Content-Length",str(f.stat().st_size)); self.send_header("Content-Disposition",f'attachment; filename="{quote(name)}"'); self.end_headers()
            with f.open("rb") as stream:
                while chunk:=stream.read(1024*1024): self.wfile.write(chunk)
            return
        # Only serve the public app shell. Never expose project data, credentials,
        # source files, backups, or Kaggle artifacts through SimpleHTTPRequestHandler.
        if p.path not in ("/", "/index.html"):
            return self.send_json({"error":"not found"},404)
        return super().do_GET()
    def do_HEAD(self):
        p=urlparse(self.path)
        if p.path in ("/", "/index.html"):
            return super().do_HEAD()
        self.send_response(404); self.end_headers()
    def do_POST(self):
        global ACCESS_KEY, VALID_ACCESS_KEYS
        p=urlparse(self.path)
        worker_path = p.path.startswith("/api/worker/") or p.path in ("/api/tts/result",)
        valid = valid_worker_key(self.headers.get("X-Worker-Key")) if worker_path else self.headers.get("X-Access-Key") in VALID_ACCESS_KEYS
        if not valid: return self.send_json({"error":"访问凭据无效，请检查登录或Worker配置"},401)
        if p.path == "/api/settings/access-key":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>4096: return self.send_json({"error":"请求过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                new_key=str(obj.get("new_key","")).strip()
                if len(new_key)<8 or len(new_key)>128: return self.send_json({"error":"新口令长度必须为8到128位"},400)
                tmp=DATA/"access.key.tmp"
                fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(fd,"w",encoding="utf-8") as f: f.write(new_key)
                os.replace(tmp,DATA/"access.key")
                try: (DATA/"access.key").chmod(0o600)
                except OSError: pass
                ACCESS_KEY=new_key; VALID_ACCESS_KEYS={new_key}
                return self.send_json({"ok":True,"message":"后台登录口令已更新，请使用新口令重新登录"})
            except Exception as e: return self.send_json({"error":"更新口令失败："+str(e)},400)
        if p.path == "/api/tts/queue":
            try:
                n=int(self.headers.get("Content-Length","0")); obj=json.loads(self.rfile.read(n) or b"{}")
                project_id=str(obj.get("project_id","")); episode_id=str(obj.get("episode_id","episode-01"))
                if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
                production=production_file(project_id,episode_id)
                if not production.exists(): return self.send_json({"error":"请先保存该集制作资料"},404)
                text=str(obj.get("text","" )).strip()
                if not text or len(text)>5000: return self.send_json({"error":"试听文本不能为空且不能超过5000字"},400)
                speaker=str(obj.get("speaker","Serena")); allowed={"Vivian","Serena","Uncle_Fu","Dylan","Eric"}
                if speaker not in allowed: return self.send_json({"error":"请选择支持的中文音色"},400)
                job_id=uuid.uuid4().hex[:16]
                job={"job_id":job_id,"project_id":project_id,"episode_id":episode_id,"text":text,"speaker":speaker,"instruct":str(obj.get("instruct","使用自然的中国大陆普通话，吐字清楚，语气自然"))[:500],"model":"Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice","language":"Chinese","status":"queued","created_at":datetime.datetime.now(datetime.timezone.utc).isoformat()}
                (TTS_JOBS/(job_id+".json")).write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8")
                usage_event(project_id,"普通话TTS","语音试听",len(text),"字符",job["model"],"queued","等待免费Kaggle TTS Worker",job_id)
                return self.send_json({"job_id":job_id,"status":"queued","message":"已加入普通话TTS免费GPU队列"},202)
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/tts/result":
            if not valid_worker_key(self.headers.get("X-Worker-Key")): return self.send_json({"error":"worker unauthorized"},401)
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{16}",job_id): return self.send_json({"error":"job id invalid"},400)
            f=TTS_JOBS/(job_id+".json")
            if not f.exists(): return self.send_json({"error":"job not found"},404)
            n=int(self.headers.get("Content-Length","0"))
            if n<44 or n>30*1024*1024: return self.send_json({"error":"audio size invalid"},413)
            raw=self.rfile.read(n)
            if len(raw)!=n or raw[:4]!=b"RIFF" or raw[8:12]!=b"WAVE": return self.send_json({"error":"invalid WAV payload"},400)
            job=json.loads(f.read_text("utf-8")); base=project_workspace(job["project_id"])
            audio=base/"audio"/(job["episode_id"]+"_"+job_id+".wav"); audio.write_bytes(raw)
            job.update(status="completed",audio_file=str(audio.relative_to(ROOT)),audio_size=n,completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            f.write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8")
            update_usage_event(job_id,"completed",f"试听完成：{audio.name}")
            return self.send_json({"ok":True,"job_id":job_id,"audio_file":job["audio_file"]})
        if p.path == "/api/tts/fail":
            if not valid_worker_key(self.headers.get("X-Worker-Key")): return self.send_json({"error":"worker unauthorized"},401)
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]; f=TTS_JOBS/(job_id+".json")
            if not f.exists(): return self.send_json({"error":"job not found"},404)
            job=json.loads(f.read_text("utf-8")); job.update(status="failed",error=self.rfile.read(int(self.headers.get("Content-Length","0"))).decode("utf-8","replace")[:2000]); f.write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8")
            update_usage_event(job_id,"failed",job["error"]); return self.send_json({"ok":True})
        if p.path == "/api/worker/result":
            if not valid_worker_key(self.headers.get("X-Worker-Key")): return self.send_json({"error":"worker unauthorized"},401)
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{16,32}",job_id): return self.send_json({"error":"invalid job id"},400)
            job_file=GPU_JOBS/(job_id+".json")
            if not job_file.exists(): return self.send_json({"error":"job not found"},404)
            n=int(self.headers.get("Content-Length","0"))
            if n<1 or n>80*1024*1024: return self.send_json({"error":"result archive size invalid"},413)
            raw=self.rfile.read(n)
            if len(raw)!=n: return self.send_json({"error":"upload interrupted"},400)
            import zipfile, io
            try:
                z=zipfile.ZipFile(io.BytesIO(raw)); names=z.namelist()
                if not names or any(Path(x).is_absolute() or ".." in Path(x).parts for x in names): raise ValueError("unsafe archive")
                dest=UPLOADS/(json.loads(job_file.read_text("utf-8"))["project_id"]+"_gpu_"+job_id)
                dest.mkdir(exist_ok=True)
                z.extractall(dest)
                job=json.loads(job_file.read_text("utf-8"))
                episode_id=job.get("episode_id","episode-01")
                episode_root=project_workspace(job["project_id"])/"episodes"/episode_id/"keyframes"; episode_root.mkdir(parents=True,exist_ok=True)
                import shutil
                copied=[]
                for img in dest.rglob('*'):
                    if img.is_file() and img.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
                        target=episode_root/img.name; shutil.copy2(img,target); copied.append(str(target.relative_to(ROOT)))
                job.update(status="completed",result_dir=str(dest.relative_to(ROOT)),result_files=names,keyframes=copied,completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
                job_file.write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8")
                update_usage_event(job_id,"completed",f"GPU任务完成，返回{len(names)}个文件")
                return self.send_json({"ok":True,"job_id":job_id,"files":len(names)})
            except Exception as e: return self.send_json({"error":"invalid result archive: "+str(e)},400)
        if p.path == "/api/worker/fail":
            if not valid_worker_key(self.headers.get("X-Worker-Key")): return self.send_json({"error":"worker unauthorized"},401)
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{16,32}",job_id): return self.send_json({"error":"invalid job id"},400)
            f=GPU_JOBS/(job_id+".json")
            if not f.exists(): return self.send_json({"error":"job not found"},404)
            job=json.loads(f.read_text("utf-8")); job.update(status="failed",error=self.rfile.read(int(self.headers.get("Content-Length","0"))).decode("utf-8","replace")[:2000])
            f.write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8"); update_usage_event(job_id,"failed",job.get("error","GPU任务失败")); return self.send_json({"ok":True})
        if p.path == "/api/episodes":
            try:
                n=int(self.headers.get("Content-Length","0")); obj=json.loads(self.rfile.read(n) or b"{}")
                project_id=str(obj.get("project_id",""))
                if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
                base=project_workspace(project_id); meta=base/"episodes.json"
                episodes=json.loads(meta.read_text("utf-8")) if meta.exists() else [{"id":"episode-01","name":"第一集"}]
                number=max([int(x["id"].split("-")[-1]) for x in episodes]+[0])+1
                episode={"id":f"episode-{number:02d}","name":str(obj.get("name") or f"第{number}集")[:80],"created_at":datetime.datetime.now(datetime.timezone.utc).isoformat()}
                episodes.append(episode); meta.write_text(json.dumps(episodes,ensure_ascii=False,indent=2),"utf-8")
                production_file(project_id,episode["id"])
                return self.send_json({"episode":episode,"episodes":episodes},201)
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/projects":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>100000: return self.send_json({"error":"request too large"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                name=str(obj.get("name","")).strip()[:100]; desc=str(obj.get("desc","")).strip()[:3000]
                if not name: return self.send_json({"error":"项目名称不能为空"},400)
                items=load_projects()
                item={"id":uuid.uuid4().hex[:12],"name":name,"desc":desc,"created_at":datetime.datetime.now().astimezone().isoformat(),"files":[]}
                items.insert(0,item); save_projects(items)
                project_workspace(item["id"])
                return self.send_json({"project":item},201)
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/providers/keys":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>20000: return self.send_json({"error":"请求过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                provider=str(obj.get("provider_id", ""))
                if provider not in ("nvidia-nim","generic-openai-compatible"): return self.send_json({"error":"当前仅支持NVIDIA和OpenAI兼容API密钥"},400)
                key=str(obj.get("api_key", "")).strip()
                if not key: return self.send_json({"error":"请输入API Key"},400)
                if len(key)>4096: return self.send_json({"error":"Key长度异常"},400)
                base=str(obj.get("base_url", "")).strip().rstrip("/")
                if provider=="nvidia-nim": base=base or "https://integrate.api.nvidia.com/v1"
                if not base.startswith("https://"): return self.send_json({"error":"API地址必须使用HTTPS"},400)
                model=str(obj.get("model", "")).strip()[:180]
                rows=load_provider_secrets(); rows[provider]={"api_key":key,"base_url":base,"model":model,"updated_at":datetime.datetime.now(datetime.timezone.utc).isoformat()}
                save_provider_secrets(rows)
                if provider=="nvidia-nim": apply_provider_secret(provider)
                return self.send_json({"ok":True,"provider_id":provider,"masked_key":"••••"+key[-4:],"stored_outside_project":True})
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/providers/test":
            try:
                n=int(self.headers.get("Content-Length","0")); obj=json.loads(self.rfile.read(n) or b"{}")
                provider=str(obj.get("provider_id", ""))
                if provider not in ("nvidia-nim","generic-openai-compatible"): return self.send_json({"ok":False,"error":"不支持的平台"},400)
                row=apply_provider_secret(provider)
                if not row.get("api_key"): return self.send_json({"ok":False,"error":"尚未保存该平台Key"},400)
                base=row.get("base_url","").rstrip("/")
                model=row.get("model","").strip() or ("nvidia/nemotron-3.5-bolt-e4b" if provider=="nvidia-nim" else "deepseek-flash")
                payload=json.dumps({"model":model,"messages":[{"role":"user","content":"Reply with exactly: API连接成功"}],"max_tokens":24,"temperature":0}).encode()
                req=urllib.request.Request(base+"/chat/completions",data=payload,headers={"Authorization":"Bearer "+row["api_key"],"Content-Type":"application/json"})
                with urllib.request.urlopen(req,timeout=45) as resp: data=json.loads(resp.read())
                content=data.get("choices",[{}])[0].get("message",{}).get("content","")
                usage_event("","NVIDIA API" if provider=="nvidia-nim" else "兼容API","连接测试",1,"次",model,"completed","连接测试成功")
                return self.send_json({"ok":True,"provider_id":provider,"model":data.get("model",model),"reply":str(content)[:200]})
            except urllib.error.HTTPError as e:
                detail=e.read(1000).decode("utf-8","replace")
                return self.send_json({"ok":False,"error":"平台返回HTTP "+str(e.code)+"："+detail[:500]},502)
            except Exception as e: return self.send_json({"ok":False,"error":"连接失败："+str(e)},502)
        if p.path == "/api/ai/storyboard":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>50000: return self.send_json({"error":"请求内容过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                story=str(obj.get("story", "")).strip()[:12000]
                if len(story)<20: return self.send_json({"error":"故事内容太短，请先写入至少20个字符的故事"},400)
                project_id=str(obj.get("project_id",""))
                if project_id and not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
                style=str(obj.get("style") or "精致2D国漫")[:100]
                style_notes=str(obj.get("style_notes") or "")[:1200]
                characters=obj.get("characters") if isinstance(obj.get("characters"),list) else []
                characters=characters[:20]
                deep=apply_provider_secret("generic-openai-compatible")
                nv=apply_provider_secret("nvidia-nim")
                if not deep.get("api_key") or not nv.get("api_key"):
                    return self.send_json({"error":"DeepSeek和NVIDIA两个平台都必须先保存Key"},503)
                def ask(row, default_model, messages, max_tokens):
                    base=str(row.get("base_url") or "").rstrip("/")
                    model=str(row.get("model") or default_model)
                    payload=json.dumps({"model":model,"messages":messages,"max_tokens":max_tokens,"temperature":0.25},ensure_ascii=False).encode()
                    req=urllib.request.Request(base+"/chat/completions",data=payload,headers={"Authorization":"Bearer "+row["api_key"],"Content-Type":"application/json"})
                    with urllib.request.urlopen(req,timeout=90) as resp: data=json.loads(resp.read())
                    return data.get("choices",[{}])[0].get("message",{}).get("content",""),data.get("model",model),data.get("usage",{})
                char_text=json.dumps(characters,ensure_ascii=False)[:10000]
                system1="你是中国大陆成熟商业二维国漫的编剧与分镜导演。只输出严格JSON对象，不要Markdown代码围栏。结构为{\"shots\":[{\"id\":1,\"shot_type\":\"远景/中景/近景/特写\",\"visual\":\"画面可见内容\",\"action\":\"动作\",\"dialogue\":\"对白或旁白\",\"camera_prompt\":\"镜头景别与构图\"}]}。根据输入故事拆成8到16个有因果、有节奏的关键镜头；不添加故事没有的人物、道具或设定；若故事正文中的“人物锚点”与外部角色资料冲突，以故事正文明确的人物锚点为最高优先级；角色姓名、年龄、外貌、服装不得擅自改写；场景连续。视觉风格为精致2D国漫/中国动画电影概念美术，东方审美、成熟人物比例、细腻线稿与国风色彩，不要日式萌系、大眼脸、赛璐璐校园番风格。"
                draft,deep_model,deep_usage=ask(deep,"deepseek-flash",[{"role":"system","content":system1},{"role":"user","content":"画风："+style+"\n补充要求："+style_notes+"\n角色设定："+char_text+"\n故事：\n"+story}],3600)
                Path("/tmp/manga-deepseek-last.txt").write_text(str(draft),"utf-8")
                # Strip common fenced wrappers before passing the draft to the visual director.
                clean=draft.strip()
                if clean.startswith("```"):
                    clean=clean.split("\n",1)[-1]
                    if clean.endswith("```"): clean=clean[:-3]
                def parse_json(text):
                    text=str(text or "").strip()
                    if text.startswith("```"):
                        text=text.split("\n",1)[-1]
                        if text.endswith("```"): text=text[:-3]
                    a=text.find("{"); b=text.rfind("}")
                    if a<0 or b<=a: raise ValueError("模型未返回有效JSON："+text[:240])
                    return json.loads(text[a:b+1])
                # NVIDIA receives only compact shot facts and returns visual prompts,
                # avoiding a second full rewrite that can truncate large JSON payloads.
                draft_obj=parse_json(draft)
                draft_shots=draft_obj.get("shots") if isinstance(draft_obj,dict) else None
                if not isinstance(draft_shots,list) or not 1<=len(draft_shots)<=16: raise ValueError("DeepSeek返回的分镜数量或结构不正确")
                compact=[{k:x.get(k,"") for k in ("id","shot_type","visual","action","camera_prompt")} for x in draft_shots if isinstance(x,dict)]
                system2="你是中国商业2D国漫视觉总监。为输入的每个镜头生成prompt和negative_prompt。只输出严格JSON：{\"shots\":[{\"id\":1,\"prompt\":\"...\",\"negative_prompt\":\"...\"}]}。id必须与输入完全一致，不增删镜头。画风精致2D中国动画电影、东方古典写实、自然成熟的东方五官比例、手绘线稿、国风色彩、层次丰富的场景、电影级光影。禁止日式动漫脸、夸张大眼、萌系、校园番、日式赛璐璐、3D塑料感、游戏立绘、角色展示背景、文字水印。prompt写成适合Stable Diffusion的简洁英文视觉提示词，negative_prompt也用英文，避免冗长。"
                refined,nv_model,nv_usage=ask(nv,"nvidia/nemotron-3.5-bolt-e4b",[{"role":"system","content":system2},{"role":"user","content":"风格补充："+style+"；"+style_notes+"\n分镜："+json.dumps(compact,ensure_ascii=False)}],3000)
                Path("/tmp/manga-nvidia-last.txt").write_text(str(refined),"utf-8")
                prompt_obj=parse_json(refined)
                prompt_rows=prompt_obj.get("shots") if isinstance(prompt_obj,dict) else None
                if not isinstance(prompt_rows,list): raise ValueError("NVIDIA未返回有效提示词列表")
                prompt_map={str(x.get("id")):x for x in prompt_rows if isinstance(x,dict)}
                normalized=[]
                for i,shot in enumerate(draft_shots,1):
                    if not isinstance(shot,dict): continue
                    visual=prompt_map.get(str(shot.get("id",i)),{})
                    normalized.append({"id":i,"shot_type":str(shot.get("shot_type") or "中景")[:20],"visual":str(shot.get("visual") or "")[:1800],"action":str(shot.get("action") or "")[:800],"dialogue":str(shot.get("dialogue") or "")[:800],"camera_prompt":str(shot.get("camera_prompt") or "")[:500],"prompt":str(visual.get("prompt") or "")[:2200],"negative_prompt":str(visual.get("negative_prompt") or "")[:1200]})
                if not normalized: raise ValueError("没有可用分镜")
                usage_event(project_id,"DeepSeek","AI故事拆解与分镜初稿",int(deep_usage.get("total_tokens",1) or 1),"tokens",deep_model,"completed","双模型分镜工作流")
                usage_event(project_id,"NVIDIA NIM","国漫画风优化与分镜质检",int(nv_usage.get("total_tokens",1) or 1),"tokens",nv_model,"completed","双模型分镜工作流")
                return self.send_json({"ok":True,"shots":normalized,"models":{"story":"DeepSeek / "+str(deep_model),"visual":"NVIDIA NIM / "+str(nv_model)},"note":"模型生成结果是待审核初稿，必须人工确认后再提交GPU生成。"})
            except urllib.error.HTTPError as e:
                detail=e.read(800).decode("utf-8","replace")
                return self.send_json({"error":"模型平台返回HTTP "+str(e.code)+"："+detail[:350]},502)
            except Exception as e: return self.send_json({"error":"双模型分镜工作流失败："+str(e)},502)
        if p.path == "/api/llm/chat":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>200000: return self.send_json({"error":"请求内容过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                provider=str(obj.get("provider_id","generic-openai-compatible"))
                if provider not in ("nvidia-nim","generic-openai-compatible"): return self.send_json({"error":"不支持的模型平台"},400)
                row=apply_provider_secret(provider)
                if not row.get("api_key"): return self.send_json({"error":"该平台尚未保存API Key"},503)
                messages=obj.get("messages")
                if not isinstance(messages,list) or not messages or len(messages)>30: return self.send_json({"error":"messages必须是1至30条消息"},400)
                model=str(obj.get("model") or row.get("model") or ("nvidia/nemotron-3.5-bolt-e4b" if provider=="nvidia-nim" else "deepseek-flash"))
                base=str(row.get("base_url") or ("https://integrate.api.nvidia.com/v1" if provider=="nvidia-nim" else "https://api.deepseek.com")).rstrip('/')
                payload=json.dumps({"model":model,"messages":messages,"max_tokens":int(obj.get("max_tokens",1600)),"temperature":float(obj.get("temperature",0.2))}).encode()
                req=urllib.request.Request(base+'/chat/completions',data=payload,headers={"Authorization":"Bearer "+row["api_key"],"Content-Type":"application/json"})
                with urllib.request.urlopen(req,timeout=90) as resp: data=json.loads(resp.read())
                usage=data.get('usage',{})
                usage_event(str(obj.get('project_id','')),provider,"LLM工作流调用",int(usage.get('total_tokens',1) or 1),"tokens",model,"completed","模型请求成功")
                return self.send_json({"ok":True,"provider_id":provider,"model":data.get("model",model),"content":data.get("choices",[{}])[0].get("message",{}).get("content","")})
            except urllib.error.HTTPError as e:
                detail=e.read(800).decode('utf-8','replace')
                return self.send_json({"error":"模型平台返回HTTP "+str(e.code)+"："+detail[:350]},502)
            except Exception as e: return self.send_json({"error":"模型调用失败："+str(e)},502)
        if p.path == "/api/nvidia/chat":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>200000: return self.send_json({"error":"请求内容过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                messages=obj.get("messages")
                if not isinstance(messages,list) or not messages or len(messages)>30:
                    return self.send_json({"error":"messages必须是1至30条消息的数组"},400)
                apply_provider_secret("nvidia-nim")
                from deployment.nvidia_client import chat
                answer=chat(messages,model=str(obj.get("model") or "") or None)
                return self.send_json({"ok":True,"provider":"nvidia-nim","model":obj.get("model") or __import__("os").environ.get("NVIDIA_MODEL","nvidia/nemotron-3.5-bolt-e4b"),"content":answer})
            except Exception as e:
                msg=str(e)
                status=503 if "not configured" in msg.lower() else 502
                return self.send_json({"error":msg},status)
        if p.path == "/api/usage/provider":
            try:
                n=int(self.headers.get("Content-Length","0")); obj=json.loads(self.rfile.read(n) or b"{}")
                name=str(obj.get("name","")).strip()[:80]
                if not name: return self.send_json({"error":"平台名称不能为空"},400)
                data=usage_data(); providers=data.setdefault("providers",[])
                row=next((x for x in providers if x.get("name")==name),None)
                if row is None: row={"name":name}; providers.append(row)
                for k in ("platform_id","model","unit","quota_limit","reset_at","quota_source","enabled"):
                    if k in obj: row[k]=obj[k]
                save_usage(data); return self.send_json({"provider":row})
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/usage/event":
            try:
                n=int(self.headers.get("Content-Length","0")); obj=json.loads(self.rfile.read(n) or b"{}")
                project_id=str(obj.get("project_id",""))
                if project_id and not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
                usage_event(project_id,str(obj.get("provider","manual"))[:80],str(obj.get("task","other"))[:80],float(obj.get("units",1)),str(obj.get("unit","次"))[:20],str(obj.get("model",""))[:100],str(obj.get("status","completed"))[:20],str(obj.get("detail",""))[:500])
                return self.send_json({"ok":True})
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/gpu/queue":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>10000: return self.send_json({"error":"request too large"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                project_id=str(obj.get("project_id",""))
                if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
                episode_id=str(obj.get("episode_id","episode-01")); prod=production_file(project_id,episode_id)
                if not prod.exists(): return self.send_json({"error":"制作资料不存在"},404)
                production=json.loads(prod.read_text("utf-8"))
                if not production.get("storyboard"): return self.send_json({"error":"请先准备分镜"},400)
                selected=obj.get("selected_shot_ids")
                all_shots=production.get("storyboard",[])
                if selected is None: selected=[int(x.get("id",i+1)) for i,x in enumerate(all_shots[:3])]
                selected=[int(x) for x in selected]
                if not 3 <= len(selected) <= 5: return self.send_json({"error":"关键帧试生成请选择3到5个分镜"},400)
                by_id={int(x.get("id",i+1)):x for i,x in enumerate(all_shots)}
                if len(set(selected))!=len(selected) or any(x not in by_id for x in selected): return self.send_json({"error":"所选分镜编号无效"},400)
                job_id=uuid.uuid4().hex[:16]
                characters=production.get("characters") or ([{"name":production.get("hero",{}).get("name","主角"),"age":"","appearance":production.get("hero",{}).get("description",""),"costume":""}] if production.get("hero") else [])
                shots=[]
                for sid in selected:
                    shot=dict(by_id[sid]); cname=str(shot.get("character") or shot.get("character_name") or "").strip()
                    match=next((c for c in characters if str(c.get("name","")).strip()==cname),None)
                    if match and match.get("reference_file"): shot["character_reference_file"]=match["reference_file"]
                    shots.append(shot)
                job={"job_id":job_id,"project_id":project_id,"episode_id":episode_id,"status":"queued","created_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"project_name":next(x["name"] for x in load_projects() if x["id"]==project_id),"style":production.get("style","Chinese anime donghua, cinematic manhua, detailed background"),"seed":20261124,"characters":characters,"approved_base_image":production.get("base_image_file"),"storyboard":shots,"selected_shot_ids":selected,"max_shots":len(selected)}
                (GPU_JOBS/(job_id+".json")).write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8")
                usage_event(project_id,"Kaggle GPU","关键帧生成任务",job["max_shots"],"镜头", "ComfyUI / SD1.5", "queued", "等待免费GPU Worker领取",job_id)
                return self.send_json({"job_id":job_id,"status":"queued","message":"已加入免费 GPU Worker 队列；等待 Kaggle Worker 领取"},202)
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/render":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>10000: return self.send_json({"error":"request too large"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                project_id=str(obj.get("project_id",""))
                if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
                episode_id=str(obj.get("episode_id","episode-01")); prod=production_file(project_id,episode_id)
                if not prod.exists() or not json.loads(prod.read_text("utf-8")).get("storyboard"): return self.send_json({"error":"请先保存至少一个分镜"},400)
                production=json.loads(prod.read_text("utf-8")); shots=production.get("storyboard",[])
                selected=obj.get("selected_shot_ids")
                if selected is not None:
                    selected=[int(x) for x in selected]
                    if not 3 <= len(selected) <= 5: return self.send_json({"error":"分镜样片请选择3到5个镜头"},400)
                    if len(set(selected))!=len(selected) or any(x not in [int(z.get("id",i+1)) for i,z in enumerate(shots)] for x in selected): return self.send_json({"error":"所选镜头编号无效"},400)
                else: selected=[int(z.get("id",i+1)) for i,z in enumerate(shots)]
                job_id=uuid.uuid4().hex[:16]; job_dir=RENDERS/project_id/job_id; job_dir.mkdir(parents=True,exist_ok=True)
                state=RENDERS/project_id/f"{job_id}.json"
                state.write_text(json.dumps({"job_id":job_id,"project_id":project_id,"episode_id":episode_id,"selected_shot_ids":selected,"status":"queued","progress":0,"message":"任务排队中"},ensure_ascii=False),"utf-8")
                log=(job_dir/"render.log").open("w")
                subprocess.Popen([sys.executable,str(ROOT/"render_episode.py"),"--project-id",project_id,"--episode-id",episode_id,"--job-id",job_id,"--shot-ids",','.join(map(str,selected))],cwd=str(ROOT),stdout=log,stderr=log,start_new_session=True)
                usage_event(project_id,"本地渲染","关键帧动态剪辑",len(selected),"镜头","FFmpeg","queued","这是静态关键帧动态剪辑，不是AI视频生成",job_id)
                return self.send_json({"job_id":job_id,"status":"queued","selected_shot_ids":selected},202)
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/production":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>2*1024*1024: return self.send_json({"error":"制作资料超过2MB"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                project_id=str(obj.get("project_id","")); episode_id=str(obj.get("episode_id","episode-01"))
                if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
                story=str(obj.get("story",""))[:50000]
                characters=obj.get("characters",[]); storyboard=obj.get("storyboard",[])
                if not isinstance(characters,list) or not isinstance(storyboard,list): return self.send_json({"error":"人物和分镜必须为列表"},400)
                data={k:v for k,v in obj.items() if k not in {"project_id","episode_id","story","characters","storyboard","updated_at"}}
                data.update({"project_id":project_id,"episode_id":episode_id,"story":story,"characters":characters[:200],"storyboard":storyboard[:1000],"updated_at":datetime.datetime.now().astimezone().isoformat()})
                dest=production_file(project_id,episode_id); tmp=dest.with_suffix(".tmp")
                tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),"utf-8"); tmp.replace(dest)
                workspace=project_workspace(project_id); episode_root=workspace/"episodes"/episode_id
                if story: (workspace/"story"/(episode_id+"_story.md")).write_text(story,"utf-8")
                (workspace/"characters"/(episode_id+"_characters.json")).write_text(json.dumps(characters,ensure_ascii=False,indent=2),"utf-8")
                (workspace/"audio"/(episode_id+"_voice_settings.json")).write_text(json.dumps(data.get("voice_settings",{}),ensure_ascii=False,indent=2),"utf-8")
                (episode_root/"production.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),"utf-8")
                return self.send_json({"production":data},200)
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/upload":
            q=parse_qs(p.query); project_id=(q.get("project_id") or [""])[0]
            items=load_projects(); item=next((x for x in items if x["id"]==project_id),None)
            if not item: return self.send_json({"error":"请先选择一个有效项目"},400)
            n=int(self.headers.get("Content-Length","0"))
            if n<1 or n>MAX_UPLOAD: return self.send_json({"error":"文件为空或超过40MB限制"},413)
            rawname=(q.get("filename") or ["upload.bin"])[0]
            rawname=Path(rawname.replace("\\","/")).name
            safe=re.sub(r"[^\w.() -]","_",rawname,flags=re.UNICODE)[:120] or "upload.bin"
            stored=f"{project_id}_{uuid.uuid4().hex[:8]}_{safe}"
            dest=UPLOADS/stored
            with dest.open("wb") as f:
                remaining=n
                while remaining:
                    chunk=self.rfile.read(min(1024*1024,remaining))
                    if not chunk: break
                    f.write(chunk); remaining-=len(chunk)
            if remaining: dest.unlink(missing_ok=True); return self.send_json({"error":"上传中断"},400)
            meta={"name":safe,"stored":stored,"size":n,"uploaded_at":datetime.datetime.now().astimezone().isoformat()}
            item["files"].append(meta); save_projects(items)
            base=project_workspace(project_id)
            import shutil
            ext=Path(safe).suffix.lower()
            if ext in (".txt",".md",".pdf",".doc",".docx"): target_folder="story"
            elif ext in (".wav",".mp3",".m4a",".aac",".flac"): target_folder="audio"
            elif any(k in safe for k in ("角色","人物","立绘","三视图","基准图")): target_folder="characters"
            else: target_folder="references"
            shutil.copy2(dest,base/target_folder/stored)
            return self.send_json({"file":meta},201)
        return self.send_json({"error":"not found"},404)
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.log_date_time_string(), fmt%args), flush=True)

if __name__=="__main__":
    print("Manga API listening on 0.0.0.0:8766",flush=True)
    ThreadingHTTPServer(("0.0.0.0",8766),Handler).serve_forever()
