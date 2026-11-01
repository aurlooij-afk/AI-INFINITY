from __future__ import annotations

"""Dedicated, single-GPU Wan2.2-TI2V-5B inference worker for AI Infinity.

This service is deliberately separate from the lightweight Creator Studio web
service. It only advertises execution when a real CUDA GPU, verified model
weights/code, acknowledged model terms, FFmpeg, and confirmed persistent
worker storage are all present.
"""

import hashlib
import fcntl
import hmac
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


MODEL_ID = "Wan-AI/Wan2.2-TI2V-5B"
MODEL_NAME = "Wan2.2-TI2V-5B"
LICENSE_ID = "apache-2.0"
MODEL_CARD_URL = "https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B"
MODEL_CODE_URL = "https://github.com/Wan-Video/Wan2.2"
LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0"
MANIFEST_NAME = ".ai-infinity-model-manifest.json"
REQUIRED_MODEL_FILES = (
    "config.json",
    "diffusion_pytorch_model.safetensors.index.json",
    "diffusion_pytorch_model-00001-of-00003.safetensors",
    "diffusion_pytorch_model-00002-of-00003.safetensors",
    "diffusion_pytorch_model-00003-of-00003.safetensors",
    "models_t5_umt5-xxl-enc-bf16.pth",
    "Wan2.2_VAE.pth",
    "README.md",
)

SOURCE_DIR = Path(os.getenv("WAN_SOURCE_DIR", "/opt/Wan2.2")).resolve()
MODEL_DIR = Path(os.getenv("WAN_MODEL_DIR", "/models/Wan2.2-TI2V-5B")).resolve()
DATA_DIR = Path(os.getenv("WAN_WORKER_DATA_DIR", "/data/wan-worker")).resolve()
DB_PATH = DATA_DIR / "wan_worker.sqlite3"
OUTPUT_DIR = DATA_DIR / "artifacts"
LOG_DIR = DATA_DIR / "logs"

MIN_TOTAL_VRAM_MB = max(24576, int(os.getenv("WAN_WORKER_MIN_TOTAL_VRAM_MB", "24576")))
MIN_FREE_VRAM_MB = max(20000, int(os.getenv("WAN_WORKER_MIN_FREE_VRAM_MB", "22000")))
MAX_DURATION_SECONDS = 5.0
MAX_RUNTIME_SECONDS = max(300, min(2400, int(os.getenv("WAN_WORKER_MAX_RUNTIME_SECONDS", "1800"))))
MAX_ARTIFACT_BYTES = max(100_000_000, min(2_000_000_000, int(os.getenv("WAN_WORKER_MAX_ARTIFACT_BYTES", "1000000000"))))
MAX_QUEUED_JOBS = max(1, min(32, int(os.getenv("WAN_WORKER_MAX_QUEUED_JOBS", "8"))))
MAX_PROMPT_CHARS = 4000
MAX_CONCURRENCY = 1

STOP = threading.Event()
WORKER_THREAD: threading.Thread | None = None
WORKER_LOCK = threading.RLock()
MODEL_CACHE_LOCK = threading.RLock()
MODEL_CACHE: dict[str, Any] = {"fingerprint": None, "result": None}
GPU_CACHE_LOCK = threading.RLock()
GPU_CACHE: dict[str, Any] = {"at": 0.0, "result": None}
ACTIVE_PROCESS: subprocess.Popen | None = None
WORKER_LOCK_HANDLE: int | None = None
WORKER_PROCESS_LOCKED = False

