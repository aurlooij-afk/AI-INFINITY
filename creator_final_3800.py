from __future__ import annotations

"""AI Infinity TARGET-2050.3800
FREE-LOCAL-FIRST-CREATOR-OS

A thin product layer over the existing real Creator Studio backend.
It adds durable creator DNA, worlds, experiments, a creation graph, reusable
material discovery and an auditable quality loop without replacing the
existing production pipeline.

Design rules:
- Core product has no billing or credit system.
- No provider-specific deployment dependency.
- Existing /infinity/studio production APIs remain the execution engine.
- Creator metadata is stored per existing studio session user.
- Quality scores are deterministic workspace checks; they never impersonate
  semantic AI review.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
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
    if not storyboard and isinstance(project, dict):
        try:
            blueprint = project.get("blueprint_json") or project.get("blueprint") or {}
            if isinstance(blueprint, str):
                blueprint = json.loads(blueprint or "{}")
            if isinstance(blueprint, dict):
                storyboard = blueprint.get("chapters") or (blueprint.get("plan") or {}).get("chapters")
        except Exception:
            storyboard = None
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
    project_getter = getattr(studio, "_get_project", None)
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
        if callable(project_getter):
            try:
                full = project_getter(pid)
                raw = full.get("request_json") if isinstance(full, dict) else {}
                if isinstance(raw, str):
                    raw = json.loads(raw or "{}")
                if isinstance(raw, dict):
                    objective = " ".join([
                        objective,
                        str(raw.get("objective") or ""),
                        str(raw.get("topic") or ""),
                        str(raw.get("title") or ""),
                    ]).strip()
            except Exception:
                pass
        obj_low = objective.lower()
        for w in worlds:
            world_terms = " ".join([
                str(w.get("name") or ""),
                str(w.get("objective") or ""),
                json.dumps(w.get("dna") or {}, ensure_ascii=False),
            ]).lower()
            tokens = [t for t in re.findall(r"[a-z0-9]{4,}", world_terms) if len(t) >= 4]
            overlap = sum(1 for token in set(tokens) if token in obj_low)
            direct = str(w.get("name") or "").strip().lower() in obj_low
            if direct or overlap >= 2:
                edges.append({"from": w["world_id"], "to": pid, "relation": "context", "evidence": "project brief overlap"})
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

    @app.post("/infinity/3800/self-test")
    def self_test(request: Request, response: Response):
        uid_ = ensure_session(request, response)
        checks = []
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        checks.append({"name": "ffmpeg executable", "passed": bool(ffmpeg), "detail": ffmpeg or "ffmpeg not installed"})
        checks.append({"name": "ffprobe executable", "passed": bool(ffprobe), "detail": ffprobe or "ffprobe not installed"})
        checks.append({"name": "local speech engine", "passed": bool(espeak), "detail": espeak or "espeak-ng/espeak not installed"})
        data_dir = Path(os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity"))
        temp_dir = None
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            probe = data_dir / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            writable = probe.read_text(encoding="utf-8") == "ok"
            probe.unlink(missing_ok=True)
        except Exception as exc:
            writable = False
            checks.append({"name": "runtime storage error detail", "passed": False, "detail": str(exc)[:500]})
        checks.append({"name": "runtime storage writable", "passed": writable, "detail": str(data_dir)})
        media_ok = False
        media_detail = "media self-test not executed"
        if writable:
            try:
                temp_dir = Path(tempfile.mkdtemp(prefix="ai-infinity-self-test-", dir=str(data_dir)))
                out = temp_dir / "self_test.mp4"
                if not (ffmpeg and ffprobe):
                    media_detail = "media binaries unavailable"
                else:
                    proc = subprocess.run(
                        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-threads", "1",
                         "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1",
                         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000:duration=1",
                         "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                         "-c:a", "aac", "-b:a", "48k", str(out)],
                        capture_output=True, text=True, timeout=30,
                    )
                    if proc.returncode != 0:
                        raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg failed")[-800:])
                    if not out.is_file() or out.stat().st_size < 5000:
                        raise RuntimeError("generated media file is missing or too small")
                    probe_run = subprocess.run(
                        [ffprobe, "-v", "error", "-show_entries", "format=duration,size:stream=codec_type,codec_name",
                         "-of", "json", str(out)],
                        capture_output=True, text=True, timeout=15,
                    )
                    if probe_run.returncode != 0:
                        raise RuntimeError((probe_run.stderr or "ffprobe failed")[-800:])
                    try:
                        probe_json = json.loads(probe_run.stdout or "{}")
                    except Exception:
                        probe_json = {}
                    streams = probe_json.get("streams") or []
                    media_ok = (
                        float((probe_json.get("format") or {}).get("duration") or 0) > 0.5
                        and any(str(s.get("codec_type")) == "video" for s in streams)
                        and any(str(s.get("codec_type")) == "audio" for s in streams)
                    )
                    media_detail = json.dumps({"format": probe_json.get("format"), "streams": streams}, ensure_ascii=False)[:1600]
            except Exception as exc:
                media_detail = str(exc)[:1200]
            finally:
                if temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)
        checks.append({"name": "real local MP4 + audio generation", "passed": media_ok, "detail": media_detail})
        passed = all(bool(x["passed"]) for x in checks if x.get("name") != "runtime storage error detail")
        return {
            "status": "passed" if passed else "degraded",
            "version": VERSION,
            "build": BUILD,
            "local_only": True,
            "external_provider_required": False,
            "checks": checks,
            "passed_checks": sum(1 for x in checks if x["passed"]),
            "total_checks": len(checks),
            "user_id": uid_,
            "truthful": True,
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
        if not isinstance(payload, dict):
            raise HTTPException(422, "world object is required")
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
        if not isinstance(payload, dict):
            raise HTTPException(422, "experiment object is required")
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
        uid_ = ensure_session(request, response)
        studio = studio_module()
        getter = getattr(studio, "_get_project", None)
        project = getter(project_id) if callable(getter) else None
        if not project or str(project.get("user_id") or "") != str(uid_):
            raise HTTPException(404, "project not found")
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

