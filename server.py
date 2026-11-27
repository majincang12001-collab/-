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
        rows=_load_preview_records