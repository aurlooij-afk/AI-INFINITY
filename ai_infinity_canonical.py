from __future__ import annotations
"""Canonical AI Infinity creator contract.

One public creator path:
describe -> understand -> success contract -> capability preflight ->
real execution -> independent evidence -> version -> memory -> improve.

This layer orchestrates the existing real Creator Studio and Reality Kernel.
It does not fabricate unavailable generators or external publishing.
"""
import hashlib, json, os, re, shutil, time, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse

VERSION="AI-INFINITY-CANONICAL-1.0"
BUILD="DIVINE-CREATOR-CLOSED-LOOP"
SCHEMA="ai-infinity-canonical-v1"

def studio():
    import studio_ultimate
    return studio_ultimate

def reality():
    try:
        import reality_first_3901
        return reality_first_3901
    except Exception:
        return None

def db():
    return studio()._connect()

def now():
    return time.time()

def uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"

def clean(value, limit=12000):
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:limit]

def digest(value):
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode()
    return hashlib.sha256(raw).hexdigest()

def project_dir(project_id):
    return Path(studio()._project_dir(project_id)).resolve()

def current_user(request, response=None):
    s=studio()
    user_id=s._get_user_id(request)
    if response is not None:
        s._set_session(response,request,user_id)
    return user_id

def require_project(project_id,user_id):
    p=studio()._get_project(project_id)
    if not p or str(p.get("user_id"))!=str(user_id):
        raise HTTPException(404,"project not found")
    return p

def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+".tmp-"+uuid.uuid4().hex)
    tmp.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2),encoding="utf-8")
    tmp.replace(path)

