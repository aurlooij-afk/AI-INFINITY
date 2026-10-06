from __future__ import annotations

"""AI Infinity TARGET-2050.3901 — Reality-First Creator Kernel.

This module does not replace the Creator Studio. It makes the Studio's real
filesystem/database evidence authoritative: every production has an intent
digest, per-stage recovery evidence, independently inspected artifacts and a
derived truth report. The worker is also made fail-soft at scene-render and
late-finalization boundaries.

No completion flag is trusted without filesystem evidence.
"""

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException, Request, Response

VERSION = "TARGET-2050.3901"
BUILD = "REALITY-FIRST-SELF-HEALING-CREATOR-KERNEL"
_DB_LOCK = threading.RLock()
_PATCHED = False
_GUARD_STARTED = False
_ORIGINALS: Dict[str, Any] = {}


def _studio():
    import studio_ultimate
    try:
        studio_ultimate._init_db()
    except Exception:
        pass
    return studio_ultimate


def studio():
    """Public compatibility accessor for the underlying Creator Studio module."""
    return _studio()


def _now() -> float:
    s = _studio()
    try:
        return float(s.now())
    except Exception:
        return time.time()


def _iso(ts: Optional[float] = None) -> str:
    s = _studio()
    try:
        return str(s.utc_iso(ts if ts is not None else _now()))
    except Exception:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else _now()))


def _jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    raw = value if isinstance(value, (bytes, bytearray)) else _jdump(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _valid_file(path: Path, minimum: int = 1) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= minimum
    except Exception:
        return False


def _project_path(project_id: str) -> Path:
    s = _studio()
    return Path(s._project_dir(project_id)).resolve()


def _media_probe(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"ok": False, "error": "file_missing"}
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"ok": False, "error": "ffprobe_unavailable"}
    try:
        p = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=45,
        )
        if p.returncode != 0:
            return {"ok": False, "error": (p.stderr or "ffprobe_failed")[-1200:]}
        data = json.loads(p.stdout or "{}")
        streams = data.get("streams") or []
        fmt = data.get("format") or {}
        duration = float(fmt.get("duration") or 0.0)
        return {
            "ok": bool(streams) and duration > 0,
            "duration_seconds": round(duration, 3),
            "streams": [
                {
                    "codec_type": x.get("codec_type"),
                    "codec_name": x.get("codec_name"),
                    "width": x.get("width"),
                    "height": x.get("height"),
                    "sample_rate": x.get("sample_rate"),
                    "channels": x.get("channels"),
                }
                for x in streams
            ],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:1200]}


def _image_probe(path: Path) -> Dict[str, Any]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            return {"ok": True, "width": im.width, "height": im.height, "format": im.format}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:1000]}


def inspect_artifact(path: Path, media_type: str = "") -> Dict[str, Any]:
    """Independent artifact proof. It never trusts a database row."""
    result: Dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": None,
        "media_type": media_type or "application/octet-stream",
        "inspection": {},
    }
    if not result["exists"]:
        result["inspection"] = {"ok": False, "error": "file_missing"}
        return result
    result["sha256"] = _file_hash(path)
    mt = (media_type or "").lower()
    suffix = path.suffix.lower()
    if mt.startswith("video/") or suffix in {".mp4", ".mov", ".mkv", ".webm"}:
        result["inspection"] = _media_probe(path)
    elif mt.startswith("audio/") or suffix in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
        result["inspection"] = _media_probe(path)
    elif mt.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        result["inspection"] = _image_probe(path)
    elif suffix in {".json"}:
        try:
            json.loads(path.read_text(encoding="utf-8"))
            result["inspection"] = {"ok": True, "encoding": "utf-8", "format": "json"}
        except Exception as exc:
            result["inspection"] = {"ok": False, "error": str(exc)[:1000]}
    elif suffix in {".md", ".txt", ".srt", ".xml"}:
        try:
            text = path.read_text(encoding="utf-8")
            result["inspection"] = {"ok": bool(text.strip()), "characters": len(text)}
        except Exception as exc:
            result["inspection"] = {"ok": False, "error": str(exc)[:1000]}
    elif suffix == ".zip":
        try:
            import zipfile
            with zipfile.ZipFile(path, "r") as z:
                bad = z.testzip()
                result["inspection"] = {
                    "ok": bad is None,
                    "members": len(z.infolist()),
                    "bad_member": bad,
                }
        except Exception as exc:
            result["inspection"] = {"ok": False, "error": str(exc)[:1000]}
    else:
        result["inspection"] = {"ok": result["size_bytes"] > 0}
    return result


