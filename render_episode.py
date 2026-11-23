#!/usr/bin/env python3
"""Render a Chinese narrated motion-comic animatic from storyboard stills.

This is an offline MVP renderer: it animates available keyframes with a subtle
Ken Burns move, creates Mandarin narration with eSpeak NG, writes SRT subtitles,
and muxes an MP4. It does not claim to synthesize new motion between frames.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import argparse, json, re, subprocess, sys, time

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'; UPLOADS=DATA/'uploads'; RENDERS=DATA/'renders'
FONT='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
W,H,FPS=1280,720,25

def run(args, **kw):
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
def duration(path):
    x=subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path)],text=True)
    return float(x.strip())
def wrap(text, limit=28):
    text=re.sub(r'\s+',' ',str(text or '')).strip()
    if not text:return ''
    return '\n'.join(text[i:i+limit] for i in range(0,len(text),limit))
def make_card(path, project, shot, idx):
    im=Image.new('RGB',(W,H),(20,27,48)); d=ImageDraw.Draw(im)
    try: title=ImageFont.truetype(FONT,38); body=ImageFont.truetype(FONT,27); small=ImageFont.truetype(FONT,20)
    except: title=body=small=ImageFont.load_default()
    d.rectangle((50,48,W-50,H-48),outline=(120,150,210),width=2)
    d.text((90,78),f'{project}  ·  分镜 {idx:02d}',font=small,fill=(170,195,230))
    d.text((90,155),str(shot.get('shot_type','中景')),font=title,fill=(245,220,155))
    visual=wrap(shot.get('visual') or shot.get('description') or '待补充画面描述',24)
    d.multiline_text((90,235),visual,font=body,fill=(245,245,250),spacing=14)
    action=wrap('动作：'+str(shot.get('action') or '待补充'),30)
    d.multiline_text((90,440),action,font=small,fill=(170,205,225),spacing=8)
    d.text((90,620),'AI 漫剧 · 动态分镜预览（尚未生成该镜头原画）',font=small,fill=(140,150,175))
    im.save(path)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--project-id',required=True); ap.add_argument('--job-id',required=True); a=ap.parse_args()
    pid=a.project_id; job=a.job_id
    prod=DATA/'production'/f'{pid}.json'; projects=json.loads((DATA/'projects.json').read_text('utf-8'))
    project=next((x for x in projects if x['id']==pid),None)
    if not project or not prod.exists(): raise SystemExit('Project or production data missing')
    data=json.loads(prod.read_text('utf-8')); shots=data.get('storyboard',[])
    if not shots: raise SystemExit('No storyboard shots. Add and save at least one shot first.')
    out=RENDERS/pid/job; out.mkdir(parents=True,exist_ok=True)
    state=RENDERS/pid/f'{job}.json'; state.parent.mkdir(parents=True,exist_ok=True)
    def status(stage,progress,message): state.write_text(json.dumps({'job_id':job,'status':stage,'progress':progress,'message':message,'updated_at':time.time()},ensure_ascii=False,indent=2),'utf-8')
    status('running',2,'正在准备分镜素材')
    project_files=[x for x in project.get('files',[]) if x.get('stored')]
    clips=[]; audios=[]; subtitles=[]; cursor=0.0
    for idx,shot in enumerate(shots,1):
        status('running',min(10+int((idx-1)/len(shots)*70),80),f'正在制作第 {idx}/{len(shots)} 个镜头')
        candidates=[]
        pattern=f'shot_{int(shot.get("id",idx)):04d}'
        for meta in project_files:
            nm=meta.get('name','')
            if pattern in nm and Path(nm).suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
                candidates.append(UPLOADS/meta['stored'])
        if not candidates:
            for f in UPLOADS.glob(f'{pid}_*'):
                if pattern in f.name and f.suffix.lower() in ('.png','.jpg','.jpeg','.webp'): candidates.append(f)
        img=out/f'image_{idx:04d}.png'
        if candidates:
            Image.open(candidates[0]).convert('RGB').save(img)
        else:
            make_card(img,project['name'],shot,idx)
        speech=str(shot.get('dialogue') or shot.get('narration') or shot.get('visual') or '').strip()
        if speech.startswith('待提取') or speech.startswith('待补充') or speech.startswith('待生成'): speech=''
        wav=out/f'voice_{idx:04d}.wav'
        if speech:
            try: run(['espeak-ng','-v','cmn','-s','155','-w',str(wav),speech])
            except Exception: speech=''
        if not speech:
            run(['ffmpeg','-y','-f','lavfi','-i','anullsrc=r=24000:cl=mono','-t','1.0','-c:a','pcm_s16le',str(wav)])
        ad=duration(wav); clipdur=max(3.5,ad+0.8); frames=max(1,int(clipdur*FPS))
        padded=out/f'voice_padded_{idx:04d}.wav'
        run(['ffmpeg','-y','-i',str(wav),'-af','apad','-t',f'{clipdur:.3f}','-c:a','pcm_s16le',str(padded)])
        clip=out/f'clip_{idx:04d}.mp4'
        vf=f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},zoompan=z='min(zoom+0.00035,1.045)':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p"
        run(['ffmpeg','-y','-loop','1','-i',str(img),'-vf',vf,'-frames:v',str(frames),'-r',str(FPS),'-an','-c:v','libx264','-preset','ultrafast','-crf','24',str(clip)])
        clips.append(clip); audios.append(padded)
        if speech:
            start=cursor; end=cursor+clipdur
            txt=wrap(speech,26)
            def ts(t):
                ms=int(round(t*1000)); h=ms//3600000; ms%=3600000; m=ms//60000; ms%=60000; sec=ms//1000; ms%=1000
                return f'{h:02}:{m:02}:{sec:02},{ms:03}'
            subtitles.append(f'{idx}\n{ts(start)} --> {ts(end)}\n{txt}\n')
        cursor+=clipdur
    status('running',85,'正在合成视频、配音和字幕')
    concat=out/'clips.txt'; concat.write_text(''.join(f"file '{p.as_posix()}'\n" for p in clips),'utf-8')
    silent=out/'silent.mp4'; run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(silent)])
    aconcat=out/'audio.txt'; aconcat.write_text(''.join(f"file '{p.as_posix()}'\n" for p in audios),'utf-8')
    audio=out/'narration.wav'; run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(aconcat),'-c','copy',str(audio)])
    srt=out/'subtitles.srt'; srt.write_text('\n'.join(subtitles),'utf-8')
    mp4=out/'episode_preview.mp4'
    vf=f"subtitles=filename='{srt.as_posix()}':force_style='FontName=Noto Sans CJK SC,FontSize=26,Outline=2,Shadow=1,Alignment=2,MarginV=38'"
    run(['ffmpeg','-y','-i',str(silent),'-i',str(audio),'-vf',vf,'-c:v','libx264','-preset','ultrafast','-crf','23','-c:a','aac','-b:a','128k','-shortest','-movflags','+faststart',str(mp4)])
    (out/'manifest.json').write_text(json.dumps({'project':project['name'],'shots':len(shots),'duration_seconds':round(duration(mp4),2),'video':'episode_preview.mp4','subtitles':'subtitles.srt','narration':'narration.wav','render_mode':'offline_motion_comic_animatic','note':'缺少原画的镜头使用文字占位卡；Ken Burns 动效不等于 AI 生成的真实角色动作。'},ensure_ascii=False,indent=2),'utf-8')
    status('done',100,'动态分镜预览已完成')
    print(json.dumps({'job_id':job,'video':str(mp4),'duration':round(duration(mp4),2),'shots':len(shots)},ensure_ascii=False))
if __name__=='__main__':
    try: main()
    except Exception as e:
        import traceback
        print(traceback.format_exc(),file=sys.stderr)
        try:
            ap=argparse.ArgumentParser(); ap.add_argument('--project-id'); ap.add_argument('--job-id'); a,_=ap.parse_known_args()
            state=RENDERS/a.project_id/f'{a.job_id}.json'; state.parent.mkdir(parents=True,exist_ok=True)
            state.write_text(json.dumps({'job_id':a.job_id,'status':'failed','progress':0,'message':str(e)},ensure_ascii=False),'utf-8')
        except Exception: pass
        raise
[exit status 0]