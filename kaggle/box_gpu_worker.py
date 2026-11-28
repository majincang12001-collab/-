"""Kaggle free-GPU pull worker for the Box manga console.

Set MANGA_BOX_URL and MANGA_WORKER_KEY as Kaggle secrets/environment variables.
Run from a Kaggle GPU notebook after model bundle + keyframe dataset are attached:
  %run /kaggle/working/box_gpu_worker.py
The worker only initiates outbound HTTPS requests; no inbound Kaggle port is needed.
"""
from pathlib import Path
import io, json, os, time, zipfile, urllib.request, urllib.parse, urllib.error, subprocess, sys

BASE=(os.environ.get('MANGA_BOX_URL') or '').rstrip('/')
KEY=os.environ.get('MANGA_WORKER_KEY') or ''
PROJECT_ID=(os.environ.get('MANGA_PROJECT_ID') or 'b29a4b95cfee').strip()  # Default to the active first-episode project; override explicitly for another project.
ROOT=Path('/kaggle/working')
if not BASE or not KEY:
    raise RuntimeError('请在 Kaggle Secrets/环境变量设置 MANGA_BOX_URL 和 MANGA_WORKER_KEY')
HEADERS={'X-Access-Key':KEY,'X-Worker-Key':KEY,'Accept':'application/json'}

def request(path, data=None, content_type='application/json', timeout=90):
    headers=dict(HEADERS)
    if data is not None: headers['Content-Type']=content_type
    req=urllib.request.Request(BASE+path,data=data,headers=headers,method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req,timeout=timeout) as response: return response.read()

def characters_with_references(job):
    def is_memorial(c):
        return (str(c.get('entity_type') or '') == 'memorial_only'
                or '墓碑' in str(c.get('visual_lock') or '')
                or '不生成实体' in str(c.get('negative_constraints') or ''))
    return [c for c in job.get('characters',[]) if c.get('reference_file') and not is_memorial(c)]


def shot_character_names(shot):
    raw=shot.get('character_names') or shot.get('characters') or shot.get('character') or shot.get('character_name') or []
    if isinstance(raw,str):
        import re
        return list(dict.fromkeys(x.strip() for x in re.split(r'[、，,；;|]',raw) if x.strip()))
    if isinstance(raw,list):
        return list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))
    return []


def mark_shots_without_character_references(job):
    referenced_names={str(c.get('name','')).strip() for c in characters_with_references(job)}
    for shot in job.get('storyboard',[]):
        raw=shot.get('character_names') or shot.get('characters') or shot.get('character') or shot.get('character_name') or []
        if isinstance(raw,str):
            import re
            names=list(dict.fromkeys(x.strip() for x in re.split(r'[、，,；;|]',raw) if x.strip()))
        elif isinstance(raw,list):
            names=list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))
        else: names=[]
        if len(names)>1:
            # Diffusers supports multiple images for one IP-Adapter with per-image
            # spatial masks. Enable only when every cast member has their own ref.
            if 2 <= len(names) <= 4 and all(name in referenced_names for name in names):
                shot['multi_character_reference']=True
                # A queue payload may have been produced by an older server that
                # marked every multi-cast shot text-only. Clear that stale flag.
                shot.pop('text_only_character', None)
                shot.pop('multi_character_reference_note', None)
            else:
                shot['text_only_character']=True
                shot.pop('multi_character_reference', None)
                shot['multi_character_reference_note']='多人镜头中有角色缺少独立基准图，暂不挂载任何单人参考图，避免身份串用。'
        elif not names or names[0] not in referenced_names:
            shot['text_only_character']=True
    return referenced_names

def fail_job(job_id, detail):
    if not job_id: return
    try:
        request('/api/worker/fail?job_id='+urllib.parse.quote(job_id),data=str(detail).encode('utf-8','replace')[:1800],content_type='text/plain',timeout=30)
        print('JOB_MARKED_FAILED',job_id,flush=True)
    except Exception as exc:
        print('JOB_FAIL_REPORT_ERROR',job_id,type(exc).__name__,str(exc),flush=True)


