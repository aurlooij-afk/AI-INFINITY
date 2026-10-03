import json, os, re, shutil, subprocess, tempfile, time, uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(os.getenv('AI_INFINITY_DATA_DIR','/tmp/ai-infinity')) / 'content_factory'
ROOT.mkdir(parents=True, exist_ok=True)
PROJECTS: Dict[str, Dict[str, Any]] = {}


def _uid(prefix='studio'):
    return f'{prefix}_{uuid.uuid4().hex}'

def _http_json(url: str, headers: Optional[dict]=None, timeout: int=25):
    req=Request(url, headers={'User-Agent':'AI-Infinity/3621 (+https://ai-infinity-ca5e.onrender.com)', **(headers or {})})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8','replace'))

def _download(url: str, path: Path, headers: Optional[dict]=None, timeout: int=60):
    req=Request(url, headers={'User-Agent':'AI-Infinity/3621', **(headers or {})})
    with urlopen(req, timeout=timeout) as r, path.open('wb') as f:
        shutil.copyfileobj(r, f)
    return path

def _safe_name(s):
    return re.sub(r'[^a-zA-Z0-9._-]+','_',s)[:120]

def _ffmpeg(*args, timeout=240):
    p=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*map(str,args)],capture_output=True,text=True,timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-3000:] or 'ffmpeg failed')
    return p

def _ffprobe_duration(path: Path) -> float:
    try:
        p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True,text=True,timeout=30)
        return max(0.1,float(p.stdout.strip()))
    except Exception:
        return 0.1

def _clean_json(text: str):
    text=text.strip()
    m=re.search(r'\{.*\}',text,re.S)
    if m: text=m.group(0)
    return json.loads(text)

def _fallback_plan(title: str, objective: str, fmt: str, duration: int) -> dict:
    topic=title.strip() or objective.strip() or 'the topic'
    if len(topic)>120: topic=topic[:120]
    scenes=max(6,min(24,round(duration/8)))
    beats=[]
    for i in range(scenes):
        beats.append({'id':i+1,'heading':f'{topic} — chapter {i+1}','narration':f'In this section, we examine {topic} from a practical and useful angle. The key point is what this means, why it matters, and what the viewer can take away.','visual_query':topic,'on_screen':topic,'duration':max(4,round(duration/scenes,1))})
    return {'title':topic,'hook':f'What should you know about {topic} before you act?','audience':'general audience','tone':'clear, intelligent, engaging','claims_to_verify':[topic],'chapters':beats,'cta':'Follow for the next practical explanation.'}


def _ai_plan(model_fn: Optional[Callable], title, objective, fmt, duration, audience, tone):
    if not model_fn:
        return _fallback_plan(title,objective,fmt,duration), 'builtin'
    prompt=f'''Create a professional {fmt} content production plan. Topic/title: {title or objective}. Objective: {objective}. Audience: {audience}. Tone: {tone}. Target duration: {duration} seconds. Return ONLY valid JSON with keys title, hook, audience, tone, chapters, cta. chapters must be an array of 6-24 objects, each with heading,narration,visual_query,on_screen,duration. Write original useful narration, not filler. Avoid unsupported factual claims and mark facts that need verification in claims_to_verify.'''
    try:
        r=model_fn({'messages':[{'role':'user','content':prompt}],'temperature':0.65})
        txt=(r or {}).get('text') or (r or {}).get('answer') or ''
        data=_clean_json(txt)
        if isinstance(data,dict) and isinstance(data.get('chapters'),list) and data['chapters']:
            return data, (r or {}).get('provider','model')
    except Exception:
        pass
    return _fallback_plan(title,objective,fmt,duration), 'builtin'


def _pexels_videos(query, outdir, limit=6):
    key=os.getenv('PEXELS_API_KEY','').strip()
    if not key: return []
    try:
        data=_http_json('https://api.pexels.com/videos/search?'+urlencode({'query':query,'per_page':min(limit,15),'orientation':'landscape'}),{'Authorization':key})
        out=[]
        for v in data.get('videos',[]):
            files=sorted(v.get('video_files',[]), key=lambda x:(x.get('width') or 0), reverse=True)
            f=next((x for x in files if (x.get('width') or 0)>=720 and x.get('link')),None) or next((x for x in files if x.get('link')),None)
            if not f: continue
            p=outdir/f"pexels_{v.get('id','x')}.mp4"
            _download(f['link'],p)
            out.append({'path':str(p),'source':'Pexels','source_url':v.get('url'),'creator':v.get('user',{}).get('name'),'license':'Pexels license','width':f.get('width'),'height':f.get('height')})
        return out
    except Exception: return []

