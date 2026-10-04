"""TARGET-2050.3623 — AI Infinity Content Empire Autopilot.

One idea expands into a bounded multi-format campaign using the existing creator
studio queue. This is additive and keeps every 3622 production primitive intact.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

VERSION = "TARGET-2050.3623"
BUILD = "INFINITY-CONTENT-EMPIRE-AUTOPILOT"
MAX_DAYS = 7
MAX_JOBS = 35
FORMATS = [
    {"key": "hero_video", "content_type": "video", "label": "Hero video", "role": "flagship"},
    {"key": "short_1", "content_type": "video", "label": "Short #1", "role": "hook"},
    {"key": "short_2", "content_type": "video", "label": "Short #2", "role": "insight"},
    {"key": "article", "content_type": "article", "label": "Article", "role": "search"},
    {"key": "social", "content_type": "social", "label": "Social pack", "role": "distribution"},
]
DB_PATH = Path(os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity")) / "infinity_empire.sqlite3"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DB_LOCK = threading.RLock()


def now() -> float:
    return time.time()


def jd(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


with DB_LOCK, connect() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS infinity_empire_campaigns(
        campaign_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        objective TEXT NOT NULL,
        days INTEGER NOT NULL,
        jobs_requested INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        status TEXT NOT NULL,
        config_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS infinity_empire_jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT NOT NULL,
        day_index INTEGER NOT NULL,
        format_key TEXT NOT NULL,
        project_id TEXT,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        result_json TEXT NOT NULL,
        UNIQUE(campaign_id, day_index, format_key),
        FOREIGN KEY(campaign_id) REFERENCES infinity_empire_campaigns(campaign_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_empire_user ON infinity_empire_campaigns(user_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_empire_campaign ON infinity_empire_jobs(campaign_id, day_index, id);
    """)


def _session_user(request: Any, response: Any) -> str:
    raw = ""
    try:
        raw = (request.cookies.get("ai_infinity_user") or "").strip()
    except Exception:
        pass
    if raw:
        return raw[:120]
    raw = "web-" + uuid.uuid4().hex[:20]
    response.set_cookie("ai_infinity_user", raw, httponly=True, samesite="lax", secure=bool(os.getenv("AI_INFINITY_COOKIE_SECURE", "").strip()), path="/")
    return raw


def _safe_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(low, min(high, n))


