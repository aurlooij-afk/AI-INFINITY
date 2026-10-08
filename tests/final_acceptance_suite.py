from __future__ import annotations
import concurrent.futures, hashlib, json, os, re, shutil, sqlite3, subprocess, sys, time, zipfile
from pathlib import Path
from urllib.parse import quote
import requests

REPO_ROOT=Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0,str(REPO_ROOT))

TMP=Path("/tmp/ai-infinity-final-acceptance"); TMP.mkdir(parents=True,exist_ok=True)
PYURL="https://www.python.org/"
def req(s,b,p,m="GET",**kw):
    r=s.request(m,b+p,timeout=180,**kw)
    if not r.ok:
        try:x=r.json()
        except Exception:x=r.text[:3000]
        raise AssertionError(f"{m} {p} -> {r.status_code}: {x}")
    try:return r.json()
    except Exception:return r.content
def start(d,port,extra=None):
    d.mkdir(parents=True,exist_ok=True)
    e=os.environ.copy(); e.update({"AI_INFINITY_DATA_DIR":str(d),"AI_INFINITY_3601_BACKGROUND":"false","AI_INFINITY_FAST_MODE":"1","AI_INFINITY_REQUIRE_SOURCE_VISUALS":"0","AI_INFINITY_REQUIRE_RESEARCH_EVIDENCE":"1","AI_INFINITY_SMOKE_ALLOW_UNVERIFIED_DELIVERY":"1","PYTHONUNBUFFERED":"1"}); e.update(extra or {})
    diag=subprocess.run(
        [sys.executable,"-c",
         "import inspect,studio_ultimate as s; f=s.tts; print('TTS_DIAG',f.__module__,f.__qualname__,getattr(getattr(f,'__code__',None),'co_filename',None),getattr(getattr(f,'__code__',None),'co_firstlineno',None),inspect.signature(f)); print('WRAPPED',getattr(f,'__wrapped__',None))"],
        env=e,cwd=Path.cwd(),capture_output=True,text=True,timeout=60,
    )
    print("BOOT_DIAG",diag.stdout.strip(),diag.stderr.strip())
    diag2=subprocess.run(
        [sys.executable,"-c",
         "import inspect,main,studio_ultimate as s; f=s.tts; print('MAIN_TTS_DIAG',f.__module__,f.__qualname__,getattr(getattr(f,'__code__',None),'co_filename',None),getattr(getattr(f,'__code__',None),'co_firstlineno',None),inspect.signature(f))"],
        env=e,cwd=Path.cwd(),capture_output=True,text=True,timeout=60,
    )
    print("MAIN_BOOT_DIAG",diag2.stdout.strip(),diag2.stderr.strip())
    log=(d/"server.log").open("ab")
    p=subprocess.Popen([sys.executable,"-m","uvicorn","main:app","--host","127.0.0.1","--port",str(port)],env=e,cwd=Path.cwd(),stdout=log,stderr=subprocess.STDOUT)
    b=f"http://127.0.0.1:{port}"; end=time.time()+90
    while time.time()<end:
        try:req(requests.Session(),b,"/health"); return p,b,log
        except Exception:time.sleep(1)
    p.kill(); log.close(); raise RuntimeError((d/"server.log").read_text(errors="replace")[-12000:])
def stop(p,log):
    if p.poll() is None:
        p.terminate()
        try:p.wait(20)
        except subprocess.TimeoutExpired:p.kill();p.wait(10)
    log.close()
def create(s,b,cmd,**kw):
    x=req(s,b,"/infinity/canonical/create","POST",json={"command":cmd,**kw}); pid=str(x.get("project_id") or ""); assert pid,x; return pid
def waitp(s,b,pid,timeout=600):
    end=time.time()+timeout; last={}
    while time.time()<end:
        last=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}")
        if str(last.get("status","")).lower() in {"completed","completed_with_qc_warnings","failed","cancelled"}:
            assert str(last.get("status")).lower() == "completed",last
            assert str(last.get("state") or "").upper() == "COMPLETED",last
            return last
        time.sleep(2)
    raise AssertionError(f"timeout {pid}: {last}")
