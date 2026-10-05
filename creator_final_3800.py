from __future__ import annotations

"""AI Infinity TARGET-2050.3800
FREE-LOCAL-FIRST-CREATOR-OS

A thin product layer over the existing real Creator Studio backend.
It adds durable creator DNA, worlds, experiments, a creation graph, reusable
material discovery and an auditable quality loop without replacing the
existing production pipeline.

Design rules:
- Core product has no billing or credit system.
- No Render-specific dependency.
- Existing /infinity/studio production APIs remain the execution engine.
- Creator metadata is stored per existing studio session user.
- Quality scores are deterministic workspace checks; they never impersonate
  semantic AI review.
"""

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request, Response

VERSION = "TARGET-2050.3800"
BUILD = "FREE-LOCAL-FIRST-CREATOR-OPERATING-SYSTEM"
FREE_FOREVER_MODE = True
BILLING_ENABLED = False
DB_LOCK = threading.RLock()
DATA_DIR = __import__("pathlib").Path(__import__("os").getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity"))
DB_PATH = __import__("pathlib").Path(__import__("os").getenv("AI_INFINITY_DB_PATH", str(DATA_DIR / "ai_infinity.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def clean(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def init_db() -> None:
    with DB_LOCK, connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS creator_dna_3800(
                user_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS creator_worlds_3800(
                world_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                objective TEXT NOT NULL,
                dna_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_creator_worlds_user_3800
              ON creator_worlds_3800(user_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS creator_experiments_3800(
                experiment_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                concept_json TEXT NOT NULL,
                chosen INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_creator_experiments_user_3800
              ON creator_experiments_3800(user_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS creator_events_3800(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                event TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_creator_events_user_3800
              ON creator_events_3800(user_id, created_at DESC);
            """
        )


init_db()


def studio_module():
    import studio_ultimate
    return studio_ultimate


def current_user(request: Request) -> str:
    studio = studio_module()
    getter = getattr(studio, "_get_user_id", None)
    if callable(getter):
        try:
            return str(getter(request))
        except Exception:
            pass
    # Defensive fallback for standalone loading.
    return "local_" + uuid.uuid5(uuid.NAMESPACE_URL, str(request.headers.get("user-agent", "unknown"))).hex[:20]


def ensure_session(request: Request, response: Optional[Response]) -> str:
    studio = studio_module()
    user_id = current_user(request)
    setter = getattr(studio, "_set_session", None)
    if callable(setter) and response is not None:
        try:
            setter(response, request, user_id)
        except Exception:
            pass
    return user_id


def record(user_id: str, event: str, payload: Dict[str, Any]) -> None:
    with DB_LOCK, connect() as c:
        c.execute(
            "INSERT INTO creator_events_3800(user_id,event,payload_json,created_at) VALUES(?,?,?,?)",
            (user_id, event, json_text(payload), now()),
        )


def load_dna(user_id: str) -> Dict[str, Any]:
    with DB_LOCK, connect() as c:
        row = c.execute(
            "SELECT profile_json FROM creator_dna_3800 WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if not row:
        return {
            "identity": "",
            "audience": "general audience",
            "tone": "clear, intelligent, human",
            "visual_style": "premium editorial",
            "languages": ["English"],
            "brand_voice": "",
            "default_platforms": ["youtube", "instagram", "tiktok"],
            "recurring_rules": [],
        }
    try:
        return json.loads(row["profile_json"] or "{}")
    except Exception:
        return {}


def save_dna(user_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = {
        "identity": clean(profile.get("identity"), 300),
        "audience": clean(profile.get("audience"), 300) or "general audience",
        "tone": clean(profile.get("tone"), 500) or "clear, intelligent, human",
        "visual_style": clean(profile.get("visual_style"), 500) or "premium editorial",
        "languages": [clean(x, 60) for x in (profile.get("languages") or ["English"]) if clean(x, 60)][:12],
        "brand_voice": clean(profile.get("brand_voice"), 1500),
        "default_platforms": [clean(x, 60).lower() for x in (profile.get("default_platforms") or []) if clean(x, 60)][:12],
        "recurring_rules": [clean(x, 300) for x in (profile.get("recurring_rules") or []) if clean(x, 300)][:30],
    }
    with DB_LOCK, connect() as c:
        c.execute(
            "INSERT INTO creator_dna_3800(user_id,profile_json,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET profile_json=excluded.profile_json,updated_at=excluded.updated_at",
            (user_id, json_text(profile), now()),
        )
    record(user_id, "creator_dna_saved", {"fields": sorted(profile.keys())})
    return profile


def list_worlds(user_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK, connect() as c:
        rows = c.execute(
            "SELECT * FROM creator_worlds_3800 WHERE user_id=? ORDER BY updated_at DESC LIMIT 100",
            (user_id,),
        ).fetchall()
    result = []
    for row in rows:
        x = dict(row)
        try:
            x["dna"] = json.loads(x.pop("dna_json") or "{}")
        except Exception:
            x["dna"] = {}
            x.pop("dna_json", None)
        result.append(x)
    return result


def list_experiments(user_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK, connect() as c:
        rows = c.execute(
            "SELECT * FROM creator_experiments_3800 WHERE user_id=? ORDER BY updated_at DESC LIMIT 100",
            (user_id,),
        ).fetchall()
    result = []
    for row in rows:
        x = dict(row)
        try:
            x["concept"] = json.loads(x.pop("concept_json") or "{}")
        except Exception:
            x["concept"] = {}
            x.pop("concept_json", None)
        x["chosen"] = bool(x.get("chosen"))
        result.append(x)
    return result


def quality_for_project(project_id: str) -> Dict[str, Any]:
    studio = studio_module()
    getter = getattr(studio, "_get_project", None)
    assets_fn = getattr(studio, "_asset_rows", None)
    if not callable(getter) or not callable(assets_fn):
        raise HTTPException(503, "creator backend inspection is unavailable")
    project = getter(project_id)
    if not project:
        raise HTTPException(404, "project not found")
    assets = assets_fn(project_id) or []
    asset_names = {str(x.get("path") or "").split("/")[-1] for x in assets}
    result = project.get("result") or {}
    artifacts = result.get("artifacts") if isinstance(result, dict) else {}
    if not isinstance(artifacts, dict):
        artifacts = {}

    checks = []
    def check(name: str, passed: bool, detail: str):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    status = str(project.get("status") or "")
    check("Production reaches a terminal state", status in {"completed", "completed_with_qc_warnings"}, status or "unknown")
    expected = {"final.mp4", "thumbnail.jpg", "captions.srt", "script.md", "manifest.json", "sources.json", "audio_master.mp3"}
    available = expected.intersection(asset_names.union(set(artifacts.keys())))
    check("Core creator package is present", len(available) >= 5, f"{len(available)}/{len(expected)} core artifacts detected")
    storyboard = result.get("storyboard") if isinstance(result, dict) else None
    check("Storyboard exists", bool(storyboard or result.get("chapters")), "Storyboard/chapters data is recorded" if (storyboard or result.get("chapters")) else "No storyboard data recorded")
    qc = result.get("qc") if isinstance(result, dict) else None
    if isinstance(qc, dict) and "passed" in qc:
        check("Media QC", bool(qc.get("passed")), "Backend media QC reported " + ("pass" if qc.get("passed") else "fail"))
    else:
        check("Media QC", "final.mp4" in asset_names, "Final media artifact is present; backend QC detail unavailable")

    passed = sum(1 for x in checks if x["passed"])
    score = round(100 * passed / max(1, len(checks)))
    recommendations = []
    if score < 100:
        recommendations.append("Open the Studio and repair the failed checks before distribution.")
    if "captions.srt" not in asset_names:
        recommendations.append("Generate or attach captions.")
    if "thumbnail.jpg" not in asset_names:
        recommendations.append("Create a publish-ready thumbnail.")
    return {
        "project_id": project_id,
        "score": score,
        "checks": checks,
        "recommendations": recommendations,
        "truthful": True,
        "assessment": "deterministic workspace checks, not a claim of human-level editorial judgment",
    }


def graph_for_user(user_id: str) -> Dict[str, Any]:
    studio = studio_module()
    projects_fn = getattr(studio, "_project_list", None)
    projects = projects_fn(user_id, 100) if callable(projects_fn) else []
    worlds = list_worlds(user_id)
    nodes = []
    edges = []
    for w in worlds:
        nodes.append({"id": w["world_id"], "type": "world", "label": w["name"]})
    for p in projects:
        pid = str(p.get("project_id") or p.get("id") or uid("project"))
        nodes.append({"id": pid, "type": "project", "label": p.get("title") or p.get("name") or "Project", "status": p.get("status")})
        objective = str(p.get("objective") or p.get("topic") or "")
        for w in worlds:
            if w["name"].lower() in objective.lower() or str(w.get("objective") or "").lower() in objective.lower():
                edges.append({"from": w["world_id"], "to": pid, "relation": "context"})
    return {"nodes": nodes[:220], "edges": edges[:400], "truthful": True}


def register(app: Any) -> None:
    @app.get("/infinity/3800/health")
    def health(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        return {
            "status": "healthy",
            "version": VERSION,
            "build": BUILD,
            "free_forever_mode": FREE_FOREVER_MODE,
            "billing_enabled": BILLING_ENABLED,
            "render_dependency": False,
            "creator_backend": True,
            "creation_dna": True,
            "world_engine": True,
            "creative_lab": True,
            "creation_graph": True,
            "quality_loop": True,
            "truthful": True,
            "user_id": uid_,
        }

    @app.get("/infinity/3800/capabilities")
    def capabilities(request: Request, response: Response):
        ensure_session(request, response)
        return {
            "version": VERSION,
            "core": [
                "intent_to_real_production",
                "real_video_audio_image_editing",
                "creation_dna",
                "worlds",
                "creative_lab",
                "quality_loop",
                "creation_graph",
                "versioned_projects",
                "multi_format_outputs",
                "local_free_first_processing",
            ],
            "free_policy": {
                "core_free": True,
                "credits": False,
                "billing": False,
                "paid_api_required": False,
                "render_required": False,
                "remote_ai_optional": True,
                "local_fallbacks": True,
            },
            "truthful": True,
        }

    @app.get("/infinity/3800/dna")
    def dna_get(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        return {"profile": load_dna(uid_), "truthful": True}

    @app.put("/infinity/3800/dna")
    async def dna_put(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        payload = await request.json()
        profile = save_dna(uid_, payload if isinstance(payload, dict) else {})
        return {"status": "saved", "profile": profile, "truthful": True}

    @app.get("/infinity/3800/worlds")
    def worlds_get(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        return {"worlds": list_worlds(uid_), "truthful": True}

    @app.post("/infinity/3800/worlds")
    async def worlds_post(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, "world object is required")
        name = clean(payload.get("name"), 180) or "Unnamed World"
        objective = clean(payload.get("objective"), 2500)
        world_id = uid("world")
        dna = payload.get("dna") if isinstance(payload.get("dna"), dict) else {}
        t = now()
        with DB_LOCK, connect() as c:
            c.execute(
                "INSERT INTO creator_worlds_3800(world_id,user_id,name,objective,dna_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (world_id, uid_, name, objective, json_text(dna), "active", t, t),
            )
        record(uid_, "world_created", {"world_id": world_id, "name": name})
        return {"status": "created", "world": list_worlds(uid_)[0], "truthful": True}

    @app.put("/infinity/3800/worlds/{world_id}")
    async def worlds_put(world_id: str, request: Request, response: Response):
        uid_ = ensure_session(request, response)
        payload = await request.json()
        with DB_LOCK, connect() as c:
            row = c.execute(
                "SELECT * FROM creator_worlds_3800 WHERE world_id=? AND user_id=?",
                (world_id, uid_),
            ).fetchone()
            if not row:
                raise HTTPException(404, "world not found")
            name = clean(payload.get("name"), 180) or row["name"]
            objective = clean(payload.get("objective"), 2500) or row["objective"]
            dna = payload.get("dna") if isinstance(payload.get("dna"), dict) else json.loads(row["dna_json"] or "{}")
            c.execute(
                "UPDATE creator_worlds_3800 SET name=?,objective=?,dna_json=?,updated_at=? WHERE world_id=? AND user_id=?",
                (name, objective, json_text(dna), now(), world_id, uid_),
            )
        record(uid_, "world_updated", {"world_id": world_id})
        return {"status": "updated", "world": next((x for x in list_worlds(uid_) if x["world_id"] == world_id), None), "truthful": True}

    @app.get("/infinity/3800/experiments")
    def experiments_get(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        return {"experiments": list_experiments(uid_), "truthful": True}

    @app.post("/infinity/3800/experiments")
    async def experiments_post(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        payload = await request.json()
        title = clean(payload.get("title"), 180) or "Creative Experiment"
        concept = payload if isinstance(payload, dict) else {}
        eid = uid("experiment")
        with DB_LOCK, connect() as c:
            c.execute(
                "INSERT INTO creator_experiments_3800(experiment_id,user_id,title,concept_json,chosen,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (eid, uid_, title, json_text(concept), int(bool(payload.get("chosen"))), now(), now()),
            )
        record(uid_, "experiment_created", {"experiment_id": eid, "title": title})
        return {"status": "created", "experiment_id": eid, "experiments": list_experiments(uid_), "truthful": True}

    @app.post("/infinity/3800/experiments/{experiment_id}/choose")
    def experiment_choose(experiment_id: str, request: Request, response: Response):
        uid_ = ensure_session(request, response)
        with DB_LOCK, connect() as c:
            row = c.execute(
                "SELECT * FROM creator_experiments_3800 WHERE experiment_id=? AND user_id=?",
                (experiment_id, uid_),
            ).fetchone()
            if not row:
                raise HTTPException(404, "experiment not found")
            c.execute(
                "UPDATE creator_experiments_3800 SET chosen=0,updated_at=? WHERE user_id=?",
                (now(), uid_),
            )
            c.execute(
                "UPDATE creator_experiments_3800 SET chosen=1,updated_at=? WHERE experiment_id=? AND user_id=?",
                (now(), experiment_id, uid_),
            )
        record(uid_, "experiment_chosen", {"experiment_id": experiment_id})
        return {"status": "chosen", "experiments": list_experiments(uid_), "truthful": True}

    @app.get("/infinity/3800/graph")
    def graph(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        return graph_for_user(uid_)

    @app.get("/infinity/3800/quality/{project_id}")
    def quality(project_id: str, request: Request, response: Response):
        ensure_session(request, response)
        return quality_for_project(project_id)

    @app.get("/infinity/3800/reusable")
    def reusable(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        studio = studio_module()
        projects_fn = getattr(studio, "_project_list", None)
        assets_fn = getattr(studio, "_asset_rows", None)
        projects = projects_fn(uid_, 100) if callable(projects_fn) else []
        rows = []
        core = {"final.mp4", "thumbnail.jpg", "captions.srt", "script.md", "manifest.json", "sources.json", "audio_master.mp3"}
        for p in projects:
            pid = str(p.get("project_id") or "")
            if not pid or not callable(assets_fn):
                continue
            for item in (assets_fn(pid) or []):
                name = str(item.get("path") or "").split("/")[-1]
                if name and name not in core:
                    rows.append({
                        "project_id": pid,
                        "project_title": p.get("title"),
                        "asset_name": name,
                        "kind": item.get("kind"),
                        "metadata": item.get("metadata") or {},
                        "download_url": f"/infinity/studio/project/{pid}/asset/{name}",
                    })
        return {"items": rows[:200], "truthful": True}

    @app.get("/infinity/3800/summary")
    def summary(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        studio = studio_module()
        projects_fn = getattr(studio, "_project_list", None)
        projects = projects_fn(uid_, 100) if callable(projects_fn) else []
        worlds = list_worlds(uid_)
        experiments = list_experiments(uid_)
        status_counts: Dict[str, int] = {}
        for p in projects:
            status = str(p.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "projects": len(projects),
            "worlds": len(worlds),
            "experiments": len(experiments),
            "status_counts": status_counts,
            "free_core": True,
            "render_dependency": False,
            "truthful": True,
        }

    @app.get("/infinity/3800/events")
    def events(request: Request, response: Response, limit: int = 50):
        uid_ = ensure_session(request, response)
        with DB_LOCK, connect() as c:
            rows = c.execute(
                "SELECT event,payload_json,created_at FROM creator_events_3800 WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (uid_, max(1, min(int(limit), 200))),
            ).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except Exception:
                payload = {}
            out.append({"event": r["event"], "payload": payload, "created_at": r["created_at"]})
        return {"events": out, "truthful": True}