def _campaign(campaign_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    with DB_LOCK, connect() as c:
        row = c.execute("SELECT * FROM infinity_empire_campaigns WHERE campaign_id=? AND user_id=?", (campaign_id, user_id)).fetchone()
    return dict(row) if row else None


def _jobs(campaign_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK, connect() as c:
        rows = c.execute("SELECT * FROM infinity_empire_jobs WHERE campaign_id=? ORDER BY day_index,id", (campaign_id,)).fetchall()
    out = []
    for row in rows:
        x = dict(row)
        try:
            x["result"] = json.loads(x.pop("result_json") or "{}")
        except Exception:
            x["result"] = {}
            x.pop("result_json", None)
        out.append(x)
    return out


def _request_for(objective: str, title: str, day: int, fmt: Dict[str, str], cfg: Dict[str, Any], campaign_id: str) -> Dict[str, Any]:
    req = {
        "title": title,
        "topic": objective,
        "objective": objective,
        "content_type": fmt["content_type"],
        "format": "long" if fmt["key"] == "hero_video" else "short" if "short" in fmt["key"] else "adaptive",
        "audience": cfg["audience"],
        "tone": cfg["tone"],
        "style": cfg["style"],
        "campaign_id": campaign_id,
        "campaign_day": day,
        "campaign_role": fmt["role"],
        "campaign_format": fmt["label"],
        "features": ["seo", "captions", "accessibility", "provenance", "fact_check"],
        "idempotency_key": f"{campaign_id}:{day}:{fmt['key']}",
    }
    if fmt["key"] == "hero_video":
        req.update({"duration_seconds": cfg["hero_duration_seconds"], "scene_count": 6})
    elif "short" in fmt["key"]:
        req.update({"duration_seconds": cfg["short_duration_seconds"], "scene_count": 3})
    return req


def _launch(objective: str, title: str, days: int, user_id: str, cfg: Dict[str, Any], model_fn: Optional[Callable]) -> Dict[str, Any]:
    from studio_ultimate import enqueue
    campaign_id = "empire-" + uuid.uuid4().hex[:16]
    t = now()
    with DB_LOCK, connect() as c:
        c.execute("INSERT INTO infinity_empire_campaigns VALUES(?,?,?,?,?,?,?,?,?,?)", (campaign_id, user_id, title, objective, days, days * len(FORMATS), t, t, "launching", jd(cfg)))
    jobs = []
    for day in range(1, days + 1):
        for fmt in FORMATS:
            job_title = f"{title} — Day {day} — {fmt['label']}"
            payload = _request_for(objective, job_title, day, fmt, cfg, campaign_id)
            try:
                result = enqueue(payload, user_id, model_fn)
                status = str(result.get("status") or "queued")
                project_id = result.get("project_id")
            except Exception as exc:
                result = {"status": "failed_to_queue", "error": str(exc)[:800], "truthful": True}
                status, project_id = "failed_to_queue", None
            jobs.append({"day": day, "format": fmt["key"], "title": job_title, "project_id": project_id, "status": status})
            with DB_LOCK, connect() as c:
                c.execute("INSERT OR REPLACE INTO infinity_empire_jobs(campaign_id,day_index,format_key,project_id,title,status,created_at,updated_at,result_json) VALUES(?,?,?,?,?,?,?,?,?)", (campaign_id, day, fmt["key"], project_id, job_title, status, now(), now(), jd(result)))
    final_status = "queued" if all(x["status"] != "failed_to_queue" for x in jobs) else "queued_with_errors"
    with DB_LOCK, connect() as c:
        c.execute("UPDATE infinity_empire_campaigns SET status=?,updated_at=? WHERE campaign_id=?", (final_status, now(), campaign_id))
    return {"version": VERSION, "build": BUILD, "campaign_id": campaign_id, "status": final_status, "jobs": jobs, "truthful": True}


def _public(c: Dict[str, Any], jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    completed = 0
    active = 0
    for j in jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
        state = (j.get("result") or {}).get("state")
        if state in {"completed", "completed_with_qc_warnings"}: completed += 1
        elif state in {"queued", "running"} or j["status"] in {"queued", "scheduled"}: active += 1
    return {
        "version": VERSION,
        "build": BUILD,
        "campaign": {k: c[k] for k in ("campaign_id","title","objective","days","jobs_requested","status","created_at","updated_at")},
        "summary": {"jobs": len(jobs), "queued_or_running": active, "completed": completed, "queue_records": counts, "formats_per_day": len(FORMATS)},
        "jobs": jobs,
        "truthful": True,
    }


EMPIRE_UI = """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>AI Infinity — Empire Autopilot</title><style>*{box-sizing:border-box}body{margin:0;background:#070b12;color:#edf3ff;font:15px system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:24px}.hero{padding:24px;border:1px solid #263650;border-radius:24px;background:linear-gradient(145deg,#0e1625,#09101a)}h1{margin:0 0 8px;font-size:34px}p{color:#9eabc1}.grid{display:grid;grid-template-columns:1.1fr .9fr;gap:16px;margin-top:16px}.card{background:#0b121d;border:1px solid #25334b;border-radius:18px;padding:18px}.wide{grid-column:1/-1}.row{display:flex;gap:12px;justify-content:space-between;padding:12px;border:1px solid #1f2c42;border-radius:12px;margin:8px 0}.pill{padding:5px 9px;border-radius:999px;background:#18263a}textarea,input,select{width:100%;background:#070d16;color:#fff;border:1px solid #2a3952;border-radius:10px;padding:10px;margin-top:6px}textarea{min-height:130px}button{background:#3b73c9;color:#fff;border:0;padding:11px 16px;border-radius:10px;font-weight:700;cursor:pointer}.small{font-size:12px;color:#8190a7}@media(max-width:840px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}}</style></head><body><main><section class='hero'><div class='small'>TARGET-2050.3623</div><h1>∞ Empire Autopilot</h1><p>One idea → multiple real creator-studio jobs → one live campaign radar.</p></section><div class='grid'><section class='card'><h2>Launch</h2><label>Objective</label><textarea id='objective' placeholder='Build a 7-day educational campaign about practical AI tools for small businesses'></textarea><label>Title</label><input id='title' value='AI Infinity Campaign'><label>Days</label><select id='days'><option>1</option><option selected>3</option><option>5</option><option>7</option></select><label>Audience</label><input id='audience' value='general audience'><p><button onclick='launch()'>Launch Empire →</button></p><div id='out' class='small'></div></section><section class='card'><h2>Production matrix</h2><div class='row'><span>Hero video</span><span class='pill'>flagship</span></div><div class='row'><span>Short #1</span><span class='pill'>hook</span></div><div class='row'><span>Short #2</span><span class='pill'>insight</span></div><div class='row'><span>Article</span><span class='pill'>search</span></div><div class='row'><span>Social pack</span><span class='pill'>distribution</span></div></section><section class='card wide'><h2>Live radar</h2><div id='radar' class='small'>No campaign yet.</div></section></div></main><script>const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function api(u,o={}){o.credentials='include';o.headers=Object.assign({'Content-Type':'application/json'},o.headers||{});let r=await fetch(u,o),t=await r.text(),j={};try{j=JSON.parse(t)}catch{j={detail:t}}if(!r.ok)throw Error(j.detail||'request failed');return j}async function launch(){let objective=$('objective').value.trim();if(objective.length<8){$('out').textContent='Objective must be at least 8 characters.';return}$('out').textContent='Launching…';try{let d=await api('/infinity/studio/empire',{method:'POST',body:JSON.stringify({objective,title:$('title').value.trim(),days:Number($('days').value),audience:$('audience').value.trim()})});$('out').textContent='Campaign '+d.campaign_id+' queued with '+d.jobs.length+' jobs.';load(d.campaign_id)}catch(e){$('out').textContent=e.message}}async function load(id){try{let d=await api('/infinity/studio/empire/'+encodeURIComponent(id)),s=d.summary;let h='<div class=row><span>Campaign</span><b>'+esc(d.campaign.title)+'</b></div><div class=row><span>Jobs</span><b>'+s.jobs+'</b></div><div class=row><span>Completed</span><b>'+s.completed+'</b></div><div class=row><span>Queued/running</span><b>'+s.queued_or_running+'</b></div>';h+=d.jobs.map(j=>'<div class=row><span>Day '+j.day_index+' · '+esc(j.title)+'</span><span class=pill>'+esc((j.result&&j.result.state)||j.status)+'</span></div>').join('');$('radar').innerHTML=h}catch(e){$('radar').textContent=e.message}} </script></body></html>"""


def register(app: Any, model_fn: Optional[Callable] = None) -> None:
    from fastapi import HTTPException, Request, Response
    from fastapi.responses import HTMLResponse

    @app.get("/infinity/studio/empire", response_class=HTMLResponse)
    def empire_ui() -> str:
        return EMPIRE_UI

    @app.get("/infinity/studio/empire/list")
    def empire_list(request: Request, response: Response, limit: int = 20) -> Dict[str, Any]:
        user_id = _session_user(request, response)
        limit = _safe_int(limit, 20, 1, 50)
        with DB_LOCK, connect() as c:
            rows = [dict(x) for x in c.execute("SELECT * FROM infinity_empire_campaigns WHERE user_id=? ORDER BY updated_at DESC LIMIT ?", (user_id, limit)).fetchall()]
        return {"version": VERSION, "build": BUILD, "campaigns": [_public(x, _jobs(x["campaign_id"]))["campaign"] | _public(x, _jobs(x["campaign_id"]))["summary"] for x in rows], "truthful": True}

    @app.get("/infinity/studio/empire/health")
    def empire_health() -> Dict[str, Any]:
        return {"status":"healthy","version":VERSION,"build":BUILD,"formats_per_day":len(FORMATS),"max_days":MAX_DAYS,"max_jobs":MAX_JOBS,"uses_existing_production_queue":True,"truthful":True}

    @app.post("/infinity/studio/empire")
    async def empire_create(request: Request, response: Response) -> Dict[str, Any]:
        user_id = _session_user(request, response)
        payload = await request.json()
        objective = str(payload.get("objective") or "").strip()
        if len(objective) < 8:
            raise HTTPException(422, "objective must be at least 8 characters")
        days = _safe_int(payload.get("days"), 3, 1, MAX_DAYS)
        title = str(payload.get("title") or objective[:120]).strip()[:160]
        cfg = {
            "audience": str(payload.get("audience") or "general audience").strip()[:120],
            "tone": str(payload.get("tone") or "clear, modern and useful").strip()[:120],
            "style": str(payload.get("style") or "professional creator studio").strip()[:120],
            "hero_duration_seconds": _safe_int(payload.get("hero_duration_seconds"), 45, 15, 180),
            "short_duration_seconds": _safe_int(payload.get("short_duration_seconds"), 20, 8, 60),
        }
        response.headers["X-AI-Infinity-Feature"] = VERSION
        return _launch(objective, title, days, user_id, cfg, model_fn)

    @app.get("/infinity/studio/empire/{campaign_id}")
    def empire_get(campaign_id: str, request: Request, response: Response) -> Dict[str, Any]:
        user_id = _session_user(request, response)
        c = _campaign(campaign_id, user_id)
        if not c:
            raise HTTPException(404, "campaign not found")
        jobs = _jobs(campaign_id)
        try:
            from studio_ultimate import _get_project
            for job in jobs:
                pid = job.get("project_id")
                if pid:
                    p = _get_project(pid)
                    if p:
                        job["result"] = {"state":p.get("status"),"progress":p.get("progress"),"stage":p.get("stage"),"error":p.get("error")}
        except Exception:
            pass
        states = [(j.get("result") or {}).get("state") for j in jobs]
        states = [x for x in states if x]
        if states and all(x in {"completed","completed_with_qc_warnings"} for x in states):
            c["status"] = "completed"
        elif any(x == "failed" for x in states):
            c["status"] = "running_with_failures"
        elif states:
            c["status"] = "running"
        return _public(c, jobs)