def _pixabay_videos(query, outdir, limit=6):
    key=os.getenv('PIXABAY_API_KEY','').strip()
    if not key: return []
    try:
        data=_http_json('https://pixabay.com/api/videos/?'+urlencode({'key':key,'q':query,'per_page':min(limit,20)}))
        out=[]
        for v in data.get('hits',[]):
            choices=v.get('videos',{})
            f=choices.get('large') or choices.get('medium') or choices.get('small')
            if not f or not f.get('url'): continue
            p=outdir/f"pixabay_{v.get('id','x')}.mp4"; _download(f['url'],p)
            out.append({'path':str(p),'source':'Pixabay','source_url':v.get('pageURL'),'creator':v.get('user'),'license':'Pixabay Content License','width':f.get('width'),'height':f.get('height')})
        return out
    except Exception: return []

def _commons_videos(query, outdir, limit=6):
    # Commons is public and keyless; prefer actual video media, never fabricate a fake clip.
    try:
        url='https://commons.wikimedia.org/w/api.php?'+urlencode({'action':'query','format':'json','generator':'search','gsrsearch':f'{query} filetype:video','gsrnamespace':6,'gsrlimit':limit,'prop':'imageinfo','iiprop':'url|mime|extmetadata'})
        data=_http_json(url)
        out=[]
        for page in data.get('query',{}).get('pages',{}).values():
            info=(page.get('imageinfo') or [{}])[0]; media=info.get('url'); mime=info.get('mime','')
            if not media or ('video' not in mime and not re.search(r'\.(webm|ogv|mp4)(\?|$)',media,re.I)): continue
            p=outdir/f"commons_{page.get('pageid','x')}.media"
            try: _download(media,p)
            except Exception: continue
            meta=info.get('extmetadata') or {}
            out.append({'path':str(p),'source':'Wikimedia Commons','source_url':page.get('canonicalurl') or 'https://commons.wikimedia.org','creator':(meta.get('Artist') or {}).get('value'),'license':(meta.get('LicenseShortName') or {}).get('value'),'title':page.get('title')})
        return out
    except Exception: return []

def _find_clips(queries, outdir, max_clips=12):
    clips=[]; seen=set()
    for q in queries:
        for getter in (_pexels_videos,_pixabay_videos,_commons_videos):
            for item in getter(q,outdir,limit=4):
                if item['path'] in seen: continue
                seen.add(item['path']); clips.append(item)
                if len(clips)>=max_clips: return clips
    return clips

def _make_voice(plan, workdir, voice='en-US-AriaNeural'):
    text='\n\n'.join(str(x.get('narration','')).strip() for x in plan['chapters'] if x.get('narration'))
    txt=workdir/'narration.txt'; txt.write_text(text,encoding='utf-8')
    out=workdir/'narration.mp3'
    # Neural voice first when edge-tts can reach its service; local voice is a real fallback.
    try:
        import asyncio, edge_tts
        async def run():
            await edge_tts.Communicate(text, voice).save(str(out))
        asyncio.run(run())
        if out.exists() and out.stat().st_size>10000: return out,'edge-tts-neural'
    except Exception: pass
    exe=shutil.which('espeak-ng') or shutil.which('espeak')
    if not exe: raise RuntimeError('No narration engine available')
    wav=workdir/'narration.wav'
    subprocess.run([exe,'-s','155','-w',str(wav),text],check=True,timeout=180)
    _ffmpeg('-i',wav,'-codec:a','libmp3lame','-q:a','2',out)
    return out,'local-espeak'

def _make_music(workdir, seconds):
    out=workdir/'music.m4a'
    # Original procedural ambient bed, not a copied song.
    _ffmpeg('-f','lavfi','-i',f'sine=frequency=110:sample_rate=48000:duration={max(1,seconds)}','-f','lavfi','-i',f'sine=frequency=165:sample_rate=48000:duration={max(1,seconds)}','-filter_complex','[0:a]volume=0.035[a0];[1:a]volume=0.018[a1];[a0][a1]amix=inputs=2:duration=longest,lowpass=f=900,afade=t=in:st=0:d=2,afade=t=out:st='+str(max(0,seconds-3))+':d=3[a]','-map','[a]','-c:a','aac','-b:a','128k',out)
    return out

