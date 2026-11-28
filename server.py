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
SECRET_DIR = Path(os.environ.get("MANGA_SECRET_DIR", "/var/lib/manga-console"))
PROVIDER_SECRET_FILE = SECRET_DIR / "provider-keys.json"
SECRET_DIR.mkdir(parents=True, exist_ok=True)
try: SECRET_DIR.chmod(0o700)
except OSError: pass
WORKSPACES.mkdir(parents=True, exist_ok=True)
USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
PRODUCTION.mkdir(exist_ok=True); RENDERS.mkdir(exist_ok=True)
GPU_JOBS = DATA / "gpu_jobs"
GPU_JOBS.mkdir(exist_ok=True)
SCNET_NODES = DATA / "scnet_nodes"
SCNET_NODES.mkdir(exist_ok=True)
TTS_JOBS = DATA / "tts_jobs"
TTS_JOBS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True); UPLOADS.mkdir(exist_ok=True)
MAX_UPLOAD = 40 * 1024 * 1024
ACCESS_KEY_FILE = Path(os.environ.get("MANGA_ACCESS_KEY_FILE", str(DATA / "access.key")))
try:
    ACCESS_KEY = ACCESS_KEY_FILE.read_text("utf-8").strip() if ACCESS_KEY_FILE.is_file() else ""
except OSError:
    ACCESS_KEY = ""
# One-time bootstrap token for setting the human login key when the configured
# login file is intentionally empty. It is removed immediately after success.
SETUP_TOKEN_FILE = SECRET_DIR / "login-bootstrap.token"
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


def enforce_canonical_storyboard_cast(draft_shots, draft_characters, canonical_characters):
    """Only user-registered, renderable entities may enter a generation cast.

    Names discovered by an LLM remain suggestions. Inscriptions, dialogue, and
    prop labels must never auto-create or summon a person into the frame.
    Memorial-only records are never renderable cast members.
    """
    def is_memorial(row):
        return (str(row.get("entity_type") or "") == "memorial_only"
                or "墓碑" in str(row.get("visual_lock") or "")
                or "不生成实体" in str(row.get("negative_constraints") or ""))

    canonical = {str(row.get("name") or "").strip(): row for row in canonical_characters
                 if isinstance(row, dict) and str(row.get("name") or "").strip() and not is_memorial(row)}
    suggestions = {}
    for row in draft_characters or []:
        if isinstance(row, dict):
            name = str(row.get("name") or "").strip()
            if name and name not in canonical:
                suggestions[name] = row
    normalized = []
    for source in draft_shots or []:
        if not isinstance(source, dict):
            continue
        shot = dict(source)
        raw = shot.get("characters") or shot.get("character_names") or shot.get("character") or []
        if isinstance(raw, str):
            names = [x.strip() for x in re.split(r"[、，,；;|]", raw) if x.strip()]
        elif isinstance(raw, list):
            names = list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))
        else:
            names = []
        allowed = [name for name in names if name in canonical]
        unverified = [name for name in names if name not in canonical]
        for name in unverified:
            suggestions.setdefault(name, {"name": name, "reason": "AI分镜提及但尚未由用户登记确认；可能只是道具铭文、对白或旁白"})
        shot["characters"] = allowed
        shot.pop("character_names", None)
        shot.pop("character", None)
        shot["unverified_character_names"] = unverified
        normalized.append(shot)
    return normalized, list(canonical_characters or []), list(suggestions.values())

def archive_and_replace_keyframe(source: Path, target: Path) -> Path | None:
    """Preserve an existing frame before atomically replacing it with a worker result."""
    target.parent.mkdir(parents=True,exist_ok=True)
    archived=None
    if target.is_file():
        history=target.parent/"keyframe_history"/target.stem
        history.mkdir(parents=True,exist_ok=True)
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        archived=history/(stamp+target.suffix.lower())
        import shutil
        shutil.copy2(target,archived)
    tmp=target.with_name(target.stem+".incoming"+target.suffix.lower())
    import shutil
    shutil.copy2(source,tmp)
    os.replace(tmp,target)
    return archived
MEDIA_TOKENS = {}
MEDIA_TOKEN_LOCK = threading.Lock()
KEYFRAME_TOKENS = {}
PREVIEW_TOKEN_FILE = SECRET_DIR / "preview_tokens.json"
PREVIEW_TOKEN_LOCK = threading.Lock()
PREVIEW_TTL_SECONDS = 45 * 60

def _load_preview_records():
    try:
        data=json.loads(PREVIEW_TOKEN_FILE.read_text("utf-8"))
        return data if isinstance(data,dict) else {}
    except Exception:
        return {}

def _save_preview_records(records):
    SECRET_DIR.mkdir(parents=True,exist_ok=True)
    tmp=PREVIEW_TOKEN_FILE.with_suffix(".tmp")
    fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(records,f,ensure_ascii=False)
    os.replace(tmp,PREVIEW_TOKEN_FILE)
    try: PREVIEW_TOKEN_FILE.chmod(0o600)
    except OSError: pass

def issue_preview_token():
    token=secrets.token_urlsafe(32)
    digest=__import__("hashlib").sha256(token.encode()).hexdigest()
    with PREVIEW_TOKEN_LOCK:
        rows=_load_preview_records()
        now=time.time()
        rows={k:v for k,v in rows.items() if float(v.get("expires_at",0))>now and not v.get("revoked")}
        rows[digest]={"created_at":now,"expires_at":now+PREVIEW_TTL_SECONDS,"revoked":False}
        _save_preview_records(rows)
    return token, now+PREVIEW_TTL_SECONDS

def preview_token_valid(token):
    if not token or len(token)>256: return False
    digest=__import__("hashlib").sha256(token.encode()).hexdigest()
    with PREVIEW_TOKEN_LOCK:
        row=_load_preview_records().get(digest,{})
        return bool(row and not row.get("revoked") and float(row.get("expires_at",0))>time.time())

