#!/usr/bin/env python3
"""Free Kaggle GPU worker for Qwen3-TTS Mandarin voice previews.

Set MANGA_SERVER_URL and MANGA_WORKER_KEY in Kaggle Secrets.
Install once in the Kaggle notebook: pip install -U qwen-tts soundfile
The worker uses the 0.6B CustomVoice model and does not call paid APIs.
"""
import io, json, os, time, urllib.request, urllib.error, traceback

SERVER=os.environ.get('MANGA_SERVER_URL','').rstrip('/')
KEY=os.environ.get('MANGA_WORKER_KEY','')
MODEL=os.environ.get('QWEN_TTS_MODEL','Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice')
POLL_SECONDS=int(os.environ.get('MANGA_TTS_POLL_SECONDS','12'))

if not SERVER or not KEY:
    raise SystemExit('Missing Kaggle Secrets: MANGA_SERVER_URL and MANGA_WORKER_KEY')

import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

print('CUDA:',torch.cuda.is_available())
if not torch.cuda.is_available(): raise SystemExit('A free CUDA GPU session is required')
print('Loading:',MODEL)
model=Qwen3TTSModel.from_pretrained(MODEL,device_map='cuda:0',dtype=torch.float16,attn_implementation='sdpa')
print('Qwen3-TTS ready')

def request(path,data=None,content_type='application/json'):
    headers={'X-Access-Key':KEY,'X-Worker-Key':KEY}
    if data is not None: headers['Content-Type']=content_type
    req=urllib.request.Request(SERVER+path,data=data,headers=headers,method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req,timeout=180) as r: return r.read()

while True:
    try:
        payload=json.loads(request('/api/tts/next'))
        job=payload.get('job')
        if not job:
            time.sleep(POLL_SECONDS); continue
        jid=job['job_id']; print('TTS job',jid,job.get('speaker'))
        try:
            wavs,sr=model.generate_custom_voice(
                text=job['text'], language='Chinese', speaker=job.get('speaker','Serena'),
                instruct=job.get('instruct','使用自然的中国大陆普通话，吐字清楚，语气自然')
            )
            buf=io.BytesIO(); sf.write(buf,wavs[0],sr,format='WAV'); audio=buf.getvalue()
            result=json.loads(request('/api/tts/result?job_id='+jid,audio,'audio/wav'))
            print('Completed',jid,result)
        except Exception as exc:
            traceback.print_exc()
            try: request('/api/tts/fail?job_id='+jid,str(exc).encode('utf-8'),'text/plain; charset=utf-8')
            except Exception: traceback.print_exc()
    except KeyboardInterrupt:
        print('Worker stopped'); break
    except Exception:
        traceback.print_exc(); time.sleep(POLL_SECONDS)