def _concat_video_clips(clips, target_seconds, workdir, width=1920, height=1080):
    if not clips: return None
    norm=[]
    for i,c in enumerate(clips):
        p=workdir/f'norm_{i}.mp4'
        try:
            _ffmpeg('-i',c['path'],'-t','8','-vf',f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}','-an','-r','30','-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p',p,timeout=180)
            if p.exists() and p.stat().st_size>5000: norm.append(p)
        except Exception: pass
    if not norm: return None
    manifest=workdir/'clips.txt'; manifest.write_text(''.join(f"file '{str(p).replace(chr(39),chr(39)+chr(92)+chr(39)+chr(39))}'\n" for p in norm),encoding='utf-8')
    out=workdir/'visuals.mp4'
    _ffmpeg('-f','concat','-safe','0','-i',manifest,'-t',target_seconds,'-an','-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p',out,timeout=300)
    return out if out.exists() else None

def _srt(plan, duration, path):
    lines=[]; t=0.0
    total=max(1,sum(float(x.get('duration') or 1) for x in plan['chapters']))
    for i,ch in enumerate(plan['chapters'],1):
        d=duration*(float(ch.get('duration') or 1)/total); start=t; end=min(duration,t+d); t=end
        def ts(x):
            ms=int(round((x-int(x))*1000)); sec=int(x); return f'{sec//3600:02}:{sec%3600//60:02}:{sec%60:02},{ms:03}'
        text=re.sub(r'\s+',' ',str(ch.get('narration',''))).strip()
        lines.append(f'{i}\n{ts(start)} --> {ts(end)}\n{text}\n')
    path.write_text('\n'.join(lines),encoding='utf-8')

def _assemble(visuals, narration, music, captions, out, duration):
    if not visuals: raise RuntimeError('No real motion-video source was available. Configure PEXELS_API_KEY/PIXABAY_API_KEY or allow Wikimedia Commons video retrieval.')
    # Use narration as the timing authority; trim visuals to its duration and burn captions.
    filt='[1:a]volume=1.0[vo];[2:a]volume=0.14[m];[vo][m]amix=inputs=2:duration=first:dropout_transition=2[a]'
    subtitle_path=str(captions).replace('\\','/').replace(':','\\:')
    subtitle_filter=f"subtitles={subtitle_path}"
    _ffmpeg('-i',visuals,'-i',narration,'-i',music,'-filter_complex',filt,'-map','0:v:0','-map','[a]','-vf',subtitle_filter,'-t',duration,'-c:v','libx264','-preset','medium','-crf','19','-c:a','aac','-b:a','192k','-movflags','+faststart',out,timeout=600)

