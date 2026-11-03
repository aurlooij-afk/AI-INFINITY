from __future__ import annotations

"""Run one explicitly authorized live Wan2.2 GPU inference and verify its bytes.

The script never sends a generation job unless a private HTTPS worker URL, bearer
token and AI_INFINITY_WAN_LIVE_INFERENCE_ACK=true are all present. It proves a
real 2-second worker output with independent hash, FFprobe and FFmpeg checks.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

PINNED_SOURCE_REVISION = "1ea34ff48f87168174e12956e200b1d908b1c5ff"
MODEL_ID = "Wan-AI/Wan2.2-TI2V-5B"
PROVIDER_ID = "wan2.2-ti2v-5b"
LICENSE_ID = "apache-2.0"


class VerificationBlocked(RuntimeError):
    pass


def _emit(result: dict[str, Any], output: Path | None) -> None:
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("WAN_LIVE_INFERENCE_RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True))


def _flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_error(value: Any, limit: int = 700) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)https?://[^\s\"']+", "[redacted-url]", text)
    text = re.sub(r"(?i)\b(authorization|token|secret|api[_-]?key)\b\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
    return " ".join(text.split())[:limit]


def _worker_request(base: str, token: str, method: str, path: str, *, payload: dict | None = None, timeout: tuple[int, int] = (10, 30)):
    if not path.startswith("/v1/"):
        raise ValueError("worker route is outside the authenticated /v1 API")
    url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    response = requests.request(
        method,
        url,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        json=payload,
        timeout=timeout,
        allow_redirects=False,
    )
    if 300 <= response.status_code < 400:
        raise RuntimeError("worker API attempted an HTTP redirect; redirects are rejected")
    if not response.ok:
        raise RuntimeError(f"worker API returned HTTP {response.status_code}: {_safe_error(response.text)}")
    return response


def _check_capabilities(value: dict[str, Any]) -> None:
    model = value.get("model") if isinstance(value.get("model"), dict) else {}
    source = value.get("source") if isinstance(value.get("source"), dict) else {}
    hardware = value.get("hardware") if isinstance(value.get("hardware"), dict) else {}
    storage = value.get("storage") if isinstance(value.get("storage"), dict) else {}
    reasons = list(value.get("reasons") or [])
    checks = {
        "execution_ready": value.get("execution_ready") is True,
        "provider": value.get("provider") == PROVIDER_ID,
        "model_id": model.get("model_id") == MODEL_ID,
        "model_verified": model.get("verified") is True,
        "model_license": model.get("license") == LICENSE_ID and model.get("license_verified") is True,
        "model_terms": model.get("terms_acknowledged") is True,
        "source_verified": source.get("verified") is True and source.get("revision") == PINNED_SOURCE_REVISION,
        "gpu_available": hardware.get("available") is True,
        "gpu_vram": int(hardware.get("total_vram_mb") or 0) >= 24576 and int(hardware.get("free_vram_mb") or 0) >= 22000,
        "persistent_storage": storage.get("persistent") is True,
        "ffmpeg": value.get("ffmpeg") is True,
        "ffprobe": value.get("ffprobe") is True,
    }
    if not all(checks.values()):
        missing = [key for key, passed in checks.items() if not passed]
        raise VerificationBlocked("worker has not proved all live inference prerequisites: " + ", ".join(missing) + ("; " + "; ".join(map(str, reasons)) if reasons else ""))


def _download_artifact(base: str, token: str, path: str, target: Path, expected_sha: str, expected_size: int) -> None:
    if not path.startswith("/v1/jobs/") or not path.endswith("/artifact"):
        raise RuntimeError("worker returned an unsupported artifact route")
    url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    response = requests.get(
        url,
        headers={"Authorization": "Bearer " + token, "Accept": "video/mp4"},
        timeout=(10, 60),
        allow_redirects=False,
        stream=True,
    )
    if 300 <= response.status_code < 400 or not response.ok:
        raise RuntimeError(f"worker artifact download returned HTTP {response.status_code}")
    digest = hashlib.sha256()
    total = 0
    try:
        with target.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 1024 * 1024 * 1024:
                    raise RuntimeError("worker artifact exceeded the 1 GiB verification ceiling")
                digest.update(chunk)
                stream.write(chunk)
    finally:
        response.close()
    actual_sha = digest.hexdigest()
    if total != expected_size or actual_sha != expected_sha:
        raise RuntimeError("downloaded worker artifact size or SHA-256 does not match the job record")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--max-wait-seconds", type=int, default=900)
    args = parser.parse_args()

    base = os.getenv("AI_INFINITY_WAN_WORKER_URL", "").strip().rstrip("/")
    token = os.getenv("AI_INFINITY_WAN_WORKER_TOKEN", "").strip()
    acknowledged = _flag(os.getenv("AI_INFINITY_WAN_LIVE_INFERENCE_ACK"))
    if not base or not token:
        _emit({
            "status": "BLOCKED",
            "reason": "authenticated Wan2.2 worker URL/token are not configured",
            "inference_request_sent": False,
            "truthful": True,
        }, args.output)
        return 3
    if not acknowledged:
        _emit({
            "status": "BLOCKED",
            "reason": "owner has not authorized a live GPU inference smoke job",
            "inference_request_sent": False,
            "truthful": True,
        }, args.output)
        return 3

    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        _emit({
            "status": "FAIL",
            "reason": "worker URL must be a valid HTTPS origin without embedded credentials, query or fragment",
            "inference_request_sent": False,
            "truthful": True,
        }, args.output)
        return 2

    job_id = None
    try:
        capability_response = _worker_request(base, token, "GET", "/v1/capabilities")
        capabilities = capability_response.json()
        if not isinstance(capabilities, dict):
            raise RuntimeError("worker capabilities response is not a JSON object")
        try:
            _check_capabilities(capabilities)
        except VerificationBlocked as exc:
            raise VerificationBlocked(str(exc)) from None

        idempotency_key = "ai-infinity-live-smoke-" + uuid.uuid4().hex
        payload = {
            "prompt": "A cinematic aerial tracking shot over a dramatic mountain valley at sunrise, natural atmospheric light, realistic landscape cinematography.",
            "duration_seconds": 2.0,
            "aspect_ratio": "16:9",
            "seed": 20503624,
            "idempotency_key": idempotency_key,
        }
        created = _worker_request(base, token, "POST", "/v1/jobs", payload=payload).json()
        job_id = str(created.get("job_id") or "")
        if not re.fullmatch(r"wan_[a-f0-9]{32}", job_id):
            raise RuntimeError("worker did not return a valid persistent Wan job id")

        deadline = time.monotonic() + max(60, min(int(args.max_wait_seconds), 1800))
        current = created
        while time.monotonic() < deadline:
            status = str(current.get("status") or "").lower()
            if status == "succeeded":
                break
            if status in {"failed", "cancelled", "canceled"}:
                raise RuntimeError("Wan inference job ended as " + status + ": " + _safe_error(current.get("error")))
            time.sleep(3)
            current = _worker_request(base, token, "GET", f"/v1/jobs/{job_id}").json()
        else:
            raise TimeoutError("Wan inference job did not finish before the configured live verification deadline")

        metadata = current.get("metadata") if isinstance(current.get("metadata"), dict) else {}
        artifact = current.get("artifact") if isinstance(current.get("artifact"), dict) else {}
        expected_sha = str(artifact.get("sha256") or current.get("artifact_sha256") or "")
        expected_size = int(artifact.get("size_bytes") or current.get("artifact_size_bytes") or 0)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or expected_size < 100_000:
            raise RuntimeError("successful Wan job has no valid artifact SHA-256 or size")
        if (
            metadata.get("model_id") != MODEL_ID
            or metadata.get("license") != LICENSE_ID
            or metadata.get("license_verified") is not True
            or metadata.get("source_revision") != PINNED_SOURCE_REVISION
            or metadata.get("generation_method") != "open_source_wan2_2_local_gpu_inference"
        ):
            raise RuntimeError("successful Wan job metadata does not prove the pinned open-source model/source/license")
        artifact_path = str(artifact.get("url") or "")
        if not artifact_path:
            raise RuntimeError("successful Wan job has no artifact download route")

        with tempfile.TemporaryDirectory(prefix="ai-infinity-wan-live-verify-") as raw:
            media = Path(raw) / "wan-live-smoke.mp4"
            _download_artifact(base, token, artifact_path, media, expected_sha, expected_size)

            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(media)],
                capture_output=True, text=True, timeout=60, check=False,
            )
            if probe.returncode != 0:
                raise RuntimeError("FFprobe failed on the downloaded Wan output")
            probed = json.loads(probe.stdout or "{}")
            video = next((row for row in probed.get("streams", []) if row.get("codec_type") == "video"), {})
            if (int(video.get("width") or 0), int(video.get("height") or 0)) != (1280, 720):
                raise RuntimeError("Wan output dimensions do not match the requested 720p landscape profile")
            duration = float((probed.get("format") or {}).get("duration") or 0)
            if not 1.8 <= duration <= 2.6:
                raise RuntimeError(f"Wan output duration is outside the two-second smoke tolerance: {duration:.3f}s")
            decode = subprocess.run(
                ["ffmpeg", "-v", "error", "-xerror", "-i", str(media), "-f", "null", "-"],
                capture_output=True, text=True, timeout=300, check=False,
            )
            if decode.returncode != 0:
                raise RuntimeError("full FFmpeg decode failed on the downloaded Wan output")

        _emit({
            "status": "PASS",
            "provider": PROVIDER_ID,
            "model_id": MODEL_ID,
            "model_revision": metadata.get("model_revision"),
            "source_revision": metadata.get("source_revision"),
            "license": metadata.get("license"),
            "license_verified": metadata.get("license_verified"),
            "gpu": capabilities.get("hardware"),
            "storage_persistent": (capabilities.get("storage") or {}).get("persistent") is True,
            "job_id": job_id,
            "duration_seconds": duration,
            "resolution": [1280, 720],
            "full_decode": "PASS",
            "artifact_size_bytes": expected_size,
            "artifact_sha256": expected_sha,
            "inference_request_sent": True,
            "human_creative_approval": "PENDING",
            "rights_status": "REVIEW_REQUIRED",
            "truthful": True,
        }, args.output)
        return 0
    except VerificationBlocked as exc:
        _emit({
            "status": "BLOCKED",
            "reason": _safe_error(exc),
            "job_id": job_id,
            "inference_request_sent": False,
            "truthful": True,
        }, args.output)
        return 3
    except Exception as exc:
        _emit({
            "status": "FAIL",
            "error_type": type(exc).__name__,
            "error": _safe_error(exc),
            "job_id": job_id,
            "inference_request_sent": bool(job_id),
            "truthful": True,
        }, args.output)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