app = FastAPI(
    title="AI Infinity Open-Source Video Worker",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=8, max_length=MAX_PROMPT_CHARS)
    duration_seconds: float = Field(ge=2.0, le=MAX_DURATION_SECONDS)
    aspect_ratio: str = Field(default="16:9", pattern=r"^(16:9|9:16)$")
    seed: int = Field(default=-1, ge=-1, le=2_147_483_647)
    idempotency_key: str = Field(min_length=8, max_length=128)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=FULL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def _now() -> float:
    return time.time()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS wan_jobs (
                job_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_sha256 TEXT NOT NULL,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                artifact_name TEXT,
                artifact_sha256 TEXT,
                artifact_size_bytes INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS wan_jobs_status_idx
                ON wan_jobs(status, created_at);
            """
        )


def _bearer(authorization: str | None) -> None:
    expected_token = os.getenv("AI_INFINITY_WAN_WORKER_TOKEN", "").strip()
    supplied = str(authorization or "")
    expected = f"Bearer {expected_token}" if expected_token else ""
    if not expected_token or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="worker authentication required")


def _source_state() -> dict[str, Any]:
    generate = SOURCE_DIR / "generate.py"
    package = SOURCE_DIR / "wan" / "__init__.py"
    license_file = SOURCE_DIR / "LICENSE.txt"
    result: dict[str, Any] = {
        "verified": False,
        "source_url": MODEL_CODE_URL,
        "source_dir_present": generate.is_file() and package.is_file(),
        "license_verified": False,
        "revision": None,
        "reason": "official Wan2.2 checkout is incomplete",
    }
    if not result["source_dir_present"]:
        return result
    try:
        license_text = license_file.read_text(encoding="utf-8", errors="replace")
        if "Apache License" not in license_text or "Version 2.0" not in license_text:
            result["reason"] = "Wan2.2 source code license file is missing or not Apache-2.0"
            return result
        remote = subprocess.run(
            ["git", "-C", str(SOURCE_DIR), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        origin = (remote.stdout or "").strip().lower().removesuffix(".git")
        allowed = {
            "https://github.com/wan-video/wan2.2",
            "http://github.com/wan-video/wan2.2",
            "git@github.com:wan-video/wan2.2",
            "ssh://git@github.com/wan-video/wan2.2",
        }
        if remote.returncode != 0 or origin not in allowed:
            result["reason"] = "Wan2.2 code origin is not the official Wan-Video/Wan2.2 repository"
            return result
        dirty = subprocess.run(
            ["git", "-C", str(SOURCE_DIR), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if dirty.returncode != 0 or (dirty.stdout or "").strip():
            result["reason"] = "official Wan2.2 source checkout has local modifications"
            return result
        revision = subprocess.run(
            ["git", "-C", str(SOURCE_DIR), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        revision_sha = (revision.stdout or "").strip()
        if revision.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", revision_sha):
            result["reason"] = "Wan2.2 source revision could not be identified"
            return result
        result.update({
            "verified": True,
            "license_verified": True,
            "revision": revision_sha,
            "reason": "official repository origin and Apache-2.0 code license verified",
        })
        return result
    except Exception as exc:
        result["reason"] = f"source verification error: {type(exc).__name__}: {str(exc)[:180]}"
        return result


def _model_fingerprint(manifest: dict[str, Any]) -> str:
    values = []
    for item in manifest.get("files") or []:
        rel = str(item.get("path") or "")
        path = (MODEL_DIR / rel).resolve()
        try:
            path.relative_to(MODEL_DIR)
        except ValueError:
            return "invalid-path"
        if not path.is_file() or path.is_symlink():
            values.append((rel, "missing"))
        else:
            st = path.stat()
            values.append((rel, st.st_size, st.st_mtime_ns))
    payload = {"files": values, "terms_acknowledged": _truthy(os.getenv("AI_INFINITY_WAN_MODEL_TERMS_ACKNOWLEDGED"))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _model_state(force: bool = False) -> dict[str, Any]:
    manifest_path = MODEL_DIR / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "verified": False,
            "license_verified": False,
            "model_id": MODEL_ID,
            "reason": f"verified model manifest unavailable: {type(exc).__name__}",
        }

    fingerprint = _model_fingerprint(manifest)
    with MODEL_CACHE_LOCK:
        if not force and fingerprint == MODEL_CACHE.get("fingerprint") and MODEL_CACHE.get("result"):
            return dict(MODEL_CACHE["result"])

    result: dict[str, Any] = {
        "verified": False,
        "license_verified": False,
        "model_id": MODEL_ID,
        "revision": manifest.get("revision"),
        "license": manifest.get("license"),
        "model_card_url": MODEL_CARD_URL,
        "license_url": LICENSE_URL,
        "terms_acknowledged": _truthy(os.getenv("AI_INFINITY_WAN_MODEL_TERMS_ACKNOWLEDGED")),
        "verified_file_count": 0,
        "reason": "model verification did not complete",
    }
    try:
        if manifest.get("model_id") != MODEL_ID:
            raise ValueError("model manifest repository id mismatch")
        if manifest.get("license") != LICENSE_ID:
            raise ValueError("model license is not the expected Apache-2.0 license")
        if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("revision") or "")):
            raise ValueError("model revision is not an immutable Hugging Face commit SHA")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise ValueError("model manifest file list is missing")
        indexed = {str(x.get("path")): x for x in files if isinstance(x, dict)}
        missing = [name for name in REQUIRED_MODEL_FILES if name not in indexed]
        if missing:
            raise ValueError("model manifest is missing required files: " + ", ".join(missing))
        readme = (MODEL_DIR / "README.md").read_text(encoding="utf-8", errors="replace")
        if not re.search(r"(?m)^license:\s*apache-2\.0\s*$", readme, re.I):
            raise ValueError("downloaded model card does not declare Apache-2.0")
        if "License Agreement" not in readme or "Apache 2.0 License" not in readme:
            raise ValueError("downloaded model card license agreement evidence is missing")
        expected_notice_sha = hashlib.sha256(readme.encode("utf-8")).hexdigest()
        if manifest.get("model_card_sha256") != expected_notice_sha:
            raise ValueError("model card bytes do not match the prepared license evidence")

        verified_count = 0
        for rel, item in indexed.items():
            path = (MODEL_DIR / rel).resolve()
            try:
                path.relative_to(MODEL_DIR)
            except ValueError:
                raise ValueError("model manifest contains a path traversal entry")
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"model file missing or symlinked: {rel}")
            size = path.stat().st_size
            if size != int(item.get("size_bytes") or -1):
                raise ValueError(f"model file size mismatch: {rel}")
            if _sha256(path) != str(item.get("sha256") or ""):
                raise ValueError(f"model file SHA-256 mismatch: {rel}")
            verified_count += 1
        if not result["terms_acknowledged"]:
            raise ValueError("operator must acknowledge the Wan model card usage terms")
        result.update({
            "verified": True,
            "license_verified": True,
            "verified_file_count": verified_count,
            "reason": "immutable model revision, Apache-2.0 card and all manifested weight hashes verified",
        })
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {str(exc)[:320]}"

    with MODEL_CACHE_LOCK:
        MODEL_CACHE["fingerprint"] = fingerprint
        MODEL_CACHE["result"] = dict(result)
    return result


def _gpu_state(force: bool = False) -> dict[str, Any]:
    with GPU_CACHE_LOCK:
        if not force and _now() - float(GPU_CACHE.get("at") or 0) < 5:
            cached = GPU_CACHE.get("result")
            if isinstance(cached, dict):
                return dict(cached)
    result: dict[str, Any] = {
        "available": False,
        "name": None,
        "total_vram_mb": 0,
        "free_vram_mb": 0,
        "cuda_version": None,
        "torch_version": None,
        "reason": "CUDA GPU is unavailable",
    }
    try:
        import torch

        result["torch_version"] = str(torch.__version__)
        result["cuda_version"] = str(torch.version.cuda or "")
        if not torch.cuda.is_available():
            result["reason"] = "PyTorch cannot access a CUDA device"
        else:
            props = torch.cuda.get_device_properties(0)
            free_bytes, total_bytes = torch.cuda.mem_get_info(0)
            total_mb = int(total_bytes / (1024 * 1024))
            free_mb = int(free_bytes / (1024 * 1024))
            result.update({
                "name": str(props.name),
                "total_vram_mb": total_mb,
                "free_vram_mb": free_mb,
                "compute_capability": f"{props.major}.{props.minor}",
            })
            if total_mb < MIN_TOTAL_VRAM_MB:
                result["reason"] = f"GPU has {total_mb} MiB VRAM; Wan TI2V-5B requires at least {MIN_TOTAL_VRAM_MB} MiB"
            elif free_mb < MIN_FREE_VRAM_MB:
                result["reason"] = f"GPU has {free_mb} MiB free; worker requires {MIN_FREE_VRAM_MB} MiB free before a job"
            else:
                result.update({"available": True, "reason": "CUDA GPU meets the configured Wan TI2V-5B memory floor"})
    except Exception as exc:
        result["reason"] = f"GPU probe failed: {type(exc).__name__}: {str(exc)[:180]}"
    with GPU_CACHE_LOCK:
        GPU_CACHE.update({"at": _now(), "result": dict(result)})
    return result


def _storage_state() -> dict[str, Any]:
    confirmed = _truthy(os.getenv("WAN_WORKER_PERSISTENT_STORAGE_CONFIRMED"))
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DATA_DIR / (".write-probe-" + uuid.uuid4().hex)
        probe.write_bytes(b"ai-infinity-wan-worker")
        probe.unlink()
        free_bytes = shutil.disk_usage(DATA_DIR).free
        writable = free_bytes >= 10 * 1024 * 1024 * 1024
        return {
            "persistent": bool(confirmed and writable),
            "operator_confirmed": confirmed,
            "writable": True,
            "free_bytes": free_bytes,
            "minimum_free_bytes": 10 * 1024 * 1024 * 1024,
            "path": str(DATA_DIR),
            "reason": "persistent storage confirmed and at least 10 GiB free" if confirmed and writable else
                      "mount a persistent worker volume, confirm it explicitly, and provide at least 10 GiB free",
        }
    except Exception as exc:
        return {"persistent": False, "operator_confirmed": confirmed, "writable": False,
                "reason": f"worker storage probe failed: {type(exc).__name__}: {str(exc)[:180]}"}


def _capabilities(force: bool = False) -> dict[str, Any]:
    if not WORKER_PROCESS_LOCKED:
        process_lock_reason = "another process owns the single-GPU worker lock; run one Uvicorn worker process"
    else:
        process_lock_reason = ""
    source = _source_state()
    # Re-hash the multi-GB checkpoint only when its file fingerprint changes;
    # refresh GPU free-memory telemetry on demand before every new inference.
    model = _model_state(force=False)
    gpu = _gpu_state(force=force)
    storage = _storage_state()
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    token_configured = bool(os.getenv("AI_INFINITY_WAN_WORKER_TOKEN", "").strip())
    reasons = []
    if not token_configured:
        reasons.append("worker authentication token is not configured")
    if process_lock_reason:
        reasons.append(process_lock_reason)
    if not source.get("verified"):
        reasons.append(str(source.get("reason") or "Wan source is not verified"))
    if not model.get("verified") or not model.get("license_verified"):
        reasons.append(str(model.get("reason") or "Wan model is not verified"))
    if not gpu.get("available"):
        reasons.append(str(gpu.get("reason") or "suitable GPU unavailable"))
    if not storage.get("persistent"):
        reasons.append(str(storage.get("reason") or "persistent storage is not verified"))
    if not ffmpeg or not ffprobe:
        reasons.append("FFmpeg and FFprobe are required for media validation")
    ready = not reasons
    return {
        "provider": "wan2.2-ti2v-5b",
        "execution_ready": ready,
        "status": "ready" if ready else "unavailable",
        "model": model,
        "source": source,
        "hardware": gpu,
        "storage": storage,
        "runtime": {
            "max_concurrency": MAX_CONCURRENCY,
            "max_duration_seconds": MAX_DURATION_SECONDS,
            "max_runtime_seconds": MAX_RUNTIME_SECONDS,
            "max_output_bytes": MAX_ARTIFACT_BYTES,
            "supported_aspect_ratios": ["16:9", "9:16"],
            "resolution": {"16:9": "1280x704", "9:16": "704x1280"},
            "fps": 24,
            "minimum_total_vram_mb": MIN_TOTAL_VRAM_MB,
            "minimum_free_vram_mb": MIN_FREE_VRAM_MB,
        },
        "ffmpeg": bool(ffmpeg),
        "ffprobe": bool(ffprobe),
        "reasons": reasons,
        "truthful": True,
    }


def _job_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    try:
        metadata = json.loads(data.pop("metadata_json", "{}") or "{}")
    except Exception:
        metadata = {}
    data.pop("request_json", None)
    data.pop("artifact_name", None)
    data["metadata"] = metadata
    if data.get("artifact_sha256"):
        data["artifact"] = {
            "url": f"/v1/jobs/{data['job_id']}/artifact",
            "sha256": data["artifact_sha256"],
            "size_bytes": data.get("artifact_size_bytes"),
            "media_type": "video/mp4",
        }
    else:
        data["artifact"] = None
    data["truthful"] = True
    return data


def _probe_artifact(path: Path, requested: dict[str, Any], frame_num: int) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise RuntimeError("FFmpeg and FFprobe must be installed on the worker")
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("Wan did not produce a regular MP4 artifact")
    size = path.stat().st_size
    if size < 100_000 or size > MAX_ARTIFACT_BYTES:
        raise RuntimeError(f"generated artifact size {size} is outside worker limits")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=45, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("FFprobe could not inspect generated media: " + (result.stderr or "")[-500:])
    data = json.loads(result.stdout or "{}")
    video = next((x for x in data.get("streams", []) if x.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("generated artifact contains no video stream")
    expected = (1280, 720) if requested["aspect_ratio"] == "16:9" else (720, 1280)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if (width, height) != expected:
        raise RuntimeError(f"generated resolution {width}x{height} does not match requested {expected[0]}x{expected[1]}")
    duration = float((data.get("format") or {}).get("duration") or 0)
    expected_duration = frame_num / 24.0
    if duration <= 0 or abs(duration - expected_duration) > 0.5:
        raise RuntimeError(f"generated duration {duration:.3f}s does not match expected {expected_duration:.3f}s")
    decode = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if decode.returncode != 0:
        raise RuntimeError("full media decode failed: " + (decode.stderr or "")[-500:])
    try:
        fps = float(Fraction(str(video.get("avg_frame_rate") or "0/1")))
    except Exception:
        fps = 0.0
    if abs(fps - 24.0) > 0.05:
        raise RuntimeError(f"generated frame rate {fps:.3f} does not match the model's 24fps output")
    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "codec": video.get("codec_name"),
        "size_bytes": size,
        "full_decode": True,
    }


def _normalize_hd_output(source: Path, target: Path, aspect_ratio: str) -> None:
    """Pad Wan's 1280x704 / 704x1280 frames to exact HD without stretching."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to normalize the Wan model output to HD")
    filters = "pad=1280:720:0:8:black" if aspect_ratio == "16:9" else "pad=720:1280:8:0:black"
    result = subprocess.run(
        [
            ffmpeg, "-y", "-v", "error", "-i", str(source),
            "-vf", filters, "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "18", "-pix_fmt", "yuv420p", "-r", "24",
            "-an", "-movflags", "+faststart", str(target),
        ],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if result.returncode != 0 or not target.is_file():
        raise RuntimeError("Wan HD output normalization failed: " + (result.stderr or "")[-700:])


def _command(request: dict[str, Any], output: Path, frame_num: int) -> list[str]:
    aspect = request["aspect_ratio"]
    size = "1280*704" if aspect == "16:9" else "704*1280"
    return [
        os.getenv("WAN_PYTHON", "python"),
        str(SOURCE_DIR / "generate.py"),
        "--task", "ti2v-5B",
        "--size", size,
        "--ckpt_dir", str(MODEL_DIR),
        "--offload_model", "True",
        "--convert_model_dtype",
        "--t5_cpu",
        "--frame_num", str(frame_num),
        "--base_seed", str(int(request.get("seed", -1))),
        "--prompt", str(request["prompt"]),
        "--save_file", str(output),
    ]


def _set_job(job_id: str, **values: Any) -> None:
    if not values:
        return
    allowed = {
        "status", "attempt", "artifact_name", "artifact_sha256",
        "artifact_size_bytes", "metadata_json", "error", "started_at",
        "completed_at", "updated_at",
    }
    invalid = set(values) - allowed
    if invalid:
        raise ValueError("invalid job field update")
    values["updated_at"] = _now()
    assignments = ", ".join(f"{key}=?" for key in values)
    with _connect() as c:
        c.execute(
            f"UPDATE wan_jobs SET {assignments} WHERE job_id=?",
            [*values.values(), job_id],
        )


def _get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as c:
        row = c.execute("SELECT * FROM wan_jobs WHERE job_id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def _recover_jobs() -> None:
    """Recover persisted worker jobs after a process/host restart."""
    _ensure()
    with _connect() as c:
        rows = c.execute("SELECT * FROM wan_jobs WHERE status='running'").fetchall()
    for row in rows:
        item = dict(row)
        final = OUTPUT_DIR / f"{item['job_id']}.mp4"
        try:
            if final.is_file() and not final.is_symlink() and final.stat().st_size > 100_000:
                digest = _sha256(final)
                request = json.loads(item["request_json"])
                frame_num = _frame_count(float(request["duration_seconds"]))
                metadata = _probe_artifact(final, request, frame_num)
                model = _model_state(force=False)
                source = _source_state()
                if not model.get("verified") or not model.get("license_verified") or not source.get("verified"):
                    raise RuntimeError("cannot recover a successful inference without re-verifying the model, license and official code")
                metadata.update({
                    "model_id": MODEL_ID,
                    "model": MODEL_NAME,
                    "model_revision": model.get("revision"),
                    "license": LICENSE_ID,
                    "license_url": LICENSE_URL,
                    "license_verified": True,
                    "source_revision": source.get("revision"),
                    "generation_method": "open_source_wan2_2_local_gpu_inference",
                    "creative_approval": "PENDING",
                    "rights_status": "REVIEW_REQUIRED",
                    "recovered_after_restart": True,
                })
                _set_job(
                    item["job_id"], status="succeeded", artifact_name=final.name,
                    artifact_sha256=digest, artifact_size_bytes=final.stat().st_size,
                    metadata_json=json.dumps(metadata), error=None, completed_at=_now(),
                )
                continue
        except Exception:
            pass
        try:
            final.unlink(missing_ok=True)
        except Exception:
            pass
        _set_job(
            item["job_id"], status="queued",
            error="Recovered after worker restart; interrupted inference will be retried.",
            started_at=None,
        )


def _frame_count(duration_seconds: float) -> int:
    # Wan2.2 requires 4n+1 frames. Round up so a generated clip covers the
    # requested scene duration instead of returning a subtly short candidate.
    import math

    frames_needed = math.ceil(float(duration_seconds) * 24.0)
    return max(49, min(121, math.ceil((frames_needed - 1) / 4) * 4 + 1))


def _run_job(job: dict[str, Any]) -> None:
    global ACTIVE_PROCESS
    job_id = str(job["job_id"])
    request = json.loads(job["request_json"])
    final = OUTPUT_DIR / f"{job_id}.mp4"
    partial = OUTPUT_DIR / f"{job_id}.partial.mp4"
    log_path = LOG_DIR / f"{job_id}.log"
    _set_job(job_id, status="running", attempt=int(job.get("attempt") or 0) + 1,
             started_at=_now(), error=None)
    process = None
    try:
        capabilities = _capabilities(force=True)
        if not capabilities.get("execution_ready"):
            raise RuntimeError("GPU worker is not execution-ready: " + "; ".join(capabilities.get("reasons") or []))
        # The model manifest and license hashes are checked before each new
        # execution when the model fingerprint changes or the worker restarts.
        frame_num = _frame_count(float(request["duration_seconds"]))
        try:
            partial.unlink(missing_ok=True)
        except Exception:
            pass
        env = dict(os.environ)
        env.update({
            "OMP_NUM_THREADS": "4",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "TOKENIZERS_PARALLELISM": "false",
        })
        command = _command(request, partial, frame_num)
        started = _now()
        with log_path.open("wb") as log_stream:
            process = subprocess.Popen(
                command,
                cwd=str(SOURCE_DIR),
                env=env,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
            ACTIVE_PROCESS = process
            while process.poll() is None:
                if STOP.is_set():
                    raise RuntimeError("worker is shutting down")
                if _now() - started > MAX_RUNTIME_SECONDS:
                    raise TimeoutError(f"model inference exceeded {MAX_RUNTIME_SECONDS} seconds")
                _set_job(job_id, status="running")
                time.sleep(2)
            return_code = int(process.returncode or 0)
        ACTIVE_PROCESS = None
        if return_code != 0:
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-1200:]
            except Exception:
                tail = ""
            raise RuntimeError(f"Wan inference exited with code {return_code}: {tail}")
        if not partial.is_file() or partial.stat().st_size < 100_000:
            raise RuntimeError("Wan inference completed without a usable video file")
        _normalize_hd_output(partial, final, request["aspect_ratio"])
        partial.unlink(missing_ok=True)
        metadata = _probe_artifact(final, request, frame_num)
        digest = _sha256(final)
        metadata.update({
            "model_id": MODEL_ID,
            "model": MODEL_NAME,
            "model_revision": capabilities["model"]["revision"],
            "license": LICENSE_ID,
            "license_url": LICENSE_URL,
            "license_verified": True,
            "source_revision": capabilities["source"]["revision"],
            "gpu": capabilities["hardware"]["name"],
            "seed": request.get("seed"),
            "frame_count": frame_num,
            "generation_method": "open_source_wan2_2_local_gpu_inference",
            "creative_approval": "PENDING",
            "rights_status": "REVIEW_REQUIRED",
        })
        _set_job(
            job_id, status="succeeded", artifact_name=final.name,
            artifact_sha256=digest, artifact_size_bytes=final.stat().st_size,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
            error=None, completed_at=_now(),
        )
    except Exception as exc:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    pass
        ACTIVE_PROCESS = None
        try:
            partial.unlink(missing_ok=True)
        except Exception:
            pass
        # A graceful service shutdown is recoverable: leave the job in running
        # state so startup can deterministically requeue it. Other failures are
        # terminal and remain visible to Creator Studio.
        if STOP.is_set():
            _set_job(
                job_id, status="running",
                error="Worker shutdown interrupted inference; startup recovery will retry this job.",
            )
        else:
            _set_job(
                job_id, status="failed",
                error=f"{type(exc).__name__}: {str(exc)[:1800]}",
            )


def _worker_loop() -> None:
    while not STOP.is_set():
        try:
            with _connect() as c:
                row = c.execute(
                    "SELECT * FROM wan_jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
            if row:
                _run_job(dict(row))
                continue
        except Exception:
            # Leave the persisted row visible; a supervisor or next poll can
            # expose the underlying failure without fabricating job completion.
            pass
        STOP.wait(1.0)


def _start_worker() -> None:
    global WORKER_THREAD, WORKER_LOCK_HANDLE, WORKER_PROCESS_LOCKED
    with WORKER_LOCK:
        if WORKER_THREAD and WORKER_THREAD.is_alive():
            return
        STOP.clear()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = DATA_DIR / ".wan-gpu-worker.lock"
        try:
            WORKER_LOCK_HANDLE = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(WORKER_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
            WORKER_PROCESS_LOCKED = True
        except (OSError, BlockingIOError):
            WORKER_PROCESS_LOCKED = False
            if WORKER_LOCK_HANDLE is not None:
                try:
                    os.close(WORKER_LOCK_HANDLE)
                except Exception:
                    pass
                WORKER_LOCK_HANDLE = None
            return
        _recover_jobs()
        WORKER_THREAD = threading.Thread(
            target=_worker_loop, name="ai-infinity-wan-gpu-worker", daemon=True
        )
        WORKER_THREAD.start()


def _stop_processes() -> None:
    global WORKER_LOCK_HANDLE, WORKER_PROCESS_LOCKED
    STOP.set()
    process = ACTIVE_PROCESS
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            except Exception:
                pass
    if WORKER_LOCK_HANDLE is not None:
        try:
            fcntl.flock(WORKER_LOCK_HANDLE, fcntl.LOCK_UN)
            os.close(WORKER_LOCK_HANDLE)
        except Exception:
            pass
        WORKER_LOCK_HANDLE = None
        WORKER_PROCESS_LOCKED = False


@app.on_event("startup")
def startup() -> None:
    _ensure()
    _start_worker()


@app.on_event("shutdown")
def shutdown() -> None:
    _stop_processes()


@app.get("/health")
def health() -> dict[str, Any]:
    # Public health is intentionally coarse; hardware/model details need auth.
    return {"service": "ai-infinity-wan-video-worker", "status": "online", "truthful": True}


@app.get("/v1/capabilities")
def capabilities(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _bearer(authorization)
    return _capabilities()


@app.post("/v1/jobs")
def create_job(payload: GenerateRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _bearer(authorization)
    capabilities = _capabilities()
    if not capabilities.get("execution_ready"):
        raise HTTPException(
            status_code=503,
            detail={"status": "unavailable", "reasons": capabilities.get("reasons") or []},
        )
    prompt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", payload.prompt).strip()
    if len(prompt) < 8:
        raise HTTPException(status_code=422, detail="prompt is too short after normalization")
    request_data = {
        "prompt": prompt,
        "duration_seconds": float(payload.duration_seconds),
        "aspect_ratio": payload.aspect_ratio,
        "seed": int(payload.seed),
    }
    request_sha = hashlib.sha256(json.dumps(request_data, sort_keys=True).encode()).hexdigest()
    with _connect() as c:
        old = c.execute(
            "SELECT * FROM wan_jobs WHERE idempotency_key=?",
            (payload.idempotency_key,),
        ).fetchone()
        if old:
            if old["request_sha256"] != request_sha:
                raise HTTPException(status_code=409, detail="idempotency key was already used for a different request")
            return _job_public(old)
        queued = int(c.execute("SELECT COUNT(*) FROM wan_jobs WHERE status='queued'").fetchone()[0])
        if queued >= MAX_QUEUED_JOBS:
            raise HTTPException(status_code=429, detail="GPU worker queue is full; retry after a job completes")
        job_id = "wan_" + uuid.uuid4().hex
        ts = _now()
        c.execute(
            "INSERT INTO wan_jobs(job_id,idempotency_key,request_sha256,request_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (job_id, payload.idempotency_key, request_sha, json.dumps(request_data), "queued", ts, ts),
        )
        row = c.execute("SELECT * FROM wan_jobs WHERE job_id=?", (job_id,)).fetchone()
    return _job_public(row)


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _bearer(authorization)
    if not re.fullmatch(r"wan_[a-f0-9]{32}", job_id):
        raise HTTPException(status_code=404, detail="job not found")
    row = _get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_public(row)


@app.get("/v1/jobs/{job_id}/artifact")
def get_artifact(job_id: str, authorization: str | None = Header(default=None)) -> FileResponse:
    _bearer(authorization)
    if not re.fullmatch(r"wan_[a-f0-9]{32}", job_id):
        raise HTTPException(status_code=404, detail="artifact not found")
    row = _get_job(job_id)
    if not row or row.get("status") != "succeeded":
        raise HTTPException(status_code=409, detail="artifact is not ready")
    path = OUTPUT_DIR / f"{job_id}.mp4"
    if not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=409, detail="persisted artifact is unavailable")
    if path.stat().st_size != int(row.get("artifact_size_bytes") or -1):
        raise HTTPException(status_code=409, detail="artifact size does not match its persisted job record")
    if _sha256(path) != str(row.get("artifact_sha256") or ""):
        raise HTTPException(status_code=409, detail="artifact SHA-256 does not match its persisted job record")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")
