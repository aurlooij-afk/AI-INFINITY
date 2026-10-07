"""Targeted correctness patch for the professional creator timeline.

Keeps the generated scene master filename as the stable scene identity while
allowing the editor to reorder scenes. This prevents an ordered UI list from
silently swapping the underlying media files.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List
from fastapi import HTTPException, Request

VERSION = "TARGET-2050.PRO-CREATOR-TIMELINE.1"
BUILD_NOTE = "stable-scene-identity-v1"

def install(app: Any) -> None:
    import studio_ultimate as studio
    import professional_creator_v2 as base

    def owned(pid: str, request: Request):
        uid = studio._get_user_id(request)
        p = studio._get_project(pid)
        if not p or p.get("user_id") != uid:
            raise HTTPException(404, "project not found")
        return uid, p

    def normalize(pid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = studio._get_project(pid)
        if not p:
            raise HTTPException(404, "project not found")
        root = studio._project_dir(pid)
        rows = payload.get("scenes")
        if not isinstance(rows, list) or not rows:
            raise HTTPException(422, "timeline.scenes is required")
        out: List[Dict[str, Any]] = []
        seen = set()
        cursor = 0.0
        for pos, raw in enumerate(rows[:40], 1):
            if not isinstance(raw, dict):
                continue
            asset = Path(str(raw.get("source_asset") or "")).name
            if not asset:
                try:
                    source_index = int(raw.get("source_index") or raw.get("index"))
                    asset = f"scene_{source_index:02d}.mp4"
                except Exception:
                    raise HTTPException(422, "each timeline scene needs a source_asset")
            if not asset.startswith("scene_") or not asset.endswith(".mp4"):
                raise HTTPException(422, "timeline contains an invalid scene source")
            try:
                source_index = int(asset[6:8])
            except Exception:
                raise HTTPException(422, "timeline contains an invalid scene source")
            if source_index in seen:
                raise HTTPException(422, "timeline contains duplicate source scenes")
            seen.add(source_index)
            src = root / asset
            if not src.exists():
                raise HTTPException(409, f"missing scene source: {asset}")
            source_duration = float(studio.probe_duration(src))
            try:
                trim_start = max(0.0, float(raw.get("trim_start") or 0))
                trim_end = max(0.0, float(raw.get("trim_end") or 0))
            except Exception:
                trim_start = trim_end = 0.0
            usable = source_duration - trim_start - trim_end
            if usable <= 0.25:
                raise HTTPException(422, f"trim removes scene {source_index}")
            row = {
                "position": pos,
                "index": pos,
                "source_index": source_index,
                "source_asset": asset,
                "heading": base._safe(raw.get("heading") or f"Scene {source_index}", 240),
                "narration": base._safe(raw.get("narration"), 6000),
                "image_prompt": base._safe(raw.get("image_prompt"), 1000),
                "on_screen": base._safe(raw.get("on_screen"), 500),
                "trim_start": round(trim_start, 3),
                "trim_end": round(trim_end, 3),
                "duration": round(usable, 3),
                "source_duration": round(source_duration, 3),
                "enabled": bool(raw.get("enabled", True)),
                "start": round(cursor, 3),
                "end": round(cursor + usable, 3),
            }
            out.append(row)
            if row["enabled"]:
                cursor += usable
        if not out or not any(x["enabled"] for x in out):
            raise HTTPException(422, "timeline must contain at least one enabled scene")
        cursor = 0.0
        for row in out:
            row["start"] = round(cursor, 3)
            row["end"] = round(cursor + row["duration"], 3)
            if row["enabled"]:
                cursor += row["duration"]
        return {
            "version": VERSION,
            "project_id": pid,
            "title": p.get("title"),
            "fps": 30,
            "resolution": [1920, 1080],
            "tracks": [{"id": "video", "type": "video"}, {"id": "audio", "type": "audio"}],
            "duration_seconds": round(cursor, 3),
            "scenes": out,
            "truthful": True,
        }

    def save(pid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        tl = normalize(pid, payload)
        path = studio._project_dir(pid) / "timeline.json"
        path.write_text(json.dumps(tl, ensure_ascii=False, indent=2), encoding="utf-8")
        studio.register_artifact(pid, path, "application/json", {"editor": VERSION})
        studio.audit_event(pid, "professional_timeline_saved_corrected", {"scene_count": len(tl["scenes"])})
        return tl

    @app.get("/infinity/pro2/project/{pid}/timeline")
    def timeline(pid: str, request: Request):
        uid, p = owned(pid, request)
        path = studio._project_dir(pid) / "timeline.json"
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(stored, dict) and stored.get("scenes"):
                    stored["owner"] = uid
                    stored["truthful"] = True
                    return stored
            except Exception:
                pass
        out = base._timeline(studio, p)
        out["owner"] = uid
        return out

    @app.put("/infinity/pro2/project/{pid}/timeline")
    async def put_timeline(pid: str, request: Request):
        uid, p = owned(pid, request)
        if p.get("status") not in {"completed", "completed_with_qc_warnings"}:
            raise HTTPException(409, "timeline requires completed scene masters")
        payload = await request.json()
        payload["project_id"] = pid
        tl = save(pid, payload)
        return {"status": "saved", "timeline": tl, "truthful": True}

    @app.post("/infinity/pro2/project/{pid}/timeline/render")
    async def render_timeline(pid: str, request: Request):
        uid, p = owned(pid, request)
        if p.get("status") not in {"completed", "completed_with_qc_warnings"}:
            raise HTTPException(409, "timeline render requires completed scene masters")
        payload = await request.json()
        payload["project_id"] = pid
        tl = save(pid, payload)
        try:
            rendered = base._render(studio, pid, tl)
            variants = base._variants(studio, pid, rendered["master"])
            package = base._package(studio, pid)
            truth = base._truth(studio, studio._get_project(pid))
            return {
                "status": "completed" if truth.get("verified") else "completed_with_qc_review",
                "project_id": pid,
                "quality": rendered["quality"],
                "variants": variants,
                "package": package.name,
                "verified": bool(truth.get("verified")),
                "truth": truth,
                "download_url": f"/infinity/studio/project/{pid}/asset/final.mp4",
                "truthful": True,
            }
        except Exception as exc:
            studio._update_project(pid, status="failed", stage="timeline_render_failed", error=str(exc)[:800])
            studio.audit_event(pid, "professional_timeline_render_failed", {"error": str(exc)[:1000]})
            raise HTTPException(500, "professional timeline render failed: " + str(exc)[:500])