def create_project(req: dict, model_fn=None):
    fmt=str(req.get('format','long')).lower(); fmt='short' if fmt in {'short','reel','tiktok','shorts'} else 'long'
    duration=int(req.get('duration') or (45 if fmt=='short' else 480)); duration=max(15,min(duration,3600 if fmt=='long' else 180))
    title=str(req.get('title','')).strip(); objective=str(req.get('objective','')).strip()
    if not objective and not title: raise ValueError('title or objective is required')
    audience=str(req.get('audience','general audience')); tone=str(req.get('tone','engaging, useful, natural'))
    pid=_uid('content'); work=ROOT/pid; work.mkdir(parents=True,exist_ok=True)
    plan,provider=_ai_plan(model_fn,title,objective,fmt,duration,audience,tone)
    queries=[str(x.get('visual_query') or plan.get('title') or objective) for x in plan['chapters']]
    clips=_find_clips(queries,work,max_clips=min(18,max(6,len(plan['chapters']))))
    # A production request should never silently turn into colored placeholder slides.
    if not clips:
        return {'status':'needs_visual_source','project_id':pid,'message':'No real motion-video source was reachable. No fake slideshow was produced. Add PEXELS_API_KEY or PIXABAY_API_KEY, or ensure Wikimedia Commons video access is available.','ai_provider':provider,'plan':plan,'truthful':True}
    narration,voice=_make_voice(plan,work,str(req.get('voice','en-US-AriaNeural')))
    ndur=_ffprobe_duration(narration); target=min(float(duration),max(15,ndur))
    music=_make_music(work,int(target+1)); captions=work/'captions.srt'; _srt(plan,target,captions)
    visuals=_concat_video_clips(clips,target,work)
    if not visuals: raise RuntimeError('Video normalization failed; no final video was claimed.')
    final=work/'final.mp4'; _assemble(visuals,narration,music,captions,final,target)
    storyboard=work/'storyboard.json'; storyboard.write_text(json.dumps({'plan':plan,'clips':clips,'voice':voice,'ai_provider':provider},indent=2,ensure_ascii=False),encoding='utf-8')
    sources=work/'sources.json'; sources.write_text(json.dumps(clips,indent=2,ensure_ascii=False),encoding='utf-8')
    result={'status':'completed','project_id':pid,'title':plan.get('title'),'format':fmt,'duration_seconds':round(target,2),'ai_provider':provider,'voice_provider':voice,'visual_source_count':len(clips),'visuals_are_real_video':True,'script_url':f'/infinity/3607/content/{pid}/script','storyboard_url':f'/infinity/3607/content/{pid}/storyboard','captions_url':f'/infinity/3607/content/{pid}/captions.srt','sources_url':f'/infinity/3607/content/{pid}/sources.json','download_url':f'/infinity/3607/content/{pid}.mp4','narration_url':f'/infinity/3607/content/{pid}/narration.mp3','music_url':f'/infinity/3607/content/{pid}/music.m4a','truthful':True}
    (work/'script.md').write_text('# '+str(plan.get('title'))+'\n\n## Hook\n'+str(plan.get('hook'))+'\n\n'+'\n\n'.join(f"## {x.get('heading')}\n{x.get('narration')}" for x in plan['chapters'])+'\n\n## CTA\n'+str(plan.get('cta','')),encoding='utf-8')
    PROJECTS[pid]=result
    return result


def register(app, model_fn=None):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, Field
    class ContentRequest(BaseModel):
        title: str=''
        objective: str=''
        format: str='long'
        duration: int|None=None
        audience: str='general audience'
        tone: str='engaging, useful, natural'
        voice: str='en-US-AriaNeural'
    @app.get('/infinity/3607/health')
    def health():
        return {'status':'healthy','build':'TARGET-2050.3621-COMPAT-3607-PRO-CONTENT-STUDIO','long_form':True,'short_form':True,'real_motion_video_required':True,'fake_slideshow_fallback':False,'captions':True,'voice':True,'music':True,'source_licensing_metadata':True,'truthful':True}
    @app.post('/infinity/3607/content')
    def create(req: ContentRequest):
        try:return create_project(req.model_dump(),model_fn=model_fn)
        except Exception as e: raise HTTPException(status_code=422,detail=str(e))
    @app.get('/infinity/3607/content/{project_id}')
    def status(project_id:str):
        if project_id not in PROJECTS: raise HTTPException(404,'project not found in this process')
        return PROJECTS[project_id]
    def asset(project_id, name, media_type=None):
        p=ROOT/project_id/name
        if not p.exists(): raise HTTPException(404,'asset not found')
        return FileResponse(p,media_type=media_type)
    @app.get('/infinity/3607/content/{project_id}.mp4')
    def video(project_id:str): return asset(project_id,'final.mp4','video/mp4')
    @app.get('/infinity/3607/content/{project_id}/script')
    def script(project_id:str): return asset(project_id,'script.md','text/markdown')
    @app.get('/infinity/3607/content/{project_id}/storyboard')
    def storyboard(project_id:str): return asset(project_id,'storyboard.json','application/json')
    @app.get('/infinity/3607/content/{project_id}/sources.json')
    def sources(project_id:str): return asset(project_id,'sources.json','application/json')
    @app.get('/infinity/3607/content/{project_id}/captions.srt')
    def captions(project_id:str): return asset(project_id,'captions.srt','application/x-subrip')
    @app.get('/infinity/3607/content/{project_id}/narration.mp3')
    def narration(project_id:str): return asset(project_id,'narration.mp3','audio/mpeg')
    @app.get('/infinity/3607/content/{project_id}/music.m4a')
    def music(project_id:str): return asset(project_id,'music.m4a','audio/mp4')
