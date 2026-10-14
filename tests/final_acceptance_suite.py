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
    e=os.environ.copy(); e.update({"AI_INFINITY_DATA_DIR":str(d),"AI_INFINITY_3601_BACKGROUND":"false","AI_INFINITY_FAST_MODE":"1","AI_INFINITY_REQUIRE_SOURCE_VISUALS":"0","AI_INFINITY_REQUIRE_RESEARCH_EVIDENCE":"1","PYTHONUNBUFFERED":"1"}); e.update(extra or {})
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
            status = str(last.get("status") or "").lower()
            if status != "completed":
                try:
                    diagnostic = capture_failed_project_evidence(s, b, pid, last)
                    if diagnostic:
                        print("NONPASSING_PROJECT_EVIDENCE=" + json.dumps(diagnostic, ensure_ascii=False, default=str))
                except Exception as exc:
                    print("NONPASSING_PROJECT_EVIDENCE_CAPTURE_FAILED=" + type(exc).__name__ + ": " + str(exc)[:1000])
            assert status == "completed",last
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
def create_review_packet(project_id, files, truth, production, rights, media_probe):
    evidence_value=os.getenv("AI_INFINITY_EVIDENCE_DIR","").strip()
    if not evidence_value:
        return None
    from PIL import Image, ImageDraw, ImageFont
    evidence_dir=Path(evidence_value).resolve()
    evidence_dir.mkdir(parents=True,exist_ok=True)
    final=Path(files["final.mp4"])
    video=next((x for x in (media_probe.get("streams") or []) if x.get("codec_type")=="video"),{})
    audio=next((x for x in (media_probe.get("streams") or []) if x.get("codec_type")=="audio"),{})
    duration=float((media_probe.get("format") or {}).get("duration") or 0)
    width,height=int(video.get("width") or 0),int(video.get("height") or 0)
    frames=[]
    import tempfile
    with tempfile.TemporaryDirectory(prefix="aii-review-frames-") as work:
        for index,fraction in enumerate((0.06,0.18,0.30,0.42,0.54,0.66,0.78,0.90),1):
            stamp=duration*fraction
            frame_path=Path(work)/("frame-%02d.jpg"%index)
            subprocess.run(
                ["ffmpeg","-hide_banner","-loglevel","error","-y","-ss",f"{stamp:.3f}",
                 "-i",str(final),"-frames:v","1","-vf","scale=480:270",str(frame_path)],
                check=True,capture_output=True,text=True,timeout=120,
            )
            with Image.open(frame_path) as image:
                frames.append((image.convert("RGB").copy(),stamp))
    cols,cell_w,cell_h,pad=3,500,305,20
    rows=(len(frames)+cols-1)//cols
    sheet=Image.new("RGB",(cols*cell_w+(cols+1)*pad,rows*cell_h+(rows+1)*pad),(15,21,29))
    draw=ImageDraw.Draw(sheet)
    try:
        small=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",18)
    except Exception:
        small=ImageFont.load_default()
    for index,(frame,stamp) in enumerate(frames):
        col,row=index%cols,index//cols
        x,y=pad+col*(cell_w+pad),pad+row*(cell_h+pad)
        draw.text((x+5,y+1),f"Frame {index+1:02d} at {stamp:.1f}s",fill=(235,241,247),font=small)
        sheet.paste(frame,(x+10,y+27))
    contact=evidence_dir/(project_id+"-contact-sheet.png")
    sheet.save(contact,"PNG",optimize=True)
    src=json.loads(Path(files["sources.json"]).read_text(encoding="utf-8"))
    scorecard={
        "schema":"ai-infinity-human-editorial-scorecard.v1","project_id":project_id,
        "human_review_status":"PENDING","reviewers":[],
        "native_language_review":{"status":"PENDING","required_for_non_English_release":True},
        "scale":{"minimum":1,"maximum":5},
        "dimensions":[
            {"name":name,"critical":critical,"rating_1_to_5":None,"notes":""}
            for name,critical in [
                ("brief_adherence",True),("hook_and_story_arc",True),
                ("scene_relevance_and_variety",True),("pacing_and_transitions",False),
                ("voice_intelligibility_and_delivery",True),("audio_mix_and_loudness",True),
                ("caption_accuracy_and_synchronization",True),("visual_composition_and_brand",False),
                ("facts_and_source_support",True),("rights_and_attribution",True),
                ("language_and_cultural_fit",True),("editability_and_project_recovery",False),
            ]
        ],
        "internal_release_policy":{"average_minimum":4.0,"critical_dimension_minimum":3,"critical_fact_or_rights_failure_allowed":False},
        "policy_note":"Internal review policy; not an external industry certification.",
    }
    metadata={
        "project_id":project_id,"truth_state":truth.get("state"),"truth_verified":truth.get("verified"),
        "fallback_visual_count":(truth.get("checks") or {}).get("fallback_visual_count"),
        "requested":truth.get("requested"),"duration_seconds":round(duration,3),
        "resolution":[width,height],"aspect_ratio":round(width/height,5) if width and height else None,
        "video_codec":video.get("codec_name"),"audio_codec":audio.get("codec_name"),
        "media_sha256":sha(final),"media_size_bytes":final.stat().st_size,
        "source_count":int(src.get("source_count") or len(src.get("sources") or [])),
        "rights_asset_count":len(rights.get("assets") or []),"human_review_status":"PENDING",
    }
    readme=(
        "AI Infinity reviewer packet\n\n"
        "Contains downloaded deliverables, provenance/source and rights records, an eight-frame contact sheet, "
        "and a blank editorial scorecard. The scorecard is intentionally unscored: no human or native-language "
        "review is implied. Review the entire video and verify claims, sources, rights, pacing, audio, captions, "
        "language and editability before release. Any fallback visual, unresolved rights, critical factual issue, "
        "or failed truth gate blocks professional verification and publishing.\n"
    )
    packet=evidence_dir/(project_id+"-reviewer-packet.zip")
    with zipfile.ZipFile(packet,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for name,path in sorted(files.items()):
            path=Path(path)
            if not path.is_file() or name=="package.zip":
                continue
            compression=zipfile.ZIP_STORED if path.suffix.lower() in {".mp4",".mp3",".zip"} else zipfile.ZIP_DEFLATED
            z.write(path,"artifacts/"+name,compress_type=compression)
        z.write(contact,"review/contact-sheet.png")
        z.writestr("review/README.md",readme)
        z.writestr("review/production-truth.json",json.dumps(truth,ensure_ascii=False,indent=2,default=str))
        z.writestr("review/media-properties.json",json.dumps(metadata,ensure_ascii=False,indent=2,default=str))
        z.writestr("review/editorial-scorecard-template.json",json.dumps(scorecard,ensure_ascii=False,indent=2))
    return {"packet_name":packet.name,"packet_sha256":sha(packet),"packet_size_bytes":packet.stat().st_size,
            "contact_sheet_name":contact.name,"contact_sheet_sha256":sha(contact),
            "media_sha256":metadata["media_sha256"],"human_review_status":"PENDING"}



def capture_failed_project_evidence(s, b, project_id, status_payload):
    """Capture real media/provenance before a strict acceptance assertion stops the run."""
    configured = os.getenv("AI_INFINITY_EVIDENCE_DIR", "").strip()
    if not configured:
        return None
    evidence_dir = Path(configured).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    project_dir = TMP / "base" / "creator_studio" / str(project_id)
    final = project_dir / "final.mp4"
    if not final.is_file():
        matches = [p for p in TMP.rglob("final.mp4") if p.parent.name == str(project_id)]
        final = matches[0] if matches else final
    evidence = {
        "schema": "ai-infinity-failed-production-evidence.v1",
        "project_id": str(project_id),
        "project_status": status_payload.get("status"),
        "captured_at_epoch": time.time(),
        "final_media": {"path": str(final), "exists": final.is_file()},
        "truth_state": "NOT_CAPTURED",
        "truth_verified": False,
        "contact_sheet": "NOT_CREATED",
        "human_review_status": "PENDING",
    }
    if not final.is_file():
        evidence["capture_error"] = "The real local final.mp4 was not present in the test runtime."
        (evidence_dir / (str(project_id) + "-failure-evidence.json")).write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return evidence

    evidence["final_media"].update({"size_bytes": final.stat().st_size, "sha256": sha(final)})
    media_probe = probe(final)
    evidence["ffprobe"] = media_probe
    files = {"final.mp4": final}
    artifact_names = (
        "thumbnail.jpg", "audio_master.mp3", "captions.srt", "script.md", "sources.json",
        "manifest.json", "production_manifest.json", "rights_manifest.json", "provenance.json",
        "fact_check.json", "visual_rights.json", "article.md", "seo.json",
        "social_campaign.json", "accessibility.json",
    )
    for name in artifact_names:
        path = project_dir / name
        if path.is_file():
            files[name] = path
    for name in ("sources.json", "production_manifest.json", "rights_manifest.json"):
        if name not in files:
            try:
                files[name] = dl(s, b, str(project_id), name)
            except Exception as exc:
                evidence.setdefault("artifact_retrieval_errors", {})[name] = type(exc).__name__ + ": " + str(exc)[:500]

    truth = {}
    try:
        truth = req(s, b, f"/infinity/studio/project/{quote(str(project_id), safe='')}/truth")
        evidence["truth_state"] = truth.get("state")
        evidence["truth_verified"] = truth.get("verified") is True
        evidence["truth_checks"] = truth.get("checks") or {}
        evidence["truth_failures"] = truth.get("failures") or []
    except Exception as exc:
        evidence["truth_endpoint_error"] = type(exc).__name__ + ": " + str(exc)[:800]

    def load_artifact(name):
        path = files.get(name)
        if not path or not Path(path).is_file():
            return {}
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return {}

    production = load_artifact("production_manifest.json")
    rights = load_artifact("rights_manifest.json")
    if "sources.json" in files and truth and production and rights:
        try:
            packet = create_review_packet(str(project_id), files, truth, production, rights, media_probe)
            evidence["reviewer_packet"] = packet
            evidence["contact_sheet"] = packet.get("contact_sheet_name", "CREATED")
        except Exception as exc:
            evidence["packet_creation_error"] = type(exc).__name__ + ": " + str(exc)[:1000]

    (evidence_dir / (str(project_id) + "-failure-evidence.json")).write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return evidence


def t1(s,b):
    pid=create(s,b,"Create a professional 60-second AI Infinity launch video in English, 16:9, cinematic, research-backed, with voiceover, background music, captions, thumbnail, article, SEO metadata, social campaign package and production/rights manifests.",duration=60,format="long",aspect_ratio="16:9"); waitp(s,b,pid)
    reqd={"final.mp4","thumbnail.jpg","audio_master.mp3","captions.srt","article.md","seo.json","social_campaign.json","production_manifest.json","rights_manifest.json","package.zip"}; got=names(s,b,pid); assert reqd<=got,(reqd-got,got)
    src=jart(s,b,pid,"sources.json"); assert int(src.get("source_count") or len(src.get("sources")or[]))>0,src
    # Truth comes before final-file download: an unverified project must fail at
    # the quality gate, not be reported as a misleading artifact-download error.
    truth=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/truth")
    assert truth.get("verified") is True and truth.get("state")=="VERIFIED", truth
    checks=truth.get("checks") or {}
    assert checks.get("source_visual_policy_passed") is True, truth
    assert int(checks.get("fallback_visual_count") or 0) == 0, truth
    assert checks.get("aspect_ratio_passed") is True and checks.get("requested_aspect_ratio") == "16:9", truth
    assert int(checks.get("actual_rendered_scene_count") or 0) >= int(checks.get("minimum_professional_scene_count") or 1), truth
    assert checks.get("visual_rights_evidence_present") is True, truth
    files={n:dl(s,b,pid,n) for n in reqd}; m=probe(files["final.mp4"]); dur=float((m.get("format")or{}).get("duration")or 0); assert 59<=dur<=61,m
    assert any(x.get("codec_type")=="video" for x in m.get("streams",[])); assert any(x.get("codec_type")=="audio" for x in m.get("streams",[]))
    production=jart(s,b,pid,"production_manifest.json")
    assert production.get("project_id")==pid and production.get("truthful") is True
    manifest_artifacts=production.get("artifacts") or []
    assert manifest_artifacts, production
    artifact_hashes={str(x.get("name")):str(x.get("sha256")) for x in manifest_artifacts}
    assert artifact_hashes.get("final.mp4") == sha(files["final.mp4"]), artifact_hashes
    rights=jart(s,b,pid,"rights_manifest.json")
    assert rights.get("project_id")==pid and rights.get("truthful") is True
    assert rights.get("assets") is not None
    provenance=jart(s,b,pid,"provenance.json")
    assert provenance.get("sources") is not None
    for extra_name in ("script.md","sources.json","provenance.json","fact_check.json","manifest.json","visual_rights.json"):
        if extra_name not in files:
            files[extra_name]=dl(s,b,pid,extra_name)
    reviewer_packet=create_review_packet(pid,files,truth,production,rights,m)
    if os.getenv("AI_INFINITY_EVIDENCE_DIR","").strip():
        assert reviewer_packet and reviewer_packet.get("packet_size_bytes",0)>100000,reviewer_packet
    with zipfile.ZipFile(files["package.zip"]) as z:
        package_names=set(z.namelist())
        required_package={"final.mp4","thumbnail.jpg","audio_master.mp3","captions.srt","article.md","production_manifest.json","rights_manifest.json","provenance.json","seo.json","social_campaign.json"}
        assert required_package<=package_names,(required_package-package_names,sorted(package_names))
    return {
        "test":1,"project_id":pid,"duration":dur,"truth_verified":True,
        "manifest_hash_verified":True,"reviewer_packet":reviewer_packet,
    }
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
    up=s.post(b+"/infinity/studio/upload",files={"file":(f.name,f.read_bytes(),"application/pdf")},timeout=180)
    if not up.ok:
        print("UPLOAD_FAILURE",up.status_code,up.text[:3000])
        roots=[Path(os.environ.get("AI_INFINITY_DATA_DIR","/tmp")), Path("/tmp/ai-infinity-final-acceptance")]
        logs=[]
        for root in roots:
            if root.exists():
                logs.extend(root.rglob("server.log"))
        for log_path in logs[-5:]:
            print("UPLOAD_SERVER_LOG",str(log_path),log_path.read_text(errors="replace")[-16000:])
        raise AssertionError(up.text)
    assert up.json().get("text_extracted") is True,up.text
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
    # A process restart in the same ephemeral container is not a durable-volume proof.
    d=TMP/"restart-ephemeral";shutil.rmtree(d,ignore_errors=True)
    extra={
        "AI_INFINITY_PERSISTENCE_MODE":"durable",
        "AI_INFINITY_PERSISTENT_VOLUME_CONFIRMED":"true",
        "AI_INFINITY_PROJECT_STATE_DURABLE":"true",
        "AI_INFINITY_PROJECT_STATE_BACKEND":"sqlite_persistent_volume",
        "AI_INFINITY_REQUIRE_RESEARCH_EVIDENCE":"0",
    }
    p,b,l=start(d,18082,extra);s=requests.Session()
    try:
        c=req(s,b,"/infinity/canonical/capabilities")
        assert c.get("storage",{}).get("persistent") is False,c
        pid=create(s,b,"Create a 20-second English cinematic project for process-restart recovery.",duration=20,format="short")
        waitp(s,b,pid)
    finally:stop(p,l)
    p2,b2,l2=start(d,18082,extra)
    try:
        st=req(s,b2,f"/infinity/studio/project/{quote(pid,safe='')}")
        assert st.get("project_id")==pid and st.get("status") in {"completed","completed_with_qc_warnings"},st
        assert "final.mp4" in names(s,b2,pid)
        c2=req(s,b2,"/infinity/canonical/capabilities")
        assert c2.get("storage",{}).get("persistent") is False,c2
        return {
            "test":8,"project_id":pid,
            "process_restart_persisted":True,
            "persistent_storage_claimed":False,
            "confirmation_flags_rejected":True,
        }
    finally:stop(p2,l2)
def t9(s,b):
    cookie=s.cookies.get("ai_infinity_session");assert cookie
    def one(label):
        q=requests.Session();q.cookies.set("ai_infinity_session",cookie);return create(q,b,f"Create a 20-second cinematic video for CONCURRENCY-{label} with a distinct narrative.",duration=20,format="short",idempotency_key=f"final-acceptance-{label}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:pids=list(ex.map(one,("ALPHA","BETA")))
    assert len(set(pids))==2;p=[waitp(s,b,x) for x in pids];text=json.dumps(p,ensure_ascii=False);assert "CONCURRENCY-ALPHA" in text and "CONCURRENCY-BETA" in text;textitles={str(x.get("title")or"") for x in p};assert len(textitles)==2
    return {"test":9,"project_ids":pids,"isolated":True}
def t10(s,b,d,other_project_id):
    pid=create(
        s,b,
        "Create a 20-second English cinematic recovery test video.",
        duration=20,format="short",idempotency_key="final-acceptance-corruption",
        acceptance_fixture_id="cc0-recovery-illustration-v1",
    )
    waitp(s,b,pid)
    scene=d/"creator_studio"/pid/"scene_01.mp4"
    assert scene.is_file() and scene.stat().st_size>10000,scene
    got=names(s,b,pid)
    assert "ci_fixture_01.png" in got,got
    rights=jart(s,b,pid,"visual_rights.json")
    rights_assets=rights.get("assets") or []
    assert any(
        x.get("source")=="AI Infinity deterministic CC0 recovery-test illustration"
        and "CC0 1.0" in str(x.get("license") or "")
        and x.get("rights_state")=="blocked_fallback"
        for x in rights_assets
    ),rights

    # Snapshot an already completed project to prove retry cannot mutate another tenant/project.
    assert other_project_id and other_project_id != pid
    other_scene=d/"creator_studio"/other_project_id/"scene_01.mp4"
    assert other_scene.is_file() and other_scene.stat().st_size>10000,other_scene
    other_sha_before=sha(other_scene)
    other_state_before=req(s,b,f"/infinity/studio/project/{quote(other_project_id,safe='')}")
    assert str(other_state_before.get("status") or "").lower()=="completed",other_state_before

    scene.write_bytes(b"CORRUPTED-INTERMEDIATE")
    corrupted=sha(scene)
    with sqlite3.connect(d/"ai_infinity.db") as c:
        c.execute(
            "UPDATE studio_projects_3610 SET status='failed',stage='failed',error='acceptance fault injection' WHERE project_id=?",
            (pid,),
        )
        c.commit()
    retry=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/retry","POST")
    assert retry.get("status")=="queued",retry
    waitp(s,b,pid)
    recovered=sha(scene)
    assert recovered!=corrupted and scene.stat().st_size>10000,(corrupted,recovered)
    media=probe(scene)
    streams=media.get("streams") or []
    assert any(x.get("codec_type")=="video" for x in streams),media
    assert any(x.get("codec_type")=="audio" for x in streams),media
    seconds=float((media.get("format") or {}).get("duration") or 0)
    assert 1.0 <= seconds <= 15.0,media
    decode=subprocess.run(
        ["ffmpeg","-v","error","-i",str(scene),"-f","null","-"],
        capture_output=True,text=True,timeout=120,
    )
    assert decode.returncode==0,decode.stderr[-2000:]

    ev=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/audit")
    audit=ev.get("audit") or []
    retry_positions=[i for i,x in enumerate(audit) if str(x.get("event") or "")=="retry_requested"]
    assert retry_positions,audit
    retry_pos=retry_positions[-1]
    assert any(str(x.get("event") or "")=="scene_completed" for x in audit[retry_pos+1:]),audit
    assert sha(other_scene)==other_sha_before
    other_state_after=req(s,b,f"/infinity/studio/project/{quote(other_project_id,safe='')}")
    assert str(other_state_after.get("status") or "").lower()=="completed",other_state_after
    assert "final.mp4" in names(s,b,other_project_id)

    tr=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/truth")
    checks=tr.get("checks") or {}
    fallback_count=int(checks.get("fallback_visual_count") or 0)
    if fallback_count:
        assert tr.get("verified") is False and tr.get("state")=="REVIEW_REQUIRED",tr
        assert checks.get("source_visual_policy_passed") is False,tr
        assert checks.get("qualified_illustrative_fallback") is True,tr
        assert checks.get("illustrative_fallback_delivery_eligible") is True,tr
        assert not (tr.get("failures") or []),tr
        publish=req(
            s,b,"/infinity/studio/publish","POST",
            json={"project_id":pid,"provider":"youtube","asset":"final"},
        )
        blocked_status = str(publish.get("status") or "")
        assert blocked_status in {"blocked_by_quality_gate", "blocked_by_editorial_review"}, publish
        assert not publish.get("url") and not publish.get("external_url"), publish
        if blocked_status == "blocked_by_quality_gate":
            assert publish.get("truthful") is True, publish
        else:
            assert publish.get("publish_ready") is False, publish
            assert (publish.get("editorial_review") or {}).get("approved") is False, publish
    else:
        assert tr.get("verified") is True and tr.get("state")=="VERIFIED",tr
        assert checks.get("source_visual_policy_passed") is True,tr
        assert not (tr.get("failures") or []),tr
    return {
        "test":10,"project_id":pid,"recovered":True,
        "recovery_sha256":recovered,"decoded_scene_seconds":seconds,
        "retry_audit":True,"cross_project_isolation":True,
        "truth_state":tr.get("state"),"fallback_visual_count":fallback_count,
        "publish_blocked_when_fallback":bool(fallback_count),
        "publish_block_status":(publish.get("status") if fallback_count else None),
    }
def main():
    d=TMP/"base";shutil.rmtree(d,ignore_errors=True);p,b,l=start(d,18080,{"AI_INFINITY_TEST_MEDIA":"1"});s=requests.Session();out=[]
    try:
        out += [t1(s,b),t2(s,b),t3(s,b),t4(s,b),t5(),t6(),t7(),t8(),t9(s,b)]
        out.append(t10(s,b,d,out[-1]["project_ids"][0]))
    finally:stop(p,l)
    print(json.dumps({"passed":True,"tests":out},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