def names(s,b,pid):
    x=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/artifacts")
    return {str(a.get("asset_name") or a.get("name")) for a in x.get("artifacts",[])}
def dl(s,b,pid,name,min_bytes=1):
    r=s.get(b+f"/infinity/studio/project/{quote(pid,safe='')}/asset/{quote(name,safe='')}",timeout=180)
    assert r.ok,(name,r.status_code,r.text[:1000])
    p=TMP/f"{pid}-{name.replace('/','_')}"; p.write_bytes(r.content); assert p.stat().st_size>=min_bytes,(name,p.stat().st_size); return p
def jart(s,b,pid,name):return json.loads(dl(s,b,pid,name).read_text(encoding="utf-8"))
def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda:f.read(1048576),b""):h.update(c)
    return h.hexdigest()
def probe(p):
    x=subprocess.run(["ffprobe","-v","error","-show_format","-show_streams","-of","json",str(p)],capture_output=True,text=True,timeout=120); assert x.returncode==0,x.stderr; return json.loads(x.stdout)
def pdf(path):
    text="AI Infinity acceptance source. Real PDF document for research, script, podcast, video, article and social packaging."
    stream=f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objs=[b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",b"5 0 obj << /Length "+str(len(stream)).encode()+b" >> stream\n"+stream+b"\nendstream endobj\n"]
    out=bytearray(b"%PDF-1.4\n"); offs=[0]
    for o in objs:offs.append(len(out));out.extend(o)
    x=len(out);out.extend(f"xref\n0 {len(objs)+1}\n".encode());out.extend(b"0000000000 65535 f \n")
    for o in offs[1:]:out.extend(f"{o:010d} 00000 n \n".encode())
    out.extend(f"trailer << /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode());path.write_bytes(out)
def t1(s,b):
    pid=create(s,b,"Create a professional 60-second AI Infinity launch video in English, 16:9, cinematic, research-backed, with voiceover, background music, captions, thumbnail, article, SEO metadata, social campaign package and production/rights manifests.",duration=60,format="long",aspect_ratio="16:9"); waitp(s,b,pid)
    reqd={"final.mp4","thumbnail.jpg","audio_master.mp3","captions.srt","article.md","seo.json","social_campaign.json","production_manifest.json","rights_manifest.json","package.zip"}; got=names(s,b,pid); assert reqd<=got,(reqd-got,got)
    files={n:dl(s,b,pid,n) for n in reqd}; m=probe(files["final.mp4"]); dur=float((m.get("format")or{}).get("duration")or 0); assert 59<=dur<=61,m
    assert any(x.get("codec_type")=="video" for x in m.get("streams",[])); assert any(x.get("codec_type")=="audio" for x in m.get("streams",[]))
    src=jart(s,b,pid,"sources.json"); assert int(src.get("source_count") or len(src.get("sources")or[]))>0,src
    with zipfile.ZipFile(files["package.zip"]) as z: assert {"final.mp4","thumbnail.jpg","audio_master.mp3","captions.srt","article.md"}<=set(z.namelist())
    return {"test":1,"project_id":pid,"duration":dur}
def t2(s,b):
    pid=create(s,b,"Create a professional 60-second AI Infinity launch video in Pashto, 9:16 vertical, cinematic, with natural voiceover, captions and music.",duration=60,format="short",aspect_ratio="9:16",language="Pashto"); waitp(s,b,pid)
    sc=dl(s,b,pid,"script.md").read_text(encoding="utf-8"); cp=dl(s,b,pid,"captions.srt").read_text(encoding="utf-8")
    assert any(c in sc for c in "ځښږڅټډړګڼ") and any(c in cp for c in "ځښږڅټډړګڼ"),(sc[:1500],cp[:1000])
    assert len(re.findall(r"[\u0600-\u06ff]",sc))>=40
    state=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}")
    provider=str((state.get("result") or {}).get("voice_provider") or "")
    assert ("pashto" in provider.lower()) or ("ps-af" in provider.lower()),state
    voice=dl(s,b,pid,"narration_01.mp3")
    assert voice.stat().st_size>2000,voice
    vp=probe(voice)
    assert any(x.get("codec_type")=="audio" for x in vp.get("streams",[])),vp
    tr=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/truth"); assert tr.get("verified") is True,tr
    return {"test":2,"project_id":pid,"pashto_text_verified":True,"pashto_voice_provider":provider,"voice_bytes":voice.stat().st_size}
def t3(s,b):
    f=TMP/"acceptance-source.pdf"; pdf(f); assert f.stat().st_size>500
    up=s.post(b+"/infinity/studio/upload",files={"file":(f.name,f.read_bytes(),"application/pdf")},timeout=180); assert up.ok,up.text; assert up.json().get("text_extracted") is True,up.text
    pid=create(s,b,"Turn the uploaded PDF into research-backed content: produce a podcast package, video, article and social campaign with evidence, captions and a final production package.",source_file_names=[f.name],content_type="video",duration=20,format="short"); waitp(s,b,pid)
    got=names(s,b,pid); assert {"final.mp4","podcast_rss.xml","article.md","social_campaign.json","sources.json","captions.srt"}<=got,got
    src=jart(s,b,pid,"sources.json"); assert any(str(x.get("title"))==f.name for x in src.get("sources",[])),src
    return {"test":3,"project_id":pid,"pdf_executed":True}
def t4(s,b):
    pid=create(s,b,"Create a 20-second research-backed video article package about Python's official website with evidence, captions, SEO and social copy.",duration=20,format="short",reference_urls=[PYURL]); waitp(s,b,pid)
    got=names(s,b,pid); assert {"final.mp4","thumbnail.jpg","captions.srt","article.md","seo.json","social_campaign.json","sources.json"}<=got,got
    src=jart(s,b,pid,"sources.json"); assert any(str(x.get("url")or"")==PYURL for x in src.get("sources",[])),src
    return {"test":4,"project_id":pid,"reference_url":PYURL}
def t5():
    import studio_ultimate as st
    out=TMP/"repurpose"; shutil.rmtree(out,ignore_errors=True);out.mkdir(); master=out/"input75s.mp4"
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-f","lavfi","-i","testsrc=size=1280x720:rate=24","-f","lavfi","-i","sine=frequency=440:sample_rate=48000","-t","75","-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k",str(master)],check=True,timeout=180)
    old=st.FAST_MODE;st.FAST_MODE=False
    try:vs=st.make_variants(master,out,[],"Acceptance input")
    finally:st.FAST_MODE=old
    assert len(vs)==3,vs; ps=[Path(v["path"]) for v in vs]; assert all(p.is_file() and p.stat().st_size>10000 for p in ps); assert len({sha(p) for p in ps})==3
    return {"test":5,"short_count":3}
def t6():
    import studio_ultimate as st
    old1=os.environ.get("AI_INFINITY_FORCE_PRIMARY_VISUAL_FAILURE");old2=os.environ.get("HF_TOKEN");os.environ["AI_INFINITY_FORCE_PRIMARY_VISUAL_FAILURE"]="1";os.environ["HF_TOKEN"]="forced-test-token"
    out=TMP/"forced";shutil.rmtree(out,ignore_errors=True);out.mkdir()
    try:a=st.acquire_scene_asset({"heading":"Forced fallback","visual_query":"professional technology laboratory","image_prompt":"professional cinematic laboratory scene"},out,1,prefer_motion=True,duration=5)
    finally:
        if old1 is None:os.environ.pop("AI_INFINITY_FORCE_PRIMARY_VISUAL_FAILURE",None)
        else:os.environ["AI_INFINITY_FORCE_PRIMARY_VISUAL_FAILURE"]=old1
        if old2 is None:os.environ.pop("HF_TOKEN",None)
        else:os.environ["HF_TOKEN"]=old2
    assert st.MEDIA_DEBUG_ERRORS.get("primary-visual-provider")=="forced_failure",st.MEDIA_DEBUG_ERRORS
    p=Path(a["path"]);assert p.is_file() and p.stat().st_size>1000 and not str(a.get("source","")).startswith("Hugging Face"),a
    return {"test":6,"fallback_source":a.get("source")}
def t7():
    d=TMP/"no-external";p,b,l=start(d,18081,{"AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS":"1","AI_INFINITY_REQUIRE_RESEARCH_EVIDENCE":"0"});s=requests.Session()
    try:
        pid=create(s,b,"Create a 20-second English cinematic video using only local/free execution.",duration=20,format="short");state=waitp(s,b,pid);bp=state.get("blueprint")or{};r=bp.get("research")or{};assert r.get("status")=="degraded",r;f=dl(s,b,pid,"final.mp4");m=probe(f);assert all(any(x.get("codec_type")==k for x in m.get("streams",[])) for k in ("video","audio"))
        return {"test":7,"project_id":pid,"research_status":"degraded"}
    finally:stop(p,l)
def t8():
    d=Path("/data/final-acceptance-restart");shutil.rmtree(d,ignore_errors=True);extra={"AI_INFINITY_PERSISTENCE_MODE":"durable","AI_INFINITY_PERSISTENT_VOLUME_CONFIRMED":"true","AI_INFINITY_PROJECT_STATE_DURABLE":"false","AI_INFINITY_REQUIRE_RESEARCH_EVIDENCE":"0"}
    p,b,l=start(d,18082,extra);s=requests.Session()
    try:
        c=req(s,b,"/infinity/canonical/capabilities");assert c.get("storage",{}).get("persistent") is True,c
        pid=create(s,b,"Create a 20-second English cinematic project for restart persistence.",duration=20,format="short");waitp(s,b,pid)
    finally:stop(p,l)
    p2,b2,l2=start(d,18082,extra)
    try:
        st=req(s,b2,f"/infinity/studio/project/{quote(pid,safe='')}");assert st.get("project_id")==pid and st.get("status") in {"completed","completed_with_qc_warnings"},st;assert "final.mp4" in names(s,b2,pid);c2=req(s,b2,"/infinity/canonical/capabilities");assert c2.get("storage",{}).get("persistent") is True,c2;return {"test":8,"project_id":pid,"persistent":True}
    finally:stop(p2,l2)
def t9(s,b):
    cookie=s.cookies.get("ai_infinity_session");assert cookie
    def one(label):
        q=requests.Session();q.cookies.set("ai_infinity_session",cookie);return create(q,b,f"Create a 20-second cinematic video for CONCURRENCY-{label} with a distinct narrative.",duration=20,format="short",idempotency_key=f"final-acceptance-{label}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:pids=list(ex.map(one,("ALPHA","BETA")))
    assert len(set(pids))==2;p=[waitp(s,b,x) for x in pids];text=json.dumps(p,ensure_ascii=False);assert "CONCURRENCY-ALPHA" in text and "CONCURRENCY-BETA" in text;textitles={str(x.get("title")or"") for x in p};assert len(textitles)==2
    return {"test":9,"project_ids":pids,"isolated":True}
def t10(s,b,d):
    pid=create(s,b,"Create a 20-second English cinematic recovery test video.",duration=20,format="short",idempotency_key="final-acceptance-corruption");waitp(s,b,pid);scene=d/"creator_studio"/pid/"scene_01.mp4";assert scene.is_file() and scene.stat().st_size>10000
    original=sha(scene);scene.write_bytes(b"CORRUPTED-INTERMEDIATE");corrupted=sha(scene);assert corrupted!=original
    with sqlite3.connect(d/"ai_infinity.db") as c:c.execute("UPDATE studio_projects_3610 SET status='failed',stage='failed',error='acceptance fault injection' WHERE project_id=?",(pid,));c.commit()
    assert req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/retry","POST").get("status")=="queued";waitp(s,b,pid);recovered=sha(scene);assert recovered!=corrupted and scene.stat().st_size>10000
    ev=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/audit");assert "scene_completed" in [str(x.get("event")or"") for x in ev.get("events",[])];tr=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/truth");assert tr.get("verified") is True,tr
    return {"test":10,"project_id":pid,"recovered":True}
def main():
    d=TMP/"base";shutil.rmtree(d,ignore_errors=True);p,b,l=start(d,18080);s=requests.Session();out=[]
    try:
        out += [t1(s,b),t2(s,b),t3(s,b),t4(s,b),t5(),t6(),t7(),t8(),t9(s,b),t10(s,b,d)]
    finally:stop(p,l)
    print(json.dumps({"passed":True,"tests":out},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
