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
def waitp(s,b,pid,timeout=600,allow_review_draft=False,allow_review_required=False):
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
            if status == "completed_with_qc_warnings" and allow_review_draft:
                truth = req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/truth")
                checks = truth.get("checks") if isinstance(truth.get("checks"),dict) else {}
                # Only a technically eligible illustrative draft may use this mode.
                # It is still REVIEW_REQUIRED and final delivery/publishing stay blocked.
                assert truth.get("verified") is False,truth
                assert str(truth.get("state") or "").upper() == "REVIEW_REQUIRED",truth
                assert checks.get("source_visual_policy_passed") is False,truth
                assert int(checks.get("fallback_visual_count") or 0) > 0,truth
                assert checks.get("visual_rights_evidence_present") is True,truth
                assert checks.get("illustrative_fallback_delivery_eligible") is True,truth
                assert truth.get("publish_allowed_by_quality_gate") is False,truth
            elif status == "completed_with_qc_warnings" and allow_review_required:
                # Recovery tests may exercise a completed-but-unverified project whose
                # source rights or other gates remain unresolved. Continue testing the
                # recovery behavior only when the application is truthful and fail-closed.
                truth = req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/truth")
                assert truth.get("verified") is False,truth
                assert str(truth.get("state") or "").upper() == "REVIEW_REQUIRED",truth
                assert truth.get("publish_allowed_by_quality_gate") is False,truth
                final_url = b+f"/infinity/studio/project/{quote(pid,safe='')}/asset/final.mp4"
                blocked = s.get(final_url,timeout=60)
                assert blocked.status_code == 409,("unverified final.mp4 must remain gated",blocked.status_code,blocked.text[:500])
            else:
                assert status == "completed",last
            runtime_state = str(last.get("state") or "").upper()
            if status == "completed_with_qc_warnings" and (allow_review_draft or allow_review_required):
                # A review-required completion must retain the truthful degraded state;
                # the gates above independently verified that delivery/publishing remain blocked.
                # The lifecycle field can reflect completed rendering even when
                # the separate quality truth remains REVIEW_REQUIRED. Both outcomes
                # are allowed only after the checks above prove delivery is blocked.
                assert runtime_state in {"DEGRADED", "COMPLETED"}, last
            else:
                assert runtime_state == "COMPLETED", last
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
def integrated_lufs(p):
    x=subprocess.run(
        ["ffmpeg","-hide_banner","-nostats","-i",str(p),"-vn","-af","ebur128=framelog=verbose","-f","null","-"],
        capture_output=True,text=True,timeout=180,
    )
    values=re.findall(r"\bI:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*LUFS\b",(x.stderr or "")+(x.stdout or ""))
    assert x.returncode==0 and values, (x.returncode,x.stderr[-2000:])
    return float(values[-1])