def init_schema():
    with studio().DB_LOCK,db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS canonical_projects(
          project_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,intent_json TEXT NOT NULL,
          success_json TEXT NOT NULL,manifest_json TEXT NOT NULL,current_version_id TEXT,
          state TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_canonical_projects_user
          ON canonical_projects(user_id,updated_at DESC);
        CREATE TABLE IF NOT EXISTS canonical_versions(
          version_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,parent_version_id TEXT,
          engine_project_id TEXT,kind TEXT NOT NULL,label TEXT NOT NULL,state TEXT NOT NULL,
          operation_json TEXT NOT NULL,artifact_name TEXT,artifact_sha256 TEXT,
          artifact_size INTEGER,evidence_json TEXT NOT NULL,created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_canonical_versions_project
          ON canonical_versions(project_id,created_at ASC);
        CREATE TABLE IF NOT EXISTS canonical_memories(
          memory_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT,
          kind TEXT NOT NULL,text TEXT NOT NULL,value_json TEXT NOT NULL,
          scope TEXT NOT NULL,confidence REAL NOT NULL,source TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL,updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_canonical_memory_user
          ON canonical_memories(user_id,project_id,active,updated_at DESC);
        CREATE TABLE IF NOT EXISTS canonical_events(
          event_id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT,user_id TEXT,
          event TEXT NOT NULL,data_json TEXT NOT NULL,created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS canonical_profiles(
          user_id TEXT PRIMARY KEY,profile_json TEXT NOT NULL,
          created_at REAL NOT NULL,updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS canonical_idempotency(
          user_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,
          project_id TEXT NOT NULL,request_hash TEXT NOT NULL,created_at REAL NOT NULL,
          PRIMARY KEY(user_id,idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS canonical_assets(
          asset_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT,
          asset_name TEXT NOT NULL,path TEXT NOT NULL,kind TEXT NOT NULL,
          media_type TEXT,sha256 TEXT,metadata_json TEXT NOT NULL,
          created_at REAL NOT NULL,updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_canonical_assets_project
          ON canonical_assets(user_id,project_id,updated_at DESC);
        """)

init_schema()

def event(project_id,user_id,name,data=None):
    with studio().DB_LOCK,db() as c:
        c.execute(
            "INSERT INTO canonical_events(project_id,user_id,event,data_json,created_at) VALUES(?,?,?,?,?)",
            (project_id,user_id,name,json.dumps(data or {},ensure_ascii=False,sort_keys=True),now())
        )

def profile(user_id):
    with studio().DB_LOCK,db() as c:
        row=c.execute("SELECT profile_json FROM canonical_profiles WHERE user_id=?",(user_id,)).fetchone()
    if not row:
        return {
            "audience":"general audience","tone":"clear, intelligent, human",
            "visual_style":"premium editorial","brand_voice":"",
            "languages":["English"],"platforms":["youtube","instagram","tiktok"],
            "default_aspect_ratio":"16:9"
        }
    try:return json.loads(row[0] or "{}")
    except Exception:return {}

def save_profile(user_id,p):
    base=profile(user_id)
    for k,dflt,lim in [
        ("audience","general audience",600),("tone","clear, intelligent, human",700),
        ("visual_style","premium editorial",900),("brand_voice","",1600)
    ]:
        base[k]=clean(p.get(k),lim) or dflt
    base["languages"]=[clean(x,60) for x in (p.get("languages") or ["English"]) if clean(x,60)][:12]
    base["platforms"]=[clean(x,40).lower() for x in (p.get("platforms") or []) if clean(x,40)][:12]
    raw=clean(p.get("default_aspect_ratio"),10)
    base["default_aspect_ratio"]=raw if raw in {"16:9","9:16","1:1","4:5"} else "16:9"
    with studio().DB_LOCK,db() as c:
        ts=now()
        c.execute(
          "INSERT INTO canonical_profiles(user_id,profile_json,created_at,updated_at) VALUES(?,?,?,?) "
          "ON CONFLICT(user_id) DO UPDATE SET profile_json=excluded.profile_json,updated_at=excluded.updated_at",
          (user_id,json.dumps(base,ensure_ascii=False,sort_keys=True),ts,ts)
        )
    return base

def add_memory(user_id,text_value,kind="context",value=None,project_id=None,scope="project",confidence=1.0,source="user"):
    kind=kind if kind in {"preference","decision","style","constraint","lesson","context"} else "context"
    with studio().DB_LOCK,db() as c:
        mid=uid("mem")
        c.execute(
          "INSERT INTO canonical_memories(memory_id,user_id,project_id,kind,text,value_json,scope,confidence,source,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
          (mid,user_id,project_id,kind,clean(text_value,4000),
           json.dumps(value if value is not None else text_value,ensure_ascii=False,sort_keys=True,default=str),
           clean(scope,40) or "project",max(0,min(1,float(confidence))),
           clean(source,100) or "user",1,now(),now())
        )
    event(project_id,user_id,"memory_added",{"memory_id":mid,"kind":kind,"scope":scope})
    return {"memory_id":mid,"kind":kind,"text":clean(text_value,4000),"scope":scope,
            "confidence":max(0,min(1,float(confidence))),"source":source}

def list_memories(user_id,project_id=None,limit=100):
    limit=max(1,min(500,int(limit)))
    sql="SELECT * FROM canonical_memories WHERE user_id=? AND active=1";params=[user_id]
    if project_id:
        sql+=" AND (project_id=? OR project_id IS NULL)";params.append(project_id)
    sql+=" ORDER BY CASE WHEN project_id IS NOT NULL THEN 0 ELSE 1 END,updated_at DESC LIMIT ?";params.append(limit)
    with studio().DB_LOCK,db() as c:rows=[dict(r) for r in c.execute(sql,params).fetchall()]
    for r in rows:
        try:r["value"]=json.loads(r.pop("value_json") or "{}")
        except Exception:r["value"]={}
    return rows

def compile_intent(command,raw):
    text=clean(command)
    if len(text)<8:raise HTTPException(422,"Describe what you want to bring to life.")
    low=text.lower()
    ct=clean(raw.get("content_type"),30).lower()
    if ct not in {"video","podcast","article","social"}:ct="video"
    if ct=="video":
        if re.search(r"\b(podcast|audio show)\b",low):ct="podcast"
        elif re.search(r"\b(article|blog|essay)\b",low):ct="article"
        elif re.search(r"\b(social|linkedin post|instagram post|tweet|x post)\b",low):ct="social"
    fmt=clean(raw.get("format"),30).lower()
    if fmt not in {"long","short","shorts","reel","tiktok"}:fmt=""
    explicit_ratio=clean(raw.get("aspect_ratio"),10)
    if explicit_ratio not in {"16:9","9:16","1:1","4:5"}:explicit_ratio=""
    if explicit_ratio:ratio=explicit_ratio
    elif re.search(r"\b9\s*:?\s*16\b|\bvertical\b|\bportrait\b|\breel\b|\bshorts?\b|\btiktok\b",low):ratio="9:16"
    elif re.search(r"\b1\s*:?\s*1\b|\bsquare\b",low):ratio="1:1"
    elif re.search(r"\b4\s*:?\s*5\b",low):ratio="4:5"
    else:ratio="16:9"
    if not fmt:fmt="short" if ratio=="9:16" or re.search(r"\bshorts?|reel|tiktok\b",low) else "long"
    try:duration=int(raw.get("duration") or 0)
    except Exception:duration=0
    m=re.search(r"\b(\d{1,4})\s*(seconds?|secs?|s|minutes?|mins?|m)\b",low)
    if m:duration=int(m.group(1))*(60 if m.group(2).startswith("m") else 1)
    if duration<=0:duration=60 if fmt=="short" else 300
    duration=max(20,min(3600,duration))
    if duration>=120 and fmt in {"short","shorts","reel","tiktok"} and ratio!="9:16":fmt="long"
    platforms=raw.get("platforms") if isinstance(raw.get("platforms"),list) else []
    platforms=[clean(x,40).lower() for x in platforms if clean(x,40)][:12]
    for x in re.findall(r"\b(youtube|instagram|tiktok|linkedin|facebook|twitter|x)\b",low):
        x="x" if x=="twitter" else x
        if x not in platforms:platforms.append(x)
    language=clean(raw.get("language"),60) or "English"
    for x in ("English","Urdu","Hindi","Pashto","Dari","Arabic"):
        if re.search(rf"\b{re.escape(x)}\b",low,re.I):language=x;break
    title=clean(raw.get("title"),180)
    if not title:title=re.split(r"[.!?\n]",text,maxsplit=1)[0][:150] or "AI Infinity Creation"
    return {
      "schema":SCHEMA,"title":title,"objective":text,
      "audience":clean(raw.get("audience"),600) or "general audience",
      "tone":clean(raw.get("tone"),700) or "cinematic, intelligent, useful",
      "visual_style":clean(raw.get("visual_style"),900) or "premium editorial",
      "voice":clean(raw.get("voice"),160) or "en-US-AriaNeural",
      "brand_voice":clean(raw.get("brand_voice"),1600),
      "call_to_action":clean(raw.get("call_to_action"),600),
      "quality_preset":clean(raw.get("quality_preset"),30) or "balanced",
      "constraints":{"duration_seconds":duration,"aspect_ratio":ratio,"format":fmt,
                     "content_type":ct,"language":language,"platforms":platforms},
      "success":{
        "technical_qc_required":True,"duration_tolerance_seconds":1.0,"aspect_tolerance":0.02,
        "required_artifacts":(
          ["final.mp4","audio_master.mp3","captions.srt","thumbnail.jpg","script.md","manifest.json"]
          if ct in {"video","social"} else
          (["audio_master.mp3","podcast_rss.xml"] if ct=="podcast" else ["article.md","thumbnail.jpg"])
        ),
        "creative_evaluation":"best-effort when a semantic model is configured"
      },
      "reference_urls":[clean(x,1200) for x in (raw.get("reference_urls") or [])[:12] if clean(x,1200)],
      "source_file_names":[clean(x,260) for x in (raw.get("source_file_names") or [])[:12] if clean(x,260)],
      "notes":clean(raw.get("notes"),4000),"created_at":now()
    }

def capabilities(user_id):
    from ai_infinity.persistence_truth import project_state_durability
    root=Path(os.getenv("AI_INFINITY_DATA_DIR","/tmp/ai-infinity")).resolve()
    ffmpeg=bool(shutil.which("ffmpeg"));ffprobe=bool(shutil.which("ffprobe"))
    tts=bool(shutil.which("espeak-ng") or shutil.which("espeak"))
    try:
        root.mkdir(parents=True,exist_ok=True);probe=root/".canonical-probe";probe.write_text("ok");probe.unlink(missing_ok=True);writable=True
    except Exception:writable=False
    remote=os.getenv("AI_INFINITY_REMOTE_WORKER_URL","").strip()
    hf=bool(os.getenv("HF_TOKEN","").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN","").strip())
    youtube=False
    try:youtube=any(x.get("provider")=="youtube" and x.get("active") for x in studio()._connections(user_id))
    except Exception:pass
    return {
      "version":VERSION,"truthful":True,
      "local":{"ffmpeg":ffmpeg,"ffprobe":ffprobe,"offline_tts":tts,"media_core":ffmpeg and ffprobe},
      "remote":{"configured":bool(remote),"health_verified":False},
      "providers":{"huggingface_configured":hf,"youtube_connected":youtube},
      "storage":project_state_durability(root) | {"writable":writable},
      "execution_paths":["local"]+(["remote"] if remote else [])
    }

def preflight(intent,user_id):
    caps=capabilities(user_id);blockers=[];warnings=[]
    if intent["constraints"]["content_type"] in {"video","social","podcast"} and not caps["local"]["ffmpeg"]:
        blockers.append({"code":"FFMPEG_UNAVAILABLE","message":"FFmpeg is required for real media production."})
    if not caps["local"]["ffprobe"]:
        blockers.append({"code":"FFPROBE_UNAVAILABLE","message":"Independent media inspection is unavailable."})
    if intent["constraints"]["content_type"] in {"video","social","podcast"} and not caps["local"]["offline_tts"]:
        blockers.append({"code":"VOICE_ENGINE_UNAVAILABLE","message":"No local narration engine is available."})
    if not caps["storage"]["persistent"]:
        warnings.append({"code":"EPHEMERAL_STORAGE","message":"Runtime is under /tmp; configure durable storage for host replacement."})
    if not caps["providers"]["huggingface_configured"]:
        warnings.append({"code":"OPTIONAL_AI_PROVIDER_OFF","message":"Optional external AI generation is not configured; the local/free-first path remains executable."})
    return {"ready":not blockers,"execution_path":"local","blockers":blockers,"warnings":warnings,"capabilities":caps,"truthful":True}

def artifact_evidence(path):
    if not path.is_file():return {"exists":False,"valid":False,"error":"artifact_missing"}
    r=reality()
    try:
        if r:
            x=r.inspect_artifact(path,"video/mp4" if path.suffix.lower()==".mp4" else "")
            return {"exists":True,"valid":bool((x.get("inspection") or {}).get("ok")),
                    "sha256":x.get("sha256"),"size_bytes":x.get("size_bytes"),
                    "inspection":x.get("inspection") or {}}
        return {"exists":True,"valid":path.stat().st_size>0,
                "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"size_bytes":path.stat().st_size}
    except Exception as exc:return {"exists":True,"valid":False,"error":str(exc)[:500]}

def versions(project_id):
    with studio().DB_LOCK,db() as c:
        rows=[dict(r) for r in c.execute("SELECT * FROM canonical_versions WHERE project_id=? ORDER BY created_at ASC",(project_id,)).fetchall()]
    for r in rows:
        try:
            r["operation"] = json.loads(r.get("operation_json") or "{}")
        except Exception:
            r["operation"] = {}
        try:
            r["evidence"] = json.loads(r.get("evidence_json") or "{}")
        except Exception:
            r["evidence"] = {}
    return rows

def version_by_id(version_id):
    with studio().DB_LOCK,db() as c:
        r=c.execute("SELECT * FROM canonical_versions WHERE version_id=?",(version_id,)).fetchone()
    if not r:return None
    d=dict(r)
    try:
        d["operation"] = json.loads(d.get("operation_json") or "{}")
    except Exception:
        d["operation"] = {}
    try:
        d["evidence"] = json.loads(d.get("evidence_json") or "{}")
    except Exception:
        d["evidence"] = {}
    return d

def current_version(project_id):
    with studio().DB_LOCK,db() as c:r=c.execute("SELECT current_version_id FROM canonical_projects WHERE project_id=?",(project_id,)).fetchone()
    return version_by_id(str(r[0])) if r and r[0] else None

def reconcile(project_id,user_id):
    p=require_project(project_id,user_id);cur=current_version(project_id)
    r=reality()
    try:base_truth=r.truth_for_project(project_id) if r else {"verified":False,"state":"REALITY_KERNEL_UNAVAILABLE"}
    except Exception as exc:base_truth={"verified":False,"state":"RECONCILIATION_ERROR","error":str(exc)[:500]}
    if cur:
        name=Path(str(cur.get("artifact_name") or "final.mp4")).name
        path=project_dir(project_id)/("final.mp4" if cur.get("kind")=="base" else name)
        ev=artifact_evidence(path)
        if cur.get("state")=="PENDING":
            ev["reality_kernel_verified"]=bool(base_truth.get("verified"))
            if ev.get("valid") and ev["reality_kernel_verified"]:
                with studio().DB_LOCK,db() as c:
                    c.execute("UPDATE canonical_versions SET state='VERIFIED',artifact_name=?,artifact_sha256=?,artifact_size=?,evidence_json=?,updated_at=? WHERE version_id=?",
                              (path.name,ev.get("sha256"),ev.get("size_bytes"),json.dumps(ev,sort_keys=True),now(),cur["version_id"]))
                cur=version_by_id(cur["version_id"])
        elif cur:cur["evidence"]=ev
    current_evidence = (cur or {}).get("evidence") or (cur or {}).get("evidence_json") or {}
    state="VERIFIED" if cur and cur.get("state")=="VERIFIED" and current_evidence.get("valid") else (
      "QUEUED" if str(p.get("status")).lower()=="queued" else str(p.get("status") or "BLOCKED").upper())
    with studio().DB_LOCK,db() as c:c.execute("UPDATE canonical_projects SET state=?,updated_at=? WHERE project_id=?",(state,now(),project_id))
    return {"project_id":project_id,"canonical_state":state,"engine_status":p.get("status"),
            "done":state=="VERIFIED","current_version":cur or {},"base_reality":base_truth,
            "truth_rule":"filesystem + independent inspection + Reality Kernel evidence","truthful":True}

def interpret_edit(command):
    text=clean(command,4000);low=text.lower();op={"command":text,"changes":[]}
    m=re.search(r"\b(?:remove|trim|cut)\s+(?:the\s+)?(?:first|intro(?:duction)?)\s+(\d+(?:\.\d+)?)\s*(?:seconds?|s)\b",low)
    if m:op["trim_start"]=float(m.group(1));op["changes"].append({"type":"trim_start","seconds":float(m.group(1))})
    m=re.search(r"\b(?:remove|trim|cut)\s+(?:the\s+)?(?:last|end)\s+(\d+(?:\.\d+)?)\s*(?:seconds?|s)\b",low)
    if m:op["trim_end"]=float(m.group(1));op["changes"].append({"type":"trim_end","seconds":float(m.group(1))})
    if re.search(r"\b(faster|speed\s*up|faster pacing)\b",low):op["speed"]=1.15;op["changes"].append({"type":"speed","value":1.15})
    elif re.search(r"\b(slower|slow\s*down|slower pacing)\b",low):op["speed"]=.90;op["changes"].append({"type":"speed","value":.90})
    if re.search(r"\b(vertical|portrait|reel|tiktok|9\s*:?\s*16)\b",low):op["aspect_ratio"]="9:16";op["changes"].append({"type":"aspect_ratio","value":"9:16"})
    elif re.search(r"\b(square|1\s*:?\s*1)\b",low):op["aspect_ratio"]="1:1";op["changes"].append({"type":"aspect_ratio","value":"1:1"})
    elif re.search(r"\b4\s*:?\s*5\b",low):op["aspect_ratio"]="4:5";op["changes"].append({"type":"aspect_ratio","value":"4:5"})
    elif re.search(r"\b(landscape|wide|16\s*:?\s*9)\b",low):op["aspect_ratio"]="16:9";op["changes"].append({"type":"aspect_ratio","value":"16:9"})
    if re.search(r"\b(dramatic|cinematic|premium|polished)\b",low):op["grade"]="cinematic";op["changes"].append({"type":"grade","value":"cinematic"})
    if re.search(r"\b(brighter|brightness)\b",low):op["brightness"]=.06;op["changes"].append({"type":"brightness","value":.06})
    if re.search(r"\b(more\s+contrast|contrasty)\b",low):op["contrast"]=1.08;op["changes"].append({"type":"contrast","value":1.08})
    if re.search(r"\b(more\s+color|more\s+vivid|saturated)\b",low):op["saturation"]=1.10;op["changes"].append({"type":"saturation","value":1.10})
    if re.search(r"\b(mute|without\s+audio)\b",low):op["mute"]=True;op["changes"].append({"type":"mute"})
    if re.search(r"\b(burn|embed).*(captions|subtitles)\b",low):op["burn_captions"]=True;op["changes"].append({"type":"burn_captions"})
    if not op["changes"]:
        op["grade"]="cinematic";op["changes"].append({"type":"grade","value":"cinematic","interpretation":"bounded visual polish"})
    return op

def render_edit(project_id,source,op):
    s=studio();out=project_dir(project_id)/(f"version_{int(now())}_{uuid.uuid4().hex[:8]}.mp4")
    duration=float(s.probe_duration(source));start=max(0,float(op.get("trim_start") or 0));end=max(0,float(op.get("trim_end") or 0))
    if start+end>=max(1,duration):raise HTTPException(422,"edit removes the entire video")
    speed=min(1.5,max(.5,float(op.get("speed") or 1)));vf=[];ratio=op.get("aspect_ratio")
    if ratio in {"16:9","9:16","1:1","4:5"}:
        w,h={"16:9":(1280,720),"9:16":(720,1280),"1:1":(720,720),"4:5":(720,900)}[ratio]
        vf.append(f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}")
    if op.get("grade")=="cinematic":vf.append("eq=contrast=1.08:saturation=1.08:brightness=-0.01")
    if op.get("brightness") is not None:vf.append(f"eq=brightness={float(op['brightness']):.4f}")
    if op.get("contrast") is not None:vf.append(f"eq=contrast={float(op['contrast']):.4f}")
    if op.get("saturation") is not None:vf.append(f"eq=saturation={float(op['saturation']):.4f}")
    if op.get("burn_captions"):
        caps=project_dir(project_id)/"captions.srt"
        if caps.is_file():
            sub=str(caps).replace("\\","/").replace(":","\\:")
            vf.append(f"subtitles={sub}:force_style='FontName=DejaVu Sans,FontSize=18,Outline=2,Shadow=1,MarginV=45,Alignment=2'")
    af=[]
    if op.get("mute"):af.append("volume=0")
    if speed!=1:af.append(f"atempo={speed:.6f}")
    args=(["-ss",start] if start else [])+["-i",source,"-t",max(.1,(duration-start-end)/speed)]
    if vf:args+=["-vf",",".join(vf)]
    if af:args+=["-af",",".join(af)]
    args+=["-map","0:v:0","-map","0:a?","-c:v","libx264","-preset",os.getenv("AI_INFINITY_VIDEO_PRESET","veryfast"),
           "-crf","20","-pix_fmt","yuv420p","-c:a","aac","-b:a","160k","-movflags","+faststart",out]
    s.ffmpeg(*args,timeout=max(180,int(duration*8)))
    ev=artifact_evidence(out)
    if not ev.get("valid"):raise HTTPException(500,"edit artifact failed independent inspection; version was not committed")
    return out,ev

def apply_edit(project_id,user_id,command):
    p=require_project(project_id,user_id);truth=reconcile(project_id,user_id)
    cur=truth.get("current_version") or {}
    source=project_dir(project_id)/Path(str(cur.get("artifact_name") or "final.mp4")).name
    if not source.is_file():source=project_dir(project_id)/"final.mp4"
    if not source.is_file():raise HTTPException(409,"no verified current media artifact exists")
    op=interpret_edit(command);out,ev=render_edit(project_id,source,op);parent=str(cur.get("version_id") or "")
    vid=uid("ver");label=f"Version {len(versions(project_id))+1}"
    with studio().DB_LOCK,db() as c:
        c.execute(
          "INSERT INTO canonical_versions(version_id,project_id,parent_version_id,engine_project_id,kind,label,state,operation_json,artifact_name,artifact_sha256,artifact_size,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (vid,project_id,parent,project_id,"edit",label,"VERIFIED",json.dumps(op,sort_keys=True),out.name,
           ev.get("sha256"),ev.get("size_bytes"),json.dumps(ev,sort_keys=True),now(),now())
        )
        c.execute("UPDATE canonical_projects SET current_version_id=?,state='VERIFIED',updated_at=? WHERE project_id=?",(vid,now(),project_id))
    try:studio()._save_asset(project_id,"version",out,"video/mp4",{"parent_version_id":parent,"operation":op})
    except Exception:pass
    event(project_id,user_id,"version_committed",{"version_id":vid,"parent_version_id":parent,"operation":op})
    return {"status":"completed","project_id":project_id,"version_id":vid,"parent_version_id":parent,
            "label":label,
            "artifact":{"name":out.name,"download_url":f"/infinity/studio/project/{project_id}/asset/{out.name}",**ev},
            "operation":op,"truthful":True,
            "message":f"{label} committed from a real media edit."}

def undo(project_id,user_id):
    require_project(project_id,user_id);rows=versions(project_id);cur=current_version(project_id)
    if len(rows)<2 or not cur:raise HTTPException(409,"nothing to undo")
    target=None
    for row in reversed(rows):
        if row["version_id"]!=cur["version_id"] and row["state"]=="VERIFIED" and row.get("artifact_name"):
            target=row;break
    if not target:raise HTTPException(409,"no verified previous version")
    path=project_dir(project_id)/Path(target["artifact_name"]).name;ev=artifact_evidence(path)
    if not ev.get("valid"):raise HTTPException(409,"previous version artifact is no longer valid")
    rid=uid("ver")
    with studio().DB_LOCK,db() as c:
        c.execute(
          "INSERT INTO canonical_versions(version_id,project_id,parent_version_id,engine_project_id,kind,label,state,operation_json,artifact_name,artifact_sha256,artifact_size,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (rid,project_id,cur["version_id"],project_id,"rollback",f"Rollback -> {target['label']}","VERIFIED",
           json.dumps({"operation":"rollback","target_version_id":target["version_id"]},sort_keys=True),
           target["artifact_name"],ev.get("sha256"),ev.get("size_bytes"),json.dumps(ev,sort_keys=True),now(),now())
        )
        c.execute("UPDATE canonical_projects SET current_version_id=?,state='VERIFIED',updated_at=? WHERE project_id=?",(target["version_id"],now(),project_id))
    event(project_id,user_id,"version_rollback",{"target_version_id":target["version_id"]})
    return {"status":"rolled_back","project_id":project_id,"current_version_id":target["version_id"],"truthful":True}

def index_assets(project_id,user_id):
    root=project_dir(project_id)
    if not root.exists():return 0
    count=0
    with studio().DB_LOCK,db() as c:
        for path in root.rglob("*"):
            if not path.is_file():continue
            try:
                sha=studio().file_sha256(path) if hasattr(studio(),"file_sha256") else hashlib.sha256(path.read_bytes()).hexdigest()
                mt={".mp4":"video/mp4",".mp3":"audio/mpeg",".wav":"audio/wav",".m4a":"audio/mp4",".png":"image/png",
                    ".jpg":"image/jpeg",".jpeg":"image/jpeg",".json":"application/json",".srt":"application/x-subrip",
                    ".md":"text/markdown",".zip":"application/zip"}.get(path.suffix.lower(),"application/octet-stream")
                aid=uid("asset")
                c.execute(
                  "INSERT OR REPLACE INTO canonical_assets(asset_id,user_id,project_id,asset_name,path,kind,media_type,sha256,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (aid,user_id,project_id,path.name,"project-artifact",mt,sha,
                   json.dumps({"size_bytes":path.stat().st_size},sort_keys=True),now(),now())
                )
                count+=1
            except Exception:pass
    return count

OVERLAY=r"""<style id="aii-canonical">
#aiiCanon{position:fixed;right:18px;bottom:18px;width:min(420px,calc(100vw - 28px));z-index:9999;background:rgba(8,12,18,.97);border:1px solid #334155;border-radius:18px;box-shadow:0 25px 80px rgba(0,0,0,.45);overflow:hidden}
#aiiCanon .h{display:flex;justify-content:space-between;padding:11px 13px;border-bottom:1px solid #24303d}#aiiCanon .h b{font-size:13px}#aiiCanon .h span{font-size:10px;color:#9fb0c2}
#aiiCanon textarea{width:100%;min-height:76px;box-sizing:border-box;resize:vertical;border:0;outline:0;background:#060a0f;color:#f5f7fa;padding:12px;font:13px/1.45 inherit}
#aiiCanon .f{display:flex;gap:7px;align-items:center;padding:8px;border-top:1px solid #24303d}#aiiCanon button{border:1px solid #334155;background:#101823;color:#eef4f9;border-radius:10px;padding:8px 10px;cursor:pointer}
#aiiCanon .go{background:#dff7b1;color:#09100b;border-color:#dff7b1;font-weight:800}.aiiTruth{font-size:9px;color:#9de2bf;margin-right:auto}.aiiOut{padding:10px 13px;border-top:1px solid #24303d;color:#aebdcb;font-size:10px;max-height:160px;overflow:auto}
@media(max-width:760px){#aiiCanon{right:9px;bottom:75px}}
</style>
<div id="aiiCanon"><div class="h"><b>∞ AI Infinity Command</b><span>reality-linked</span></div>
<textarea id="aiiCanonCmd" placeholder="Create something new, or improve the current project."></textarea>
<div class="f"><span class="aiiTruth">No claim without evidence.</span><button onclick="document.getElementById('aiiCanon').style.display='none'">Hide</button><button class="go" onclick="aiiCanonRun()">Create / Improve ↗</button></div>
<div class="aiiOut" id="aiiCanonOut">Describe an intention or an improvement. Connected versions stay in the same project.</div></div>
<script>
(function(){
var pid=localStorage.getItem("aii_canonical_project")||"";
function esc(s){return String(s||"").replace(/[&<>"']/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}})}
async function api(u,o){var r=await fetch(u,Object.assign({credentials:"same-origin"},o||{})),t=await r.text(),j={};try{j=JSON.parse(t)}catch(e){}if(!r.ok)throw Error(j.detail?.message||j.detail||t||("HTTP "+r.status));return j}
window.aiiCanonRun=async function(){
var c=(document.getElementById("aiiCanonCmd").value||"").trim(),out=document.getElementById("aiiCanonOut");
if(!c){out.textContent="Describe what you want to create or change.";return}
out.textContent="Capability check…";
try{
var r;
if(pid){r=await api("/infinity/canonical/project/"+encodeURIComponent(pid)+"/command",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({command:c})})}
else{r=await api("/infinity/canonical/create",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({command:c})});if(r.project_id){pid=r.project_id;localStorage.setItem("aii_canonical_project",pid)}}
out.innerHTML="<span>"+esc(r.message||r.status||"accepted")+"</span><br><br><span>"+esc(JSON.stringify({project_id:r.project_id,version_id:r.version_id,state:r.canonical_state||r.state},null,2))+"</span>";
}catch(e){out.textContent=e.message}
}
})();
</script>"""

def canonical_html():
    ui=str(getattr(studio(),"CREATOR_STUDIO_2030_UI",""))
    match=re.search(r"</body\\s*>",ui,re.I)
    if match:
        return ui[:match.start()]+OVERLAY+ui[match.start():]
    return ui+OVERLAY

def create_from_command(command,user_id,raw):
    intent=compile_intent(command,raw);prof=profile(user_id)
    for k in ("audience","tone","visual_style","brand_voice"):
        if not raw.get(k) and prof.get(k):intent[k]=prof[k]
    pf=preflight(intent,user_id)
    if not pf["ready"]:raise HTTPException(409,{"message":"Creation preflight failed","preflight":pf})
    idem=clean(raw.get("idempotency_key"),160);stable_intent={k:v for k,v in intent.items() if k!="created_at"};req_hash=digest({"user":user_id,"intent":stable_intent})
    if idem:
        with studio().DB_LOCK,db() as c:row=c.execute("SELECT project_id,request_hash FROM canonical_idempotency WHERE user_id=? AND idempotency_key=?",(user_id,idem)).fetchone()
        if row:
            if row[1]!=req_hash:raise HTTPException(409,"idempotency key is already bound to a different request")
            return {"status":"existing","project_id":row[0],"truthful":True}
    c=intent["constraints"]
    req={"title":intent["title"],"objective":intent["objective"],"topic":intent["objective"],"format":c["format"],
         "duration":c["duration_seconds"],"content_type":c["content_type"],"audience":intent["audience"],
         "tone":intent["tone"],"voice":intent["voice"],"language":c["language"],"platforms":c["platforms"],
         "brand_voice":intent["brand_voice"],"visual_style":intent["visual_style"],"call_to_action":intent["call_to_action"],
         "quality_preset":intent["quality_preset"],"aspect_ratio":c["aspect_ratio"],"reference_urls":intent["reference_urls"],
         "source_file_names":intent["source_file_names"],"notes":intent["notes"],"idempotency_key":idem}
    main=__import__("main")
    result=studio().enqueue(req,user_id,getattr(main,"_2700_model",None));pid=str(result.get("project_id") or "")
    if not pid:raise HTTPException(502,"creator engine did not return a project id")
    manifest={"schema":SCHEMA,"project_id":pid,"intent":intent,"intent_sha256":digest(intent),"request_sha256":req_hash,
              "preflight":pf,"execution_path":pf["execution_path"],"created_at":now(),
              "truth_rule":"no completion claim without artifact evidence"}
    write_json(project_dir(pid)/"canonical_contract.json",manifest)
    with studio().DB_LOCK,db() as c:
        c.execute(
          "INSERT INTO canonical_projects(project_id,user_id,intent_json,success_json,manifest_json,current_version_id,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
          (pid,user_id,json.dumps(intent,ensure_ascii=False,sort_keys=True),json.dumps(intent["success"],sort_keys=True),
           json.dumps(manifest,sort_keys=True),None,"QUEUED",now(),now())
        )
        if idem:c.execute("INSERT OR IGNORE INTO canonical_idempotency(user_id,idempotency_key,project_id,request_hash,created_at) VALUES(?,?,?,?,?)",(user_id,idem,pid,req_hash,now()))
        vid=uid("ver")
        c.execute(
          "INSERT INTO canonical_versions(version_id,project_id,parent_version_id,engine_project_id,kind,label,state,operation_json,artifact_name,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
          (vid,pid,None,pid,"base","Version 1","PENDING",json.dumps({"operation":"initial_creation","intent_sha256":digest(intent)},sort_keys=True),"final.mp4","{}",now(),now())
        )
        c.execute("UPDATE canonical_projects SET current_version_id=? WHERE project_id=?",(vid,pid))
    add_memory(user_id,"Current project objective","context",intent["objective"],pid,"project",1.0,"user_command")
    event(pid,user_id,"project_created",{"version_id":vid,"intent_sha256":digest(intent),"preflight":pf})
    return {**result,"project_id":pid,"intent":intent,"preflight":pf,"version":{"version_id":vid,"label":"Version 1","state":"PENDING"},"canonical":VERSION,"truthful":True}

def register(app):
    init_schema()

    @app.get("/health")
    def public_health(request:Request,response:Response):
        uid_=current_user(request,response);caps=capabilities(uid_)
        return {"status":"healthy" if caps["local"]["media_core"] else "degraded","version":VERSION,"build":BUILD,
                "deployment_revision":os.getenv("RENDER_GIT_COMMIT",""),"instance_id":os.getenv("RENDER_INSTANCE_ID",""),
                "canonical":True,"truthful":True,"capabilities":caps}

    @app.get("/infinity/canonical/health")
    def health(request:Request,response:Response):
        uid_=current_user(request,response);caps=capabilities(uid_)
        return {"status":"healthy" if caps["local"]["media_core"] else "degraded","version":VERSION,"build":BUILD,
                "deployment_revision":os.getenv("RENDER_GIT_COMMIT",""),"instance_id":os.getenv("RENDER_INSTANCE_ID",""),"schema":SCHEMA,
                "rules":{"no_ui_truth":True,"evidence_required_for_done":True,"transactional_versions":True,
                         "typed_memory":True,"bounded_recovery":True},"capabilities":caps,"truthful":True}

    @app.get("/infinity/canonical/bootstrap")
    def bootstrap(request:Request,response:Response):
        uid_=current_user(request,response)
        with studio().DB_LOCK,db() as c:
            ps=[dict(r) for r in c.execute("SELECT project_id,state,current_version_id,created_at,updated_at FROM canonical_projects WHERE user_id=? ORDER BY updated_at DESC LIMIT 100",(uid_,)).fetchall()]
        return {"version":VERSION,"projects":ps,"profile":profile(uid_),"capabilities":capabilities(uid_),"truthful":True}

    @app.post("/infinity/canonical/preflight")
    async def canonical_preflight(request:Request,response:Response):
        uid_=current_user(request,response);payload=await request.json()
        return preflight(compile_intent(clean(payload.get("command")),payload),uid_)

    @app.post("/infinity/canonical/create")
    async def canonical_create(request:Request,response:Response):
        uid_=current_user(request,response);payload=await request.json();cmd=clean(payload.get("command") or payload.get("objective") or payload.get("title"))
        return create_from_command(cmd,uid_,payload)

    @app.get("/infinity/canonical/capabilities")
    def canonical_capabilities(request:Request,response:Response):
        uid_=current_user(request,response);return capabilities(uid_)

    @app.get("/infinity/canonical/project/{project_id}/truth")
    def canonical_truth(project_id:str,request:Request,response:Response):
        uid_=current_user(request,response);return reconcile(project_id,uid_)

    @app.get("/infinity/canonical/project/{project_id}/versions")
    def canonical_versions(project_id:str,request:Request,response:Response):
        uid_=current_user(request,response);require_project(project_id,uid_)
        return {"project_id":project_id,"current_version":current_version(project_id) or {},"versions":versions(project_id),"truthful":True}

    @app.post("/infinity/canonical/project/{project_id}/command")
    async def canonical_command(project_id:str,request:Request,response:Response):
        uid_=current_user(request,response);require_project(project_id,uid_);payload=await request.json()
        cmd=clean(payload.get("command") or payload.get("instruction"),4000)
        if len(cmd)<3:raise HTTPException(422,"edit instruction is required")
        if re.search(r"\b(delete|destroy)\s+(?:the\s+)?project\b",cmd,re.I):
            raise HTTPException(409,"destructive project deletion is not part of the conversational edit contract")
        return apply_edit(project_id,uid_,cmd)

    @app.post("/infinity/canonical/project/{project_id}/undo")
    def canonical_undo(project_id:str,request:Request,response:Response):
        uid_=current_user(request,response);return undo(project_id,uid_)

    @app.get("/infinity/canonical/project/{project_id}/memory")
    def canonical_project_memory(project_id:str,request:Request,response:Response):
        uid_=current_user(request,response);require_project(project_id,uid_)
        return {"project_id":project_id,"memories":list_memories(uid_,project_id),"truthful":True}

    @app.post("/infinity/canonical/memory")
    async def canonical_memory(request:Request,response:Response):
        uid_=current_user(request,response);p=await request.json();txt=clean(p.get("text") or p.get("memory"),4000)
        if not txt:raise HTTPException(422,"memory text is required")
        return add_memory(uid_,txt,clean(p.get("kind"),40) or "context",p.get("value"),p.get("project_id"),
                          clean(p.get("scope"),40) or "project",float(p.get("confidence") or 1.0),"user")

    @app.get("/infinity/canonical/profile")
    def canonical_profile(request:Request,response:Response):
        uid_=current_user(request,response);return {"profile":profile(uid_),"truthful":True}

    @app.put("/infinity/canonical/profile")
    async def canonical_profile_put(request:Request,response:Response):
        uid_=current_user(request,response);return {"profile":save_profile(uid_,await request.json()),"truthful":True}

    @app.get("/infinity/canonical/assets/search")
    def canonical_asset_search(q:str="",request:Request=None):
        if request is None:raise HTTPException(400,"request missing")
        uid_=studio()._get_user_id(request);term=clean(q,200).lower()
        with studio().DB_LOCK,db() as c:
            rows=[dict(r) for r in c.execute("SELECT asset_id,project_id,asset_name,kind,media_type,sha256,metadata_json FROM canonical_assets WHERE user_id=? ORDER BY updated_at DESC LIMIT 500",(uid_,)).fetchall()]
        out=[]
        for r in rows:
            blob=(r["asset_name"]+" "+r["kind"]+" "+(r.get("metadata_json") or "")).lower()
            if not term or term in blob:
                try:r["metadata"]=json.loads(r.pop("metadata_json") or "{}")
                except Exception:r["metadata"]={}
                out.append(r)
        return {"query":term,"assets":out[:100],"truthful":True}

    @app.get("/infinity/canonical/project/{project_id}/assets")
    def canonical_project_assets(project_id:str,request:Request,response:Response):
        uid_=current_user(request,response);require_project(project_id,uid_);index_assets(project_id,uid_)
        with studio().DB_LOCK,db() as c:
            rows=[dict(r) for r in c.execute("SELECT asset_id,asset_name,kind,media_type,sha256,metadata_json,created_at,updated_at FROM canonical_assets WHERE user_id=? AND project_id=? ORDER BY updated_at DESC",(uid_,project_id)).fetchall()]
        for r in rows:
            try:r["metadata"]=json.loads(r.pop("metadata_json") or "{}")
            except Exception:r["metadata"]={}
        return {"project_id":project_id,"assets":rows,"truthful":True}

    @app.get("/infinity/canonical/project/{project_id}/events")
    def canonical_events(project_id:str,request:Request,response:Response):
        uid_=current_user(request,response);require_project(project_id,uid_)
        with studio().DB_LOCK,db() as c:
            rows=[dict(r) for r in c.execute("SELECT event_id,event,data_json,created_at FROM canonical_events WHERE project_id=? ORDER BY event_id ASC",(project_id,)).fetchall()]
        for r in rows:
            try:r["data"]=json.loads(r.pop("data_json") or "{}")
            except Exception:r["data"]={}
        return {"project_id":project_id,"events":rows,"truthful":True}

    @app.head("/")
    @app.head("/home")
    @app.get("/",response_class=HTMLResponse)
    @app.get("/home",response_class=HTMLResponse)
    def canonical_root(request:Request,response:Response):
        current_user(request,response)
        return HTMLResponse(canonical_html(),headers={"Cache-Control":"no-store"})

    @app.get("/infinity/canonical")
    def canonical_info():
        return {"version":VERSION,"build":BUILD,"schema":SCHEMA,"truthful":True}