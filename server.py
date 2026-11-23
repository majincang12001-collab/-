#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import json, uuid, datetime, re

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
PROJECTS = DATA / "projects.json"
PRODUCTION = DATA / "production"
PRODUCTION.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True); UPLOADS.mkdir(exist_ok=True)
MAX_UPLOAD = 40 * 1024 * 1024
ACCESS_KEY = (DATA / "access.key").read_text("utf-8").strip()

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
        if p.path.startswith("/api/") and p.path != "/api/health" and self.headers.get("X-Access-Key") != ACCESS_KEY:
            return self.send_json({"error":"访问口令不正确，请重新登录"},401)
        if p.path == "/api/health":
            return self.send_json({"ok":True,"service":"manga-console","time":datetime.datetime.now(datetime.timezone.utc).isoformat()})
        if p.path == "/api/projects":
            return self.send_json({"projects":load_projects()})
        if p.path == "/api/production":
            q=parse_qs(p.query); project_id=(q.get("project_id") or [""])[0]
            if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
            f=PRODUCTION/(project_id+".json")
            try: data=json.loads(f.read_text("utf-8")) if f.exists() else {"project_id":project_id,"story":"","characters":[],"storyboard":[]}
            except Exception: data={"project_id":project_id,"story":"","characters":[],"storyboard":[]}
            return self.send_json({"production":data})
        if p.path.startswith("/api/files/"):
            name=Path(p.path.removeprefix("/api/files/")).name
            f=UPLOADS/name
            if not f.is_file(): return self.send_json({"error":"not found"},404)
            self.send_response(200); self.send_header("Content-Type","application/octet-stream"); self.send_header("Content-Length",str(f.stat().st_size)); self.send_header("Content-Disposition",f'attachment; filename="{quote(name)}"'); self.end_headers()
            with f.open("rb") as stream:
                while chunk:=stream.read(1024*1024): self.wfile.write(chunk)
            return
        return super().do_GET()
    def do_POST(self):
        p=urlparse(self.path)
        if self.headers.get("X-Access-Key") != ACCESS_KEY:
            return self.send_json({"error":"访问口令不正确，请重新登录"},401)
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
                return self.send_json({"project":item},201)
            except Exception as e: return self.send_json({"error":str(e)},400)
        if p.path == "/api/production":
            try:
                n=int(self.headers.get("Content-Length","0"))
                if n>2*1024*1024: return self.send_json({"error":"制作资料超过2MB"},413)
                obj=json.loads(self.rfile.read(n) or b"{}")
                project_id=str(obj.get("project_id",""))
                if not any(x["id"]==project_id for x in load_projects()): return self.send_json({"error":"项目不存在"},404)
                story=str(obj.get("story",""))[:50000]
                characters=obj.get("characters",[]); storyboard=obj.get("storyboard",[])
                if not isinstance(characters,list) or not isinstance(storyboard,list): return self.send_json({"error":"人物和分镜必须为列表"},400)
                data={"project_id":project_id,"story":story,"characters":characters[:200],"storyboard":storyboard[:1000],"updated_at":datetime.datetime.now().astimezone().isoformat()}
                dest=PRODUCTION/(project_id+".json"); tmp=dest.with_suffix(".tmp")
                tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),"utf-8"); tmp.replace(dest)
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
            return self.send_json({"file":meta},201)
        return self.send_json({"error":"not found"},404)
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.log_date_time_string(), fmt%args), flush=True)

if __name__=="__main__":
    print("Manga API listening on 0.0.0.0:8766",flush=True)
    ThreadingHTTPServer(("0.0.0.0",8766),Handler).serve_forever()