def pdf(path):
    # A small but real, text-extractable source brief. The acceptance test should
    # prove that upload content drives the project topic, not just that a PDF exists.
    lines = [
        "Title: AI Infinity Creator Operating System",
        "Brief: AI Infinity connects research writing visual selection and editing.",
        "Source rights: Record creator item page license terms and file SHA-256.",
        "Delivery: Render narration captions video article audio and social copy.",
        "Quality: Verify duration aspect ratio media decode and audio.",
        "Recovery: Reload saved projects and confirm artifact checksums after restart.",
    ]
    def escape_pdf_string(value):
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    operations = ["BT /F1 12 Tf 72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            operations.append("T*")
        operations.append(f"({escape_pdf_string(line)}) Tj")
    stream = (" ".join(operations) + " ET").encode("ascii")
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
    reqd={"final.mp4","thumbnail.jpg","audio_master.mp3","narration_only.mp3","captions.srt","article.md","seo.json","social_campaign.json","production_manifest.json","rights_manifest.json","package.zip"}; got=names(s,b,pid); assert reqd<=got,(reqd-got,got)
    src=jart(s,b,pid,"sources.json"); assert int(src.get("source_count") or len(src.get("sources")or[]))>0,src
    # Truth comes before final-file download: an unverified project must fail at
    # the quality gate, not be reported as a misleading artifact-download error.
    truth=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/truth")
    if not (truth.get("verified") is True and truth.get("state")=="VERIFIED"):
        # A completed render can still fail independent professional truth.
        # Capture its actual MP4, FFprobe metadata, provenance, and reviewer
        # contact sheet before preserving the strict acceptance failure.
        try:
            diagnostic = capture_failed_project_evidence(
                s, b, pid,
                {"status": "completed", "reason": "professional truth gate did not verify"},
            )
            if diagnostic:
                print("NONPASSING_PROJECT_EVIDENCE=" + json.dumps(
                    diagnostic, ensure_ascii=False, default=str
                ))
        except Exception as exc:
            print("NONPASSING_PROJECT_EVIDENCE_CAPTURE_FAILED=" +
                  type(exc).__name__ + ": " + str(exc)[:1000])
    assert truth.get("verified") is True and truth.get("state")=="VERIFIED", truth
    checks=truth.get("checks") or {}
    assert checks.get("source_visual_policy_passed") is True, truth
    assert int(checks.get("fallback_visual_count") or 0) == 0, truth
    assert checks.get("aspect_ratio_passed") is True and checks.get("requested_aspect_ratio") == "16:9", truth
    assert int(checks.get("actual_rendered_scene_count") or 0) >= int(checks.get("minimum_professional_scene_count") or 1), truth
    assert checks.get("visual_rights_evidence_present") is True, truth
    files={n:dl(s,b,pid,n) for n in reqd}; m=probe(files["final.mp4"]); dur=float((m.get("format")or{}).get("duration")or 0); assert 59<=dur<=61,m
    assert any(x.get("codec_type")=="video" for x in m.get("streams",[])); assert any(x.get("codec_type")=="audio" for x in m.get("streams",[]))
    audio_probe=probe(files["audio_master.mp3"]); audio_dur=float((audio_probe.get("format")or{}).get("duration")or 0)
    assert any(x.get("codec_type")=="audio" for x in audio_probe.get("streams",[])),audio_probe
    assert abs(audio_dur-dur)<=1.0,("programme audio master must match the final video mix",audio_dur,dur)
    audio_lufs=integrated_lufs(files["audio_master.mp3"])
    assert -22.0<=audio_lufs<=-12.0,("programme audio master loudness outside delivery range",audio_lufs)
    voice_probe=probe(files["narration_only.mp3"])
    assert any(x.get("codec_type")=="audio" for x in voice_probe.get("streams",[])),voice_probe
    assert sha(files["audio_master.mp3"]) != sha(files["narration_only.mp3"]), "programme mix and isolated narration must remain distinct artifacts"
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
        required_package={"final.mp4","thumbnail.jpg","audio_master.mp3","narration_only.mp3","captions.srt","article.md","production_manifest.json","rights_manifest.json","provenance.json","seo.json","social_campaign.json"}
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
    src=jart(s,b,pid,"sources.json")
    uploaded = [x for x in src.get("sources",[]) if str(x.get("title"))==f.name and x.get("source")=="user upload"]
    assert uploaded and uploaded[0].get("text_extracted") is True,src
    assert "AI Infinity Creator Operating System" in str(uploaded[0].get("snippet") or uploaded[0].get("summary") or ""),uploaded[0]
    script=dl(s,b,pid,"script.md").read_text(encoding="utf-8")
    assert script.startswith("# AI Infinity Creator Operating System"),script[:1200]
    assert "AI Infinity" in script and "captions" in script.lower() and "editing" in script.lower(),script[:2000]
    return {"test":3,"project_id":pid,"pdf_executed":True,"upload_topic_used":"AI Infinity Creator Operating System"}
def t4(s,b):
    pid=create(s,b,"Create a 20-second research-backed video article package about Python's official website with evidence, captions, SEO and social copy.",duration=20,format="short",reference_urls=[PYURL]); waitp(s,b,pid,allow_review_draft=True)
    got=names(s,b,pid); assert {"final.mp4","thumbnail.jpg","captions.srt","article.md","seo.json","social_campaign.json","sources.json"}<=got,got
    src=jart(s,b,pid,"sources.json"); assert any(str(x.get("url")or"")==PYURL for x in src.get("sources",[])),src

    # Exercise the installed FastAPI routes against an actual completed project,
    # not just isolated serializer helpers. The portable ZIP must carry the media
    # and its source/rights records with verifiable byte hashes.
    export=req(s,b,f"/infinity/pro2/project/{quote(pid,safe='')}/timeline/otio/export","POST")
    assert export.get("status")=="exported" and export.get("truthful") is True,export
    assert int(export.get("scene_count") or 0)>=1,export
    assert export.get("human_review_status")=="PENDING" and export.get("requires_human_review") is True,export
    durability=export.get("durability") or {}
    assert {"timeline","bundle"} <= set(durability),export
    verified_statuses={"primary_only","durable_partial","durable_multi_provider","durable_5_provider"}
    for label,expected_sha,expected_size in (
        ("timeline",export["timeline"]["sha256"],None),
        ("bundle",export["bundle"]["sha256"],int(export["bundle"]["size_bytes"])),
    ):
        report=durability[label]
        assert report.get("truthful") is True,report
        status=str(report.get("status") or "")
        assert status in verified_statuses | {"unconfigured","failed","not_persisted","uploaded_unverified"},report
        if report.get("verified") is True:
            assert status in verified_statuses,report
            assert report.get("sha256")==expected_sha,report
            if expected_size is not None:
                assert int(report.get("size_bytes") or -1)==expected_size,report
            assert report.get("successful_providers"),report
        else:
            assert status not in verified_statuses,report

    timeline_response=s.get(b+export["timeline"]["download_url"],timeout=180)
    assert timeline_response.status_code==200 and timeline_response.content,(timeline_response.status_code,timeline_response.text[:1000])
    timeline_path=TMP/f"{pid}-{export['timeline']['name']}"
    timeline_path.write_bytes(timeline_response.content)
    assert sha(timeline_path)==export["timeline"]["sha256"],export

    bundle_response=s.get(b+export["bundle"]["download_url"],timeout=180)
    assert bundle_response.status_code==200 and bundle_response.content,(bundle_response.status_code,bundle_response.text[:1000])
    bundle_path=TMP/f"{pid}-{export['bundle']['name']}"
    bundle_path.write_bytes(bundle_response.content)
    assert sha(bundle_path)==export["bundle"]["sha256"],export
    with zipfile.ZipFile(bundle_path) as archive:
        bundle_names=set(archive.namelist())
        assert "project.otio" in bundle_names and "media/audio_master.mp3" in bundle_names,bundle_names
        assert any(name.startswith("media/scene_") and name.endswith(".mp4") for name in bundle_names),bundle_names
        manifest=json.loads(archive.read("metadata/timeline-export-manifest.json"))
        assert manifest.get("human_review_status")=="PENDING",manifest
        assert manifest.get("truth_state") in {"VERIFIED","REVIEW_REQUIRED"},manifest
        for item in manifest.get("files") or []:
            payload=archive.read(item["path"])
            assert len(payload)==item["size_bytes"],item
            assert hashlib.sha256(payload).hexdigest()==item["sha256"],item

    imported_response=s.post(
        b+f"/infinity/pro2/project/{quote(pid,safe='')}/timeline/otio/import",
        data=timeline_path.read_bytes(),
        headers={"Content-Type":"application/vnd.opentimelineio+json"},
        timeout=180,
    )
    assert imported_response.status_code==200,(imported_response.status_code,imported_response.text[:2000])
    imported=imported_response.json()
    assert imported.get("status")=="imported" and imported.get("truthful") is True,imported
    assert imported.get("render_required") is True and imported.get("truth_verified") is False,imported
    assert imported.get("human_review_status")=="PENDING",imported
    assert any(row.get("enabled") is True for row in (imported.get("timeline") or {}).get("scenes") or []),imported
    return {
        "test":4,"project_id":pid,"reference_url":PYURL,
        "otio_routes_passed":True,"otio_scene_count":export["scene_count"],
        "otio_bundle_bytes":bundle_path.stat().st_size,"otio_manifest_hashes_verified":True,
        "otio_import_requires_rerender_and_human_review":True,
    }
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
        pid=create(s,b,"Create a 20-second English cinematic video using only local/free execution.",duration=20,format="short");state=waitp(s,b,pid,allow_review_draft=True);bp=state.get("blueprint")or{};r=bp.get("research")or{};assert r.get("status")=="degraded",r
        # This environment deliberately disables external providers. A playable
        # local illustration draft is allowed, but it must stay REVIEW_REQUIRED
        # and the API must refuse to deliver it as professionally verified media.
        truth=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/truth")
        assert truth.get("verified") is False,truth
        assert str(truth.get("state") or "").upper()=="REVIEW_REQUIRED",truth
        assert truth.get("publish_allowed_by_quality_gate") is False,truth
        final_url=b+f"/infinity/studio/project/{quote(pid,safe='')}/asset/final.mp4"
        download=s.get(final_url,timeout=180)
        assert download.status_code==409,("unverified final.mp4 must remain gated",download.status_code,download.text[:500])
        final_path=d/"creator_studio"/pid/"final.mp4"
        assert final_path.is_file() and final_path.stat().st_size>10000,final_path
        m=probe(final_path)
        assert all(any(x.get("codec_type")==k for x in m.get("streams",[])) for k in ("video","audio")),m
        duration=float((m.get("format") or {}).get("duration") or 0)
        assert abs(duration-20.0)<=1.0,("unexpected draft duration",duration)
        decoded=subprocess.run(["ffmpeg","-v","error","-xerror","-i",str(final_path),"-f","null","-"],capture_output=True,text=True,timeout=180)
        assert decoded.returncode==0,decoded.stderr[-2000:]
        draft_sha256=sha(final_path)
        return {"test":7,"project_id":pid,"research_status":"degraded","truth_state":"REVIEW_REQUIRED","final_download_blocked":True,"draft_media_sha256":draft_sha256,"draft_bytes":final_path.stat().st_size,"decoded":True}
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
        waitp(s,b,pid,allow_review_required=True)
    finally:stop(p,l)
    p2,b2,l2=start(d,18082,extra)
    try:
        st=req(s,b2,f"/infinity/studio/project/{quote(pid,safe='')}")
        assert st.get("project_id")==pid and st.get("status") in {"completed","completed_with_qc_warnings"},st
        assert "final.mp4" in names(s,b2,pid)
        truth=req(s,b2,f"/infinity/studio/project/{quote(pid,safe='')}/truth")
        if truth.get("verified") is not True:
            assert str(truth.get("state") or "").upper() == "REVIEW_REQUIRED",truth
            assert truth.get("publish_allowed_by_quality_gate") is False,truth
            final_url=b2+f"/infinity/studio/project/{quote(pid,safe='')}/asset/final.mp4"
            blocked=s.get(final_url,timeout=60)
            assert blocked.status_code==409,("unverified final.mp4 must remain gated after restart",blocked.status_code,blocked.text[:500])
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
    labels=("ALPHA","BETA","GAMMA","DELTA")
    def one(label):
        q=requests.Session();q.cookies.set("ai_infinity_session",cookie)
        return create(q,b,f"Create a 20-second cinematic video for CONCURRENCY-{label} with a distinct narrative.",duration=20,format="short",idempotency_key=f"final-acceptance-{label}")
    started=time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        pids=list(ex.map(one,labels))
    assert len(set(pids))==4
    # Submit four real projects at once, then observe every independent project
    # through completion. Provider degradation is acceptable only if each
    # unverified final download remains quality-gated.
    p=[waitp(s,b,x,allow_review_required=True) for x in pids]
    elapsed=round(time.monotonic()-started,3)
    combined=json.dumps(p,ensure_ascii=False)
    for label in labels:
        assert "CONCURRENCY-"+label in combined, (label,combined[:4000])
    titles={str(x.get("title")or"") for x in p}
    assert len(titles)==4, f"concurrent briefs collapsed to duplicate titles: {titles!r}"
    return {
        "test":9,
        "project_ids":pids,
        "concurrency_level":4,
        "requested_duration_seconds":20,
        "end_to_end_elapsed_seconds":elapsed,
        "individual_titles":sorted(titles),
        "isolated":True,
        "measured_scope":"four concurrent 20-second project submissions through final status",
    }
def _assert_isolation_project_state(s,b,project_id,state):
    """Accept degraded drafts only while every production-delivery gate stays closed."""
    status=str(state.get("status") or "").lower()
    if status == "completed":
        return
    assert status == "completed_with_qc_warnings", state
    truth=req(s,b,f"/infinity/studio/project/{quote(project_id,safe='')}/truth")
    assert truth.get("verified") is False, truth
    assert str(truth.get("state") or "").upper() == "REVIEW_REQUIRED", truth
    assert truth.get("publish_allowed_by_quality_gate") is False, truth
    final_url=b+f"/infinity/studio/project/{quote(project_id,safe='')}/asset/final.mp4"
    blocked=s.get(final_url,timeout=60)
    assert blocked.status_code == 409, ("another project must remain gated during recovery",blocked.status_code,blocked.text[:500])


def t10(s,b,d,other_project_id):
    pid=create(
        s,b,
        "Create a 20-second English cinematic recovery test video.",
        duration=20,format="short",idempotency_key="final-acceptance-corruption",
        acceptance_fixture_id="cc0-recovery-illustration-v1",
    )
    waitp(s,b,pid,allow_review_required=True)
    scene=d/"creator_studio"/pid/"scene_01.mp4"
    assert scene.is_file() and scene.stat().st_size>10000,scene
    got=names(s,b,pid)
    fixture = d/"creator_studio"/pid/"ci_fixture_01.png"
    assert fixture.is_file() and fixture.stat().st_size > 1000, {"fixture":str(fixture),"registered_artifacts":sorted(got)}
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
    _assert_isolation_project_state(s,b,other_project_id,other_state_before)

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
    waitp(s,b,pid,allow_review_required=True)
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
    _assert_isolation_project_state(s,b,other_project_id,other_state_after)
    assert "final.mp4" in names(s,b,other_project_id)

    tr=req(s,b,f"/infinity/studio/project/{quote(pid,safe='')}/truth")
    checks=tr.get("checks") or {}
    fallback_count=int(checks.get("fallback_visual_count") or 0)
    if fallback_count:
        assert tr.get("verified") is False and tr.get("state")=="REVIEW_REQUIRED",tr
        assert checks.get("source_visual_policy_passed") is False,tr
        assert checks.get("qualified_illustrative_fallback") is True,tr
        # The recovery fixture intentionally carries a CC0 label but lacks the
        # application's complete original-work evidence contract. It must remain
        # blocked for rights review even though it is useful for retry fault injection.
        assert checks.get("visual_rights_evidence_present") is False,tr
        assert int(checks.get("visual_rights_unresolved_count") or 0) > 0,tr
        assert checks.get("illustrative_fallback_delivery_eligible") is False,tr
        failures=[str(x).lower() for x in (tr.get("failures") or [])]
        assert any("commercial-use/attribution evidence unresolved" in x for x in failures),tr
        publish_response=s.post(
            b+"/infinity/studio/publish",
            json={"project_id":pid,"provider":"youtube","asset":"final"},
            timeout=180,
        )
        assert publish_response.status_code in {200,201,202,409}, (
            "unverified recovery draft must be blocked from publishing",
            publish_response.status_code,
            publish_response.text[:1000],
        )
        try:
            publish=publish_response.json()
        except Exception:
            publish={}
        if publish_response.status_code == 409:
            # This route fails closed by raising HTTP 409 before it can issue a
            # remote upload ID. Accept the actual API contract; do not turn this
            # rejection into a successful publish state.
            detail=str(publish.get("detail") or "").lower()
            assert "professional verification must pass" in detail, publish
            assert not publish.get("url") and not publish.get("external_url"), publish
            publish_block_status="http_409_quality_gate"
        else:
            blocked_status = str(publish.get("status") or "")
            assert blocked_status in {"blocked_by_quality_gate", "blocked_by_editorial_review"}, publish
            assert not publish.get("url") and not publish.get("external_url"), publish
            if blocked_status == "blocked_by_quality_gate":
                assert publish.get("truthful") is True, publish
            else:
                assert publish.get("publish_ready") is False, publish
                assert (publish.get("editorial_review") or {}).get("approved") is False, publish
            publish_block_status=blocked_status
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
        "publish_block_status":(publish_block_status if fallback_count else None),
    }

