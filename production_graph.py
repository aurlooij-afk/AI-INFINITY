from __future__ import annotations
import base64,hashlib,json,mimetypes,os,re,shutil,sqlite3,subprocess,threading,time,uuid
from pathlib import Path
from typing import Any,Dict
from urllib.parse import urlparse
import requests
try: import redis
except Exception: redis=None
from fastapi import HTTPException,Request
from fastapi.responses import FileResponse,HTMLResponse

VERSION="TARGET-2050.4000"; BUILD="PROFESSIONAL-PRODUCTION-GRAPH"; LOCK=threading.RLock(); PATCHED=False; WORKER_STARTED=False
WAN_PROVIDER_ID="wan2.2-ti2v-5b"
WAN_SOURCE_REVISION="1ea34ff48f87168174e12956e200b1d908b1c5ff"
WAN_CAP_LOCK=threading.RLock()
WAN_CAP_CACHE={"at":0.0,"value":None}
WAN_CAP_TTL_SECONDS=15.0

def s(): import studio_ultimate; return studio_ultimate
def dbpath(): return Path(os.getenv("AI_INFINITY_DB_PATH",str(getattr(s(),"DB_PATH","/tmp/ai-infinity/ai_infinity.db")))).resolve()
def db():
    # The graph shares Creator Studio's SQLite database and must share its
    # configured connection factory too. In production this is a
    # DurableConnection when the project-state snapshot adapter is active;
    # using a raw sqlite3 connection here bypasses the post-commit snapshot
    # scheduler and can strand graph/jobs/candidates outside the recovery point.
    studio = s()
    target_path = dbpath()
    canonical_path = Path(getattr(studio, "DB_PATH", target_path)).resolve()
    connect = getattr(studio, "_connect", None)
    if callable(connect) and canonical_path == target_path:
        connection = connect()
        connection.row_factory = sqlite3.Row
        return connection

    # Some isolated adapter tests deliberately redirect only the graph's DB
    # path. Do not let those test databases leak into Creator Studio's shared
    # global DB. The real app's DB_PATH and dbpath() both derive from the same
    # AI_INFINITY_DB_PATH / DATA_DIR configuration, so production takes the
    # durable connection path above.
    connection = sqlite3.connect(target_path, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection
def now(): return time.time()
def clean(x,n=4000): return str(x or "").replace("\x00"," ").strip()[:n]
def jd(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
def sha(x): return hashlib.sha256(jd(x).encode()).hexdigest()
def uid(r):
    try:return s()._get_user_id(r)
    except Exception:return "anonymous"
def ensure():
    with LOCK,db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS pg_projects(project_id TEXT PRIMARY KEY,user_id TEXT,brief_sha TEXT,status TEXT,stage TEXT,progress REAL DEFAULT 0,updated_at REAL);
        CREATE TABLE IF NOT EXISTS pg_shots(shot_id TEXT PRIMARY KEY,project_id TEXT,seq INTEGER,purpose TEXT,duration REAL,narration TEXT,visual_prompt TEXT,status TEXT DEFAULT 'planned',winner_candidate_id TEXT,critic_score REAL,revision_count INTEGER DEFAULT 0,metadata TEXT DEFAULT '{}',updated_at REAL);
        CREATE INDEX IF NOT EXISTS pg_shots_i ON pg_shots(project_id,seq);
        CREATE TABLE IF NOT EXISTS pg_candidates(candidate_id TEXT PRIMARY KEY,shot_id TEXT,project_id TEXT,provider TEXT,model TEXT,status TEXT,asset_path TEXT,source_url TEXT,score REAL,critic_json TEXT DEFAULT '{}',error TEXT,created_at REAL,updated_at REAL,storage_status TEXT DEFAULT 'not_persisted',storage_json TEXT DEFAULT '{}');
        CREATE INDEX IF NOT EXISTS pg_candidates_i ON pg_candidates(shot_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS pg_reviews(review_id TEXT PRIMARY KEY,project_id TEXT,shot_id TEXT,candidate_id TEXT,reviewer TEXT,score REAL,decision TEXT,report_json TEXT,created_at REAL);
        CREATE TABLE IF NOT EXISTS pg_jobs(job_id TEXT PRIMARY KEY,user_id TEXT,project_id TEXT,shot_id TEXT,kind TEXT,provider TEXT,status TEXT,payload_json TEXT,result_json TEXT,error TEXT,created_at REAL,updated_at REAL);
        CREATE INDEX IF NOT EXISTS pg_jobs_i ON pg_jobs(status,created_at);
        """)
        columns = {row["name"] for row in c.execute("PRAGMA table_info(pg_candidates)").fetchall()}
        if "storage_status" not in columns:
            c.execute("ALTER TABLE pg_candidates ADD COLUMN storage_status TEXT DEFAULT 'not_persisted'")
        if "storage_json" not in columns:
            c.execute("ALTER TABLE pg_candidates ADD COLUMN storage_json TEXT DEFAULT '{}'")
        job_columns = {row["name"] for row in c.execute("PRAGMA table_info(pg_jobs)").fetchall()}
        if "provider_job_id" not in job_columns:
            c.execute("ALTER TABLE pg_jobs ADD COLUMN provider_job_id TEXT")
        if "provider_meta_json" not in job_columns:
            c.execute("ALTER TABLE pg_jobs ADD COLUMN provider_meta_json TEXT DEFAULT '{}'")
def redis_client():
    u=os.getenv("REDIS_URL","").strip()
    if not u or redis is None:return None
    try:r=redis.Redis.from_url(u,decode_responses=True,socket_timeout=3,socket_connect_timeout=3); r.ping(); return r
    except Exception:return None
def owned(pid,r):
    p=s()._get_project(pid)
    if not p or p.get("user_id")!=uid(r):raise HTTPException(404,"project not found")
    return p
def plan(p):
    raw=p.get("blueprint_json") or "{}"
    try:x=raw if isinstance(raw,dict) else json.loads(raw)
    except Exception:x={}
    for k in ("plan","blueprint","result"):
        if isinstance(x.get(k),dict) and (x[k].get("chapters") or x[k].get("scenes")):return x[k]
    return x if isinstance(x,dict) else {}
def sync(pid):
    ensure(); p=s()._get_project(pid)
    if not p:raise HTTPException(404,"project not found")
    req=p.get("request_json") if isinstance(p.get("request_json"),dict) else {}
    if isinstance(p.get("request_json"),str):
        try:req=json.loads(p["request_json"] or "{}")
        except Exception:req={}
    brief={"title":p.get("title"),"objective":req.get("objective") or req.get("topic"),"tone":req.get("tone"),"audience":req.get("audience"),"style":req.get("visual_style"),"platforms":req.get("platforms")}
    t=now()
    with LOCK,db() as c:
        c.execute("""INSERT INTO pg_projects(project_id,user_id,brief_sha,status,stage,progress,updated_at) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(project_id) DO UPDATE SET brief_sha=excluded.brief_sha,status=excluded.status,stage=excluded.stage,progress=excluded.progress,updated_at=excluded.updated_at""",
        (pid,p.get("user_id"),sha(brief),p.get("status"),p.get("stage") or "intent",float(p.get("progress") or 0),t))
        chapters=plan(p).get("chapters") or plan(p).get("scenes") or []
        for i,ch in enumerate(chapters[:360],1):
            if not isinstance(ch,dict):continue
            sid=f"shot_{pid[:12]}_{i:03d}"
            c.execute("""INSERT INTO pg_shots(shot_id,project_id,seq,purpose,duration,narration,visual_prompt,status,metadata,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(shot_id) DO UPDATE SET purpose=excluded.purpose,duration=excluded.duration,narration=excluded.narration,visual_prompt=excluded.visual_prompt,metadata=excluded.metadata,updated_at=excluded.updated_at""",
            (sid,pid,i,clean(ch.get("purpose") or ch.get("heading"),300),float(ch.get("actual_duration") or ch.get("duration") or 0) or None,clean(ch.get("narration"),10000),clean(ch.get("image_prompt") or ch.get("visual_query") or ch.get("heading"),2500),"planned",jd({k:ch.get(k) for k in ("on_screen","proof_needed","visual_query","camera","lighting","composition") if ch.get(k)!=None}),t))
    return graph(pid)
def graph(pid):
    ensure()
    with LOCK,db() as c:
        p=c.execute("SELECT * FROM pg_projects WHERE project_id=?",(pid,)).fetchone()
        shots=c.execute("SELECT * FROM pg_shots WHERE project_id=? ORDER BY seq",(pid,)).fetchall()
        cand=c.execute("SELECT * FROM pg_candidates WHERE project_id=? ORDER BY created_at DESC",(pid,)).fetchall()
        rev=c.execute("SELECT * FROM pg_reviews WHERE project_id=? ORDER BY created_at DESC",(pid,)).fetchall()
        jobs=c.execute("SELECT * FROM pg_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 100",(pid,)).fetchall()
    cs=[]
    for x in cand:
        d=dict(x)
        try:d["critic"]=json.loads(d.pop("critic_json") or "{}")
        except Exception:d["critic"]={}
        cs.append(d)
    for candidate in cs:
        candidate.pop("asset_path", None)
        candidate["media_url"] = f"/infinity/production/project/{pid}/candidate/{candidate['candidate_id']}/media"
    return {"version":VERSION,"build":BUILD,"project":dict(p) if p else None,"shots":[dict(x) for x in shots],"candidates":cs,"reviews":[dict(x) for x in rev],"jobs":[dict(x) for x in jobs],
            "summary":{"shots":len(shots),"candidates":len(cand),
                       "accepted":sum(1 for x in cand if x["status"]=="selected_for_editor"),
                       "selected":sum(1 for x in cand if x["status"]=="selected_for_editor"),
                       "technical_ready":sum(1 for x in cand if x["status"]=="ready_for_creative_review"),
                       "queued":sum(1 for x in jobs if x["status"]=="queued")},"truthful":True}
def wan_worker_configuration():
    base = os.getenv("AI_INFINITY_WAN_WORKER_URL", "").strip().rstrip("/")
    token = os.getenv("AI_INFINITY_WAN_WORKER_TOKEN", "").strip()
    if not base or not token:
        return {"configured": False, "base_url": "", "token": ""}
    parsed = urlparse(base)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        return {"configured": False, "base_url": "", "token": "", "error": "worker URL must use HTTPS except loopback development"}
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return {"configured": False, "base_url": "", "token": "", "error": "worker URL is malformed"}
    return {"configured": True, "base_url": base, "token": token}


def wan_worker_capabilities(force=False):
    cfg = wan_worker_configuration()
    if not cfg.get("configured"):
        return {
            "configured": False, "enabled": False, "status": "not_configured",
            "reason": cfg.get("error") or "a private verified Wan GPU worker URL and token are not configured",
            "pricing_class": "open_source_self_hosted", "model": "Wan2.2-TI2V-5B",
            "truthful": True,
        }
    with WAN_CAP_LOCK:
        cached = WAN_CAP_CACHE.get("value")
        if not force and cached and now() - float(WAN_CAP_CACHE.get("at") or 0) < WAN_CAP_TTL_SECONDS:
            return dict(cached)
    try:
        response = requests.get(
            cfg["base_url"] + "/v1/capabilities",
            headers={"Authorization": "Bearer " + cfg["token"], "Accept": "application/json"},
            timeout=(3, 8),
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise RuntimeError("worker capability endpoint returned HTTP " + str(response.status_code))
        raw = response.json()
        model = raw.get("model") if isinstance(raw.get("model"), dict) else {}
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        hardware = raw.get("hardware") if isinstance(raw.get("hardware"), dict) else {}
        storage = raw.get("storage") if isinstance(raw.get("storage"), dict) else {}
        ready = bool(
            raw.get("execution_ready") is True
            and raw.get("provider") == WAN_PROVIDER_ID
            and model.get("verified") is True
            and model.get("license_verified") is True
            and model.get("model_id") == "Wan-AI/Wan2.2-TI2V-5B"
            and model.get("license") == "apache-2.0"
            and model.get("terms_acknowledged") is True
            and source.get("verified") is True
            and source.get("revision") == WAN_SOURCE_REVISION
            and hardware.get("available") is True
            and int(hardware.get("total_vram_mb") or 0) >= 24576
            and int(hardware.get("free_vram_mb") or 0) >= 22000
            and re.fullmatch(r"[0-9a-f]{40}", str(model.get("revision") or "")) is not None
            and storage.get("persistent") is True
            and raw.get("ffmpeg") is True
            and raw.get("ffprobe") is True
        )
        value = {
            "configured": True,
            "enabled": ready,
            "status": "ready" if ready else "worker_not_ready",
            "reason": None if ready else "; ".join(raw.get("reasons") or ["worker did not prove every required execution gate"]),
            "pricing_class": "open_source_self_hosted",
            "model": "Wan2.2-TI2V-5B",
            "model_id": model.get("model_id"),
            "model_revision": model.get("revision"),
            "source_revision": source.get("revision"),
            "license": model.get("license"),
            "license_verified": bool(model.get("license_verified")),
            "gpu": hardware,
            "persistent_storage": bool(storage.get("persistent")),
            "worker_runtime": raw.get("runtime") if isinstance(raw.get("runtime"), dict) else {},
            "compute_cost_state": "operator_hosted_compute_cost_not_included",
            "truthful": True,
        }
    except Exception as exc:
        value = {
            "configured": True,
            "enabled": False,
            "status": "worker_unreachable",
            "reason": f"{type(exc).__name__}: {str(exc)[:220]}",
            "pricing_class": "open_source_self_hosted",
            "model": "Wan2.2-TI2V-5B",
            "truthful": True,
        }
    with WAN_CAP_LOCK:
        WAN_CAP_CACHE.update({"at": now(), "value": dict(value)})
    return value


def providers():
    wan_state = wan_worker_capabilities()
    return {"version":VERSION,"providers":[
    {"id":"runway","video":True,
     "configured":bool(os.getenv("RUNWAYML_API_SECRET","").strip()),
     "enabled":bool(os.getenv("RUNWAYML_API_SECRET","").strip()) and os.getenv("AI_INFINITY_RUNWAY_PAID_USAGE_ACK","").strip().lower() in {"1","true","yes","on"},
     "status":("enabled_billing_acknowledged_unverified" if os.getenv("RUNWAYML_API_SECRET","").strip() and os.getenv("AI_INFINITY_RUNWAY_PAID_USAGE_ACK","").strip().lower() in {"1","true","yes","on"} else ("paid_usage_not_authorized" if os.getenv("RUNWAYML_API_SECRET","").strip() else "not_configured")),
     "pricing_class":"paid_metered","availability":"not_verified","model":os.getenv("AI_INFINITY_RUNWAY_MODEL","gen4.5")},
    {"id":"openai_video","video":False,"configured":False,"enabled":False,"status":"deprecated","reason":"The Sora API was discontinued on 2026-09-24.","model":os.getenv("AI_INFINITY_OPENAI_VIDEO_MODEL","sora-2-pro")},
    {"id":"openai_vision","review":True,
     "configured":bool(os.getenv("OPENAI_API_KEY","").strip() and os.getenv("AI_INFINITY_VISION_MODEL","").strip()),
     "enabled":bool(os.getenv("OPENAI_API_KEY","").strip() and os.getenv("AI_INFINITY_VISION_MODEL","").strip()) and os.getenv("AI_INFINITY_OPENAI_VISION_PAID_USAGE_ACK","").strip().lower() in {"1","true","yes","on"},
     "status":("not_configured" if not os.getenv("OPENAI_API_KEY","").strip() else
               "model_not_configured" if not os.getenv("AI_INFINITY_VISION_MODEL","").strip() else
               "paid_usage_not_authorized" if os.getenv("AI_INFINITY_OPENAI_VISION_PAID_USAGE_ACK","").strip().lower() not in {"1","true","yes","on"} else
               "billing_acknowledged_unverified"),
     "pricing_class":"paid_metered","availability":"not_verified",
     "model":os.getenv("AI_INFINITY_VISION_MODEL","").strip() or None},
    {"id":"elevenlabs","voice":True,"sfx":True,"configured":bool(os.getenv("ELEVENLABS_API_KEY","").strip())},
    {"id":"tavily","research":True,"configured":bool(os.getenv("TAVILY_API_KEY","").strip())},
    {"id":"exa","research":True,"configured":bool(os.getenv("EXA_API_KEY","").strip())},
    {"id":"brave","research":True,"configured":bool(os.getenv("BRAVE_SEARCH_API_KEY","").strip())},
    {"id":"remotion","render":True,"configured":bool(os.getenv("REMOTION_RENDER_URL","").strip())},
    {"id":"r2","storage":True,"configured":bool(os.getenv("CLOUDFLARE_R2_BUCKET","").strip() and os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID","").strip())},
    {"id":WAN_PROVIDER_ID,"video":True,"native_generation":True,"free_model":True,
     "configured":wan_state.get("configured",False),
     "enabled":wan_state.get("enabled",False),
     "status":wan_state.get("status","not_configured"),
     "reason":wan_state.get("reason"),
     "pricing_class":"open_source_self_hosted","paid_api":False,
     "compute_cost_state":"operator_hosted_compute_cost_not_included",
     "model":"Wan2.2-TI2V-5B","license":"Apache-2.0",
     "license_verified":wan_state.get("license_verified",False),
     "model_revision":wan_state.get("model_revision"),
     "gpu":wan_state.get("gpu"),
     "persistent_storage":wan_state.get("persistent_storage",False)}],
    "queue":{"backend":"redis" if redis_client() else "sqlite-fallback","redis_configured":bool(os.getenv("REDIS_URL","").strip())},"truthful":True}
def runway_create(d):
    k=os.getenv("RUNWAYML_API_SECRET","").strip()
    ack=os.getenv("AI_INFINITY_RUNWAY_PAID_USAGE_ACK","").strip().lower() in {"1","true","yes","on"}
    if not k:raise RuntimeError("RUNWAYML_API_SECRET not configured")
    if not ack:raise RuntimeError("Runway is a paid metered provider; explicit AI_INFINITY_RUNWAY_PAID_USAGE_ACK is required before any API request")
    body={"model":d.get("model") or os.getenv("AI_INFINITY_RUNWAY_MODEL","gen4.5"),"promptText":clean(d.get("prompt"),5000),"ratio":d.get("ratio") or "1280:768","duration":max(2,min(10,int(d.get("duration") or 5)))}
    if d.get("prompt_image"):body["promptImage"]=d["prompt_image"]
    r=requests.post("https://api.dev.runwayml.com/v1/image_to_video",headers={"Authorization":f"Bearer {k}","X-Runway-Version":"2024-11-06","Content-Type":"application/json"},json=body,timeout=60); r.raise_for_status(); return r.json()
def runway_poll(tid,limit=900):
    k=os.getenv("RUNWAYML_API_SECRET",""); end=now()+max(30,min(limit,1800)); last={}
    while now()<end:
        r=requests.get(f"https://api.dev.runwayml.com/v1/tasks/{tid}",headers={"Authorization":f"Bearer {k}","X-Runway-Version":"2024-11-06"},timeout=30); r.raise_for_status(); last=r.json(); st=str(last.get("status") or "").upper()
        if st in {"SUCCEEDED","FAILED","CANCELED"}:return last
        time.sleep(5)
    return {"status":"TIMEOUT","last":last}
def download(url,out):
    with requests.get(url,stream=True,timeout=180,headers={"User-Agent":"AI-Infinity/ProductionGraph"}) as r:
        r.raise_for_status()
        with out.open("wb") as f:
            for ch in r.iter_content(1024*1024):
                if ch:f.write(ch)
    if out.stat().st_size<1000:raise RuntimeError("empty remote artifact")
    return out
def enqueue(user,pid,sid,kind,provider,payload):
    ensure();jid="pgjob_"+uuid.uuid4().hex;payload=dict(payload);cid=None;t=now()
    if kind=="video" and sid:
        cid="cand_"+uuid.uuid4().hex;payload["candidate_id"]=cid
        with LOCK,db() as c:c.execute("INSERT INTO pg_candidates(candidate_id,shot_id,project_id,provider,model,status,critic_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(cid,sid,pid,provider,payload.get("model") or "","queued","{}",t,t))
    with LOCK,db() as c:c.execute("INSERT INTO pg_jobs(job_id,user_id,project_id,shot_id,kind,provider,status,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(jid,user,pid,sid,kind,provider,"queued",jd(payload),t,t))
    r=redis_client(); backend="sqlite-fallback"
    if r:
        try:r.rpush("aii:production:queue",jid);backend="redis"
        except Exception:pass
    return {"job_id":jid,"candidate_id":cid,"status":"queued","queue_backend":backend,"truthful":True}
def _wan_worker_headers():
    cfg = wan_worker_configuration()
    if not cfg.get("configured"):
        raise RuntimeError(cfg.get("error") or "open-source Wan GPU worker is not configured")
    return cfg, {"Authorization": "Bearer " + cfg["token"], "Accept": "application/json"}


def _download_verified_worker_artifact(url, headers, output, expected_sha, expected_size):
    digest = hashlib.sha256()
    received = 0
    temporary = output.with_suffix(output.suffix + ".part")
    temporary.unlink(missing_ok=True)
    try:
        with requests.get(url, headers=headers, stream=True, timeout=(5, 90), allow_redirects=False) as response:
            if response.status_code != 200:
                raise RuntimeError("GPU worker artifact endpoint returned HTTP " + str(response.status_code))
            declared = response.headers.get("Content-Length")
            if declared and int(declared) != int(expected_size):
                raise RuntimeError("GPU worker artifact Content-Length does not match its persisted job record")
            with temporary.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > 1_000_000_000:
                        raise RuntimeError("GPU worker artifact exceeds the 1 GB application transfer limit")
                    digest.update(chunk)
                    stream.write(chunk)
        if received != int(expected_size) or digest.hexdigest() != str(expected_sha):
            raise RuntimeError("GPU worker artifact failed independent size/SHA-256 verification")
        temporary.replace(output)
        return output
    except Exception:
        temporary.unlink(missing_ok=True)
        raise



DURABLE_ASSET_STATES = frozenset({
    "primary_only",
    "durable_partial",
    "durable_multi_provider",
    "durable_5_provider",
})


def _require_verified_durable_asset(
    saved: Dict[str, Any],
    expected_sha256: str,
    expected_size_bytes: int,
    provider: str,
) -> Dict[str, Any]:
    """Refuse candidate completion unless Creator Studio's own object store read-back verified the exact bytes."""
    durable = saved.get("durable_storage") if isinstance(saved, dict) else None
    durable = durable if isinstance(durable, dict) else {}
    status = str(durable.get("status") or "not_persisted").strip().lower()
    providers = durable.get("successful_providers")
    providers = [str(value) for value in providers if str(value).strip()] if isinstance(providers, list) else []
    stored_sha = str(durable.get("sha256") or "").lower()
    try:
        stored_size = int(durable.get("size_bytes") or 0)
    except (TypeError, ValueError):
        stored_size = 0
    asset_id = str(durable.get("asset_id") or "").strip()
    object_key = str(durable.get("object_key") or "").strip()

    failures = []
    if status not in DURABLE_ASSET_STATES:
        failures.append("project object storage status is " + status)
    if not providers:
        failures.append("no object-store provider completed verified read-back")
    if len(str(expected_sha256 or "")) != 64 or stored_sha != str(expected_sha256).lower():
        failures.append("stored object SHA-256 does not match generated artifact")
    if int(expected_size_bytes or 0) <= 0 or stored_size != int(expected_size_bytes):
        failures.append("stored object size does not match generated artifact")
    if not asset_id or not object_key:
        failures.append("durable object identity is incomplete")

    if failures:
        raise RuntimeError(
            f"{provider} generated a media file, but Creator Studio has not verified a durable project copy: "
            + "; ".join(failures)
            + ". The candidate is not ready for selection or delivery."
        )
    return durable


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _process_wan_video_job(job, payload):
    jid = str(job["job_id"])
    candidate_id = str(payload.get("candidate_id") or "")
    if not re.fullmatch(r"cand_[a-f0-9]{32}", candidate_id):
        raise RuntimeError("open-source video job is missing its registered candidate id")
    capabilities = wan_worker_capabilities(force=True)
    if not capabilities.get("enabled"):
        raise RuntimeError("open-source Wan worker is not execution-ready: " + str(capabilities.get("reason") or "unavailable"))
    try:
        duration = float(payload.get("duration") or payload.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        raise RuntimeError("requested Wan clip duration is invalid")
    if not 2.0 <= duration <= 5.0:
        raise RuntimeError("Wan2.2 TI2V-5B adapter supports native clips from 2 to 5 seconds; split or revise this scene")
    aspect = str(payload.get("aspect_ratio") or "16:9")
    if aspect not in {"16:9", "9:16"}:
        aspect = "16:9"
    cfg, headers = _wan_worker_headers()
    worker_request = {
        "prompt": clean(payload.get("prompt"), 4000),
        "duration_seconds": duration,
        "aspect_ratio": aspect,
        "seed": int(payload.get("seed") or -1),
        "idempotency_key": candidate_id,
    }
    with LOCK, db() as conn:
        existing = conn.execute("SELECT provider_job_id FROM pg_jobs WHERE job_id=?", (jid,)).fetchone()
    remote_id = str(existing["provider_job_id"] or "") if existing else ""
    if not remote_id:
        response = requests.post(
            cfg["base_url"] + "/v1/jobs",
            headers={**headers, "Content-Type": "application/json"},
            json=worker_request,
            timeout=(5, 30),
            allow_redirects=False,
        )
        if response.status_code not in {200, 201}:
            detail = response.text[:500]
            raise RuntimeError(f"open-source GPU worker rejected job (HTTP {response.status_code}): {detail}")
        submitted = response.json()
        remote_id = str(submitted.get("job_id") or "")
        if not re.fullmatch(r"wan_[a-f0-9]{32}", remote_id):
            raise RuntimeError("open-source GPU worker returned an invalid job id")
        with LOCK, db() as conn:
            conn.execute(
                "UPDATE pg_jobs SET provider_job_id=?,provider_meta_json=?,updated_at=? WHERE job_id=?",
                (remote_id, jd({"worker_provider": WAN_PROVIDER_ID}), now(), jid),
            )
    deadline = now() + 2100
    last_heartbeat = 0.0
    remote = {}
    while now() < deadline:
        response = requests.get(
            cfg["base_url"] + "/v1/jobs/" + remote_id,
            headers=headers,
            timeout=(5, 30),
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise RuntimeError("open-source GPU worker job poll returned HTTP " + str(response.status_code))
        remote = response.json()
        status = str(remote.get("status") or "").lower()
        if status == "succeeded":
            break
        if status in {"failed", "cancelled", "canceled"}:
            raise RuntimeError("open-source Wan inference failed: " + str(remote.get("error") or "no error detail")[:1400])
        if status not in {"queued", "running"}:
            raise RuntimeError("open-source GPU worker returned an unknown job status")
        if now() - last_heartbeat >= 8:
            with LOCK, db() as conn:
                conn.execute("UPDATE pg_jobs SET updated_at=? WHERE job_id=?", (now(), jid))
            last_heartbeat = now()
        time.sleep(4)
    else:
        raise TimeoutError("open-source Wan inference did not finish within the 35-minute application deadline")
    artifact = remote.get("artifact") if isinstance(remote.get("artifact"), dict) else {}
    expected_sha = str(artifact.get("sha256") or "")
    expected_size = int(artifact.get("size_bytes") or 0)
    if len(expected_sha) != 64 or expected_size <= 100_000 or expected_size > 1_000_000_000:
        raise RuntimeError("GPU worker success record has invalid artifact hash or size")
    root = Path(os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity")) / "production-jobs" / jid
    root.mkdir(parents=True, exist_ok=True)
    output = root / (candidate_id + ".mp4")
    artifact_path = _download_verified_worker_artifact(
        cfg["base_url"] + "/v1/jobs/" + remote_id + "/artifact",
        headers, output, expected_sha, expected_size,
    )
    technical = tech(artifact_path)
    if technical.get("decision") != "technical_pass":
        raise RuntimeError("downloaded Wan candidate failed independent FFprobe/FFmpeg/HD validation: " + jd(technical)[:900])
    if float((technical.get("checks") or {}).get("duration_seconds") or 0) + 0.25 < duration:
        raise RuntimeError("Wan candidate is shorter than the requested scene duration")
    remote_meta = remote.get("metadata") if isinstance(remote.get("metadata"), dict) else {}
    if remote_meta.get("license_verified") is not True or remote_meta.get("license") != "apache-2.0":
        raise RuntimeError("GPU worker artifact is missing verified Apache-2.0 model provenance")
    if remote_meta.get("model_id") != "Wan-AI/Wan2.2-TI2V-5B":
        raise RuntimeError("GPU worker artifact model identity does not match the approved Wan2.2 model")
    saved = s()._save_asset(
        job["project_id"], "candidate", artifact_path, "video/mp4",
        {
            "provider": WAN_PROVIDER_ID,
            "model": "Wan2.2-TI2V-5B",
            "candidate_id": candidate_id,
            "remote_job_id": remote_id,
            "source": "independently_hosted_open_source_gpu_worker",
            "model_revision": remote_meta.get("model_revision"),
            "license": "Apache-2.0",
            "license_url": "https://www.apache.org/licenses/LICENSE-2.0",
            "license_verified": True,
            "technical_report": technical,
            "creative_approval": "PENDING",
            "rights_status": "REVIEW_REQUIRED",
        },
    )
    durable = _require_verified_durable_asset(
        saved, expected_sha, expected_size, "Wan2.2 GPU worker"
    )
    storage_status = str(durable.get("status") or "not_persisted")
    with LOCK, db() as conn:
        conn.execute(
            "UPDATE pg_candidates SET status='ready',provider=?,model=?,asset_path=?,source_url=?,storage_status=?,storage_json=?,updated_at=? WHERE candidate_id=?",
            (WAN_PROVIDER_ID, "Wan2.2-TI2V-5B", str(artifact_path), cfg["base_url"] + "/v1/jobs/" + remote_id + "/artifact",
             storage_status, jd(durable), now(), candidate_id),
        )
    return {
        "status": "succeeded",
        "provider": WAN_PROVIDER_ID,
        "model": "Wan2.2-TI2V-5B",
        "remote_job_id": remote_id,
        "local_path": str(artifact_path),
        "sha256": expected_sha,
        "size_bytes": expected_size,
        "duration_seconds": float((technical.get("checks") or {}).get("duration_seconds") or 0),
        "license": "Apache-2.0",
        "license_verified": True,
        "storage_status": storage_status,
        "creative_approval": "PENDING",
        "rights_status": "REVIEW_REQUIRED",
        "truthful": True,
    }


def process(job):
    jid=job["job_id"]
    try:d=json.loads(job.get("payload_json") or "{}")
    except Exception:d={}
    with LOCK,db() as c:
        current=c.execute("SELECT status FROM pg_jobs WHERE job_id=?",(jid,)).fetchone()
        if not current or str(current["status"]) != "queued":
            return
        c.execute("UPDATE pg_jobs SET status='running',updated_at=? WHERE job_id=? AND status='queued'",(now(),jid))
    try:
        p=str(job.get("provider") or "").lower()
        if job["kind"]=="video" and p=="runway":
            if os.getenv("AI_INFINITY_RUNWAY_PAID_USAGE_ACK","").strip().lower() not in {"1","true","yes","on"}:
                raise RuntimeError("paid Runway usage is not authorized; job was not sent to provider")
            q=runway_create(d);tid=q.get("id")
            if not tid:raise RuntimeError("Runway returned no task id")
            done=runway_poll(tid,int(d.get("timeout") or 900))
            if str(done.get("status") or "").upper()!="SUCCEEDED":raise RuntimeError("Runway task failed: "+jd(done)[:1200])
            outs=done.get("output") or [];url=outs[0] if isinstance(outs,list) and outs else done.get("output_url")
            if not url:raise RuntimeError("Runway succeeded without output URL")
            if not re.fullmatch(r"cand_[a-f0-9]{32}", str(d.get("candidate_id") or "")):
                raise RuntimeError("Runway video job is missing its registered candidate id")
            root=Path(os.getenv("AI_INFINITY_DATA_DIR","/tmp/ai-infinity"))/"production-jobs"/jid
            root.mkdir(parents=True,exist_ok=True)
            cid=str(d.get("candidate_id") or "")
            safe_id = cid if re.fullmatch(r"cand_[a-f0-9]{32}", cid) else "candidate_" + hashlib.sha256(jid.encode()).hexdigest()[:20]
            fp=download(str(url),root/(safe_id+".mp4"))
            storage_meta={"durable_storage":{"status":"not_persisted","successful_providers":[],"truthful":True}}
            if cid:
                try:
                    saved=s()._save_asset(job["project_id"],"candidate",fp,"video/mp4",{"provider":"runway","candidate_id":cid,"task_id":tid})
                    if isinstance(saved,dict):
                        storage_meta=saved
                except Exception as storage_exc:
                    storage_meta={"durable_storage":{"status":"not_persisted","error":f"{type(storage_exc).__name__}: {str(storage_exc)[:400]}","successful_providers":[],"truthful":True}}
                durable = _require_verified_durable_asset(
                    storage_meta, _sha256_path(fp), fp.stat().st_size, "Runway"
                )
                storage_status=str(durable.get("status") or "not_persisted")
                with LOCK,db() as c:c.execute(
                    "UPDATE pg_candidates SET status='ready',asset_path=?,source_url=?,model=?,storage_status=?,storage_json=?,updated_at=? WHERE candidate_id=?",
                    (str(fp),str(url),d.get("model") or "gen4.5",storage_status,jd(durable),now(),cid))
            result={"status":"succeeded","task_id":tid,"output_url":url,"local_path":str(fp),
                    "storage_status":str((storage_meta.get("durable_storage") or {}).get("status") or "not_persisted"),
                    "truthful":True}
        elif job["kind"]=="video" and p==WAN_PROVIDER_ID:
            result = _process_wan_video_job(job, d)
        else:raise RuntimeError("unsupported production-graph provider/job")
        with LOCK,db() as c:c.execute("UPDATE pg_jobs SET status='completed',result_json=?,updated_at=? WHERE job_id=?",(jd(result),now(),jid))
    except Exception as e:
        message = f"{type(e).__name__}: {str(e)}"[:3000]
        candidate_id = str(d.get("candidate_id") or "")
        with LOCK,db() as c:
            c.execute("UPDATE pg_jobs SET status='failed',error=?,updated_at=? WHERE job_id=?",(message,now(),jid))
            if candidate_id:
                c.execute("UPDATE pg_candidates SET status='failed',error=?,updated_at=? WHERE candidate_id=?",(message,now(),candidate_id))
def worker():
    global WORKER_STARTED
    WORKER_STARTED=True;r=redis_client()
    stale_seconds=max(60,min(900,int(os.getenv("AI_INFINITY_PRODUCTION_JOB_STALE_SECONDS","120"))))
    while True:
        try:
            # Reclaim only stale running jobs. The remote worker receives the
            # candidate id as an idempotency key, so a web-process restart does
            # not create a second model inference for the same candidate.
            cutoff=now()-stale_seconds
            with LOCK,db() as c:
                c.execute("UPDATE pg_jobs SET status='queued',updated_at=?,error=COALESCE(error,'Recovered after Creator Studio worker restart') WHERE status='running' AND updated_at<?",(now(),cutoff))
            row=None
            if r:
                item=r.blpop("aii:production:queue",timeout=2)
                if item:
                    with LOCK,db() as c:row=c.execute("SELECT * FROM pg_jobs WHERE job_id=? AND status='queued'",(item[1],)).fetchone()
            if not row:
                with LOCK,db() as c:row=c.execute("SELECT * FROM pg_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:process(dict(row));continue
        except Exception:pass
        time.sleep(1)
def tech(path):
    """Evaluate media mechanics only; this is never a creative approval."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return {"score": 0, "decision": "technical_reject", "checks": {"file_present": False},
                "creative_approval": "NOT_EVALUATED"}
    size_ok = path.stat().st_size > 1000
    checks = {"file_present": True, "size_ok": size_ok, "audio_optional": True}
    ffprobe = shutil.which("ffprobe")
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg_bin:
        return {"score": 0, "decision": "technical_unavailable",
                "checks": {**checks, "ffprobe_available": bool(ffprobe),
                           "ffmpeg_available": bool(ffmpeg_bin)},
                "creative_approval": "NOT_EVALUATED"}
    if path.suffix.lower() not in {".mp4", ".mov", ".webm", ".m4v", ".mkv"}:
        return {"score": 0, "decision": "technical_reject",
                "checks": {**checks, "supported_video_container": False},
                "creative_approval": "NOT_EVALUATED"}
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=45, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "ffprobe failed")[-500:])
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        video = next((x for x in streams if x.get("codec_type") == "video"), {})
        audio = next((x for x in streams if x.get("codec_type") == "audio"), {})
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        duration = float((data.get("format") or {}).get("duration") or 0)
        has_video = bool(video)
        has_duration = duration > 0
        hd = max(width, height) >= 1280 and min(width, height) >= 720
        decode = subprocess.run(
            [ffmpeg_bin, "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        decode_ok = decode.returncode == 0
        checks.update({
            "ffprobe_available": True, "ffmpeg_available": True,
            "supported_video_container": True, "video": has_video,
            "audio": bool(audio), "audio_optional": True,
            "duration": has_duration, "duration_seconds": round(duration, 3),
            "width": width, "height": height, "hd_resolution": hd,
            "full_decode": decode_ok,
        })
        # Video models commonly emit silent clips. Requiring an embedded audio
        # stream would reject valid video-only candidates before the separate
        # narration/music mix stage can run.
        score = (10 * bool(path.is_file()) + 10 * size_ok + 30 * has_video
                 + 15 * has_duration + 20 * hd + 15 * decode_ok)
        if size_ok and has_video and has_duration and hd and decode_ok:
            decision = "technical_pass"
        elif has_video and has_duration:
            decision = "technical_review"
        else:
            decision = "technical_reject"
        return {"score": min(100, score), "decision": decision, "checks": checks,
                "creative_approval": "NOT_EVALUATED"}
    except Exception as exc:
        return {"score": 0, "decision": "technical_unavailable",
                "checks": {**checks, "probe_error": f"{type(exc).__name__}: {str(exc)[:500]}"},
                "creative_approval": "NOT_EVALUATED"}


def candidate_local_path(project_id, candidate_id, user_id):
    """Return candidate bytes only when they match the exact verified durable object."""
    ensure()
    with LOCK, db() as c:
        row = c.execute(
            "SELECT c.*,p.user_id FROM pg_candidates c JOIN pg_projects p ON p.project_id=c.project_id "
            "WHERE c.candidate_id=? AND c.project_id=?",
            (candidate_id, project_id),
        ).fetchone()
    if not row or str(row["user_id"] or "") != str(user_id or ""):
        raise HTTPException(404, "candidate not found")

    expected_name = str(candidate_id) + ".mp4"
    raw_path = Path(row["asset_path"] or "")
    if raw_path.name != expected_name:
        raise HTTPException(409, "candidate media identity does not match its registered candidate id")
    if raw_path.is_symlink():
        raise HTTPException(409, "candidate media is a symlink; refusing access")

    storage_status = str(row["storage_status"] or "not_persisted")
    try:
        storage_info = json.loads(row["storage_json"] or "{}")
    except Exception:
        storage_info = {}
    providers = storage_info.get("successful_providers") if isinstance(storage_info, dict) else []
    expected_sha = str(storage_info.get("sha256") or "") if isinstance(storage_info, dict) else ""
    try:
        expected_size = int(storage_info.get("size_bytes") or -1) if isinstance(storage_info, dict) else -1
    except (TypeError, ValueError):
        expected_size = -1
    if (
        storage_status not in {"primary_only", "durable_partial", "durable_multi_provider", "durable_5_provider"}
        or not providers or not expected_sha or len(expected_sha) != 64 or expected_size <= 0
    ):
        raise HTTPException(409, "candidate media has no verified durable object, SHA-256 and size record")

    path = raw_path.resolve()
    jobs_root = (Path(os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity")) / "production-jobs").resolve()

    def within(candidate_path, root):
        try:
            candidate_path.relative_to(root)
            return True
        except ValueError:
            return False

    def bytes_match(candidate_path):
        if candidate_path.is_symlink() or not candidate_path.is_file():
            return False
        try:
            if candidate_path.stat().st_size != expected_size:
                return False
            digest = hashlib.sha256()
            with candidate_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest() == expected_sha
        except (OSError, PermissionError):
            return False

    studio_module = None
    project_root = None
    if path.exists():
        # Worker-created candidates normally live beneath production-jobs.
        # Validate that trusted root first; resolving the Creator Studio project
        # directory is needed only for project-local assets or recovery.
        in_jobs_root = within(path, jobs_root)
        if not in_jobs_root:
            studio_module = s()
            project_root = studio_module._project_dir(project_id).resolve()
            if not within(path, project_root):
                raise HTTPException(409, "candidate media is outside its verified job/project directory")
        if bytes_match(path):
            return path
    else:
        # Do not import Creator Studio for the normal worker path unless a
        # verified project-store recovery operation is actually required.
        if not within(path, jobs_root):
            studio_module = s()
            project_root = studio_module._project_dir(project_id).resolve()

    if studio_module is None:
        studio_module = s()
        project_root = studio_module._project_dir(project_id).resolve()
    restored = studio_module._rehydrate_project_asset(
        project_id, str(user_id), expected_name, force=True
    )
    if restored is None:
        raise HTTPException(409, "candidate media could not be restored from verified object storage")
    restored_path = Path(restored).resolve()
    if restored_path.is_symlink() or not within(restored_path, project_root):
        raise HTTPException(409, "restored candidate media is outside its project directory")
    if restored_path.name != expected_name or restored_path.suffix.lower() != ".mp4" or not bytes_match(restored_path):
        raise HTTPException(409, "restored candidate does not match the verified object SHA-256 and size")
    with LOCK, db() as c:
        c.execute(
            "UPDATE pg_candidates SET asset_path=?,updated_at=? WHERE candidate_id=? AND project_id=?",
            (str(restored_path), now(), candidate_id, project_id),
        )
    return restored_path

def review(cid, user):
    """Record technical QA without silently approving the shot creatively."""
    ensure()
    with LOCK, db() as c:
        row = c.execute("SELECT * FROM pg_candidates WHERE candidate_id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "candidate not found")
        project = c.execute(
            "SELECT * FROM pg_projects WHERE project_id=? AND user_id=?",
            (row["project_id"], user),
        ).fetchone()
    if not project:
        raise HTTPException(404, "project not found")

    media_path = candidate_local_path(row["project_id"], cid, user)
    technical = tech(media_path)
    score = float(technical.get("score") or 0)
    technical_decision = str(technical.get("decision") or "technical_unavailable")
    if technical_decision == "technical_pass":
        candidate_status = "ready_for_creative_review"
        decision = "technical_passed"
    elif technical_decision == "technical_review":
        candidate_status = "technical_review_required"
        decision = "technical_review_required"
    elif technical_decision == "technical_reject":
        candidate_status = "technical_rejected"
        decision = "technical_rejected"
    else:
        candidate_status = "technical_unavailable"
        decision = "technical_unavailable"

    report = {
        "technical": technical,
        "creative_review": {"status": "PENDING", "evaluated": False},
        "human_review": {"status": "PENDING", "evaluated": False},
        "truthful": True,
    }
    review_id = "rev_" + uuid.uuid4().hex
    with LOCK, db() as c:
        c.execute(
            "INSERT INTO pg_reviews(review_id,project_id,shot_id,candidate_id,reviewer,score,decision,report_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (review_id, project["project_id"], row["shot_id"], cid, "technical-qc",
             score, decision, jd(report), now()),
        )
        c.execute(
            "UPDATE pg_candidates SET score=?,critic_json=?,status=?,updated_at=? WHERE candidate_id=?",
            (score, jd(report), candidate_status, now(), cid),
        )
    return {
        "review_id": review_id, "score": score, "decision": decision,
        "candidate_status": candidate_status, "report": report,
        "creative_approval": "PENDING", "human_review_status": "PENDING",
        "truthful": True,
    }


def select_for_editor(cid, user, project_id):
    """Let the authenticated project owner choose a durable, technically ready candidate."""
    ensure()
    with LOCK, db() as c:
        candidate_row = c.execute(
            "SELECT * FROM pg_candidates WHERE candidate_id=? AND project_id=?",
            (cid, project_id),
        ).fetchone()
        project_row = c.execute(
            "SELECT * FROM pg_projects WHERE project_id=? AND user_id=?",
            (project_id, user),
        ).fetchone()
        if not candidate_row or not project_row:
            raise HTTPException(404, "candidate not found")
        candidate = dict(candidate_row)
        shot = c.execute(
            "SELECT * FROM pg_shots WHERE shot_id=? AND project_id=?",
            (candidate["shot_id"], project_id),
        ).fetchone()
        if not shot:
            raise HTTPException(404, "shot not found")
        if candidate["status"] != "ready_for_creative_review":
            raise HTTPException(409, "candidate must pass technical QA before creator selection")
        storage_status = str(candidate["storage_status"] or "not_persisted")
        try:
            storage_info = json.loads(candidate["storage_json"] or "{}")
        except Exception:
            storage_info = {}
        if (
            storage_status not in {"primary_only", "durable_partial", "durable_multi_provider", "durable_5_provider"}
            or not storage_info.get("successful_providers")
        ):
            raise HTTPException(409, "candidate is previewable but not durably stored; verified object storage is required before editor selection")

    # Restore outside the first SQLite read transaction; _rehydrate_project_asset
    # may update the shared studio asset table and candidate path after restart.
    candidate_local_path(project_id, cid, user)

    report = {
        "selection": "creator_selected_candidate_for_editor",
        "selected_by": user,
        "creative_approval": "PENDING",
        "editor_insertion": "READY_FOR_CANONICAL_RERENDER",
        "storage_status": storage_status,
        "truthful": True,
    }
    review_id = "rev_" + uuid.uuid4().hex
    with LOCK, db() as c:
        current = c.execute(
            "SELECT status,shot_id,storage_status,storage_json FROM pg_candidates "
            "WHERE candidate_id=? AND project_id=?",
            (cid, project_id),
        ).fetchone()
        current_project = c.execute(
            "SELECT project_id FROM pg_projects WHERE project_id=? AND user_id=?",
            (project_id, user),
        ).fetchone()
        if not current or not current_project or current["status"] != "ready_for_creative_review":
            raise HTTPException(409, "candidate changed while it was being restored; refresh and try again")
        if str(current["storage_status"] or "") != storage_status:
            raise HTTPException(409, "candidate storage status changed while it was being restored")
        try:
            current_storage = json.loads(current["storage_json"] or "{}")
        except Exception:
            current_storage = {}
        if not current_storage.get("successful_providers"):
            raise HTTPException(409, "candidate lost verified durable storage during selection")
        c.execute(
            "UPDATE pg_candidates SET status='selected_for_editor',updated_at=? WHERE candidate_id=? AND project_id=?",
            (now(), cid, project_id),
        )
        c.execute(
            "UPDATE pg_shots SET winner_candidate_id=?,status='selected_for_editor',updated_at=? WHERE shot_id=? AND project_id=?",
            (cid, now(), current["shot_id"], project_id),
        )
        c.execute(
            "INSERT INTO pg_reviews(review_id,project_id,shot_id,candidate_id,reviewer,score,decision,report_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (review_id, project_id, current["shot_id"], cid, "creator", None,
             "selected_for_editor", jd(report), now()),
        )

    try:
        s().audit_event(project_id, "production_candidate_selected_for_editor", {
            "candidate_id": cid, "shot_id": candidate["shot_id"], "truthful": True,
        })
    except Exception:
        pass
    return {
        "review_id": review_id, "candidate_id": cid, "shot_id": candidate["shot_id"],
        "status": "selected_for_editor", "creative_approval": "PENDING",
        "editor_insertion": "READY_FOR_CANONICAL_RERENDER",
        "storage_status": storage_status, "truthful": True,
    }

def selected_candidate_id_for_scene(project_id, scene_index):
    ensure()
    with LOCK, db() as c:
        row = c.execute(
            "SELECT winner_candidate_id,status FROM pg_shots WHERE project_id=? AND seq=?",
            (project_id, int(scene_index)),
        ).fetchone()
    if not row or row["status"] != "selected_for_editor":
        return ""
    return str(row["winner_candidate_id"] or "")


def has_selected_candidates(project_id):
    ensure()
    with LOCK, db() as c:
        row = c.execute(
            "SELECT COUNT(*) FROM pg_shots s JOIN pg_candidates c ON c.candidate_id=s.winner_candidate_id "
            "WHERE s.project_id=? AND s.status='selected_for_editor' AND c.status='selected_for_editor' "
            "AND c.storage_status IN ('primary_only','durable_partial','durable_multi_provider','durable_5_provider')",
            (project_id,),
        ).fetchone()
    return bool(row and int(row[0] or 0) > 0)


def selected_candidate_asset(project_id, scene_index, requested_duration):
    """Resolve a creator-selected native video candidate for the canonical render path."""
    ensure()
    try:
        index = int(scene_index)
    except (TypeError, ValueError):
        return None
    with LOCK, db() as c:
        shot = c.execute(
            "SELECT shot_id,winner_candidate_id,status FROM pg_shots WHERE project_id=? AND seq=?",
            (project_id, index),
        ).fetchone()
        if not shot or not shot["winner_candidate_id"]:
            return None
        candidate = c.execute(
            "SELECT c.*,p.user_id FROM pg_candidates c JOIN pg_projects p ON p.project_id=c.project_id WHERE c.candidate_id=? AND c.project_id=? AND c.shot_id=?",
            (shot["winner_candidate_id"], project_id, shot["shot_id"]),
        ).fetchone()
    if not candidate or candidate["status"] != "selected_for_editor" or shot["status"] != "selected_for_editor":
        raise RuntimeError("selected shot candidate state is inconsistent; refusing a fallback visual")

    try:
        path = candidate_local_path(project_id, str(candidate["candidate_id"]), str(candidate["user_id"] or ""))
    except HTTPException as exc:
        raise RuntimeError(str(exc.detail)) from exc

    report = tech(path)
    if report.get("decision") != "technical_pass":
        raise RuntimeError("selected generated candidate no longer passes media decode/HD checks")
    media_duration = float((report.get("checks") or {}).get("duration_seconds") or 0)
    try:
        target_duration = float(requested_duration)
    except (TypeError, ValueError):
        target_duration = 0.0
    if target_duration <= 0 or media_duration + 0.25 < target_duration:
        raise RuntimeError(
            f"selected generated clip is {media_duration:.3f}s but this scene requires "
            f"{target_duration:.3f}s; regenerate a sufficiently long shot or revise the scene duration"
        )

    return {
        "kind": "video",
        "path": str(path),
        "source": "provider_generated_video",
        "provider": str(candidate["provider"] or ""),
        "model": str(candidate["model"] or ""),
        "source_url": str(candidate["source_url"] or ""),
        "candidate_id": str(candidate["candidate_id"]),
        "generation_method": "native_ai_video_generation",
        "quality_tier": "native_ai_generated_video",
        "license": "Apache-2.0" if str(candidate["provider"] or "") == WAN_PROVIDER_ID else "provider_terms_not_independently_verified",
        "license_url": "https://www.apache.org/licenses/LICENSE-2.0" if str(candidate["provider"] or "") == WAN_PROVIDER_ID else "",
        "license_verified": True if str(candidate["provider"] or "") == WAN_PROVIDER_ID else False,
        "rights_status": "REVIEW_REQUIRED",
        "commercial_use_state": "UNKNOWN_REVIEW_REQUIRED",
        "attribution": "provider_terms_to_be_checked",
        "duration_seconds": media_duration,
        "technical_report": report,
        "creative_approval": "PENDING",
        "truthful": True,
    }



def install():
    global PATCHED
    if PATCHED:return
    ensure();m=s()
    if not hasattr(m,"_pg_stage_original"):
        m._pg_stage_original=m._stage
        def stage(pid,name,progress,**extra):
            m._pg_stage_original(pid,name,progress,**extra)
            try:
                p=m._get_project(pid)
                with LOCK,db() as c:c.execute("""INSERT INTO pg_projects(project_id,user_id,brief_sha,status,stage,progress,updated_at) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET status=excluded.status,stage=excluded.stage,progress=excluded.progress,updated_at=excluded.updated_at""",(pid,(p or {}).get("user_id"),sha((p or {}).get("request_json")),str((p or {}).get("status") or "running"),name,float(progress),now()))
            except Exception:pass
        m._stage=stage
    if not hasattr(m, "_pg_original_scene_render_signature"):
        original_signature = m._scene_render_signature
        m._pg_original_scene_render_signature = original_signature
        def signature_with_selected_candidate(chapter, request, scene_index):
            base_signature = original_signature(chapter, request, scene_index)
            project_id = getattr(m.ACTIVE_PROJECT, "project_id", None)
            if not project_id:
                return base_signature
            candidate_id = selected_candidate_id_for_scene(project_id, scene_index)
            if not candidate_id:
                return base_signature
            return hashlib.sha256((base_signature + "|selected-native-candidate|" + candidate_id).encode()).hexdigest()
        m._scene_render_signature = signature_with_selected_candidate
    if not hasattr(m, "_pg_original_acquire_scene_asset"):
        original_acquire = m.acquire_scene_asset
        m._pg_original_acquire_scene_asset = original_acquire
        def acquire_with_selected_native_video(scene, outdir, index, prefer_motion=False, duration=6.0):
            # Acceptance fixtures remain authoritative test inputs; normal user
            # projects can opt into a selected, technically verified native shot.
            try:
                fixture = m._ci_test_visual(scene, outdir, index)
            except Exception:
                fixture = None
            if fixture is not None:
                return fixture
            project_id = getattr(m.ACTIVE_PROJECT, "project_id", None)
            if project_id:
                selected = selected_candidate_asset(project_id, index, duration)
                if selected is not None:
                    m.audit_event(project_id, "native_video_candidate_used_in_render", {
                        "scene": int(index),
                        "candidate_id": selected["candidate_id"],
                        "provider": selected["provider"],
                        "model": selected["model"],
                        "creative_approval": "PENDING",
                        "rights_status": "REVIEW_REQUIRED",
                        "truthful": True,
                    })
                    return selected
            return original_acquire(scene, outdir, index, prefer_motion=prefer_motion, duration=duration)
        m.acquire_scene_asset = acquire_with_selected_native_video
    PATCHED=True
    global WORKER_STARTED
    if not WORKER_STARTED and os.getenv("AI_INFINITY_PRODUCTION_GRAPH_WORKER","1").lower() in {"1","true","yes","on"}:threading.Thread(target=worker,name="ai-infinity-production-graph",daemon=True).start()

def register(app):
    install()
    @app.get("/infinity/production/health")
    def h():return {"status":"healthy","version":VERSION,"build":BUILD,"worker_started":WORKER_STARTED,"providers":providers(),"truthful":True}
    @app.get("/infinity/production/providers")
    def ps():return providers()
    @app.get("/infinity/production/project/{pid}")
    def pg(pid:str,r:Request):owned(pid,r);return sync(pid)
    @app.post("/infinity/production/create")
    async def pc(r:Request):
        d=await r.json()
        if not isinstance(d,dict):raise HTTPException(422,"JSON object required")
        d=dict(d);d.setdefault("content_type","video");d.setdefault("quality_preset","professional");d.setdefault("idempotency_key","pg-"+uuid.uuid4().hex)
        res=s().enqueue(d,uid(r),None);pid=str(res.get("project_id") or "")
        if not pid:raise HTTPException(500,"Creator Studio did not return project id")
        return {"status":"accepted","project_id":pid,"graph":sync(pid),"truthful":True}
    @app.post("/infinity/production/project/{pid}/generate-video-batch")
    async def gb(pid:str,r:Request):
        project = owned(pid,r)
        d=await r.json()
        sid=clean(d.get("shot_id"),120)
        if not sid:raise HTTPException(422,"shot_id required")
        with LOCK,db() as c:
            shot=c.execute("SELECT duration,visual_prompt FROM pg_shots WHERE shot_id=? AND project_id=?",(sid,pid)).fetchone()
        if not shot:raise HTTPException(404,"shot not found")
        try:requested_duration=float(d.get("duration") or shot["duration"] or 5)
        except (TypeError,ValueError):raise HTTPException(422,"duration must be a number")
        wan=wan_worker_capabilities()
        if wan.get("enabled"):
            if not 2 <= requested_duration <= 5:
                raise HTTPException(422,"verified Wan2.2 worker supports 2–5 second clips; split or revise this shot duration")
            req_data=project.get("request_json") if isinstance(project.get("request_json"),dict) else {}
            if isinstance(project.get("request_json"),str):
                try:req_data=json.loads(project.get("request_json") or "{}")
                except Exception:req_data={}
            x=dict(d)
            x["duration"]=requested_duration
            x["aspect_ratio"]=str(d.get("aspect_ratio") or req_data.get("aspect_ratio") or "16:9")
            x["model"]="Wan2.2-TI2V-5B"
            x["prompt"]=clean(d.get("prompt") or shot["visual_prompt"] or "Professional cinematic shot",3800)
            job=enqueue(uid(r),pid,sid,"video",WAN_PROVIDER_ID,x)
            return {"status":"accepted","provider":WAN_PROVIDER_ID,"pricing_class":"open_source_self_hosted",
                    "compute_cost_state":"operator_hosted_compute_cost_not_included","jobs":[job],
                    "requested_clip_seconds":requested_duration,"truthful":True}
        if not os.getenv("RUNWAYML_API_SECRET","").strip():
            return {"status":"native_video_unavailable","open_source_worker":wan,
                    "reason":"No verified self-hosted open-source GPU worker is available; no native video job was created.",
                    "truthful":True}
        if os.getenv("AI_INFINITY_RUNWAY_PAID_USAGE_ACK","").strip().lower() not in {"1","true","yes","on"}:
            return {"status":"paid_provider_authorization_required","provider":"runway","pricing_class":"paid_metered","api_request_sent":False,"open_source_worker":wan,"truthful":True}
        if not 2 <= requested_duration <= 10:
            raise HTTPException(422,"Runway adapter supports native clips from 2 to 10 seconds")
        n=max(1,min(4,int(d.get("count") or 1)))
        styles=["cinematic","documentary","editorial","commercial"];jobs=[]
        for i in range(n):
            x=dict(d);x["duration"]=requested_duration
            x["prompt"]=clean(d.get("prompt") or shot["visual_prompt"] or "Professional cinematic shot",3800)+f"; variation {i+1}: {styles[i%4]}."
            jobs.append(enqueue(uid(r),pid,sid,"video","runway",x))
        return {"status":"accepted","provider":"runway","jobs":jobs,"requested_clip_seconds":requested_duration,"truthful":True}
    @app.post("/infinity/production/project/{pid}/review/{cid}")
    def rv(pid:str,cid:str,r:Request):owned(pid,r);return review(cid,uid(r))
    @app.get("/infinity/production/project/{pid}/candidate/{cid}/media")
    def candidate_media(pid:str,cid:str,r:Request):
        owned(pid,r)
        path=candidate_local_path(pid,cid,uid(r))
        return FileResponse(path,media_type="video/mp4",filename=f"{cid}.mp4")
    @app.post("/infinity/production/project/{pid}/candidate/{cid}/select")
    def candidate_select(pid:str,cid:str,r:Request):
        owned(pid,r)
        return select_for_editor(cid,uid(r),pid)
    @app.get("/production",response_class=HTMLResponse)
    def ui():return HTML

HTML=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity Production</title><style>body{margin:0;background:#070a0e;color:#eef4f8;font:15px system-ui}main{max-width:1100px;margin:auto;padding:26px}.c{background:#0e141b;border:1px solid #27343f;border-radius:18px;padding:18px;margin:12px 0}.btn{background:#d8ff91;border:0;border-radius:12px;padding:12px 16px;font-weight:800}textarea{width:100%;box-sizing:border-box;background:#080c11;color:white;border:1px solid #2d3945;border-radius:12px;padding:12px}.pill{display:inline-block;border:1px solid #2d3945;border-radius:999px;padding:5px 8px;margin:3px}.muted{color:#93a7b6}.shot{border:1px solid #27343f;border-radius:13px;padding:10px;margin:8px 0}</style></head><body><main><h1>AI Infinity</h1><p class="muted">Professional Production Graph</p><div class="c"><textarea id="b" rows="5" placeholder="Describe the content you want produced..."></textarea><p><button class="btn" onclick="go()">Start production</button> <a class="pill" href="/">Main AI Infinity</a></p><div id="m" class="muted"></div></div><div class="c"><b>Providers</b><div id="p" class="muted">Loading...</div></div><div class="c"><b>Live graph</b><div id="g" class="muted">No project.</div></div></main><script>let pid=null,runwayEnabled=false,runwayStatus='not_configured',wanEnabled=false,wanStatus='not_configured',visionEnabled=false,visionStatus='not_configured';async function A(u,o){let r=await fetch(u,o),j=await r.json();if(!r.ok)throw Error(j.detail||JSON.stringify(j));return j}async function go(){try{let b=document.getElementById('b').value.trim();if(!b)return;let j=await A('/infinity/production/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({objective:b,title:b.slice(0,120),duration:60,format:'long',content_type:'video',quality_preset:'professional'})});pid=j.project_id;document.getElementById('m').textContent='Accepted real project '+pid;poll()}catch(e){document.getElementById('m').textContent=e.message}}function esc(v){return String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}async function generateShot(sid,prompt,duration){try{let r=await A('/infinity/production/project/'+pid+'/generate-video-batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({shot_id:sid,prompt:prompt,duration:Number(duration),count:2})});document.getElementById('m').textContent=(r.status==='accepted'?(r.provider==='wan2.2-ti2v-5b'?'Queued one open-source Wan2.2 candidate. ':'Queued paid Runway candidates. '):'Generation blocked: ')+JSON.stringify(r);poll()}catch(e){document.getElementById('m').textContent=e.message}}async function critiqueCandidate(cid){try{let r=await A('/infinity/production/project/'+pid+'/vision-review/'+encodeURIComponent(cid),{method:'POST'});document.getElementById('m').textContent='Vision recommendation: '+r.decision+' · score '+(r.score??'not available')+' · approval remains '+r.creative_approval;poll()}catch(e){document.getElementById('m').textContent=e.message}}async function qaCandidate(cid){try{await A('/infinity/production/project/'+pid+'/review/'+encodeURIComponent(cid),{method:'POST'});poll()}catch(e){document.getElementById('m').textContent=e.message}}async function chooseCandidate(cid){try{let r=await A('/infinity/production/project/'+pid+'/candidate/'+encodeURIComponent(cid)+'/select',{method:'POST'});document.getElementById('m').textContent='Selected for editor. '+r.editor_insertion;poll()}catch(e){document.getElementById('m').textContent=e.message}}async function poll(){if(!pid)return;try{let j=await A('/infinity/production/project/'+pid),p=j.project||{};let shots=(j.shots||[]).map(x=>{let shotDuration=Number(x.duration||0);
let generation=wanEnabled&&shotDuration>=2&&shotDuration<=5
?'<button class="btn" data-generate="'+esc(x.shot_id)+'" data-prompt="'+esc(x.visual_prompt)+'" data-duration="5">Generate open-source Wan2.2 candidate (5s)</button>'
:wanEnabled
?'<div class="muted">Wan2.2 is connected, but native clips are limited to 2–5 seconds. Split this shot in the storyboard before generating.</div>'
:runwayEnabled&&shotDuration>=2&&shotDuration<=10
?'<button class="btn" data-generate="'+esc(x.shot_id)+'" data-prompt="'+esc(x.visual_prompt)+'" data-duration="'+shotDuration+'">Generate paid Runway candidates</button>'
:'<div class="muted">Open-source Wan worker: '+esc(wanStatus)+'; paid Runway: '+esc(runwayStatus)+'. No native-video request was sent.</div>';return '<div class="shot"><b>Shot '+String(x.seq).padStart(2,'0')+' · '+esc(x.purpose||'Scene')+'</b><div class="muted">'+esc(x.status)+' · '+Number(x.duration||0).toFixed(1)+'s</div><div style="margin-top:8px">'+generation+'</div></div>'}).join('');let candidates=(j.candidates||[]).map(x=>{let durable=['primary_only','durable_partial','durable_multi_provider','durable_5_provider'].includes(x.storage_status);let action=x.status==='ready'?'<button class="btn" data-qa="'+esc(x.candidate_id)+'">Run technical QA</button>':x.status==='ready_for_creative_review'?(visionEnabled?'<button class="btn" data-vision="'+esc(x.candidate_id)+'">AI vision critique</button> ':'')+(durable?'<button class="btn" data-select="'+esc(x.candidate_id)+'">Select for editor</button>':''):'';let visionNote=x.critic&&x.critic.vision_score!=null?'<div class="muted">AI vision recommendation: '+esc(x.critic.creative_recommendation||'review')+' · '+esc(x.critic.vision_score)+'/100. Not human approval.</div>':x.status==='ready_for_creative_review'&&!visionEnabled?'<div class="muted">Vision critique unavailable: '+esc(visionStatus)+' (no paid API request was sent).</div>':'';let note=x.status==='selected_for_editor'?'<div class="muted">Selected for the next canonical rerender. Creative approval and rights review remain pending.</div>':x.status==='ready_for_creative_review'&&!durable?'<div class="muted">Preview only: durable object storage is not verified, so this candidate cannot be selected for an editable rerender.</div>':x.status==='ready_for_creative_review'?'<div class="muted">Technical checks passed. Creative approval remains pending.</div>':'';note+=visionNote;return '<div class="shot"><b>'+esc(x.provider)+' · '+esc(x.model)+'</b><div class="muted">'+esc(x.candidate_id)+' · '+esc(x.status)+'</div><video controls playsinline preload="none" style="width:100%;max-width:560px;margin-top:8px" src="'+esc(x.media_url)+'"></video><div style="margin-top:8px">'+action+'</div>'+note+'</div>'}).join('');let rerender=p.status==='completed'&&(j.summary.selected||0)>0?'<button class="btn" id="rerender-selected">Rerender selected shots into final timeline</button><div class="muted">The existing completed master stays available until the rerender finishes. Selected generated shots replace only matching scenes; narration/music/SFX are remixed.</div>':'';document.getElementById('g').innerHTML='<b>'+Number(p.progress||0).toFixed(0)+'%</b> · '+esc(p.stage)+' · '+esc(p.status)+'<div class="muted">shots '+j.summary.shots+' · candidates '+j.summary.candidates+' · technical-ready '+(j.summary.technical_ready||0)+' · creator-selected '+(j.summary.selected||0)+'</div>'+rerender+shots+'<h3>Generated candidates</h3>'+candidates;let rerenderButton=document.getElementById('rerender-selected');if(rerenderButton)rerenderButton.addEventListener('click',async()=>{rerenderButton.disabled=true;rerenderButton.textContent='Queueing canonical rerender…';try{let r=await A('/infinity/studio/project/'+pid+'/retry',{method:'POST'});document.getElementById('m').textContent='Rerender queued: '+r.project_id;poll()}catch(e){document.getElementById('m').textContent=e.message;rerenderButton.disabled=false;rerenderButton.textContent='Rerender selected shots into final timeline'}});document.querySelectorAll('[data-generate]').forEach(el=>el.addEventListener('click',()=>generateShot(el.getAttribute('data-generate'),el.getAttribute('data-prompt'),el.getAttribute('data-duration'))));document.querySelectorAll('[data-vision]').forEach(el=>el.addEventListener('click',()=>critiqueCandidate(el.getAttribute('data-vision'))));document.querySelectorAll('[data-qa]').forEach(el=>el.addEventListener('click',()=>qaCandidate(el.getAttribute('data-qa'))));document.querySelectorAll('[data-select]').forEach(el=>el.addEventListener('click',()=>chooseCandidate(el.getAttribute('data-select'))))}catch(e){}setTimeout(poll,3000)}const requestedProjectId=new URLSearchParams(window.location.search).get('project_id');
if(requestedProjectId&&/^[A-Za-z0-9_-]{1,120}$/.test(requestedProjectId)){
  pid=requestedProjectId;
  document.getElementById('m').textContent='Opening the active Creator Studio project…';
  poll();
}
(async()=>{try{let j=await A('/infinity/production/providers');let rw=(j.providers||[]).find(x=>x.id==='runway')||{};let ww=(j.providers||[]).find(x=>x.id==='wan2.2-ti2v-5b')||{};let vw=(j.providers||[]).find(x=>x.id==='openai_vision')||{};runwayEnabled=rw.enabled===true;runwayStatus=rw.status||'unverified';wanEnabled=ww.enabled===true;wanStatus=ww.status||'unverified';visionEnabled=vw.enabled===true;visionStatus=vw.status||'unverified';document.getElementById('p').innerHTML=j.providers.map(x=>'<span class="pill">'+esc(x.id)+' · '+esc(x.status||(x.configured?'credentials_present':'not_configured'))+'</span>').join('')}catch(e){}})()</script></body></html>'''
