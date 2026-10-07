from __future__ import annotations

"""
AI Infinity — production closure layer.

This module is intentionally additive. It closes the gaps that were visible in the
creator runtime without replacing the existing creator engine:
- strict professional production truth gate
- no-placeholder visual policy by default
- evidence-backed research gate
- artifact registry reconciliation
- correct ISO timestamps / asset metadata
- host-neutral share links (no stale Render links)
- restart recovery visibility
- durable-storage + backup truth
- provider reachability diagnostics
- publish boundary tied to verified output
- evidence-first UI banner
"""
import json
import os
import shutil
import sqlite3
import subprocess
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CLOSURE_VERSION = "TARGET-2050.3624"
STRICT_VISUAL_DEFAULT = "1"
STRICT_RESEARCH_DEFAULT = "1"
PUBLISH_GATE_DEFAULT = "1"
BACKUP_KEEP = max(1, min(30, int(os.getenv("AI_INFINITY_LOCAL_BACKUPS_KEEP", "7"))))
_INSTALLED = False
_LOCK = threading.RLock()


def _studio():
    import studio_ultimate
    return studio_ultimate


def _data_dir() -> Path:
    return Path(os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity")).resolve()


def _db_path() -> Path:
    s = _studio()
    return Path(getattr(s, "DB_PATH", _data_dir() / "ai_infinity.db"))


def _now() -> float:
    return time.time()


def _iso(ts: Any) -> Optional[str]:
    try:
        if ts is None or ts == "":
            return None
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _strict_visual() -> bool:
    return os.getenv("AI_INFINITY_REQUIRE_SOURCE_VISUALS", STRICT_VISUAL_DEFAULT).strip().lower() in {"1", "true", "yes", "on"}


def _strict_research() -> bool:
    return os.getenv("AI_INFINITY_REQUIRE_RESEARCH_EVIDENCE", STRICT_RESEARCH_DEFAULT).strip().lower() in {"1", "true", "yes", "on"}


def _publish_gate() -> bool:
    return os.getenv("AI_INFINITY_REQUIRE_VERIFIED_PUBLISH", PUBLISH_GATE_DEFAULT).strip().lower() in {"1", "true", "yes", "on"}


def _objective_media_qc(path: Path) -> Dict[str, Any]:
    """Independent media anomaly scan for prolonged black/frozen frames and clipping."""
    out: Dict[str, Any] = {
        "black_duration_max": 0.0,
        "freeze_duration_max": 0.0,
        "black_frame_ok": True,
        "freeze_ok": True,
        "audio_peak_db": None,
        "audio_clipping_ok": True,
        "scan_ok": True,
        "truthful": True,
    }
    if not path.is_file():
        out["scan_ok"] = False
        return out
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(path),
            "-vf", "blackdetect=d=0.8:pix_th=0.98,freezedetect=n=-60dB:d=1.5",
            "-an", "-f", "null", "-",
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        log = (p.stderr or "") + (p.stdout or "")
        black = [float(x) for x in re.findall(r"black_duration:([0-9]+(?:\\.[0-9]+)?)", log)]
        freeze = [float(x) for x in re.findall(r"freeze_duration:([0-9]+(?:\\.[0-9]+)?)", log)]
        out["black_duration_max"] = max(black) if black else 0.0
        out["freeze_duration_max"] = max(freeze) if freeze else 0.0
        out["black_frame_ok"] = out["black_duration_max"] < 1.5
        out["freeze_ok"] = out["freeze_duration_max"] < 2.5
        if p.returncode != 0:
            out["scan_ok"] = False
    except Exception as exc:
        out["scan_ok"] = False
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        log = (p.stderr or "") + (p.stdout or "")
        peaks = re.findall(r"max_volume:\s*(-?[0-9]+(?:\\.[0-9]+)?)\s*dB", log)
        if peaks:
            peak = float(peaks[-1])
            out["audio_peak_db"] = peak
            out["audio_clipping_ok"] = peak < -0.1
        elif p.returncode != 0:
            out["scan_ok"] = False
    except Exception as exc:
        out["scan_ok"] = False
        out["audio_scan_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return out

def _public_path(project_id: str, asset_name: str) -> str:
    from urllib.parse import quote
    return f"/infinity/studio/project/{quote(str(project_id))}/asset/{quote(Path(str(asset_name)).name)}"


def _require_project(project_id: str, user_id: str) -> Dict[str, Any]:
    s = _studio()
    p = s._get_project(project_id)
    if not p or p.get("user_id") != user_id:
        from fastapi import HTTPException
        raise HTTPException(404, "project not found")
    return p


def _parse_result(p: Dict[str, Any]) -> Dict[str, Any]:
    r = p.get("result_json")
    if isinstance(r, dict):
        return r
    try:
        return json.loads(r or "{}")
    except Exception:
        return {}



def _canonical_state(p: Dict[str, Any]) -> str:
    status = str(p.get("status") or "").lower()
    stage = str(p.get("stage") or "").lower()
    if status == "cancelled":
        return "CANCELLED"
    if status == "failed":
        return "FAILED"
    if status in {"completed", "completed_with_qc_warnings"}:
        return "VERIFIED" if status == "completed" else "QC_REVIEW_REQUIRED"
    mapping = [
        ("research", "RESEARCHING"),
        ("direction", "DIRECTING"),
        ("creative", "DIRECTING"),
        ("plan", "PLANNING"),
        ("production", "GENERATING"),
        ("visual", "GENERATING"),
        ("voice", "AUDIO"),
        ("caption", "CAPTIONS"),
        ("audio", "AUDIO"),
        ("assembly", "EDITING"),
        ("edit", "EDITING"),
        ("quality", "QC"),
        ("package", "PACKAGING"),
        ("delivery", "READY"),
    ]
    for needle, state in mapping:
        if needle in stage:
            return state
    if status in {"queued"}:
        return "QUEUED"
    if status in {"running", "producing"}:
        return "GENERATING"
    return "DRAFT"

def _parse_blueprint(p: Dict[str, Any]) -> Dict[str, Any]:
    b = p.get("blueprint_json")
    if isinstance(b, dict):
        return b
    try:
        return json.loads(b or "{}")
    except Exception:
        return {}


def _project_files(project_id: str) -> Dict[str, Path]:
    s = _studio()
    root = s._project_dir(project_id)
    return {p.name: p for p in root.iterdir() if p.is_file()} if root.exists() else {}


def _source_assets(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    s = _studio()
    try:
        rows = s._asset_rows(str(p["project_id"]))
    except Exception:
        rows = []
    return [r for r in rows if str(r.get("kind") or "").lower() == "visual"]


def _artifact_registry(project_id: str) -> Dict[str, Dict[str, Any]]:
    s = _studio()
    with s.DB_LOCK, s._connect() as c:
        rows = c.execute(
            "SELECT asset_name,sha256,size_bytes,media_type,metadata_json,created_at "
            "FROM studio_artifacts_3614 WHERE project_id=?",
            (project_id,),
        ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
        except Exception:
            d["metadata"] = {}
        out[str(d["asset_name"])] = d
    return out



def _write_production_evidence(project_id: str, truth: Optional[Dict[str, Any]] = None) -> None:
    """Persist human-auditable closure artifacts after every production run."""
    s = _studio()
    root = s._project_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)

    try:
        events = s._timeline_rows(project_id)
    except Exception:
        events = []
    timeline = {
        "version": CLOSURE_VERSION,
        "project_id": project_id,
        "events": events,
        "event_count": len(events),
        "truthful": True,
    }
    (root / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    registry = _artifact_registry(project_id)
    registry_payload = {
        "version": CLOSURE_VERSION,
        "project_id": project_id,
        "integrity": "sha256",
        "artifacts": sorted([dict(item) for item in registry.values()], key=lambda x: str(x.get("asset_name") or "")),
        "truthful": True,
    }
    (root / "asset_registry.json").write_text(json.dumps(registry_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Visual-rights evidence is deliberately conservative: the system records
    # the license/policy string supplied by the source adapter, but never calls
    # an asset legally cleared when the source did not provide enough evidence.
    rights = []
    for row in _source_assets(s._get_project(project_id) or {}):
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        source = str(meta.get("source") or "")
        source_url = str(meta.get("source_url") or "")
        license_name = str(meta.get("license") or "")
        fallback = bool(meta.get("fallback"))
        rights_state = "blocked_fallback" if fallback else ("evidence_present" if license_name or str(meta.get("rights_status") or "") else "unknown")
        rights.append({
            "asset": Path(str(row.get("path") or "")).name,
            "source": source,
            "source_url": source_url,
            "license": license_name,
            "creator": meta.get("creator"),
            "rights_state": rights_state,
            "rights_status": meta.get("rights_status"),
            "truthful": True,
        })
    rights_report = {
        "version": CLOSURE_VERSION,
        "project_id": project_id,
        "asset_count": len(rights),
        "evidence_present": sum(1 for x in rights if x["rights_state"] == "evidence_present"),
        "unknown": sum(1 for x in rights if x["rights_state"] == "unknown"),
        "fallback_blocked": sum(1 for x in rights if x["rights_state"] == "blocked_fallback"),
        "assets": rights,
        "policy": "Unknown rights are never asserted as cleared.",
        "truthful": True,
    }
    (root / "visual_rights.json").write_text(json.dumps(rights_report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if truth is not None:
        (root / "production_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
def _reconcile_artifacts(project_id: str) -> Dict[str, Any]:
    s = _studio()
    files = _project_files(project_id)
    existing = _artifact_registry(project_id)
    added = 0
    repaired = 0
    for name, path in files.items():
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        sha = s.file_sha256(path)
        row = existing.get(name)
        if not row:
            try:
                s.register_artifact(
                    project_id,
                    path,
                    {
                        ".mp4": "video/mp4",
                        ".zip": "application/zip",
                        ".jpg": "image/jpeg",
                        ".png": "image/png",
                        ".mp3": "audio/mpeg",
                        ".srt": "application/x-subrip",
                        ".json": "application/json",
                        ".md": "text/markdown",
                        ".xml": "application/xml",
                    }.get(path.suffix.lower(), "application/octet-stream"),
                    {"reconciled_by": CLOSURE_VERSION},
                )
                added += 1
            except Exception:
                pass
        elif str(row.get("sha256") or "") != sha or int(row.get("size_bytes") or 0) != path.stat().st_size:
            try:
                s.register_artifact(
                    project_id,
                    path,
                    row.get("media_type") or "application/octet-stream",
                    {"reconciled_by": CLOSURE_VERSION, "repaired_registry": True},
                )
                repaired += 1
            except Exception:
                pass
    return {"existing": len(existing), "files": len(files), "added": added, "repaired": repaired}


def _professional_truth(p: Dict[str, Any], reconcile: bool = True) -> Dict[str, Any]:
    s = _studio()
    project_id = str(p["project_id"])
    req = p.get("request_json")
    if isinstance(req, str):
        try:
            req = json.loads(req or "{}")
        except Exception:
            req = {}
    req = req or {}
    result = _parse_result(p)
    blueprint = _parse_blueprint(p)
    plan = blueprint.get("plan") if isinstance(blueprint.get("plan"), dict) else blueprint
    chapters = list((plan or {}).get("chapters") or [])
    files = _project_files(project_id)
    strict_visual = _strict_visual()
    strict_research = _strict_research()
    final = files.get("final.mp4")
    checks: Dict[str, Any] = {
        "project_status_completed": p.get("status") in {"completed", "completed_with_qc_warnings"},
        "final_exists": bool(final and final.is_file() and final.stat().st_size > 10000),
        "requested_duration": float(req.get("duration") or result.get("duration_seconds") or 0),
        "actual_duration": 0.0,
        "duration_within_1s": False,
        "video_stream": False,
        "audio_stream": False,
        "h264": False,
        "audio_codec_ok": False,
        "resolution_ok": False,
        "research_sources": int(((result.get("research") or {}).get("source_count") or 0)),
        "research_required_and_present": True,
        "required_artifacts_present": False,
        "artifact_registry_consistent": False,
        "visual_sources_present": False,
        "source_visual_policy_passed": True,
        "fact_check_present": "fact_check.json" in files,
        "provenance_present": "provenance.json" in files,
        "captions_present": "captions.srt" in files and files["captions.srt"].stat().st_size > 20,
        "thumbnail_present": "thumbnail.jpg" in files and files["thumbnail.jpg"].stat().st_size > 1000,
        "manifest_present": "manifest.json" in files,
    }
    warnings: List[str] = []
    failures: List[str] = []

    if final and final.exists():
        try:
            media_qc = _objective_media_qc(final)
            checks["black_frame_ok"] = bool(media_qc.get("black_frame_ok"))
            checks["freeze_ok"] = bool(media_qc.get("freeze_ok"))
            checks["audio_clipping_ok"] = bool(media_qc.get("audio_clipping_ok"))
            checks["media_anomaly_scan_ok"] = bool(media_qc.get("scan_ok"))
            checks["black_duration_max"] = float(media_qc.get("black_duration_max") or 0)
            checks["freeze_duration_max"] = float(media_qc.get("freeze_duration_max") or 0)
            checks["audio_peak_db"] = media_qc.get("audio_peak_db")
            if not checks["black_frame_ok"]:
                failures.append(f"prolonged black frame detected ({checks["black_duration_max"]:.2f}s)")
            if not checks["freeze_ok"]:
                failures.append(f"prolonged frozen frame detected ({checks["freeze_duration_max"]:.2f}s)")
            if not checks["audio_clipping_ok"]:
                failures.append(f"audio peak is too close to digital full scale ({checks["audio_peak_db"]} dBFS)")
        except Exception as exc:
            checks["media_anomaly_scan_ok"] = False
            failures.append(f"objective media QC failed: {type(exc).__name__}: {str(exc)[:220]}")

        try:
            probe = s.ffprobe_json(final)
            streams = probe.get("streams") or []
            v = next((x for x in streams if x.get("codec_type") == "video"), {})
            a = next((x for x in streams if x.get("codec_type") == "audio"), {})
            checks["video_stream"] = bool(v)
            checks["audio_stream"] = bool(a)
            checks["h264"] = str(v.get("codec_name") or "").lower() == "h264"
            checks["audio_codec_ok"] = str(a.get("codec_name") or "").lower() in {"aac", "mp3"}
            checks["actual_duration"] = round(float((probe.get("format") or {}).get("duration") or 0), 3)
            target = checks["requested_duration"]
            checks["duration_within_1s"] = target > 0 and abs(checks["actual_duration"] - target) <= 1.0
            width, height = int(v.get("width") or 0), int(v.get("height") or 0)
            fast = bool(getattr(s, "FAST_MODE", False))
            minimum = 1280 if fast else 1920
            checks["resolution_ok"] = max(width, height) >= minimum and min(width, height) >= 720
            checks["resolution"] = [width, height]
        except Exception as exc:
            failures.append(f"final media inspection failed: {type(exc).__name__}: {str(exc)[:220]}")

    required = {"final.mp4", "captions.srt", "script.md", "thumbnail.jpg", "sources.json", "manifest.json", "fact_check.json", "provenance.json", "visual_rights.json"}
    missing = sorted(required - set(files))
    checks["required_artifacts_present"] = not missing
    if missing:
        failures.append("missing required artifacts: " + ", ".join(missing))

    registry = _artifact_registry(project_id)
    if reconcile:
        _reconcile_artifacts(project_id)
        registry = _artifact_registry(project_id)
    reg_ok = True
    for name in sorted(required):
        path = files.get(name)
        row = registry.get(name)
        if not path or not row:
            reg_ok = False
            continue
        try:
            reg_ok = reg_ok and str(row.get("sha256") or "") == s.file_sha256(path) and int(row.get("size_bytes") or 0) == path.stat().st_size
        except Exception:
            reg_ok = False
    checks["artifact_registry_consistent"] = bool(reg_ok)
    if not reg_ok:
        failures.append("artifact registry does not match file bytes")

    try:
        timeline_rows = s._timeline_rows(project_id)
    except Exception:
        timeline_rows = []
    voice_fallback_events = [x for x in timeline_rows if str(x.get("event") or "") == "voice_fallback"]
    checks["voice_fallback_count"] = len(voice_fallback_events)
    checks["professional_voice_present"] = len(voice_fallback_events) == 0
    if voice_fallback_events and str(req.get("content_type") or "video").lower() == "video":
        failures.append(f"silent narration fallback detected in {len(voice_fallback_events)} scene(s)")

    visual_rows = _source_assets(p)
    checks["visual_source_count"] = len(visual_rows)
    checks["visual_sources_present"] = len(visual_rows) >= max(1, len(chapters) if chapters else 1)
    fallback_rows = [x for x in visual_rows if bool((x.get("metadata") or {}).get("fallback"))]
    checks["fallback_visual_count"] = len(fallback_rows)
    checks["source_visual_policy_passed"] = (not fallback_rows) if strict_visual else True
    if fallback_rows and strict_visual:
        failures.append(f"placeholder/fallback visuals blocked: {len(fallback_rows)} scene assets")
    elif fallback_rows:
        warnings.append(f"{len(fallback_rows)} scenes used local original-motion fallback")

    unique_visual_hashes = set()
    for row in visual_rows:
        try:
            path = Path(str(row.get("path") or ""))
            if path.is_file():
                unique_visual_hashes.add(s.file_sha256(path))
        except Exception:
            pass
    required_unique = min(len(visual_rows), max(1, int((len(visual_rows) * 0.6) + 0.999)))
    checks["unique_visual_hashes"] = len(unique_visual_hashes)
    checks["visual_diversity_ok"] = len(unique_visual_hashes) >= required_unique
    if len(visual_rows) > 1 and not checks["visual_diversity_ok"]:
        failures.append("visual diversity check failed: the edit reuses too few distinct visual assets")

    rights_path = files.get("visual_rights.json")
    rights_data = {}
    if rights_path and rights_path.exists():
        try:
            rights_data = json.loads(rights_path.read_text(encoding="utf-8"))
        except Exception:
            rights_data = {}
    rights_assets = list(rights_data.get("assets") or []) if isinstance(rights_data, dict) else []
    rights_unknown = sum(1 for x in rights_assets if str(x.get("rights_state") or "") == "unknown")
    checks["visual_rights_evidence_present"] = bool(rights_assets) and rights_unknown == 0
    checks["visual_rights_unknown_count"] = rights_unknown
    if rights_unknown:
        failures.append(f"visual-rights evidence missing for {rights_unknown} visual asset(s)")

    fact_path = files.get("fact_check.json")
    fact_data = {}
    if fact_path and fact_path.exists():
        try:
            fact_data = json.loads(fact_path.read_text(encoding="utf-8"))
        except Exception:
            fact_data = {}
    brief_for_fact_policy = " ".join(str(req.get(k) or "") for k in ("objective", "topic", "title")).lower()
    fact_sensitive = any(x in brief_for_fact_policy for x in ("latest", "current", "news", "report", "documentary", "educational", "tutorial", "science", "history", "finance", "medical", "health", "research", "facts", "explainer"))
    checks["factual_review_auto_publish_safe"] = bool(fact_data.get("auto_publish_safe")) if isinstance(fact_data, dict) else False
    checks["factual_review_required"] = fact_sensitive
    checks["factual_claim_count"] = int(fact_data.get("claim_count") or 0) if isinstance(fact_data, dict) else 0
    if fact_sensitive and not checks["factual_review_auto_publish_safe"]:
        failures.append("factual claim review is not auto-publish safe; unresolved claims require review")

    source_count = checks["research_sources"]
    checks["research_required_and_present"] = (source_count > 0) if strict_research else True
    if strict_research and source_count <= 0:
        failures.append("research produced no usable evidence sources")

    # If the request explicitly asks for current/latest/trends/news, require a
    # recent source record rather than treating a stale general article as fresh.
    brief = " ".join(str(req.get(k) or "") for k in ("objective", "topic", "title")).lower()
    freshness_requested = any(x in brief for x in ("latest", "current", "today", "recent", "trend", "news", "2026"))
    fresh_ok = True
    if freshness_requested:
        fresh_ok = False
        research = (blueprint.get("research") or {}) if isinstance(blueprint, dict) else {}
        for src in (research.get("sources") or []):
            published = str(src.get("published_at") or src.get("published") or src.get("timestamp") or "")
            if published:
                try:
                    dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except Exception:
                    try:
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(published)
                    except Exception:
                        dt = None
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age = max(0, (_now() - dt.timestamp()) / 86400)
                    if age <= 45:
                        fresh_ok = True
                        break
    checks["fresh_evidence_ok"] = fresh_ok
    if freshness_requested and not fresh_ok:
        failures.append("brief requires fresh/current evidence but no recent source timestamp was verified")

    # The persisted QC is evidence, but re-check its critical truth here.
    persisted_qc = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    checks["persisted_qc_passed"] = bool(persisted_qc.get("passed"))
    if not checks["persisted_qc_passed"] and p.get("status") == "completed":
        failures.append("project reports completed while persisted QC is not passed")

    verified = bool(
        checks["project_status_completed"]
        and checks["final_exists"]
        and checks["duration_within_1s"]
        and checks["video_stream"]
        and checks["audio_stream"]
        and checks["h264"]
        and checks["audio_codec_ok"]
        and checks["resolution_ok"]
        and checks["required_artifacts_present"]
        and checks["artifact_registry_consistent"]
        and checks["visual_sources_present"]
        and checks["source_visual_policy_passed"]
        and checks["visual_diversity_ok"]
        and checks["professional_voice_present"]
        and checks["visual_rights_evidence_present"]
        and checks["research_required_and_present"]
        and checks["fresh_evidence_ok"]
        and (checks["factual_review_auto_publish_safe"] if checks["factual_review_required"] else True)
        and checks["fact_check_present"]
        and checks["provenance_present"]
        and checks["captions_present"]
        and checks["thumbnail_present"]
        and checks["manifest_present"]
        and checks["media_anomaly_scan_ok"]
        and checks["black_frame_ok"]
        and checks["freeze_ok"]
        and checks["audio_clipping_ok"]
        and checks["persisted_qc_passed"]
        and not failures
    )
    return {
        "version": CLOSURE_VERSION,
        "truthful": True,
        "verified": verified,
        "state": "VERIFIED" if verified else ("FAILED" if p.get("status") == "failed" else "REVIEW_REQUIRED"),
        "strict_visual_policy": strict_visual,
        "strict_research_policy": strict_research,
        "publish_gate": _publish_gate(),
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "missing_artifacts": missing,
        "requested": {"duration_seconds": checks["requested_duration"], "content_type": req.get("content_type") or "video", "quality_preset": req.get("quality_preset") or "balanced"},
        "canonical_state": _canonical_state(p),
        "artifact_paths": {name: _public_path(project_id, name) for name in sorted(files)},
        "timestamps": {
            "created_at": _iso(p.get("created_at")),
            "updated_at": _iso(p.get("updated_at")),
            "verified_at": _iso(_now()) if verified else None,
        },
        "reconciliation": _reconcile_artifacts(project_id) if reconcile else None,
    }


def _gate_error_report(project_id: str, truth: Dict[str, Any]) -> str:
    reasons = list(truth.get("failures") or [])
    if not reasons:
        reasons = ["professional verification did not pass"]
    return "Professional production gate blocked finalization: " + "; ".join(str(x) for x in reasons[:6])


def _patch_functions():
    global _INSTALLED
    if _INSTALLED:
        return
    s = _studio()

    # Block fallback visuals before a final movie can be claimed.
    original_acquire = s.acquire_scene_asset
    if not getattr(original_acquire, "__aii_closure_wrapped__", False):
        def acquire_scene_asset_closure(scene, outdir, index, prefer_motion=False, duration=6.0):
            result = original_acquire(scene, outdir, index, prefer_motion=prefer_motion, duration=duration)
            if _strict_visual() and bool((result or {}).get("fallback")):
                raise RuntimeError("professional visual gate: remote/public source unavailable; fallback visual is not eligible for final delivery")
            return result
        acquire_scene_asset_closure.__aii_closure_wrapped__ = True
        s.acquire_scene_asset = acquire_scene_asset_closure

    # Make library data first-class: include filename, size and verified availability.
    original_projects = s._project_list
    if not getattr(original_projects, "__aii_closure_wrapped__", False):
        def project_list_closure(user_id: str, limit: int = 50):
            rows = original_projects(user_id, limit)
            out = []
            for r in rows:
                d = dict(r)
                d["created_at_iso"] = _iso(d.get("created_at"))
                d["updated_at_iso"] = _iso(d.get("updated_at"))
                out.append(d)
            return out
        project_list_closure.__aii_closure_wrapped__ = True
        s._project_list = project_list_closure

    original_library = s._library_rows
    if not getattr(original_library, "__aii_closure_wrapped__", False):
        def library_rows_closure(user_id: str, limit: int = 200):
            rows = original_library(user_id, limit)
            out = []
            for r in rows:
                d = dict(r)
                pid = str(d.get("project_id") or "")
                name = str(d.get("asset_name") or d.get("name") or "")
                path = _project_files(pid).get(name)
                d["name"] = name
                d["size_bytes"] = int(path.stat().st_size) if path and path.exists() else 0
                d["available"] = bool(path and path.exists())
                d["download_url"] = _public_path(pid, name) if name else None
                d["created_at_iso"] = _iso(d.get("created_at"))
                out.append(d)
            return out
        library_rows_closure.__aii_closure_wrapped__ = True
        s._library_rows = library_rows_closure

    # Project responses expose ISO timestamps and usable asset names.
    original_public = s._project_public
    if not getattr(original_public, "__aii_closure_wrapped__", False):
        def project_public_closure(p):
            out = original_public(p)
            out["created_at_iso"] = _iso(p.get("created_at"))
            out["updated_at_iso"] = _iso(p.get("updated_at"))
            rows = s._asset_rows(p["project_id"])
            assets = []
            for x in rows:
                d = dict(x)
                d["name"] = Path(str(d.get("path") or "")).name
                d["size_bytes"] = int(Path(str(d.get("path") or "")).stat().st_size) if Path(str(d.get("path") or "")).is_file() else 0
                d["available"] = bool(Path(str(d.get("path") or "")).is_file())
                d["download_url"] = _public_path(p["project_id"], d["name"])
                d.pop("path", None)
                assets.append(d)
            out["assets"] = assets
            return out
        project_public_closure.__aii_closure_wrapped__ = True
        s._project_public = project_public_closure

    # Eliminate stale absolute Render links from new share URLs. Relative URLs
    # stay correct on Render, Blitz, local Docker and custom domains.
    original_share = s.share_url
    if not getattr(original_share, "__aii_closure_wrapped__", False):
        def share_url_closure(project_id: str, asset: str, user_id: str, ttl: int = 86400):
            from urllib.parse import quote
            exp = int(s.now()) + max(300, min(int(ttl), 604800))
            token = s._share_token(user_id, project_id, asset, exp)
            return f"/infinity/studio/share/{quote(str(project_id))}/{quote(Path(asset).name)}?token={quote(token)}"
        share_url_closure.__aii_closure_wrapped__ = True
        s.share_url = share_url_closure

    # Fix analytics so it uses the full project request/result rows rather than
    # the compact project listing that omits request_json/result_json.
    original_analytics = s._analytics_3621
    if not getattr(original_analytics, "__aii_closure_wrapped__", False):
        def analytics_closure(user_id: str):
            with s.DB_LOCK, s._connect() as c:
                projects = [dict(r) for r in c.execute(
                    "SELECT project_id,title,status,request_json,result_json,created_at,updated_at "
                    "FROM studio_projects_3610 WHERE user_id=? ORDER BY updated_at DESC LIMIT 500",
                    (user_id,),
                ).fetchall()]
                pub_counts = {f"{r[0]}:{r[1]}": int(r[2]) for r in c.execute(
                    "SELECT provider,status,COUNT(*) FROM studio_publications_3610 WHERE user_id=? GROUP BY provider,status",
                    (user_id,),
                ).fetchall()}
                asset_count = int(c.execute(
                    "SELECT COUNT(*) FROM studio_assets_3610 a JOIN studio_projects_3610 p ON p.project_id=a.project_id WHERE p.user_id=?",
                    (user_id,),
                ).fetchone()[0] or 0)
                artifact_count = int(c.execute(
                    "SELECT COUNT(*) FROM studio_artifacts_3614 a JOIN studio_projects_3610 p ON p.project_id=a.project_id WHERE p.user_id=?",
                    (user_id,),
                ).fetchone()[0] or 0)
            status_counts: Dict[str, int] = {}
            type_counts: Dict[str, int] = {}
            quality_scores: List[float] = []
            production_seconds: List[float] = []
            for p in projects:
                status = str(p.get("status") or "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
                try: req = json.loads(p.get("request_json") or "{}")
                except Exception: req = {}
                try: res = json.loads(p.get("result_json") or "{}")
                except Exception: res = {}
                ct = str(req.get("content_type") or "video")
                type_counts[ct] = type_counts.get(ct, 0) + 1
                q = res.get("quality") if isinstance(res.get("quality"), dict) else {}
                try:
                    if q.get("score") is not None: quality_scores.append(float(q["score"]))
                except Exception: pass
                if status in {"completed","completed_with_qc_warnings"}:
                    production_seconds.append(max(0.0, float(p.get("updated_at") or 0)-float(p.get("created_at") or 0)))
            total = len(projects)
            completed = status_counts.get("completed",0) + status_counts.get("completed_with_qc_warnings",0)
            return {
                "projects_total": total,
                "completed": completed,
                "active": status_counts.get("running",0) + status_counts.get("queued",0),
                "failed": status_counts.get("failed",0),
                "completion_rate": round(completed/max(1,total),4),
                "status_counts": status_counts,
                "content_type_counts": type_counts,
                "publication_counts": pub_counts,
                "asset_count": asset_count,
                "artifact_count": artifact_count,
                "average_quality": round(sum(quality_scores)/len(quality_scores),4) if quality_scores else None,
                "average_production_seconds": round(sum(production_seconds)/len(production_seconds),2) if production_seconds else None,
                "fast_mode": bool(getattr(s, "FAST_MODE", False)),
                "latency_profile": "turbo-local" if getattr(s, "FAST_MODE", False) else "balanced",
                "truthful": True,
            }
        analytics_closure.__aii_closure_wrapped__ = True
        s._analytics_3621 = analytics_closure

    # Any publish path must use the same verified-output gate.
    original_youtube = s.youtube_upload
    if not getattr(original_youtube, "__aii_closure_wrapped__", False):
        def youtube_upload_closure(user_id: str, video: Path, metadata: Dict[str, Any]):
            project_id = str(metadata.get("project_id") or "")
            if _publish_gate() and project_id:
                p = s._get_project(project_id)
                truth = _professional_truth(p, reconcile=True) if p else None
                if not truth or not truth.get("verified"):
                    return {
                        "status": "blocked_by_quality_gate",
                        "provider": "youtube",
                        "project_id": project_id,
                        "truthful": True,
                        "error": _gate_error_report(project_id, truth or {}),
                    }
            return original_youtube(user_id, video, metadata)
        youtube_upload_closure.__aii_closure_wrapped__ = True
        s.youtube_upload = youtube_upload_closure

    original_webhook = s.webhook_publish
    if not getattr(original_webhook, "__aii_closure_wrapped__", False):
        def webhook_publish_closure(user_id: str, video: Path, metadata: Dict[str, Any]):
            project_id = str(metadata.get("project_id") or "")
            if _publish_gate() and project_id:
                p = s._get_project(project_id)
                truth = _professional_truth(p, reconcile=True) if p else None
                if not truth or not truth.get("verified"):
                    return {
                        "status": "blocked_by_quality_gate",
                        "provider": "webhook",
                        "project_id": project_id,
                        "truthful": True,
                        "error": _gate_error_report(project_id, truth or {}),
                    }
            return original_webhook(user_id, video, metadata)
        webhook_publish_closure.__aii_closure_wrapped__ = True
        s.webhook_publish = webhook_publish_closure

    # Run the existing resumable/checkpointed engine, then enforce the truth gate.
    original_run = s.run_project
    if not getattr(original_run, "__aii_closure_wrapped__", False):
        def run_project_closure(project_id: str, model_fn: Optional[Any]):
            original_run(project_id, model_fn)
            try:
                p = s._get_project(project_id)
                if not p:
                    return
                try:
                    _write_production_evidence(project_id, None)
                    _reconcile_artifacts(project_id)
                except Exception:
                    pass
                truth = _professional_truth(p, reconcile=True)
                try:
                    _write_production_evidence(project_id, truth)
                    _reconcile_artifacts(project_id)
                except Exception:
                    pass
                # Re-read after evidence files are created so the persisted truth
                # artifact is itself backed by the same final state we report.
                if p.get("status") in {"completed", "completed_with_qc_warnings"} and not truth.get("verified"):
                    error = _gate_error_report(project_id, truth)
                    s.audit_event(project_id, "professional_gate_blocked", {"failures": truth.get("failures"), "warnings": truth.get("warnings")})
                    s._update_project(
                        project_id,
                        status="failed",
                        error=error[:1200],
                        stage="quality_blocked",
                        progress=min(99.0, float(p.get("progress") or 99)),
                        result_json=s.jdump({
                            "status": "failed",
                            "truthful": True,
                            "professional_gate": truth,
                            "blocked": True,
                            "reason": error[:1200],
                        }),
                    )
            finally:
                try:
                    _reconcile_artifacts(project_id)
                except Exception:
                    pass
        run_project_closure.__aii_closure_wrapped__ = True
        s.run_project = run_project_closure

    _recover_incomplete_jobs()
    _install_db_pragmas()
    _INSTALLED = True


def _recover_incomplete_jobs() -> int:
    s = _studio()
    changed = 0
    # Do not wait for the old five-minute lease on a process startup: there is
    # no live worker from the previous process anymore.
    with s.DB_LOCK, s._connect() as c:
        rows = c.execute(
            "SELECT project_id,status FROM studio_projects_3610 "
            "WHERE status IN ('running','producing')"
        ).fetchall()
        for r in rows:
            c.execute(
                "UPDATE studio_projects_3610 SET status='queued',stage='queued',updated_at=? "
                "WHERE project_id=? AND status IN ('running','producing')",
                (_now(), r["project_id"]),
            )
            try:
                s.audit_event(str(r["project_id"]), "startup_recovery_requeued", {"previous_status": r["status"]})
            except Exception:
                pass
            changed += 1
    return changed



_BACKUP_THREAD_STARTED = False
_BACKUP_THREAD_LOCK = threading.Lock()

def _ensure_backup_scheduler() -> None:
    global _BACKUP_THREAD_STARTED
    with _BACKUP_THREAD_LOCK:
        if _BACKUP_THREAD_STARTED or str(_data_dir()).startswith("/tmp"):
            return
        _BACKUP_THREAD_STARTED = True
        def loop():
            while True:
                try:
                    _backup_snapshot(False)
                except Exception:
                    pass
                # Six hours: frequent enough for project metadata recovery, while
                # keeping storage overhead bounded. Media backups remain explicit.
                time.sleep(6 * 3600)
        threading.Thread(target=loop, name="ai-infinity-local-backup", daemon=True).start()

def _install_db_pragmas() -> None:
    s = _studio()
    try:
        with s.DB_LOCK, s._connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass



def _upload_backup_offsite(target: Path) -> Dict[str, Any]:
    """Upload an explicit backup bundle to a user-configured HTTPS endpoint."""
    endpoint = os.getenv("AI_INFINITY_BACKUP_WEBHOOK", "").strip()
    if not endpoint:
        return {"configured": False, "uploaded": False}
    if not endpoint.lower().startswith("https://"):
        return {"configured": True, "uploaded": False, "error": "backup webhook must use HTTPS"}
    import zipfile as _zipfile
    bundle = target / "offsite-backup.zip"
    try:
        with _zipfile.ZipFile(bundle, "w", compression=_zipfile.ZIP_DEFLATED) as z:
            for path in target.rglob("*"):
                if path.is_file() and path.name != bundle.name:
                    z.write(path, arcname=path.relative_to(target).as_posix())
        data = bundle.read_bytes()
        digest = __import__("hashlib").sha256(data).hexdigest()
        headers = {
            "Content-Type": "application/zip",
            "Content-Length": str(len(data)),
            "X-AI-Infinity-Backup": bundle.name,
            "X-AI-Infinity-SHA256": digest,
        }
        secret = os.getenv("AI_INFINITY_BACKUP_WEBHOOK_SECRET", "").strip()
        if secret:
            headers["X-AI-Infinity-Backup-Secret"] = secret
        req = _studio().URLRequest(endpoint, data=data, headers=headers, method="POST")
        with _studio().urlopen(req, timeout=120) as response:
            status = int(getattr(response, "status", 200))
        return {"configured": True, "uploaded": 200 <= status < 300, "http_status": status, "sha256": digest, "bytes": len(data)}
    except Exception as exc:
        return {"configured": True, "uploaded": False, "error": f"{type(exc).__name__}: {str(exc)[:400]}"}

def _backup_snapshot(include_media: bool = False) -> Dict[str, Any]:
    s = _studio()
    root = _data_dir() / "creator_backups"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / stamp
    target.mkdir(parents=True, exist_ok=True)
    db_target = target / "ai_infinity.db"
    with s.DB_LOCK, s._connect() as src:
        dest = sqlite3.connect(str(db_target))
        try:
            src.backup(dest)
        finally:
            dest.close()
    manifest = {
        "version": CLOSURE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(_data_dir()),
        "db": db_target.name,
        "projects": [],
        "media_included": bool(include_media),
        "backup_webhook_configured": bool(os.getenv("AI_INFINITY_BACKUP_WEBHOOK", "").strip()),
        "truthful": True,
    }
    with s.DB_LOCK, s._connect() as c:
        projects = c.execute(
            "SELECT project_id,title,status,created_at,updated_at FROM studio_projects_3610 ORDER BY updated_at DESC"
        ).fetchall()
    for row in projects:
        pid = str(row["project_id"])
        entry = {
            "project_id": pid,
            "title": row["title"],
            "status": row["status"],
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }
        if include_media:
            src_root = s._project_dir(pid)
            dst_root = target / "projects" / s.safe_name(pid)
            dst_root.mkdir(parents=True, exist_ok=True)
            total = 0
            for path in src_root.iterdir() if src_root.exists() else []:
                if path.is_file():
                    try:
                        shutil.copy2(path, dst_root / path.name)
                        total += path.stat().st_size
                    except Exception:
                        pass
            entry["media_bytes"] = total
        manifest["projects"].append(entry)
    (target / "backup-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    offsite = _upload_backup_offsite(target)
    manifest["offsite"] = offsite
    (target / "backup-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    snapshots = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)
    for old in snapshots[BACKUP_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
    return {"status": "created", "path": str(target), "manifest": str(target / "backup-manifest.json"), "media_included": bool(include_media), "backup_webhook_configured": manifest["backup_webhook_configured"], "offsite": offsite, "truthful": True}


def _readiness(user_id: str, request: Any) -> Dict[str, Any]:
    s = _studio()
    strict_visual = _strict_visual()
    strict_research = _strict_research()
    root = _data_dir()
    local_ffmpeg = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    local_tts = bool(shutil.which("espeak-ng") or shutil.which("espeak"))
    persistence = not str(root).startswith("/tmp")
    configured = []
    for name, env, label in [
        ("OpenAI", "OPENAI_API_KEY", "AI video / Sora"),
        ("Hugging Face", "HF_TOKEN", "AI text / image / video adapters"),
        ("Runway", "RUNWAYML_API_SECRET", "AI video adapter"),
        ("Google/YouTube", "AI_INFINITY_GOOGLE_CLIENT_ID", "YouTube publishing"),
    ]:
        present = bool(os.getenv(env, "").strip())
        configured.append({"provider": name, "capability": label, "credential_present": present, "runtime_status": "configured" if present else "not_configured"})
    conn = s._connections(user_id)
    youtube = next((x for x in conn if x.get("provider") == "youtube" and x.get("active")), None)
    if youtube:
        configured.append({"provider": "YouTube OAuth", "capability": "publishing", "credential_present": True, "runtime_status": "connected"})
    host = ""
    try:
        host = str(request.base_url).rstrip("/")
    except Exception:
        host = ""
    return {
        "version": CLOSURE_VERSION,
        "host": host,
        "storage": {
            "data_dir": str(root),
            "persistent_runtime": persistence,
            "local_backups": bool((root / "creator_backups").exists()),
            "offsite_backup_configured": bool(os.getenv("AI_INFINITY_BACKUP_WEBHOOK", "").strip()),
            "truth_note": "Persistent files depend on the deployment's storage contract; local backups are not offsite backups.",
        },
        "production_policy": {
            "strict_visuals": strict_visual,
            "strict_research": strict_research,
            "verified_publish_required": _publish_gate(),
            "fallback_final_delivery": not strict_visual,
            "checkpoint_resume": True,
            "one_command": True,
        },
        "local_capabilities": {
            "ffmpeg": local_ffmpeg,
            "tts": local_tts,
            "captions": True,
            "timeline": True,
            "persistent_queue": True,
        },
        "connections": configured,
        "worker": {
            "started": bool(getattr(s, "WORKER_STARTED", False)),
            "queue_lease_seconds": int(getattr(s, "QUEUE_LEASE_SECONDS", 300)),
            "max_retries": int(getattr(s, "MAX_RETRIES", 2)),
        },
        "truthful": True,
    }


def _provider_diag() -> Dict[str, Any]:
    # This endpoint intentionally separates credential presence from reachability.
    # It never labels a provider "connected" merely because an environment variable exists.
    s = _studio()
    results: List[Dict[str, Any]] = []

    local_checks = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "espeak": bool(shutil.which("espeak-ng") or shutil.which("espeak")),
    }
    results.append({"provider": "local-media", "configured": True, "reachable": all(local_checks.values()), "authenticated": True, "model_available": True, "generation_successful": None, "artifact_valid": None, "checks": local_checks})

    def probe(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 6):
        try:
            req = s.URLRequest(url, headers={"User-Agent": f"AI-Infinity/{CLOSURE_VERSION}", **(headers or {})}, method="GET")
            with s.urlopen(req, timeout=timeout) as r:
                return True, int(getattr(r, "status", 200))
        except Exception as exc:
            return False, f"{type(exc).__name__}: {str(exc)[:180]}"

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key:
        ok, detail = probe("https://api.openai.com/v1/models", {"Authorization": f"Bearer {openai_key}"})
        results.append({"provider": "OpenAI", "configured": True, "reachable": ok, "authenticated": ok, "model_available": ok, "generation_successful": None, "artifact_valid": None, "detail": detail})
    else:
        results.append({"provider": "OpenAI", "configured": False, "reachable": False, "authenticated": False, "model_available": False, "generation_successful": None, "artifact_valid": None})

    hf_key = os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()
    if hf_key:
        ok, detail = probe("https://huggingface.co/api/whoami-v2", {"Authorization": f"Bearer {hf_key}"})
        results.append({"provider": "Hugging Face", "configured": True, "reachable": ok, "authenticated": ok, "model_available": ok, "generation_successful": None, "artifact_valid": None, "detail": detail})
    else:
        results.append({"provider": "Hugging Face", "configured": False, "reachable": False, "authenticated": False, "model_available": False, "generation_successful": None, "artifact_valid": None})

    return {"version": CLOSURE_VERSION, "providers": results, "truthful": True}


def _ui_enhancement(html: str) -> str:
    if "aii-professional-closure-3624" in html:
        return html
    script = r"""
<style id="aii-professional-closure-3624">
.aiiClosure{margin:12px 0;padding:14px 16px;border:1px solid #273244;border-radius:16px;background:linear-gradient(135deg,rgba(17,24,39,.96),rgba(13,17,24,.96));box-shadow:0 14px 40px rgba(0,0,0,.20)}
.aiiClosure .head{display:flex;align-items:center;justify-content:space-between;gap:12px}.aiiClosure b{font-weight:800}.aiiClosure .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:10px}.aiiClosure .cell{padding:10px;border:1px solid #202a38;border-radius:12px;background:#0b1017}.aiiClosure .v{font-size:12px;margin-top:3px;color:#dbe5f5}.aiiClosure .pass{color:#b7f7d0}.aiiClosure .warn{color:#f8d477}.aiiClosure .bad{color:#ff9aac}@media(max-width:900px){.aiiClosure .grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
<script>
(function(){
  if(window.__aiiProfessionalClosure3624){return;}
  window.__aiiProfessionalClosure3624=true;
  function gate(v){return v?"PASS":"BLOCKED";}
  async function showGate(){
    try{
      var r=await api("/infinity/studio/system/readiness");
      var old=document.getElementById("aiiClosureBanner"); if(old)old.remove();
      var d=document.createElement("div"); d.id="aiiClosureBanner"; d.className="aiiClosure";
      var st=r.storage||{}, pp=r.production_policy||{}, lc=r.local_capabilities||{};
      d.innerHTML="<div class='head'><b>Production truth gate</b><span class='chip "+(pp.strict_visuals?"good":"warn")+"'>"+(pp.strict_visuals?"SOURCE VISUALS REQUIRED":"FALLBACKS ALLOWED")+"</span></div>"+
        "<div class='grid'>"+
        "<div class='cell'><div class='mini'>Rendering</div><div class='v "+(lc.ffmpeg?"pass":"bad")+"'>"+gate(lc.ffmpeg)+"</div></div>"+
        "<div class='cell'><div class='mini'>Research</div><div class='v "+(pp.strict_research?"pass":"warn")+"'>"+(pp.strict_research?"EVIDENCE GATED":"DEGRADED")+" </div></div>"+
        "<div class='cell'><div class='mini'>Storage</div><div class='v "+(st.persistent_runtime?"pass":"warn")+"'>"+(st.persistent_runtime?"PERSISTENT":"EPHEMERAL")+" </div></div>"+
        "<div class='cell'><div class='mini'>Publishing</div><div class='v "+(pp.verified_publish_required?"pass":"warn")+"'>"+(pp.verified_publish_required?"VERIFIED ONLY":"REVIEW MODE")+" </div></div>"+
        "</div>";
      var host=document.querySelector(".commandHero"); if(host)host.parentNode.insertBefore(d,host.nextSibling);
    }catch(e){}
  }
  var _home=window.home;
  if(typeof _home==="function"){
    window.home=async function(){var out=await _home.apply(this,arguments); showGate(); return out;};
  }
  var _open=window.openProject;
  if(typeof _open==="function"){
    window.openProject=async function(id){
      var out=await _open.apply(this,arguments);
      try{
        var t=await api("/infinity/studio/project/"+encodeURIComponent(id)+"/truth");
        var c=document.getElementById("content");
        if(c){
          var old=document.getElementById("aiiTruthPanel"); if(old)old.remove();
          var d=document.createElement("div"); d.id="aiiTruthPanel"; d.className="aiiClosure";
          var good=!!t.verified;
          d.innerHTML="<div class='head'><b>Professional verification</b><span class='status "+(good?"completed":"failed")+"'>"+(good?"VERIFIED":"REVIEW REQUIRED")+"</span></div>"+
            "<div class='mini' style='margin-top:8px'>"+(t.failures&&t.failures.length?esc(t.failures.slice(0,3).join(" · ")):"All required production checks passed.")+"</div>"+
            "<div class='grid'><div class='cell'><div class='mini'>Duration</div><div class='v'>"+esc(String((t.checks||{}).actual_duration||0))+"s</div></div><div class='cell'><div class='mini'>Visual policy</div><div class='v'>"+(t.checks&&t.checks.source_visual_policy_passed?"PASS":"BLOCKED")+"</div></div><div class='cell'><div class='mini'>Research</div><div class='v'>"+esc(String((t.checks||{}).research_sources||0))+" sources</div></div><div class='cell'><div class='mini'>Registry</div><div class='v'>"+(t.checks&&t.checks.artifact_registry_consistent?"PASS":"REPAIR/REVIEW")+"</div></div></div>";
          c.prepend(d);
        }
      }catch(e){}
      return out;
    };
  }
  showGate();
})();
</script>
"""
    marker = "</body>"
    return html.replace(marker, script + marker, 1) if marker in html else html + script


def install() -> None:
    global _INSTALLED
    with _LOCK:
        _patch_functions()
        s = _studio()
        s.CREATOR_STUDIO_UI = _ui_enhancement(getattr(s, "CREATOR_STUDIO_UI", ""))
        s.CREATOR_STUDIO_2030_UI = _ui_enhancement(getattr(s, "CREATOR_STUDIO_2030_UI", ""))
        try:
            _install_db_pragmas()
        except Exception:
            pass
        # Expose closure functions to other code paths without changing their public API.
        s._professional_truth = _professional_truth
        s._closure_version = CLOSURE_VERSION
        _INSTALLED = True


def register(app) -> None:
    from fastapi import Request, Response
    from fastapi.responses import JSONResponse

    @app.get("/infinity/studio/production/contract")
    def production_contract(request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        r = _readiness(uid, request)
        r["contract"] = {
            "one_command": True,
            "real_artifacts_only": True,
            "strict_source_visuals": _strict_visual(),
            "research_evidence_required": _strict_research(),
            "verified_output_before_publish": _publish_gate(),
            "checkpointed_resume": True,
            "no_fake_completion": True,
        }
        return r

    @app.get("/infinity/studio/system/readiness")
    def system_readiness(request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        return _readiness(uid, request)

    @app.get("/infinity/studio/providers/diagnostics")
    def providers_diagnostics(request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        return {**_provider_diag(), "user_id": uid}

    @app.get("/infinity/studio/project/{project_id}/truth")
    def project_truth(project_id: str, request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        p = _require_project(project_id, uid)
        return _professional_truth(p, reconcile=True)

    @app.get("/infinity/studio/project/{project_id}/artifacts/verified")
    def verified_artifacts(project_id: str, request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        p = _require_project(project_id, uid)
        t = _professional_truth(p, reconcile=True)
        return {"project_id": project_id, "verified": t.get("verified"), "artifacts": t.get("artifact_paths") if t.get("verified") else {}, "truthful": True, "failures": t.get("failures") or []}

    @app.post("/infinity/studio/backup")
    async def create_backup(request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        payload = {}
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        return _backup_snapshot(bool(payload.get("include_media")))

    @app.get("/infinity/studio/backup/status")
    def backup_status(request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        root = _data_dir() / "creator_backups"
        snaps = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)[:BACKUP_KEEP] if root.exists() else []
        return {
            "version": CLOSURE_VERSION,
            "snapshots": [{"name": p.name, "path": str(p), "manifest": str(p / "backup-manifest.json")} for p in snaps],
            "offsite_configured": bool(os.getenv("AI_INFINITY_BACKUP_WEBHOOK", "").strip()),
            "truthful": True,
        }


    @app.get("/infinity/studio/project/{project_id}/timeline/graph")
    def timeline_graph(project_id: str, request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        p = _require_project(project_id, uid)
        rows = s._timeline_rows(project_id)
        nodes = []
        for i, event in enumerate(rows, 1):
            nodes.append({
                "id": f"event-{i}",
                "state": _canonical_state(p),
                "event": event.get("event") or "event",
                "time": event.get("time") or event.get("created_at_iso") or event.get("created_at"),
                "payload": event.get("payload") or {},
            })
        return {"project_id": project_id, "nodes": nodes, "edges": [{"from": nodes[i-1]["id"], "to": nodes[i]["id"]} for i in range(1, len(nodes))], "truthful": True}

    @app.get("/infinity/studio/project/{project_id}/evidence")
    def production_evidence(project_id: str, request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        p = _require_project(project_id, uid)
        truth = _professional_truth(p, reconcile=True)
        root = s._project_dir(project_id)
        read = {}
        for name in ("timeline.json", "asset_registry.json", "production_truth.json", "fact_check.json", "provenance.json", "sources.json"):
            path = root / name
            if path.exists():
                try:
                    read[name] = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    read[name] = {"available": True}
        return {"project_id": project_id, "truth": truth, "evidence": read, "truthful": True}

    @app.get("/infinity/studio/project/{project_id}/audit/truth")
    def truth_audit(project_id: str, request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        p = _require_project(project_id, uid)
        t = _professional_truth(p, reconcile=True)
        return {
            "project_id": project_id,
            "truth": t,
            "audit": s._timeline_rows(project_id),
            "truthful": True,
        }
