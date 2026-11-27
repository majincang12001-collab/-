"""Official MiniMax-H3 video API adapter. Secrets are read only from server-side provider storage."""
from __future__ import annotations
import json, os, re, urllib.request, urllib.error
from pathlib import Path

SECRET_FILE = Path(os.environ.get('MANGA_PROVIDER_SECRET_FILE', str(Path(os.environ.get('MANGA_SECRET_DIR', '/var/lib/manga-console')) / 'provider-keys.json')))
BASE_URL = 'https://api.minimax.io'
MODEL = 'MiniMax-H3'

def _secret():
    try:
        rows=json.loads(SECRET_FILE.read_text('utf-8'))
        row=rows.get('minimax-video',{}) if isinstance(rows,dict) else {}
        return str(row.get('api_key') or '').strip()
    except Exception:
        return ''

def status():
    key=_secret()
    return {'provider_id':'minimax-video','configured':bool(key),'model':MODEL,'base_url':BASE_URL,'secret_value_exposed':False}

def _request(path, method='GET', payload=None, timeout=90):
    key=_secret()
    if not key: raise RuntimeError('尚未在平台密钥管理中心保存 MiniMax API Key')
    data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
    req=urllib.request.Request(BASE_URL+path,data=data,method=method,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail=exc.read(1200).decode('utf-8','replace')
        raise RuntimeError(f'MiniMax API HTTP {exc.code}: {detail[:700]}') from None

def submit(prompt: str, *, duration=6, resolution='768P', ratio='16:9', generate_audio=True):
    prompt=str(prompt or '').strip()
    if not prompt: raise ValueError('视频提示词不能为空')
    if len(prompt)>7000: raise ValueError('提示词不能超过7000字符')
    duration=int(duration)
    if duration<4 or duration>15: raise ValueError('MiniMax-H3时长必须为4—15秒')
    if resolution not in ('768P','2K'): raise ValueError('MiniMax-H3分辨率仅支持768P或2K')
    if ratio not in ('16:9','9:16','4:3','3:4','1:1','21:9'): raise ValueError('不支持的画面比例')
    return _request('/v2/video_generation','POST',{'model':MODEL,'content':[{'type':'text','text':prompt}],'duration':duration,'resolution':resolution,'ratio':ratio,'generate_audio':bool(generate_audio)},timeout=120)

def query(task_id: str):
    task_id=str(task_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,160}',task_id): raise ValueError('任务编号格式无效')
    return _request('/v2/query/video_generation/'+task_id,'GET',timeout=45)