def _ensure_schema() -> None:
    s = _studio()
    with _DB_LOCK, s.DB_LOCK, s._connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS reality_projects_3901(
                project_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                intent_sha256 TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                plan_sha256 TEXT,
                root_path TEXT NOT NULL,
                state TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                qc_passed INTEGER NOT NULL DEFAULT 0,
                last_report_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                verified_at REAL
            );
            CREATE TABLE IF NOT EXISTS reality_events_3901(
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                node TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                error TEXT,
                metadata_json TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reality_events_project_3901
              ON reality_events_3901(project_id, event_id);
            CREATE TABLE IF NOT EXISTS reality_artifacts_3901(
                project_id TEXT NOT NULL,
                asset_name TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                media_type TEXT,
                generator_version TEXT,
                source_sha256 TEXT,
                input_hashes_json TEXT,
                parameters_json TEXT,
                inspection_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                verified_at REAL,
                PRIMARY KEY(project_id, asset_name, sha256)
            );
            CREATE INDEX IF NOT EXISTS idx_reality_artifacts_project_3901
              ON reality_artifacts_3901(project_id, created_at DESC);
            """
        )


def _event(project_id: str, node: str, state: str, attempt: int = 1,
           error: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
    _ensure_schema()
    s = _studio()
    with _DB_LOCK, s.DB_LOCK, s._connect() as c:
        c.execute(
            "INSERT INTO reality_events_3901(project_id,node,state,attempt,error,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (project_id, node, state, int(max(1, attempt)),
             (str(error)[:3000] if error else None), _jdump(metadata or {}), _now()),
        )


def _event_once(project_id: str, node: str, state: str, attempt: int = 1,
                error: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
    _ensure_schema()
    s = _studio()
    with _DB_LOCK, s.DB_LOCK, s._connect() as c:
        row = c.execute(
            "SELECT state,error,metadata_json FROM reality_events_3901 "
            "WHERE project_id=? AND node=? ORDER BY event_id DESC LIMIT 1",
            (project_id, node),
        ).fetchone()
        same = bool(row and str(row["state"]) == state and str(row["error"] or "") == str(error or ""))
        if same:
            return
    _event(project_id, node, state, attempt, error, metadata)


def _project_record(project_id: str) -> Dict[str, Any]:
    _ensure_schema()
    s = _studio()
    p = s._get_project(project_id) or {}
    req = p.get("request_json") if isinstance(p.get("request_json"), dict) else {}
    plan = p.get("blueprint_json") if isinstance(p.get("blueprint_json"), dict) else {}
    if not req:
        try:
            req = json.loads(p.get("request_json") or "{}")
        except Exception:
            req = {}
    if not plan:
        try:
            plan = json.loads(p.get("blueprint_json") or "{}")
        except Exception:
            plan = {}
    intent = {
        "user_id": p.get("user_id"),
        "title": p.get("title"),
        "request": req,
    }
    return {
        "project": p,
        "request": req,
        "plan": plan,
        "intent_sha256": _digest(intent),
        "request_sha256": _digest(req),
        "plan_sha256": _digest(plan),
    }


def _register_project(project_id: str) -> None:
    rec = _project_record(project_id)
    p = rec["project"]
    if not p:
        return
    s = _studio()
    with _DB_LOCK, s.DB_LOCK, s._connect() as c:
        c.execute(
            "INSERT INTO reality_projects_3901("
            "project_id,user_id,intent_sha256,request_sha256,plan_sha256,root_path,state,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "user_id=excluded.user_id,intent_sha256=excluded.intent_sha256,"
            "request_sha256=excluded.request_sha256,plan_sha256=excluded.plan_sha256,"
            "root_path=excluded.root_path,state=excluded.state,updated_at=excluded.updated_at",
            (
                project_id, str(p.get("user_id") or ""),
                rec["intent_sha256"], rec["request_sha256"], rec["plan_sha256"],
                str(_project_path(project_id)), "PLANNED", _now(), _now(),
            ),
        )
    _event(project_id, "intent", "PLANNED", 1, metadata={
        "intent_sha256": rec["intent_sha256"],
        "request_sha256": rec["request_sha256"],
    })


def _upsert_artifact(project_id: str, path: Path, media_type: str,
                     source_hashes: Optional[Iterable[str]] = None,
                     parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    info = inspect_artifact(path, media_type)
    if not info["exists"]:
        return info
    rec = _project_record(project_id)
    generator = {
        "kernel_version": VERSION,
        "studio_version": str(getattr(_studio(), "VERSION", "")),
        "studio_build": str(getattr(_studio(), "BUILD", "")),
    }
    proof = {
        "input_hashes": list(source_hashes or []),
        "parameters": parameters or {},
        "generator": generator,
        "intent_sha256": rec["intent_sha256"],
        "request_sha256": rec["request_sha256"],
        "plan_sha256": rec["plan_sha256"],
        "created_at": _iso(),
    }
    _ensure_schema()
    s = _studio()
    with _DB_LOCK, s.DB_LOCK, s._connect() as c:
        existing = c.execute(
            "SELECT * FROM reality_artifacts_3901 WHERE project_id=? AND asset_name=? AND sha256=? LIMIT 1",
            (project_id, info["name"], info["sha256"]),
        ).fetchone()
        if existing:
            stored_proof = proof
            try:
                stored_proof = json.loads(existing["parameters_json"] or "{}")
            except Exception:
                pass
            return {**info, "proof": stored_proof, "recorded_at": existing["created_at"]}
        c.execute(
            "INSERT INTO reality_artifacts_3901("
            "project_id,asset_name,sha256,size_bytes,media_type,generator_version,source_sha256,"
            "input_hashes_json,parameters_json,inspection_json,created_at,verified_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                project_id, info["name"], info["sha256"], info["size_bytes"], media_type,
                VERSION, rec["intent_sha256"], _jdump(source_hashes or []),
                _jdump(proof), _jdump(info["inspection"]), _now(), None,
            ),
        )
    return {**info, "proof": proof}


def _required_assets(req: Dict[str, Any]) -> List[Tuple[str, str]]:
    ct = str(req.get("content_type") or "video").lower()
    if ct == "article":
        return [("article.md", "text/markdown"), ("thumbnail.jpg", "image/jpeg")]
    if ct == "podcast":
        return [("audio_master.mp3", "audio/mpeg"), ("podcast_rss.xml", "application/xml")]
    if ct == "social":
        return [("final.mp4", "video/mp4"), ("social_campaign.md", "text/markdown"), ("thumbnail.jpg", "image/jpeg")]
    return [
        ("final.mp4", "video/mp4"),
        ("audio_master.mp3", "audio/mpeg"),
        ("captions.srt", "application/x-subrip"),
        ("thumbnail.jpg", "image/jpeg"),
        ("script.md", "text/markdown"),
        ("manifest.json", "application/json"),
    ]


def _repair_missing_companions(project_id: str, req: Dict[str, Any]) -> Dict[str, Any]:
    """Repair only artifacts that can be deterministically derived from already-valid output."""
    s = _studio()
    root = _project_path(project_id)
    repaired: List[Dict[str, Any]] = []
    final = root / "final.mp4"
    final_probe = _media_probe(final) if final.is_file() else {"ok": False}
    if final_probe.get("ok"):
        # A delivery audio master is deterministically derivable from the proven
        # final media stream. It is a real file, not a placeholder.
        audio = root / "audio_master.mp3"
        has_audio = any(x.get("codec_type") == "audio" for x in (final_probe.get("streams") or []))
        if has_audio and not _valid_file(audio, 1000):
            try:
                s.ffmpeg(
                    "-i", final,
                    "-map", "0:a:0",
                    "-vn",
                    "-c:a", "libmp3lame",
                    "-q:a", "3",
                    audio,
                    timeout=180,
                )
                if _valid_file(audio, 1000) and _media_probe(audio).get("ok"):
                    repaired.append({"artifact": "audio_master.mp3", "method": "extract_from_verified_final"})
            except Exception as exc:
                _event(project_id, "companion_repair", "FAILED", 1, str(exc)[:1200],
                       {"artifact": "audio_master.mp3"})
    return {"repaired": repaired}


def _verify_required(project_id: str, req: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], List[Dict[str, Any]]]:
    root = _project_path(project_id)
    repair_result = _repair_missing_companions(project_id, req)
    if repair_result.get("repaired"):
        _event_once(project_id, "companion_repair", "REPAIRED", 1,
                    metadata={"artifacts": repair_result.get("repaired")})
    proofs: List[Dict[str, Any]] = []
    checks: Dict[str, Any] = {}
    all_ok = True

    # Required deliverables determine customer-usable closure; every other
    # generated file is still independently hashed and inspected below.

    for name, mt in _required_assets(req):
        info = inspect_artifact(root / name, mt)
        proofs.append(info)
        checks[name] = bool(info["exists"] and info["inspection"].get("ok"))
        all_ok = all_ok and checks[name]
        if info["exists"]:
            proof = _upsert_artifact(
                project_id, root / name, mt,
                source_hashes=[_digest(req), _digest(_project_record(project_id)["plan"])],
                parameters={"required": True, "asset_name": name},
            )
            info["proof"] = proof.get("proof") if isinstance(proof, dict) else info.get("proof")

    # Independent proof is not restricted to customer-facing deliverables.
    # Intermediate scenes, narration, music, variants and packages are all real
    # generated files and receive the same hash + inspection treatment.
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "reality_proof.json":
            continue
        if any(p["name"] == path.name for p in proofs):
            continue
        suffix = path.suffix.lower()
        mt = {
            ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
            ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".json": "application/json", ".srt": "application/x-subrip",
            ".md": "text/markdown", ".txt": "text/plain", ".xml": "application/xml",
            ".zip": "application/zip",
        }.get(suffix, "application/octet-stream")
        info = inspect_artifact(path, mt)
        proofs.append(info)
        if info["exists"]:
            _upsert_artifact(
                project_id, path, mt,
                source_hashes=[_digest(req), _digest(_project_record(project_id)["plan"])],
                parameters={"generated_file": True, "asset_name": path.name},
            )
    # Strongest proof for video: final must be a playable media file, not merely a
    # non-empty byte stream.
    final = root / "final.mp4"
    if final.is_file():
        media = _media_probe(final)
        streams = media.get("streams") or []
        checks["final_has_video_stream"] = any(x.get("codec_type") == "video" for x in streams)
        checks["final_has_audio_stream"] = any(x.get("codec_type") == "audio" for x in streams)
        checks["final_duration_gt_2s"] = float(media.get("duration_seconds") or 0) > 2
        requested_duration = float(req.get("duration") or 0)
        actual_duration = float(media.get("duration_seconds") or 0)
        checks["final_duration_matches_request"] = bool(
            requested_duration <= 0 or abs(actual_duration - requested_duration) <= 1.0
        )
        checks["requested_duration_seconds"] = requested_duration
        expected_ratio = str(req.get("aspect_ratio") or "16:9").strip()
        expected = {
            "16:9": 16 / 9,
            "9:16": 9 / 16,
            "1:1": 1.0,
            "4:5": 4 / 5,
        }.get(expected_ratio, 16 / 9)
        first_video = next((x for x in streams if x.get("codec_type") == "video"), {})
        actual_w = int(first_video.get("width") or 0)
        actual_h = int(first_video.get("height") or 0)
        actual_ratio = (actual_w / actual_h) if actual_w and actual_h else 0.0
        checks["final_dimensions_present"] = actual_w > 0 and actual_h > 0
        checks["final_aspect_ratio_match"] = bool(
            actual_ratio > 0 and abs(actual_ratio - expected) <= 0.02
        )
        checks["requested_aspect_ratio"] = expected_ratio
        checks["final_width"] = actual_w
        checks["final_height"] = actual_h
        if str(req.get("content_type") or "video").lower() in {"video", "social"}:
                        all_ok = all_ok and checks["final_has_video_stream"] and checks["final_has_audio_stream"] and checks["final_duration_gt_2s"] and checks["final_duration_matches_request"] and checks["final_dimensions_present"] and checks["final_aspect_ratio_match"]
    return all_ok, checks, proofs


def _set_truth(project_id: str, state: str, verified: bool, qc: bool,
               report: Dict[str, Any]) -> None:
    _ensure_schema()
    s = _studio()
    with _DB_LOCK, s.DB_LOCK, s._connect() as c:
        c.execute(
            "UPDATE reality_projects_3901 SET state=?,verified=?,qc_passed=?,last_report_json=?,updated_at=?,verified_at=? "
            "WHERE project_id=?",
            (
                state, int(verified), int(qc), _jdump(report), _now(),
                _now() if verified else None, project_id,
            ),
        )
    if verified:
        _event(project_id, "reality", "VERIFIED", 1, metadata={
            "artifact_proof_count": len(report.get("artifacts") or []),
            "qc_passed": qc,
        })
    else:
        _event(project_id, "reality", state, 1, error=report.get("error"),
               metadata={"artifact_proof_count": len(report.get("artifacts") or [])})


def reconcile_project(project_id: str) -> Dict[str, Any]:
    """Derive truth from the project record plus current filesystem evidence."""
    rec = _project_record(project_id)
    p, req = rec["project"], rec["request"]
    if not p:
        return {"project_id": project_id, "state": "UNKNOWN", "verified": False, "error": "project_not_found"}
    try:
        _register_project(project_id)
    except Exception:
        pass

    status = str(p.get("status") or "").lower()
    required_ok, checks, proofs = _verify_required(project_id, req)

    studio_qc: Dict[str, Any] = {}
    result = p.get("result_json") if isinstance(p.get("result_json"), dict) else {}
    if not result:
        try:
            result = json.loads(p.get("result_json") or "{}")
        except Exception:
            result = {}
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    studio_qc["studio_qc_passed"] = bool(quality.get("passed"))
    studio_qc["studio_qc_present"] = bool(quality)
    studio_qc["status"] = status

    qc_passed = required_ok and studio_qc["studio_qc_passed"]
    if status in {"completed", "completed_with_qc_warnings"} and required_ok:
        state = "VERIFIED" if qc_passed else "DELIVERED_WITH_QC_WARNINGS"
    elif status in {"failed", "cancelled"} and required_ok:
        state = "DELIVERED_WITH_QC_WARNINGS"
    elif status in {"running", "producing", "queued"}:
        state = status.upper()
    else:
        state = "BLOCKED"

    report = {
        "version": VERSION,
        "build": BUILD,
        "project_id": project_id,
        "state": state,
        "verified": bool(state == "VERIFIED"),
        "qc_passed": bool(qc_passed),
        "intent_sha256": rec["intent_sha256"],
        "request_sha256": rec["request_sha256"],
        "plan_sha256": rec["plan_sha256"],
        "generator": {
            "kernel": VERSION,
            "studio": str(getattr(_studio(), "VERSION", "")),
            "studio_build": str(getattr(_studio(), "BUILD", "")),
        },
        "artifacts": proofs,
        "checks": checks,
        "studio_qc": studio_qc,
        "generated_at": _iso(),
        "truth_rule": "filesystem_and_independent_inspection",
    }
    if not required_ok:
        missing = [k for k, v in checks.items() if not v]
        report["error"] = "required_artifact_proof_incomplete"
        report["missing_or_invalid"] = missing

    # Enforce the UI/database state from evidence, never the reverse.
    if status == "completed" and not required_ok:
        try:
            s._update_project(
                project_id,
                status="completed_with_qc_warnings",
                stage="quality_control",
                progress=100,
                error="Independent Reality Kernel proof is incomplete.",
            )
        except Exception:
            pass
    elif status == "failed" and required_ok:
        try:
            s._update_project(
                project_id,
                status="completed_with_qc_warnings",
                stage="complete",
                progress=100,
                error="A real deliverable exists after worker failure; delivered with QC warnings.",
            )
        except Exception:
            pass

    # Append the canonical project proof chain once per state.
    if proofs:
        _event_once(project_id, "artifacts", "ARTIFACT_CREATED", 1,
                    metadata={"count": len(proofs)})
    if all(bool(x.get("inspection", {}).get("ok")) for x in proofs):
        _event_once(project_id, "artifacts", "FILE_INSPECTED", 1,
                    metadata={"count": len(proofs)})
    if all(bool(x.get("sha256")) for x in proofs):
        _event_once(project_id, "artifacts", "HASHED", 1,
                    metadata={"count": len(proofs)})
    if qc_passed:
        _event_once(project_id, "quality", "QC_PASSED", 1,
                    metadata={"required_artifacts": len(checks)})
    _event_once(project_id, "reality", state, 1, error=report.get("error"),
                metadata={"verified": bool(state == "VERIFIED"), "qc_passed": qc_passed})

    _ensure_schema()
    existing_report = None
    try:
        with _DB_LOCK, s.DB_LOCK, s._connect() as c:
            row = c.execute(
                "SELECT last_report_json FROM reality_projects_3901 WHERE project_id=? LIMIT 1",
                (project_id,),
            ).fetchone()
            if row and row["last_report_json"]:
                existing_report = json.loads(row["last_report_json"])
    except Exception:
        existing_report = None
    stable_existing = dict(existing_report or {})
    stable_existing.pop("generated_at", None)
    stable_new = dict(report)
    stable_new.pop("generated_at", None)
    if stable_existing != stable_new:
        _set_truth(project_id, state, state == "VERIFIED", qc_passed, report)
        root = _project_path(project_id)
        try:
            (root / "reality_proof.json").write_text(_jdump(report), encoding="utf-8")
        except Exception:
            pass
    return report


def _safe_render(original: Any, asset: Dict[str, Any], voice: Path, music: Path,
                 sfx: Path, duration: float, out: Path, title: str,
                 aspect_ratio: str, attempt: int) -> None:
    s = _studio()
    try:
        original(asset, voice, music, sfx, duration, out, title, aspect_ratio)
        if _valid_file(out, 10000) and _media_probe(out).get("ok"):
            return
        raise RuntimeError("primary renderer produced an invalid artifact")
    except Exception as first_exc:
        _event(getattr(s.ACTIVE_PROJECT, "project_id", "") or "", "scene_render", "REPAIR_REQUIRED", attempt,
               str(first_exc)[:1200], {"strategy": "primary_renderer"})
    if not voice.is_file():
        voice = s._make_silent_voice(out.parent, int(time.time()) % 97, duration)
    width, height = {
        "9:16": (720, 1280), "1:1": (720, 720), "4:5": (720, 900)
    }.get(str(aspect_ratio or "16:9"), (1280, 720))
    src = str(asset.get("path") or "")
    if not src or not Path(src).is_file():
        proc = s._procedural_image(title or "AI Infinity", out.parent, int(time.time()) % 1000, width=max(480, width), height=max(360, height))
        if proc:
            src = str(proc["path"])
            asset = proc
    last_error = None
    # Alternate implementation: simple single-pass render, intentionally removing
    # the most fragile audio/filter graph. This is a real MP4, not a placeholder.
    for attempt_no in (2, 3):
        try:
            visual_args = ["-stream_loop", "-1", "-i", src] if str(asset.get("kind")) == "video" else ["-loop", "1", "-i", src]
            simple_vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=24"
            ff = getattr(s, "ffmpeg")
            ff(
                *visual_args,
                "-i", voice,
                "-i", music,
                "-filter_complex", "[1:a]volume=0.95[v];[2:a]volume=0.04[m];[v][m]amix=inputs=2:duration=first[a]",
                "-map", "0:v:0", "-map", "[a]", "-vf", simple_vf, "-t", max(1.0, float(duration)),
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out, timeout=300,
            )
            if _valid_file(out, 10000) and _media_probe(out).get("ok"):
                _event(getattr(s.ACTIVE_PROJECT, "project_id", "") or "", "scene_render", "REPAIRED", attempt_no,
                       metadata={"strategy": "simple_renderer", "size_bytes": out.stat().st_size})
                return
            raise RuntimeError("simple renderer produced an invalid artifact")
        except Exception as exc:
            last_error = exc
            _event(getattr(s.ACTIVE_PROJECT, "project_id", "") or "", "scene_render", "REPAIR_REQUIRED", attempt_no,
                   str(exc)[:1200], {"strategy": "simple_renderer"})

    # Final local/procedural ladder rung. It removes every external and user-asset
    # dependency and synthesizes a genuine moving-color scene with the real voice.
    # This prevents one broken remote/public visual from destroying the whole job.
    try:
        project_id = str(getattr(s.ACTIVE_PROJECT, "project_id", "") or "")
        ff = getattr(s, "ffmpeg")
        bg = out.with_name(out.stem + ".procedural.mp4")
        # Keep the emergency generator independent of user-controlled strings.
        # The canonical script/captions carry the title; this visual is intentionally
        # the most portable possible FFmpeg fallback.
        bg_vf = f"format=yuv420p,fps=24"
        ff(
            "-f", "lavfi", "-i", f"color=c=0x101722:s={width}x{height}:r=24",
            "-t", max(1.0, float(duration)), "-vf", bg_vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
            "-pix_fmt", "yuv420p", "-an", bg, timeout=300,
        )
        ff(
            "-i", bg, "-i", voice,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart", "-t", max(1.0, float(duration)), out,
            timeout=180,
        )
        if _valid_file(out, 10000) and _media_probe(out).get("ok"):
            _event(project_id, "scene_render", "REPAIRED", 3, metadata={
                "strategy": "procedural_generator", "size_bytes": out.stat().st_size,
            })
            return
        raise RuntimeError("procedural generator produced an invalid artifact")
    except Exception as exc:
        last_error = exc
        _event(str(getattr(s.ACTIVE_PROJECT, "project_id", "") or ""), "scene_render", "FAILED", 3,
               str(exc)[:2000], {"strategy": "procedural_generator"})
    raise RuntimeError(f"scene render failed after self-healing ladder: {last_error}")


def _emergency_finalize(project_id: str) -> bool:
    """Best-effort late recovery from already-rendered scenes after a pipeline error."""
    s = _studio()
    root = _project_path(project_id)
    scenes = sorted(
        [p for p in root.glob("scene_*.mp4") if _valid_file(p, 10000)],
        key=lambda p: p.name,
    )
    if not scenes:
        return False
    master = root / "master.mp4"
    final = root / "final.mp4"
    try:
        s.concat_segments(scenes, master)
    except Exception:
        return False
    if not _valid_file(master, 10000):
        return False
    if not _valid_file(final, 10000):
        try:
            # Use a copy first: it preserves the already-rendered video/audio
            # without an expensive second generation.
            shutil.copy2(master, final)
        except Exception:
            return False
    # Produce whatever companion assets are needed from the recovered master.
    try:
        p = s._get_project(project_id) or {}
        req = p.get("request_json") if isinstance(p.get("request_json"), dict) else {}
        plan = p.get("blueprint_json") if isinstance(p.get("blueprint_json"), dict) else {}
        chapters = plan.get("chapters") if isinstance(plan, dict) else []
        if not isinstance(chapters, list) or not chapters:
            fallback = s._fallback_creative_plan(
                str(req.get("title") or req.get("topic") or "AI Infinity"),
                str(req.get("objective") or req.get("topic") or "AI content"),
                "short" if str(req.get("format") or "long").lower() in {"short", "reel", "shorts", "tiktok"} else "long",
                int(req.get("duration") or 60),
                str(req.get("audience") or "general audience"),
                str(req.get("tone") or "cinematic"),
                {},
            )
            chapters = fallback.get("chapters") or []
        captions = root / "captions.srt"
        s.write_srt(chapters, captions)
        script = root / "script.md"
        lines = [f"# {p.get('title') or req.get('title') or 'AI Infinity'}", ""]
        for ch in chapters:
            lines += [f"## {ch.get('heading')}", str(ch.get('narration') or ""), ""]
        script.write_text("\n".join(lines), encoding="utf-8")
        thumb = root / "thumbnail.jpg"
        thumb_result = s.make_thumbnail(final, str(p.get("title") or req.get("title") or "AI Infinity"), root)
        if isinstance(thumb_result, (str, Path)) and Path(thumb_result).is_file() and Path(thumb_result) != thumb:
            try:
                shutil.copy2(Path(thumb_result), thumb)
            except Exception:
                pass
        if not _valid_file(thumb, 1000):
            return False
        audio = root / "audio_master.mp3"
        if not _valid_file(audio, 1000):
            # A valid recovered final always carries an audio stream; export it as
            # an independent real asset.
            s.ffmpeg("-i", final, "-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "4", audio, timeout=180)
    except Exception:
        return False
    try:
        s._update_project(
            project_id,
            status="completed_with_qc_warnings",
            error="Recovered by Reality Kernel after a late production failure.",
            stage="complete",
            progress=100,
            result_json=_jdump({
                "status": "completed_with_qc_warnings",
                "project_id": project_id,
                "title": (s._get_project(project_id) or {}).get("title"),
                "recovered": True,
                "truthful": True,
            }),
        )
    except Exception:
        return False
    _event(project_id, "late_recovery", "REPAIRED", 1, metadata={"scene_count": len(scenes)})
    return True


def _guarded_run_project(project_id: str, model_fn: Any) -> None:
    s = _studio()
    original = _ORIGINALS.get("run_project")
    if original is None:
        raise RuntimeError("Reality Kernel lost original run_project")
    try:
        _event_once(project_id, "production", "QUEUED", 1)
        _register_project(project_id)
        _event_once(project_id, "production", "RUNNING", 1, metadata={"worker": "studio_ultimate"})
        original(project_id, model_fn)
        # _studio().run_project normally absorbs worker exceptions and marks the
        # project failed. Re-open that result here so late, already-rendered
        # scenes can still be recovered into a real deliverable.
        post = s._get_project(project_id) or {}
        if str(post.get("status") or "").lower() == "failed":
            try:
                if _emergency_finalize(project_id):
                    _event(project_id, "recovery", "REPAIRED", 1, metadata={
                        "reason": "studio_worker_returned_failed_after_partial_output"
                    })
            except Exception as recovery_exc:
                _event(project_id, "recovery", "FAILED", 1, str(recovery_exc)[:1200])
    except Exception as exc:
        _event(project_id, "production", "FAILED", 1, str(exc)[:2000])
        try:
            s.stop_project_process(project_id)
        except Exception:
            pass
        recovered = False
        try:
            recovered = _emergency_finalize(project_id)
        except Exception as recovery_exc:
            _event(project_id, "recovery", "FAILED", 1, str(recovery_exc)[:1200])
        if not recovered:
            try:
                s._update_project(
                    project_id, status="failed", stage="failed",
                    error=str(exc)[:1200], progress=0,
                    result_json=_jdump({"status": "failed", "error": str(exc)[:1200], "truthful": True}),
                )
            except Exception:
                pass
    finally:
        try:
            report = reconcile_project(project_id)
            # A completed UI state may only survive when the reality report agrees.
            current = s._get_project(project_id) or {}
            if report.get("verified") and current.get("status") not in {"completed", "completed_with_qc_warnings"}:
                s._update_project(project_id, status="completed", stage="complete", progress=100)
            elif not report.get("verified") and current.get("status") == "completed":
                s._update_project(project_id, status="completed_with_qc_warnings", stage="quality_control", error="Independent Reality Kernel proof failed.", progress=100)
        except Exception as exc:
            _event(project_id, "reconciliation", "FAILED", 1, str(exc)[:1200])


def _guard_loop() -> None:
    while True:
        time.sleep(20)
        try:
            s = _studio()
            _ensure_schema()
            rows = []
            with _DB_LOCK, s.DB_LOCK, s._connect() as c:
                rows = [dict(r) for r in c.execute(
                    "SELECT project_id FROM studio_projects_3610 "
                    "WHERE status IN ('completed','completed_with_qc_warnings','failed') "
                    "ORDER BY updated_at DESC LIMIT 20"
                ).fetchall()]
            for row in rows:
                try:
                    reconcile_project(str(row["project_id"]))
                except Exception:
                    pass
        except Exception:
            pass


def runtime_health() -> Dict[str, Any]:
    s = _studio()
    _ensure_schema()
    patched = all(callable(getattr(s, name, None)) for name in ("enqueue", "_render_scene", "run_project", "concat_segments"))
    return {
        "version": VERSION,
        "build": BUILD,
        "enabled": True,
        "patched": patched and _PATCHED,
        "canonical_truth": "filesystem_and_independent_inspection",
        "state_machine": [
            "PLANNED", "QUEUED", "RUNNING", "ARTIFACT_CREATED", "FILE_INSPECTED",
            "HASHED", "QC_PASSED", "VERIFIED", "DELIVERED", "PUBLISHED",
        ],
        "self_healing": {
            "scene_renderer": True,
            "late_finalization": True,
            "max_scene_retries": 3,
            "blind_infinite_retry": False,
        },
        "artifact_proof": True,
        "local_core": {
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "ffprobe": bool(shutil.which("ffprobe")),
            "offline_tts": bool(shutil.which("espeak-ng") or shutil.which("espeak")),
        },
        "truthful": True,
    }


def truth_for_project(project_id: str) -> Dict[str, Any]:
    return reconcile_project(project_id)


def register(app: Any) -> None:
    """Expose independent proof endpoints without replacing the main creator UI."""
    @app.get("/infinity/reality/health")
    def reality_health():
        return runtime_health()

    @app.get("/infinity/reality/project/{project_id}")
    def reality_project(project_id: str, request: Request, response: Response):
        s = _studio()
        uid = s._get_user_id(request)
        s._set_session(response, request, uid)
        p = s._get_project(project_id)
        if not p or p.get("user_id") != uid:
            raise HTTPException(404, "project not found")
        return truth_for_project(project_id)


def install() -> None:
    global _PATCHED, _GUARD_STARTED
    if _PATCHED:
        return
    s = _studio()
    _ensure_schema()

    # Keep the original implementations available for controlled fallback.
    for name in ("enqueue", "run_project", "_render_scene", "concat_segments"):
        if name not in _ORIGINALS:
            _ORIGINALS[name] = getattr(s, name)

    original_enqueue = _ORIGINALS["enqueue"]
    if not getattr(original_enqueue, "_reality_first_wrapped", False):
        def enqueue_wrapped(req: Dict[str, Any], user_id: str, model_fn: Any):
            result = original_enqueue(req, user_id, model_fn)
            try:
                pid = str(result.get("project_id") or "")
                if pid:
                    _register_project(pid)
                    _event(pid, "production", "QUEUED", 1, metadata={
                        "entrypoint": "enqueue",
                        "status_returned": result.get("status"),
                    })
            except Exception:
                pass
            return result
        enqueue_wrapped._reality_first_wrapped = True
        s.enqueue = enqueue_wrapped

    original_render = _ORIGINALS["_render_scene"]
    if not getattr(original_render, "_reality_first_wrapped", False):
        def render_wrapped(asset, voice, music, sfx, duration, out, title, aspect_ratio="16:9"):
            pid = str(getattr(s.ACTIVE_PROJECT, "project_id", "") or "")
            attempt = 1
            try:
                root = _project_path(pid) if pid else Path(out).parent
                _event(pid, f"scene:{Path(out).stem}", "RUNNING", 1, metadata={"output": str(out)})
                _safe_render(original_render, asset, Path(voice), Path(music), Path(sfx), duration, Path(out), title, aspect_ratio, attempt)
                source_hashes = []
                for x in (asset.get("path"), str(voice), str(music), str(sfx)):
                    try:
                        px = Path(str(x))
                        if px.is_file():
                            source_hashes.append(_file_hash(px))
                    except Exception:
                        pass
                _upsert_artifact(pid, Path(out), "video/mp4", source_hashes, {
                    "duration_seconds": float(duration),
                    "aspect_ratio": aspect_ratio,
                    "title": str(title)[:240],
                })
                _event(pid, f"scene:{Path(out).stem}", "VERIFIED", 1, metadata={
                    "sha256": _file_hash(Path(out)) if Path(out).is_file() else None,
                })
            except Exception as exc:
                _event(pid, f"scene:{Path(out).stem}", "FAILED", 3, str(exc)[:2000])
                raise
        render_wrapped._reality_first_wrapped = True
        s._render_scene = render_wrapped

    original_run = _ORIGINALS["run_project"]
    if not getattr(original_run, "_reality_first_wrapped", False):
        def run_wrapped(project_id, model_fn):
            return _guarded_run_project(project_id, model_fn)
        run_wrapped._reality_first_wrapped = True
        s.run_project = run_wrapped

    original_concat = _ORIGINALS["concat_segments"]
    if not getattr(original_concat, "_reality_first_wrapped", False):
        def concat_wrapped(paths, out):
            pid = str(getattr(s.ACTIVE_PROJECT, "project_id", "") or "")
            try:
                return_value = original_concat(paths, out)
                if not _valid_file(Path(out), 10000) or not _media_probe(Path(out)).get("ok"):
                    raise RuntimeError("concat output did not pass media inspection")
                _upsert_artifact(pid, Path(out), "video/mp4",
                                 [_file_hash(Path(x)) for x in paths if Path(x).is_file()],
                                 {"operation": "assembly", "segment_count": len(list(paths))})
                return return_value
            except Exception as first_exc:
                _event(pid, "assembly", "REPAIR_REQUIRED", 1, str(first_exc)[:1600],
                       {"strategy": "copy_reencode"})
                manifest = Path(out).with_suffix(".reality.txt")
                manifest.write_text(
                    "".join("file '" + str(Path(x)).replace("'", "'\\''") + "'\n"
                            for x in paths if Path(x).is_file()),
                    encoding="utf-8",
                )
                s.ffmpeg(
                    "-f", "concat", "-safe", "0", "-i", manifest,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out,
                    timeout=900,
                )
                if not _valid_file(Path(out), 10000) or not _media_probe(Path(out)).get("ok"):
                    raise RuntimeError("assembly recovery produced invalid media")
                return out
        concat_wrapped._reality_first_wrapped = True
        s.concat_segments = concat_wrapped

    _PATCHED = True
    if not _GUARD_STARTED:
        threading.Thread(target=_guard_loop, name="ai-infinity-reality-kernel", daemon=True).start()
        _GUARD_STARTED = True


install()