def revoke_preview_token(token):
    if not token or len(token)>256: return False
    digest=__import__("hashlib").sha256(token.encode()).hexdigest()
    with PREVIEW_TOKEN_LOCK:
        rows=_load_preview_records(); row=rows.get(digest)
        if not row or row.get("revoked"): return False
        row["revoked"]=True; row["revoked_at"]=time.time(); rows[digest]=row; _save_preview_records(rows)
    return True



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
    def end_headers(self):
        # Prevent stale cached JavaScript from leaving login controls inert.
        if urlparse(self.path).path in ("/", "/index.html"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
        super().end_headers()
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def send_json(self, obj, status=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        p=urlparse(self.path)
        if p.path == "/media/keyframe":
            q=parse_qs(p.query); token=(q.get("token") or [""])[0]
            with MEDIA_TOKEN_LOCK:
                record=KEYFRAME_TOKENS.get(token)
                if not record or record["expiry"] < time.time(): KEYFRAME_TOKENS.pop(token,None); record=None
            if not record: return self.send_error(403,"keyframe token expired")
            f=Path(record["path"]).resolve()
            base=(WORKSPACES/record["project_id"]/"episodes"/record["episode_id"]/"keyframes").resolve()
            if base not in f.parents or not f.is_file(): return self.send_error(404,"keyframe not found")
            mime={".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp"}.get(f.suffix.lower(),"application/octet-stream")
            self.send_response(200); self.send_header("Content-Type",mime); self.send_header("Content-Length",str(f.stat().st_size)); self.send_header("Cache-Control","private, max-age=300"); self.send_header("X-Content-Type-Options","nosniff"); self.end_headers()
            with f.open("rb") as stream:
                while chunk:=stream.read(1024*1024): self.wfile.write(chunk)
            return
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
        if p.path == "/prepare-kaggle-runtime.py":
            f=ROOT/"kaggle"/"prepare_kaggle_runtime.py"
            raw=f.read_bytes(); self.send_response(200); self.send_header("Content-Type","text/x-python; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if p.path == "/batch-generate-bootstrap.py":
            f=ROOT/"kaggle"/"batch_generate_from_jobs.py"
            raw=f.read_bytes(); self.send_response(200); self.send_header("Content-Type","text/x-python; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if p.path == "/video-model-preflight.py":
            f=ROOT/"kaggle"/"video_model_preflight.py"
            raw=f.read_bytes(); self.send_response(200); self.send_header("Content-Type","text/x-python; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        worker_path = p.path.startswith("/api/worker/") or p.path in ("/api/tts/next",)
        preview_api = p.path == "/api/preview/data"
        preview_ok = preview_api and preview_token_valid(self.headers.get("X-Preview-Token", ""))
        if p.path.startswith("/api/") and p.path not in ("/api/health", "/api/worker/auth-check") and not preview_ok:
            valid = valid_worker_key(self.headers.get("X-Worker-Key")) if worker_path else self.headers.get("X-Access-Key") in VALID_ACCESS_KEYS
            if not valid: return self.send_json({"error":"访问凭据无效，请检查登录或Worker配置"},401)
        if p.path == "/api/preview/data":
            q=parse_qs(p.query); project_id=(q.get("project_id") or [""])[0]
            projects=load_projects()
            safe_projects=[{"id":x.get("id"),"name":x.get("name","未命名项目"),"created_at":x.get("created_at","")} for x in projects]
            selected=next((x for x in projects if x.get("id")==project_id),None) if project_id else (projects[0] if projects else None)
            result={"projects":safe_projects,"project":None,"production":None,"jobs":[]}
            if selected:
                pid=selected.get("id","")
                result["project"]={"id":pid,"name":selected.get("name","未命名项目"),"description":selected.get("description","")}
                episode_id=(q.get("episode_id") or ["episode-01"])[0]
                if re.fullmatch(r"episode-\d{2,3}",episode_id) and re.fullmatch(r"[a-f0-9]{12}",str(pid)):
                    pf=production_file(pid,episode_id)
                    if pf.is_file():
                        try:
                            prod=json.loads(pf.read_text("utf-8"))
                            result["production"]={k:prod.get(k) for k in ("story","characters","storyboard","style","style_notes","aspect_ratio","episode_id") if k in prod}
                        except Exception: pass
                    jobs=[]
                    for jf in sorted(GPU_JOBS.glob("*.json"),key=lambda x:x.stat().st_mtime,reverse=True):
                        try: job=json.loads(jf.read_text("utf-8"))
                        except Exception: continue
                        if job.get("project_id")==pid:
                            jobs.append({k:job.get(k) for k in ("job_id","episode_id","status","created_at","completed_at","selected_shot_ids","max_shots","error")})
                    result["jobs"]=jobs[:30]
            return self.send_json(result)
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
        if p.path == "/api/character-candidates":
            q=parse_qs(p.query); project_id=(q.get("project_id") or [""])[0]; episode_id=(q.get("episode_id") or ["episode-01"])[0]
            if not re.fullmatch(r"[a-f0-9]{12}",project_id): return self.send_json({"error":"项目编号无效"},400)
            rows=[]
            for jf in sorted(GPU_JOBS.glob("*.json"),key=lambda x:x.stat().st_mtime,reverse=True):
                try: job=json.loads(jf.read_text("utf-8"))
                except Exception: continue
                if job.get("project_id")==project_id and job.get("episode_id")==episode_id and job.get("task_type")=="character_candidates":
                    rows.append({k:job.get(k) for k in ("job_id","character_name","status","created_at","completed_at","candidates","error")})
            return self.send_json({"jobs":rows[:30]})
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
        if p.path == "/api/minimax/video/status":
            try:
                q=parse_qs(p.query); task_id=(q.get("task_id") or [""])[0]
                from minimax_video_adapter import query
                result=query(task_id)
                task=result.get("task",result)
                state=str(task.get("status") or "unknown")
                task_dir=DATA/"minimax_tasks"; task_file=task_dir/(re.sub(r'[^A-Za-z0-9_-]','_',task_id)+".json")
                if task_file.is_file():
                    record=json.loads(task_file.read_text("utf-8")); record["status"]=state; record["last_checked_at"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
                    if isinstance(task.get("content"),dict): record["video_url"]=task["content"].get("url","")
                    if task.get("error"): record["error"]=str(task.get("error"))[:1000]
                    task_file.write_text(json.dumps(record,ensure_ascii=False,indent=2),"utf-8")
                usage_event("","MiniMax-H3","视频任务状态查询",1,"次","MiniMax-H3",state,"任务状态更新",task_id)
                safe={"task_id":task_id,"status":state,"video_url":(task.get("content") or {}).get("url") if isinstance(task.get("content"),dict) else None,"error":str(task.get("error") or "")[:500],"request_id":result.get("request_id")}
                return self.send_json(safe)
            except Exception as e: return self.send_json({"error":str(e)},502)
        if p.path == "/api/models/catalog":
            try:
                from model_vault import catalog
                return self.send_json(catalog())
            except Exception as exc:
                return self.send_json({"ok":False,"error":"模型仓库清单读取失败："+type(exc).__name__},500)
        if p.path == "/api/modelscope/status":
            try:
                from modelscope_adapter import status
                return self.send_json(status())
            except Exception as exc:
                return self.send_json({"provider_id":"modelscope-hub","configured":False,"error":type(exc).__name__},500)
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
        if p.path == "/api/worker/scnet/status":
            nodes=[]
            for f in sorted(SCNET_NODES.glob("*.json")):
                try:
                    row=json.loads(f.read_text("utf-8"))
                    # Only expose sanitized diagnostics; never return credentials or environment dumps.
                    nodes.append({k:row.get(k) for k in ("node_id","hostname","partition","job_id","state","gpu_summary","python","torch","dtk_detected","last_seen")})
                except Exception: continue
            return self.send_json({"provider":"scnet-hygon-dcu","nodes":nodes})
        if p.path == "/api/worker/next":
            # Worker authentication uses the private access key; never expose it to the browser.
            if not valid_worker_key(self.headers.get("X-Worker-Key")):
                return self.send_json({"error":"worker unauthorized"},401)
            q=parse_qs(p.query); only_project=(q.get("project_id") or [""])[0].strip()
            if only_project and not re.fullmatch(r"[a-f0-9]{12}",only_project):
                return self.send_json({"error":"invalid worker project filter"},400)
            pending = sorted(GPU_JOBS.glob("*.json"), key=lambda x: x.stat().st_mtime)
            for job_file in pending:
                try: job = json.loads(job_file.read_text("utf-8"))
                except Exception: continue
                if job.get("status") == "queued" and (not only_project or job.get("project_id")==only_project):
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
                    if qname:
                        character=next((c for c in job.get("characters",[]) if str(c.get("name","")).strip()==qname),None)
                        requested=(character or {}).get("reference_file")
                        # Explicit character lookup is fail-closed: never fall through
                        # to the hero/base image or another cast member's reference.
                        if not requested:
                            return self.send_json({"error":"该角色没有已绑定的专属参考图；为防止串脸，拒绝使用其他角色图片"},404)
                    else:
                        requested=job.get("reference_file") or next((c.get("reference_file") for c in job.get("characters",[]) if c.get("reference_file")),None)
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
        if p.path == "/api/instabox/status":
            try:
                import instabox_adapter
                return self.send_json(instabox_adapter.status(PROVIDER_SECRET_FILE))
            except Exception as exc:
                return self.send_json({"configured": bool(load_provider_secrets().get("instabox-worker", {}).get("api_key")), "online": False, "error": str(exc)[:200]}, 502)
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
        if p.path == "/api/keyframes":
            q=parse_qs(urlparse(self.path).query); project_id=(q.get("project_id") or [""])[0]; episode_id=(q.get("episode_id") or ["episode-01"])[0]
            if not re.fullmatch(r"[a-f0-9]{12}",project_id) or not re.fullmatch(r"episode-[0-9]{2,3}",episode_id): return self.send_json({"error":"项目或集数编号无效"},400)
            project=next((x for x in load_projects() if x.get("id")==project_id),None)
            if not project: return self.send_json({"error":"项目不存在"},404)
            folder=project_workspace(project_id)/"episodes"/episode_id/"keyframes"; items=[]
            if folder.is_dir():
                for f in sorted(folder.iterdir()):
                    if not f.is_file() or f.suffix.lower() not in (".png",".jpg",".jpeg",".webp"): continue
                    match=re.fullmatch(r"shot_(\d+)",f.stem)
                    if not match: continue
                    token=secrets.token_urlsafe(24)
                    with MEDIA_TOKEN_LOCK: KEYFRAME_TOKENS[token]={"expiry":time.time()+1800,"path":str(f),"project_id":project_id,"episode_id":episode_id}
                    history=[]; history_dir=folder/"keyframe_history"/f.stem
                    if history_dir.is_dir():
                        for old in sorted(history_dir.iterdir(),reverse=True):
                            if not old.is_file() or old.suffix.lower() not in (".png",".jpg",".jpeg",".webp"): continue
                            old_token=secrets.token_urlsafe(24)
                            with MEDIA_TOKEN_LOCK: KEYFRAME_TOKENS[old_token]={"expiry":time.time()+1800,"path":str(old),"project_id":project_id,"episode_id":episode_id}
                            history.append({"name":old.name,"url":"/media/keyframe?token="+quote(old_token),"size":old.stat().st_size})
                    items.append({"shot_id":int(match.group(1)),"name":f.name,"size":f.stat().st_size,"url":"/media/keyframe?token="+quote(token),"history":history})
            return self.send_json({"project_id":project_id,"episode_id":episode_id,"keyframes":items})
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
                keyframe_dir=project_workspace(project_id)/"episodes"/episode_id/"keyframes"
                artifacts=compile_production(production,project,project.get("files",[]),UPLOADS,keyframe_dir)
                out=PRODUCTION/(project_id+"_pipeline"); out.mkdir(parents=True,exist_ok=True)
                for name,obj in artifacts.items():
                    target=out/(name+".json"); tmp=target.with_suffix(".tmp")
                    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),"utf-8"); tmp.replace(target)
                qc=artifacts["qc_report"]
                return self.send_json({"ok":True,"project_id":project_id,"qc_status":qc["status"],
                    "summary":qc["summary"],"shots":qc["shots"],"automatic_pass_count":qc.get("automatic_pass_count",0),
                    "critical_sample_count":qc.get("critical_sample_count",0),"notice":qc.get("notice",""),
                    "regeneration_jobs":len(artifacts["regeneration_brief"]["jobs"]),
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
        if p.path == "/preview":
            f=ROOT/"preview.html"
            raw=f.read_bytes()
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store")
            self.send_header("Referrer-Policy","no-referrer"); self.send_header("X-Content-Type-Options","nosniff")
            self.end_headers(); self.wfile.write(raw); return
        # Only serve reviewed app/setup pages. The MiniMax key form is loopback-only.
        if p.path == "/setup-minimax.html":
            if self.client_address[0] not in ("127.0.0.1", "::1") or self.headers.get("X-Forwarded-For"):
                return self.send_json({"error":"仅允许SSH本地安全通道访问"},403)
            raw=(ROOT/"setup-minimax.html").read_bytes()
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(raw); return
        if p.path not in ("/", "/index.html", "/setup-login.html"):
            return self.send_json({"error":"not found"},404)
        return super().do_GET()
    def do_HEAD(self):
        p=urlparse(self.path)
        if p.path in ("/", "/index.html", "/setup-login.html"):
            return super().do_HEAD()
        self.send_response(404); self.end_headers()
    def do_POST(self):
        global ACCESS_KEY, VALID_ACCESS_KEYS
        p=urlparse(self.path)
        if p.path == "/api/setup/minimax-key":
            try:
                if self.client_address[0] not in ("127.0.0.1", "::1") or self.headers.get("X-Forwarded-For"):
                    return self.send_json({"error":"仅允许SSH本地安全通道访问"},403)
                n=int(self.headers.get("Content-Length", "0"))
                if n>8192: return self.send_json({"error":"请求过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                key=str(obj.get("api_key", "")).strip()
                if len(key)<12 or len(key)>512: return self.send_json({"error":"API Key格式长度不正确"},400)
                rows=load_provider_secrets()
                rows["minimax-video"]={"api_key":key,"base_url":"https://api.minimax.io","model":"MiniMax-H3","updated_at":datetime.datetime.now(datetime.timezone.utc).isoformat()}
                save_provider_secrets(rows)
                return self.send_json({"ok":True,"provider":"MiniMax-H3","configured":True,"key_length":len(key)})
            except Exception as e: return self.send_json({"error":str(e)},500)
        if p.path == "/api/setup/login":
            try:
                # First-run recovery is available only through an authenticated SSH
                # loopback tunnel, never through the public reverse proxy.
                local_bootstrap = (self.client_address[0] in ("127.0.0.1", "::1") and not self.headers.get("X-Forwarded-For"))
                if local_bootstrap:
                    n=int(self.headers.get("Content-Length", "0"))
                    if n>4096: return self.send_json({"error":"请求过大"},413)
                    obj=json.loads(self.rfile.read(n) or b"{}")
                    new_key=str(obj.get("new_key", "")).strip()
                    if len(new_key)<8 or len(new_key)>128: return self.send_json({"error":"登录口令长度必须为8到128位"},400)
                    ACCESS_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
                    tmp=ACCESS_KEY_FILE.with_name(ACCESS_KEY_FILE.name+".tmp")
                    fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                    with os.fdopen(fd,"w",encoding="utf-8") as f: f.write(new_key)
                    os.replace(tmp,ACCESS_KEY_FILE)
                    try: ACCESS_KEY_FILE.chmod(0o600)
                    except OSError: pass
                    ACCESS_KEY=new_key; VALID_ACCESS_KEYS={new_key}
                    # A successful localhost bootstrap supersedes any one-time token.
                    SETUP_TOKEN_FILE.unlink(missing_ok=True)
                    return self.send_json({"ok":True,"message":"首次登录口令设置成功，请返回工作台登录"})
                if not SETUP_TOKEN_FILE.is_file(): return self.send_json({"error":"一次性初始化入口已关闭"},403)
                setup=json.loads(SETUP_TOKEN_FILE.read_text("utf-8"))
                if time.time() > float(setup.get("expires_at", 0)):
                    SETUP_TOKEN_FILE.unlink(missing_ok=True)
                    return self.send_json({"error":"初始化链接已过期，请重新生成"},403)
                supplied=self.headers.get("X-Setup-Token", "")
                if not supplied or not secrets.compare_digest(supplied, str(setup.get("token", ""))):
                    return self.send_json({"error":"初始化验证失败"},403)
                n=int(self.headers.get("Content-Length", "0"))
                if n>4096: return self.send_json({"error":"请求过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                new_key=str(obj.get("new_key", "")).strip()
                if len(new_key)<8 or len(new_key)>128: return self.send_json({"error":"登录口令长度必须为8到128位"},400)
                ACCESS_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp=ACCESS_KEY_FILE.with_name(ACCESS_KEY_FILE.name+".tmp")
                fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(fd,"w",encoding="utf-8") as f: f.write(new_key)
                os.replace(tmp,ACCESS_KEY_FILE)
                try: ACCESS_KEY_FILE.chmod(0o600)
                except OSError: pass
                ACCESS_KEY=new_key; VALID_ACCESS_KEYS={new_key}
                SETUP_TOKEN_FILE.unlink(missing_ok=True)
                return self.send_json({"ok":True,"message":"登录口令初始化成功，请返回工作台登录"})
            except Exception as e:
                return self.send_json({"error":"初始化失败："+str(e)},400)
        worker_path = p.path.startswith("/api/worker/") or p.path in ("/api/tts/result",)
        valid = valid_worker_key(self.headers.get("X-Worker-Key")) if worker_path else self.headers.get("X-Access-Key") in VALID_ACCESS_KEYS
        if p.path == "/api/preview/end":
            token=self.headers.get("X-Preview-Token", "")
            if not preview_token_valid(token): return self.send_json({"error":"预览链接已失效"},403)
            revoke_preview_token(token)
            return self.send_json({"ok":True,"revoked":True})
        if not valid: return self.send_json({"error":"访问凭据无效，请检查登录或Worker配置"},401)
        if p.path == "/api/preview/create":
            token,expires=issue_preview_token()
            return self.send_json({"ok":True,"url":"/preview#"+quote(token),"expires_at":datetime.datetime.fromtimestamp(expires,datetime.timezone.utc).isoformat(),"ttl_seconds":PREVIEW_TTL_SECONDS})
        if p.path == "/api/worker/scnet/heartbeat":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n<1 or n>32768: return self.send_json({"error":"heartbeat payload size invalid"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                node_id=str(obj.get("node_id") or "")
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}",node_id): return self.send_json({"error":"invalid node_id"},400)
                def safe_text(key, limit=500):
                    value=obj.get(key,"")
                    return str(value)[:limit] if isinstance(value,(str,int,float,bool)) else ""
                row={"node_id":node_id,"hostname":safe_text("hostname",128),"partition":safe_text("partition",128),
                     "job_id":safe_text("job_id",64),"state":"preflight_reported","gpu_summary":safe_text("gpu_summary",4000),
                     "python":safe_text("python",256),"torch":safe_text("torch",512),"dtk_detected":bool(obj.get("dtk_detected")),
                     "last_seen":datetime.datetime.now(datetime.timezone.utc).isoformat()}
                target=SCNET_NODES/(node_id+".json"); tmp=target.with_suffix(".tmp")
                tmp.write_text(json.dumps(row,ensure_ascii=False,indent=2),"utf-8"); os.replace(tmp,target)
                return self.send_json({"ok":True,"node_id":node_id,"recorded":True})
            except Exception as e: return self.send_json({"error":"heartbeat rejected: "+str(e)[:300]},400)
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
        if p.path == "/api/worker/character-candidates/result":
            q=parse_qs(p.query); job_id=(q.get("job_id") or [""])[0]
            if not re.fullmatch(r"[a-f0-9]{16}",job_id): return self.send_json({"error":"候选任务编号无效"},400)
            jf=GPU_JOBS/(job_id+".json")
            if not jf.is_file(): return self.send_json({"error":"候选任务不存在"},404)
            job=json.loads(jf.read_text("utf-8"))
            if job.get("task_type")!="character_candidates": return self.send_json({"error":"任务类型不匹配"},409)
            n=int(self.headers.get("Content-Length","0"))
            if n<1 or n>80*1024*1024: return self.send_json({"error":"候选压缩包大小无效"},413)
            raw=self.rfile.read(n)
            import zipfile,io
            try:
                z=zipfile.ZipFile(io.BytesIO(raw)); names=z.namelist()
                if not names or any(Path(x).is_absolute() or ".." in Path(x).parts for x in names): raise ValueError("unsafe archive")
                temp=UPLOADS/(job["project_id"]+"_candidate_tmp_"+job_id); temp.mkdir(exist_ok=True)
                z.extractall(temp); candidates=[]
                from PIL import Image
                for i in range(1,5):
                    source=next((x for x in temp.rglob(f"shot_{i:04d}.*") if x.is_file() and x.suffix.lower() in (".png",".jpg",".jpeg",".webp")),None)
                    if source is None: continue
                    with Image.open(source) as im:
                        im.verify()
                    with Image.open(source) as im:
                        w,h=im.size
                        if w<512 or h<288 or abs(w/h-16/9)>0.03: raise ValueError(f"候选图{i}尺寸或比例不合格：{w}x{h}")
                    stored=f"{job['project_id']}_character_candidate_{job_id}_{i}.png"
                    target=UPLOADS/stored
                    import shutil
                    shutil.copyfile(source,target)
                    archive_dir=project_workspace(job["project_id"])/"characters"/job.get("episode_id","episode-01")/"candidates"/job.get("character_name","character")
                    archive_dir.mkdir(parents=True,exist_ok=True)
                    archive_target=archive_dir/stored
                    shutil.copyfile(source,archive_target)
                    candidates.append({"candidate_id":f"candidate_{i}","name":f"候选方案{i}","stored":stored,"workspace_path":str(archive_target.relative_to(ROOT)),"width":w,"height":h,"bytes":target.stat().st_size,"url":"/api/media/file?path="+quote(str(target.resolve().relative_to(ROOT.resolve())))})
                if len(candidates)<2: raise ValueError("有效候选图不足2张，不能进入选择环节")
                job.update(status="completed",candidates=candidates,completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),candidate_review_status="awaiting_human_selection")
                jf.write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8")
                shutil.rmtree(temp,ignore_errors=True)
                update_usage_event(job_id,"completed",f"角色候选图生成完成：{len(candidates)}张，等待人工选择")
                return self.send_json({"ok":True,"job_id":job_id,"candidate_count":len(candidates),"candidates":candidates})
            except Exception as e:
                return self.send_json({"error":"候选图片验收失败："+str(e)},400)
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
                        target=episode_root/img.name
                        archive_and_replace_keyframe(img,target)
                        copied.append(str(target.relative_to(ROOT)))
                # Link generated images back to their storyboard records so QC, gallery and render use the real frames.
                prod_path=production_file(job["project_id"],episode_id)
                if prod_path.exists():
                    production=json.loads(prod_path.read_text("utf-8"))
                    by_shot={int(x.get("id",i+1)):x for i,x in enumerate(production.get("storyboard",[]))}
                    for img in episode_root.iterdir():
                        if not img.is_file() or img.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'): continue
                        match=re.search(r"shot_(\d+)",img.stem)
                        if not match: continue
                        shot=by_shot.get(int(match.group(1)))
                        if shot:
                            shot["image_file"]=img.name; shot["keyframe_path"]=str(img.relative_to(ROOT)); shot["status"]="keyframe_generated"
                    prod_path.write_text(json.dumps(production,ensure_ascii=False,indent=2),"utf-8")
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
                if provider not in ("nvidia-nim","generic-openai-compatible","minimax-video","instabox-worker"): return self.send_json({"error":"不支持的API平台"},400)
                key=str(obj.get("api_key", "")).strip()
                if not key: return self.send_json({"error":"请输入API Key"},400)
                if len(key)>4096: return self.send_json({"error":"Key长度异常"},400)
                base=str(obj.get("base_url", "")).strip().rstrip("/")
                if provider=="nvidia-nim": base=base or "https://integrate.api.nvidia.com/v1"
                if provider=="minimax-video": base="https://api.minimax.io"
                if provider=="instabox-worker": base="https://api.instabox.sh"
                if not base.startswith("https://"): return self.send_json({"error":"API地址必须使用HTTPS"},400)
                if provider=="instabox-worker" and not key.startswith("ibk_"): return self.send_json({"error":"Instabox Key 格式应以 ibk_ 开头"},400)
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
                if provider not in ("nvidia-nim","generic-openai-compatible","minimax-video","instabox-worker"): return self.send_json({"ok":False,"error":"不支持的平台"},400)
                row=apply_provider_secret(provider)
                if not row.get("api_key"): return self.send_json({"ok":False,"error":"尚未保存该平台Key"},400)
                if provider=="minimax-video":
                    return self.send_json({"ok":True,"provider_id":provider,"model":"MiniMax-H3","configured":True,"note":"Key已保存。MiniMax视频API为按次计费异步任务；真正验证将在你明确提交首个镜头时进行。"})
                if provider=="instabox-worker":
                    import instabox_adapter
                    result=instabox_adapter.status(PROVIDER_SECRET_FILE)
                    return self.send_json({"ok":True,"provider_id":provider,"model":"Instabox API","configured":True,"machine_count":len(result.get("machines",[])),"usage":result.get("usage",{}),"note":"Instabox API 鉴权成功；机器状态和免费用量已读取。"})
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
        if p.path == "/api/instabox/action":
            try:
                n=int(self.headers.get("Content-Length", "0"))
                if n>4096: return self.send_json({"error":"请求过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                import instabox_adapter
                result=instabox_adapter.machine_action(PROVIDER_SECRET_FILE, str(obj.get("machine_id", "")), str(obj.get("action", "")))
                return self.send_json(result,202)
            except Exception as exc: return self.send_json({"error":str(exc)[:200]},502)
        if p.path == "/api/minimax/video/submit":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>20000: return self.send_json({"error":"请求内容过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                from minimax_video_adapter import submit
                result=submit(obj.get("prompt",""),duration=obj.get("duration",6),resolution=obj.get("resolution","768P"),ratio=obj.get("ratio","16:9"),generate_audio=obj.get("generate_audio",True))
                task_id=str(result.get("task_id") or "")
                if not task_id: return self.send_json({"error":"平台未返回task_id"},502)
                task_dir=DATA/"minimax_tasks"; task_dir.mkdir(parents=True,exist_ok=True)
                record={"task_id":task_id,"project_id":str(obj.get("project_id","")),"episode_id":str(obj.get("episode_id","episode-01")),"shot_id":obj.get("shot_id"),"prompt":str(obj.get("prompt",""))[:7000],"model":"MiniMax-H3","status":"submitted","created_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"request_id":result.get("request_id")}
                (task_dir/(re.sub(r'[^A-Za-z0-9_-]','_',task_id)+".json")).write_text(json.dumps(record,ensure_ascii=False,indent=2),"utf-8")
                usage_event(record["project_id"],"MiniMax-H3","视频生成任务",int(obj.get("duration",6)),"秒","MiniMax-H3","submitted","异步视频任务已提交",task_id)
                return self.send_json({"ok":True,"task_id":task_id,"status":"submitted","request_id":result.get("request_id"),"note":"已提交真实视频生成任务；请查询任务状态，完成后返回视频URL。"},202)
            except Exception as e: return self.send_json({"error":str(e)},502)
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
                system1="你是中国大陆成熟商业二维国漫的编剧、角色统筹与分镜导演。只输出严格JSON对象，不要Markdown代码围栏。结构为{\"characters\":[{\"name\":\"角色名或稳定临时称呼\",\"role\":\"主角/女主/配角/群演/临时角色\",\"appearance\":\"外貌特征\",\"costume\":\"服装\",\"scene_key\":\"首次出现的场景标识\",\"continuity_note\":\"后续镜头保持一致的特征\"}],\"shots\":[{\"id\":1,\"shot_type\":\"远景/中景/近景/特写\",\"visual\":\"画面可见内容\",\"action\":\"动作\",\"dialogue\":\"对白或旁白\",\"camera_prompt\":\"镜头景别与构图\",\"characters\":[\"角色名\"]}]}。根据输入故事拆成8到16个有因果、有节奏的关键镜头。角色必须严格区分“画面中真实出场的人/动物”和“仅被提及的名字”：只有身体或实体确实出现在镜头画面里的角色，才可以写入该镜头characters数组。物品、玉佩、墓碑、牌匾、书信、屏幕、文件、照片等上面刻写/印刷/显示的人名，绝不代表该人物出场；对白、旁白、回忆提及、传说称呼也绝不代表人物出场。只拍摄带有人名的物品时，characters必须是空数组，不得创建该姓名对应的人物外貌、服装或身体。优先使用已提供角色资料；故事中新出现但确实有实体出场的角色只能作为待审核建议，不得自动加入正式角色资产或自动进入GPU生成名单。只有当前场景确实出现的路人等才可列为临时角色。不得凭空添加推动剧情的新人物、道具或设定。若故事正文中的“人物锚点”与外部角色资料冲突，以故事正文明确的人物锚点为最高优先级；已有角色姓名、年龄、外貌、服装不得擅自改写。每个镜头的characters必须列出画面中实际出场的角色。视觉风格为精致2D国漫/中国动画电影概念美术，东方审美、成熟人物比例、细腻线稿与国风色彩，不要日式萌系、大眼脸、赛璐璐校园番风格。"
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
                # AI-discovered names are suggestions only. Canonical character assets
                # can be created/edited only from user-confirmed input, never from LLM text.
                normalized_cast, detected, character_suggestions = enforce_canonical_storyboard_cast(
                    draft_shots, draft_obj.get("characters") or [], characters)
                draft_shots = normalized_cast
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
                    shot_names=[str(x).strip()[:80] for x in (shot.get("characters") or []) if str(x).strip()][:12]
                    unverified_names=[str(x).strip()[:80] for x in (shot.get("unverified_character_names") or []) if str(x).strip()][:12]
                    normalized.append({"id":i,"shot_type":str(shot.get("shot_type") or "中景")[:20],"visual":str(shot.get("visual") or "")[:1800],"action":str(shot.get("action") or "")[:800],"dialogue":str(shot.get("dialogue") or "")[:800],"camera_prompt":str(shot.get("camera_prompt") or "")[:500],"characters":shot_names,"character":"、".join(shot_names),"unverified_character_names":unverified_names,"prompt":str(visual.get("prompt") or "")[:2200],"negative_prompt":str(visual.get("negative_prompt") or "")[:1200]})
                if not normalized: raise ValueError("没有可用分镜")
                usage_event(project_id,"DeepSeek","AI故事拆解与分镜初稿",int(deep_usage.get("total_tokens",1) or 1),"tokens",deep_model,"completed","双模型分镜工作流")
                usage_event(project_id,"NVIDIA NIM","国漫画风优化与分镜质检",int(nv_usage.get("total_tokens",1) or 1),"tokens",nv_model,"completed","双模型分镜工作流")
                return self.send_json({"ok":True,"shots":normalized,"characters":detected,"character_suggestions":character_suggestions,"models":{"story":"DeepSeek / "+str(deep_model),"visual":"NVIDIA NIM / "+str(nv_model)},"note":"只有已登记角色可进入镜头生成名单。AI发现的新名字仅列为待核实建议；物品铭文、墓碑姓名、对白和旁白不会自动变成人物或进入GPU生成。请人工确认后再补充正式角色资料。"})
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
        if p.path == "/api/character-candidates/queue":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>20000: return self.send_json({"error":"请求过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                project_id=str(obj.get("project_id","")); episode_id=str(obj.get("episode_id","episode-01")); name=str(obj.get("character_name","")).strip()
                if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
                if not name: return self.send_json({"error":"请指定需要建立基准图的角色"},400)
                prod=production_file(project_id,episode_id)
                if not prod.is_file(): return self.send_json({"error":"请先保存本集角色资料"},404)
                production=json.loads(prod.read_text("utf-8")); characters=production.get("characters") or []
                character=next((c for c in characters if str(c.get("name","")).strip()==name),None)
                if not character: return self.send_json({"error":"制作资料中找不到该角色"},404)
                entity_type=str(character.get("entity_type") or "")
                if not entity_type and ("墓碑" in str(character.get("visual_lock") or "") or "不生成实体" in str(character.get("visual_lock") or "") or "不生成实体" in str(character.get("negative_constraints") or "")): entity_type="memorial_only"
                if not entity_type and any(t in name+str(character.get("role") or "") for t in ("乌鸦","动物","兽","鸟")): entity_type="animal"
                if entity_type=="memorial_only" or "不生成实体" in str(character.get("negative_constraints") or "") or "墓碑" in str(character.get("visual_lock") or ""):
                    return self.send_json({"error":"该角色当前剧情设定为不生成实体，不能建立人物基准图"},409)
                hero=production.get("hero") or {}
                hero_fields={}
                if str(character.get("role") or "")=="主角" and hero.get("description"):
                    for line in str(hero.get("description")).splitlines():
                        parts=line.replace("：","\t").split("\t",1)
                        if len(parts)==2: hero_fields[parts[0].strip()]=parts[1].strip()
                if entity_type=="animal":
                    candidate_identity="黑色乌鸦；黑色羽毛；猩红眼睛；真实鸟类头身比例与爪部结构"
                else:
                    age=hero_fields.get("年龄") or str(character.get("age") or "")
                    hair=hero_fields.get("发型") or str(character.get("appearance") or "")
                    face=hero_fields.get("面容") or ""
                    costume=hero_fields.get("服装") or str(character.get("costume") or "")
                    visual_lock=str(character.get("visual_lock") or "")
                    # Keep the identity anchor compact enough for SD1.5 CLIP (77 tokens),
                    # leaving room for the requested view angle and style in the actual prompt.
                    def compact(value, limit):
                        value=str(value or "").strip().replace("；", "，").replace(";", "，")
                        return value[:limit].rstrip("，；、，。 ")
                    if entity_type == "animal":
                        candidate_identity = "黑色乌鸦；黑色羽毛；猩红眼睛；真实鸟类头身比例与爪部结构"
                    else:
                        candidate_identity="；".join(x for x in [
                            compact(age, 10), compact(face or hair, 18),
                            compact(hair if face else "", 16), compact(costume, 20)
                        ] if x)
                        if not candidate_identity:
                            candidate_identity=compact(visual_lock, 58)
                    if not candidate_identity: return self.send_json({"error":"角色外貌资料不足，请先补充年龄、发型、脸部特征和服装"},400)
                description="；".join(x for x in [str(character.get("age") or ""),str(character.get("appearance") or ""),str(character.get("costume") or ""),str(character.get("visual_lock") or "")] if x)
                if hero_fields: description += "；主角详细设定："+"；".join(f"{k}：{v}" for k,v in hero_fields.items())
                for existing_file in sorted(GPU_JOBS.glob("*.json"),key=lambda x:x.stat().st_mtime,reverse=True):
                    try: existing=json.loads(existing_file.read_text("utf-8"))
                    except Exception: continue
                    if existing.get("project_id")==project_id and existing.get("episode_id")==episode_id and existing.get("task_type")=="character_candidates" and existing.get("character_name")==name:
                        if existing.get("status") in ("queued","running") or (existing.get("status")=="completed" and not existing.get("approved_candidate_id")):
                            return self.send_json({"job_id":existing.get("job_id"),"status":existing.get("status"),"candidate_count":4,"message":"该角色已有候选任务，已复用现有任务，避免重复消耗GPU额度"},202)
                job_id=uuid.uuid4().hex[:16]
                if entity_type=="animal":
                    base_prompt=("中国国风2D动画电影美术，半写实自然动物结构，完整全身单只动物，真实鸟类比例和羽毛层次，站在一截干枯树枝上，头部和爪子完整入镜，纯净低干扰的中性深灰背景，柔和均匀光线，动物居中，单只动物单画面。")
                    negative="人类，人物面孔，拟人身体，衣服，翅膀变手臂，多只乌鸦，鸟群，额外动物，拼贴，四宫格，文字，水印，裁切头部，裁切爪子，卡通玩具，3D塑料质感"
                else:
                    base_prompt=("中国国风2D动画电影美术，半写实人物比例，清晰自然的中国人面部结构，完整全身正面站姿，双手自然下垂，头部和鞋子完整入镜，服装结构与颜色清楚，纯净低干扰的中性深灰背景，柔和均匀棚拍光，角色居中，单人单画面。")
                    negative="女性化面容，性别错误，年龄错误，现代服装，西装，校服，盔甲，武器，披风，额外人物，重复人物，双头，拼贴，四宫格，三视图文字标签，文字，水印，裁切头部，裁切脚部，夸张动漫大眼，日式萌系，赛璐璐，3D塑料质感"
                shots=[]
                for i in range(1,5):
                    if entity_type=="animal":
                        variant=("乌鸦侧面站姿，双翼收拢" if i==1 else "乌鸦三分之二侧面，头部微转" if i==2 else "乌鸦正面站姿，双眼清晰" if i==3 else "乌鸦侧头观察，双翼自然收拢")
                    else:
                        variant=("正面标准站姿，双脚自然分开" if i==1 else "身体轻微向左转约15度，面部仍清晰朝向镜头" if i==2 else "身体轻微向右转约15度，面部仍清晰朝向镜头" if i==3 else "正面站姿，轻微自然表情，服装细节清晰")
                    shots.append({"id":i,"shot_type":"全身立绘","visual":base_prompt+variant,"candidate_view_prompt":variant,"action":"自然站立，不做夸张动作","prompt":base_prompt+variant,"negative_prompt":negative,"character_names":[],"duration_seconds":1})
                job={"job_id":job_id,"project_id":project_id,"episode_id":episode_id,"task_type":"character_candidates","character_name":name,"character_role":character.get("role"),"character_entity_type":entity_type or "human","character_description":description,"candidate_identity_prompt":candidate_identity,"status":"queued","created_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"project_name":next(x["name"] for x in load_projects() if x["id"]==project_id),"style":"Chinese 2D hand-painted donghua film, semi-realistic","aspect_ratio":"16:9","seed":int(job_id,16)%2000000000,"characters":[],"storyboard":shots,"selected_shot_ids":[1,2,3,4],"max_shots":4,"text_only_reference":True,"automation_mode":"character_candidate_review","human_approval_required":True}
                (GPU_JOBS/(job_id+".json")).write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8")
                usage_event(project_id,"Kaggle GPU","角色基准图候选生成",4,"候选图","AnythingV5 SD1.5","queued","等待免费GPU Worker领取；生成后需筛选确认",job_id)
                return self.send_json({"job_id":job_id,"status":"queued","candidate_count":4,"message":"已加入角色候选图队列；生成后由你最终确认采用哪一张"},202)
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/character-candidates/approve":
            try:
                n=int(self.headers.get("Content-Length","0")); obj=json.loads(self.rfile.read(n) or b"{}")
                project_id=str(obj.get("project_id","")); episode_id=str(obj.get("episode_id","episode-01")); job_id=str(obj.get("job_id","")); candidate_id=str(obj.get("candidate_id",""))
                if not re.fullmatch(r"[a-f0-9]{12}",project_id) or not re.fullmatch(r"[a-f0-9]{16}",job_id) or not re.fullmatch(r"candidate_[1-4]",candidate_id): return self.send_json({"error":"候选编号无效"},400)
                jf=GPU_JOBS/(job_id+".json")
                if not jf.is_file(): return self.send_json({"error":"候选任务不存在"},404)
                job=json.loads(jf.read_text("utf-8"))
                if job.get("project_id")!=project_id or job.get("episode_id")!=episode_id or job.get("task_type")!="character_candidates" or job.get("status")!="completed": return self.send_json({"error":"候选任务尚未完成或项目不匹配"},409)
                candidate=next((x for x in job.get("candidates",[]) if x.get("candidate_id")==candidate_id),None)
                if not candidate: return self.send_json({"error":"候选图片不存在"},404)
                filename=candidate.get("stored",""); image=(UPLOADS/filename).resolve()
                if not filename.startswith(project_id+"_character_candidate_") or not image.is_file() or UPLOADS.resolve() not in image.parents: return self.send_json({"error":"候选文件校验失败"},400)
                prod=production_file(project_id,episode_id); production=json.loads(prod.read_text("utf-8"))
                chars=production.get("characters") or []; target=next((c for c in chars if str(c.get("name","")).strip()==job.get("character_name")),None)
                if not target: return self.send_json({"error":"角色资料已变化，找不到原角色"},409)
                target.update(reference_file=filename,reference_name=candidate.get("name",filename),reference_status="approved",reference_source="generated_candidate",approved_candidate_id=candidate_id)
                assets=production.setdefault("character_assets",[]); asset=next((x for x in assets if x.get("name")==job.get("character_name")),None)
                if asset is None: assets.append({"name":job.get("character_name"),"role":target.get("role","配角"),"reference_file":filename,"reference_name":candidate.get("name",filename),"reference_status":"approved","reference_source":"generated_candidate"})
                else: asset.update(reference_file=filename,reference_name=candidate.get("name",filename),reference_status="approved",reference_source="generated_candidate")
                if str(target.get("role") or "")=="主角":
                    hero=production.get("hero") or {}; hero_name=str(hero.get("name") or "")
                    if not hero_name or job.get("character_name") in hero_name or hero_name in job.get("character_name",""):
                        production["base_image_file"]=filename; production["approved_base_image"]=True; production["base_image_status"]="approved"; production["hero"]={**hero,"name":job.get("character_name")}
                prod.write_text(json.dumps(production,ensure_ascii=False,indent=2),"utf-8")
                job["approved_candidate_id"]=candidate_id; job["approved_at"]=datetime.datetime.now(datetime.timezone.utc).isoformat(); jf.write_text(json.dumps(job,ensure_ascii=False,indent=2),"utf-8")
                workspace=project_workspace(project_id)/"characters"/(episode_id+"_characters.json")
                workspace.write_text(json.dumps(chars,ensure_ascii=False,indent=2),"utf-8")
                update_usage_event(job_id,"completed",f"角色{job.get('character_name')}已确认采用候选图{candidate_id}")
                return self.send_json({"ok":True,"character_name":job.get("character_name"),"reference_file":filename,"approved_candidate_id":candidate_id})
            except Exception as e: return self.send_json({"error":"确认角色候选图失败："+str(e)},400)
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
                automatic_batch=bool(obj.get("automatic_batch"))
                targeted_retry=bool(obj.get("targeted_retry"))
                if (not automatic_batch and not 3 <= len(selected) <= 5) or (automatic_batch and not 1 <= len(selected) <= 5):
                    return self.send_json({"error":"普通试生成请选择3到5个分镜；自动批次每批允许1到5个分镜"},400)
                by_id={int(x.get("id",i+1)):x for i,x in enumerate(all_shots)}
                if len(set(selected))!=len(selected) or any(x not in by_id for x in selected): return self.send_json({"error":"所选分镜编号无效"},400)
                job_id=uuid.uuid4().hex[:16]
                characters=production.get("characters") or ([{"name":production.get("hero",{}).get("name","主角"),"age":"","appearance":production.get("hero",{}).get("description",""),"costume":"","role":"主角"}] if production.get("hero") else [])
                # The approved hero base image is the default identity reference for the protagonist only.
                # It is never borrowed by unrelated cast members or crowd characters.
                base_ref=production.get("base_image_file") if production.get("approved_base_image") else None
                hero_name=str((production.get("hero") or {}).get("name") or "").strip()
                if base_ref:
                    for character in characters:
                        cname=str(character.get("name") or "").strip()
                        is_memorial=(str(character.get("entity_type") or "")=="memorial_only" or "墓碑" in str(character.get("visual_lock") or "") or "不生成实体" in str(character.get("negative_constraints") or ""))
                        is_hero=(not is_memorial) and (character.get("role")=="主角" or (hero_name and (cname in hero_name or hero_name in cname)))
                        if is_hero and not character.get("reference_file"):
                            character["reference_file"]=base_ref
                            character["reference_source"]="approved_hero_base_image"
                from production_pipeline import derive_prop_states, prop_state_anchors
                all_prop_states=derive_prop_states(all_shots)
                state_by_shot_id={int(row.get("id",i+1)):all_prop_states[i] for i,row in enumerate(all_shots)}
                shots=[]
                for sid in selected:
                    shot=dict(by_id[sid])
                    raw_names=shot.get("characters") or shot.get("character_names") or shot.get("character") or shot.get("character_name") or []
                    if isinstance(raw_names,str): names=[x.strip() for x in re.split(r"[、，,；;|]",raw_names) if x.strip()]
                    elif isinstance(raw_names,list): names=[str(x).strip() for x in raw_names if str(x).strip()]
                    else: names=[]
                    shot["character_names"]=list(dict.fromkeys(names))
                    known_character_names={str(c.get("name","")).strip() for c in characters if str(c.get("name","")).strip()}
                    unknown_character_names=sorted(set(shot["character_names"])-known_character_names)
                    if unknown_character_names:
                        return self.send_json({"error":"镜头包含尚未登记确认的角色名称："+"、".join(unknown_character_names)+"。请先补充正式角色资料，不能直接送入GPU生成。"},409)
                    memorial_names=[]
                    active_names=[]
                    for cast_name in shot["character_names"]:
                        row=next(c for c in characters if str(c.get("name","")).strip()==cast_name)
                        row_type=str(row.get("entity_type") or "")
                        if not row_type and ("墓碑" in str(row.get("visual_lock") or "") or "不生成实体" in str(row.get("visual_lock") or "") or "不生成实体" in str(row.get("negative_constraints") or "")):
                            row_type="memorial_only"
                        if row_type=="memorial_only": memorial_names.append(cast_name)
                        else: active_names.append(cast_name)
                    shot["memorial_reference_names"]=memorial_names
                    shot["character_names"]=active_names
                    matches=[c for c in characters if str(c.get("name","")).strip() in shot["character_names"]]
                    # Production queue applies the same shot-scoped identity policy as the pipeline.
                    # Empty-cast shots stay empty; never inject every episode character by default.
                    anchors=[]; negatives=[]
                    animal_cast=False
                    memorial_cast=bool(memorial_names)
                    if matches:
                        names_text="、".join(shot["character_names"])
                        anchors.append("本镜头只允许出现已指定角色："+names_text+"；不得复制人物、不得新增无关人物、不得串用其他角色的脸和服装")
                    for character in matches:
                        cname=str(character.get("name") or "").strip()
                        role=str(character.get("role") or "")
                        entity_type=str(character.get("entity_type") or "")
                        if not entity_type:
                            entity_type="animal" if any(t in (cname+role) for t in ("乌鸦","动物","兽","鸟")) else ("memorial_only" if ("墓碑" in str(character.get("visual_lock") or "") or "不生成实体" in str(character.get("visual_lock") or "") or "不生成实体" in str(character.get("negative_constraints") or "")) else "human")
                        animal_cast = animal_cast or entity_type == "animal"
                        memorial_cast = memorial_cast or entity_type == "memorial_only"
                        for key in ("appearance","costume","visual_lock"):
                            value=str(character.get(key) or "").strip()
                            if value and value not in anchors: anchors.append(value)
                        value=str(character.get("negative_constraints") or "").strip()
                        if value and value not in negatives: negatives.append(value)
                    if animal_cast:
                        anchors.append("动物主体，保持真实动物解剖结构，不得拟人化，不得生成任何人类角色")
                        negatives.append("人类、女性、男性、人物拼贴、拟人身体、人物面孔")
                    if memorial_cast:
                        anchors.append("以下姓名只允许作为墓碑或纪念物铭文出现："+"、".join(memorial_names)+"；不得生成对应人物、肖像、鬼魂或人体")
                        negatives.append("人物、人体、女性、男性、面孔、女鬼、幽灵、剪影、拟人实体")
                    if matches:
                        negatives.append("重复人物、额外人物、角色身份互换、性别错误、发型漂移、服装颜色错误、角色串脸")
                    else:
                        shot["prompt"]=(str(shot.get("prompt") or shot.get("visual") or "")+"；本镜头没有任何人物或动物实体出场，只绘制明确指定的环境和物品；物品铭文、墓碑姓名、对白和旁白中的人名不得转化为人物")[:12000]
                        negatives.append("人物、人体、面孔、男性、女性、女鬼、幽灵、剪影、拟人实体、群像、额外角色")
                    if anchors:
                        shot["identity_guard_prompt"]="；".join(anchors)
                        shot["prompt"]=(str(shot.get("prompt") or shot.get("visual") or "")+"；角色身份与出场约束："+"；".join(anchors))[:12000]
                    visual_text=" ".join(str(shot.get(k) or "") for k in ("visual","action","prompt"))
                    mentions_tree=any(k in visual_text for k in ("柿子树","枯柿子树","柿子","树杈","枯枝"))
                    mentions_graveyard=any(k in visual_text for k in ("乱坟岗","坟地","坟墓","墓碑","墓前","香"))
                    is_reveal=any(k in visual_text for k in ("向下","带出","最终落在","下降","俯冲"))
                    if mentions_tree and mentions_graveyard and is_reveal:
                        shot["scene_anchor"]="镜头从同一棵枯柿子树的树梢连续向下移动，最终进入同一片荒凉乱坟岗；树与墓区的空间方位连续，不跳切到陌生地点。"
                        shot["scene_key"]="graveyard_reveal"
                    elif mentions_tree:
                        shot["scene_anchor"]="同一棵枯萎柿子树，扭曲干枯枝干与腐烂柿子的位置关系保持连贯；深夜乌云遮月，冷青色夜光和湿冷薄雾保持统一。"
                        shot["scene_key"]="persimmon_tree_night"
                    elif mentions_graveyard:
                        shot["scene_anchor"]="同一片荒凉乱坟岗夜景，墓碑高低错落、潮湿泥土与低位薄雾保持一致；墓碑朝向和祭拜区域空间关系连续。"
                        shot["scene_key"]="graveyard_night"
                    prop_anchors=[]
                    if "玉佩" in visual_text or "玉" in visual_text:
                        prop_anchors.append("同一枚暗青色龙形玉佩，盘龙雕纹、磨损边缘与微弱泛红龙眼保持一致，不改变形状和颜色。")
                    if any(k in visual_text for k in ("香","香火","香烛")):
                        prop_anchors.append("同一组墓前三炷香，香枝数量、插放位置和火头状态严格服从本镜头动作，不得擅自增加或提前点燃。")
                    if "墓碑" in visual_text:
                        prop_anchors.append("墓碑保持同一块旧石碑，石材颜色、裂痕和朝向一致；许清月姓名只在剧情指定镜头清晰出现。")
                    if "纸钱" in visual_text:
                        prop_anchors.append("纸钱只出现在墓前地面指定位置，不变成其他道具。")
                    if prop_anchors: shot["prop_anchors"]=prop_anchors
                    prop_states=state_by_shot_id.get(sid,{})
                    shot["prop_states"]=prop_states
                    state_anchors=prop_state_anchors(prop_states)
                    if state_anchors:
                        shot["prop_anchors"]=(list(shot.get("prop_anchors") or [])+state_anchors)
                    if prop_states.get("dragon_jade_pendant")=="absent":
                        negatives.append("龙形玉佩、玉佩挂件、龙形饰物、发光玉石、手持玉器")
                    if negatives:
                        shot["negative_prompt"]=(str(shot.get("negative_prompt") or "")+"；"+"；".join(negatives))[:5000]
                    if len(shot["character_names"])==1 and matches and matches[0].get("reference_file"):
                        shot["character_reference_file"]=matches[0]["reference_file"]
                    elif len(shot["character_names"])>1:
                        # Multi-reference IP-Adapter is supported by the Kaggle worker.
                        # Enable it only when every named cast member has their own approved reference.
                        refs_by_name={str(c.get("name","")).strip():c.get("reference_file") for c in characters}
                        if 2 <= len(shot["character_names"]) <= 4 and all(refs_by_name.get(name) for name in shot["character_names"]):
                            shot["multi_character_reference"]=True
                            shot.pop("text_only_character",None)
                            shot.pop("multi_character_reference_note",None)
                        else:
                            shot["text_only_character"]=True
                            shot.pop("multi_character_reference",None)
                            shot["multi_character_reference_note"]="多人镜头中有角色缺少独立基准图，暂不挂载任何单人参考图，避免身份串用。"
                    shots.append(shot)
                job={"job_id":job_id,"project_id":project_id,"episode_id":episode_id,"status":"queued","created_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"project_name":next(x["name"] for x in load_projects() if x["id"]==project_id),"style":production.get("style","Chinese anime donghua, cinematic manhua, detailed background"),"aspect_ratio":production.get("aspect_ratio","16:9"),"seed":20261124,"characters":characters,"approved_base_image":production.get("base_image_file"),"storyboard":shots,"selected_shot_ids":selected,"max_shots":len(selected),"automation_mode":"targeted_retry" if targeted_retry else ("auto_first" if automatic_batch else "manual_sample"),"automatic_batch":automatic_batch,"targeted_retry":targeted_retry,"human_approval_required_per_frame":False}
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
        if p.path == "/api/shot-review":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>12000: return self.send_json({"error":"请求内容过大"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                project_id=str(obj.get("project_id","")); episode_id=str(obj.get("episode_id","episode-01"))
                try: shot_id=int(obj.get("shot_id"))
                except (TypeError,ValueError): return self.send_json({"error":"镜头编号无效"},400)
                status=str(obj.get("status",""))
                if status not in ("approved","rejected","needs_revision","pending"): return self.send_json({"error":"复核状态无效"},400)
                if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
                dest=production_file(project_id,episode_id)
                if not dest.is_file(): return self.send_json({"error":"请先保存该集制作资料"},404)
                data=json.loads(dest.read_text("utf-8")); shots=data.get("storyboard",[])
                target=next((x for i,x in enumerate(shots) if int(x.get("id",i+1))==shot_id),None)
                if target is None: return self.send_json({"error":"找不到指定镜头"},404)
                notes=str(obj.get("notes",""))[:2000]
                target["visual_review"]={"status":status,"notes":notes,"reviewed_at":datetime.datetime.now(datetime.timezone.utc).isoformat() if status!="pending" else None}
                data["updated_at"]=datetime.datetime.now().astimezone().isoformat()
                tmp=dest.with_suffix(".tmp"); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),"utf-8"); tmp.replace(dest)
                return self.send_json({"ok":True,"shot_id":shot_id,"visual_review":target["visual_review"]})
            except Exception as e: return self.send_json({"error":"保存视觉复核失败："+str(e)},400)
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
    port = int(os.environ.get("MANGA_PORT", "8766"))
    print(f"Manga API listening on 0.0.0.0:{port}",flush=True)
    ThreadingHTTPServer(("0.0.0.0",port),Handler).serve_forever()