def main():
    print('BOX_WORKER_CONNECTED', BASE, flush=True)
    batch=request('/api/worker/batch-script',timeout=60)
    (ROOT/'batch_generate_from_jobs.py').write_bytes(batch)
    print('BATCH_SCRIPT_READY',len(batch),flush=True)
    print('GPU_CHECK', subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.total,memory.free','--format=csv,noheader'],text=True), flush=True)
    current_job_id=None
    while True:
        try:
            payload=json.loads(request('/api/worker/next'+(('?project_id='+urllib.parse.quote(PROJECT_ID)) if PROJECT_ID else ''),timeout=30).decode())
            job=payload.get('job')
            if not job:
                print('QUEUE_EMPTY; waiting 15 seconds',flush=True); time.sleep(15); continue
            jid=job['job_id']; current_job_id=jid; project=job['project_id']
            print('JOB_CLAIMED',jid,'TYPE',job.get('task_type','keyframes'),'SHOTS',job.get('max_shots'),flush=True)
            (ROOT/'manga-generation-jobs.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),'utf-8')
            ref_dir=ROOT/'character_references'; ref_dir.mkdir(exist_ok=True)
            is_candidate_job=job.get('task_type')=='character_candidates'
            referenced=[] if is_candidate_job else characters_with_references(job)
            # This legacy global path must never contain a real character image:
            # otherwise any unmatched/empty-cast shot could inherit the first face.
            # Real identity references are downloaded only by exact character name below.
            from PIL import Image
            Image.new('RGB',(512,512),(127,127,127)).save(ROOT/'character_reference.png')
            if not referenced:
                job['text_only_reference']=True
            else:
                job.pop('text_only_reference', None)
            for character in referenced:
                cname=str(character.get('name','character'))
                safe=''.join(ch if ch.isalnum() else '_' for ch in cname)[:40] or 'character'
                path=ref_dir/(safe+'.png')
                data=request('/api/worker/reference?project_id='+urllib.parse.quote(project)+'&job_id='+urllib.parse.quote(jid)+'&character_name='+urllib.parse.quote(cname),timeout=60)
                path.write_bytes(data); character['local_reference_file']=str(path)
            mark_shots_without_character_references(job)
            for shot in job.get('storyboard',[]):
                names=shot_character_names(shot)
                if len(names)>1 and shot.get('multi_character_reference') and not shot.get('text_only_character'):
                    by_name={str(c.get('name','')).strip():c for c in job.get('characters',[])}
                    shot['reference_paths']=[by_name[name]['local_reference_file'] for name in names]
                else:
                    character=next((c for c in job.get('characters',[]) if len(names)==1 and str(c.get('name','')).strip()==names[0]),None)
                    if character and character.get('local_reference_file') and not shot.get('text_only_character'):
                        shot['reference_path']=character['local_reference_file']
            (ROOT/'manga-generation-jobs.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),'utf-8')
            aspect=job.get('aspect_ratio','16:9'); width,height=(896,512) if aspect=='16:9' else ((512,896) if aspect=='9:16' else (672,896))
            env=os.environ.copy(); env.update({'MAX_SHOTS':str(job.get('max_shots',2)),'MANGA_WIDTH':str(width),'MANGA_HEIGHT':str(height),'MANGA_STEPS':'22','MANGA_REFERENCE_CROP':'face','MANGA_IP_ADAPTER_SCALE':('0.45' if referenced else '0.0')})
            out=ROOT/'manga_keyframes';
            if out.exists():
                import shutil; shutil.rmtree(out)
            subprocess.run([sys.executable,str(ROOT/'batch_generate_from_jobs.py'),'--max-shots',str(job.get('max_shots',2))],env=env,check=True)
            archive=ROOT/'manga_keyframes.zip'; raw=archive.read_bytes()
            if is_candidate_job:
                result=request('/api/worker/character-candidates/result?job_id='+jid,data=raw,content_type='application/zip',timeout=180)
                print('CHARACTER_CANDIDATES_UPLOADED',result.decode(),flush=True)
            else:
                result=request('/api/worker/result?job_id='+jid,data=raw,content_type='application/zip',timeout=180)
                print('JOB_UPLOADED',result.decode(),flush=True)
            current_job_id=None
        except urllib.error.HTTPError as e:
            detail=e.read().decode('utf-8','replace')[:1000]
            print('WORKER_HTTP_ERROR',e.code,detail,flush=True)
            if e.code in (401,403): raise
            fail_job(current_job_id, f'HTTP {e.code}: {detail}')
            current_job_id=None
            time.sleep(15)
        except Exception as e:
            detail=type(e).__name__+': '+str(e)
            print('WORKER_ERROR',detail,flush=True)
            fail_job(current_job_id,detail)
            current_job_id=None
            time.sleep(15)

if __name__=='__main__': main()