def _production_profile_acceptance(label: str, duration: int, fast_mode: bool, port: int, fmt: str):
    """Render a real source-grounded profile and independently probe the final bytes."""
    data_dir = TMP / ("profile-" + label)
    shutil.rmtree(data_dir, ignore_errors=True)
    extra = {
        "AI_INFINITY_FAST_MODE": "1" if fast_mode else "0",
        "AI_INFINITY_REQUIRE_SOURCE_VISUALS": "1",
        "AI_INFINITY_REQUIRE_RESEARCH_EVIDENCE": "1",
        "AI_INFINITY_DISABLE_EXTERNAL_PROVIDERS": "0",
    }
    process, base, log = start(data_dir, port, extra)
    session = requests.Session()
    try:
        brief = (
            f"Create a professional {duration}-second AI Infinity product launch video in English, "
            "16:9 landscape, with a clear story, relevant real-source visuals, neural voiceover, "
            "readable synchronized captions, a complete package, source records and rights evidence."
        )
        project_id = create(
            session, base, brief,
            duration=duration, format=fmt, aspect_ratio="16:9", language="English",
        )
        state = waitp(session, base, project_id, timeout=1500)
        truth = req(
            session, base,
            f"/infinity/studio/project/{quote(project_id, safe='')}/truth",
        )
        checks = truth.get("checks") or {}
        assert truth.get("verified") is True and truth.get("state") == "VERIFIED", truth
        assert checks.get("source_visual_policy_passed") is True, truth
        assert int(checks.get("fallback_visual_count") or 0) == 0, truth
        assert checks.get("visual_rights_evidence_present") is True, truth
        assert checks.get("caption_sync_passed") is True, truth
        assert checks.get("script_sentence_integrity_passed") is True, truth
        assert checks.get("professional_scene_coverage_passed") is True, truth
        assert checks.get("research_required_and_present") is True, truth

        final_file = dl(session, base, project_id, "final.mp4", min_bytes=100_000)
        media = probe(final_file)
        streams = media.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
        actual_duration = float((media.get("format") or {}).get("duration") or 0)
        assert video.get("codec_name") == "h264", media
        assert audio.get("codec_name") == "aac", media
        assert abs(actual_duration - duration) <= 1.0, media
        if fast_mode:
            assert (int(video.get("width") or 0), int(video.get("height") or 0)) == (1280, 720), media
        else:
            assert (int(video.get("width") or 0), int(video.get("height") or 0)) == (1920, 1080), media
        decode = subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-i", str(final_file), "-f", "null", "-"],
            capture_output=True, text=True, timeout=600,
        )
        assert decode.returncode == 0, decode.stderr[-3000:]

        manifest = jart(session, base, project_id, "production_manifest.json")
        assert manifest.get("truthful") is True and manifest.get("project_id") == project_id, manifest
        entries = {str(item.get("name")): str(item.get("sha256")) for item in (manifest.get("artifacts") or [])}
        assert entries.get("final.mp4") == sha(final_file), entries
        return {
            "test": 11 if label == "1080p-20s" else 12,
            "profile": label,
            "project_id": project_id,
            "requested_duration_seconds": duration,
            "actual_duration_seconds": actual_duration,
            "resolution": [int(video.get("width") or 0), int(video.get("height") or 0)],
            "scene_count": checks.get("actual_rendered_scene_count"),
            "fallback_visual_count": checks.get("fallback_visual_count"),
            "truth_verified": True,
            "full_decode_passed": True,
            "manifest_hash_verified": True,
        }
    finally:
        stop(process, log)


def t11():
    # Exercise the non-fast production renderer on a real 20-second deliverable.
    return _production_profile_acceptance("1080p-20s", 20, False, 18083, "short")


def t12():
    # Exercise the long-form timeline against a measured three-minute deliverable.
    return _production_profile_acceptance("720p-180s", 180, True, 18084, "long")

def main():
    d=TMP/"base";shutil.rmtree(d,ignore_errors=True);p,b,l=start(d,18080,{"AI_INFINITY_TEST_MEDIA":"1"});s=requests.Session();out=[]
    try:
        out += [t1(s,b),t2(s,b),t3(s,b),t4(s,b),t5(),t6(),t7(),t8(),t9(s,b)]
        out.append(t10(s,b,d,out[-1]["project_ids"][0]))
    finally:
        stop(p,l)
    # Run actual profile acceptance after shutting down the shared ten-scenario app.
    out.append(t11())
    out.append(t12())
    print(json.dumps({"passed":True,"tests":out},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
