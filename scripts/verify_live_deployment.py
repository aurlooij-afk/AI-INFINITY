from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
import zipfile
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _open_json(opener: Any, url: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "AI-Infinity/live-deployment-verifier"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="GET" if body is None else "POST")
    with opener.open(request, timeout=timeout) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"HTTP_{response.status}")
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("unexpected_non_object_json")
    return value


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"URL_{type(exc.reason).__name__}"
    return type(exc).__name__


def _probe_media(path: Path, expected_duration: float) -> dict[str, Any]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=90,
    )
    if probe.returncode != 0:
        raise RuntimeError("ffprobe_failed")
    info = json.loads(probe.stdout)
    streams = info.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float((info.get("format") or {}).get("duration") or 0)
    if not video or not audio:
        raise RuntimeError("missing_video_or_audio_stream")
    if duration <= 0 or abs(duration - expected_duration) > 1.0:
        raise RuntimeError(f"duration_mismatch:{duration:.3f}")
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=180,
    )
    if decoded.returncode != 0:
        raise RuntimeError("full_media_decode_failed")
    return {
        "duration_seconds": duration,
        "video": {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "frame_rate": video.get("avg_frame_rate"),
        },
        "audio": {"codec": audio.get("codec_name"), "channels": audio.get("channels")},
        "full_decode_passed": True,
    }


def verify(base_url: str, expected_sha: str, output: Path, wait_seconds: int = 900) -> dict[str, Any]:
    base = base_url.strip().rstrip("/")
    if not base.startswith("https://"):
        result = {"status": "BLOCKED", "reason": "live_base_url_must_be_https", "truthful": True}
        _write(output, result)
        return result
    if len(expected_sha) != 40 or any(char not in "0123456789abcdef" for char in expected_sha.lower()):
        result = {"status": "FAIL", "reason": "expected_commit_sha_invalid", "truthful": True}
        _write(output, result)
        return result

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    deadline = time.time() + max(30, wait_seconds)
    health: dict[str, Any] | None = None
    last_health_error = ""
    while time.time() < deadline:
        try:
            health = _open_json(opener, base + "/health", timeout=30)
            deployed = str(health.get("deployment_revision") or "").lower()
            if deployed == expected_sha.lower():
                break
            last_health_error = "revision_mismatch:" + (deployed or "missing")
        except Exception as exc:
            last_health_error = _safe_error(exc)
        time.sleep(5)
    else:
        result = {
            "status": "BLOCKED",
            "reason": "render_has_not_served_the_exact_candidate_revision",
            "expected_commit_sha": expected_sha.lower(),
            "observed_commit_sha": str((health or {}).get("deployment_revision") or ""),
            "last_health_error": last_health_error,
            "truthful": True,
        }
        _write(output, result)
        return result

    assert health is not None
    project_id = ""
    try:
        command = (
            "Create a 20-second English cinematic documentary about accessible creative tools, "
            "with real relevant source visuals, narration, synchronized captions, thumbnail, "
            "production manifest, rights manifest and a complete editable creator package."
        )
        created = _open_json(opener, base + "/infinity/canonical/create", {
            "command": command, "duration": 20, "format": "short", "aspect_ratio": "16:9",
            "content_type": "video",
        }, timeout=90)
        project_id = str(created.get("project_id") or ((created.get("result") or {}).get("project_id") or ""))
        if not project_id:
            raise RuntimeError("live_create_did_not_return_project_id")

        project_url = base + "/infinity/studio/project/" + urllib.parse.quote(project_id, safe="")
        project_state: dict[str, Any] = {}
        project_deadline = time.time() + 600
        while time.time() < project_deadline:
            project_state = _open_json(opener, project_url, timeout=30)
            state = str(project_state.get("state") or "").upper()
            status = str(project_state.get("status") or "").lower()
            if state == "COMPLETED" or status in {"completed", "failed", "cancelled", "completed_with_qc_warnings"}:
                if state != "COMPLETED" or status != "completed":
                    raise RuntimeError("live_creator_project_not_completed")
                break
            time.sleep(4)
        else:
            raise RuntimeError("live_creator_project_timed_out")

        artifacts = _open_json(opener, project_url + "/artifacts", timeout=30)
        names: dict[str, dict[str, Any]] = {}
        for item in artifacts.get("artifacts", []) if isinstance(artifacts.get("artifacts"), list) else []:
            name = str(item.get("asset_name") or item.get("name") or "")
            if name:
                names[name] = item
        required = [
            "final.mp4", "thumbnail.jpg", "audio_master.mp3", "captions.srt",
            "article.md", "seo.json", "social_campaign.json",
            "production_manifest.json", "rights_manifest.json", "package.zip",
        ]
        missing = sorted(set(required) - set(names))
        if missing:
            raise RuntimeError("live_output_missing_required_artifacts:" + ",".join(missing))

        with tempfile.TemporaryDirectory(prefix="ai-infinity-live-verify-") as directory:
            root = Path(directory)
            downloaded: dict[str, bytes] = {}
            for name in required:
                asset_url = project_url + "/asset/" + urllib.parse.quote(name, safe="")
                request = urllib.request.Request(asset_url, headers={"User-Agent": "AI-Infinity/live-deployment-verifier"})
                with opener.open(request, timeout=120) as response:
                    data = response.read()
                if not data:
                    raise RuntimeError("live_output_empty_artifact:" + name)
                downloaded[name] = data
            video_path = root / "final.mp4"
            video_path.write_bytes(downloaded["final.mp4"])
            media = _probe_media(video_path, 20.0)
            package_path = root / "package.zip"
            package_path.write_bytes(downloaded["package.zip"])
            with zipfile.ZipFile(package_path) as archive:
                package_entries = set(archive.namelist())
            expected_package_entries = set(required) - {"package.zip"}
            if not expected_package_entries.issubset(package_entries):
                raise RuntimeError("live_package_missing_required_entries")
            manifest = json.loads(downloaded["production_manifest.json"].decode("utf-8"))
            rights = json.loads(downloaded["rights_manifest.json"].decode("utf-8"))
            quality = manifest.get("quality_truth_checks") if isinstance(manifest, dict) else {}
            quality = quality if isinstance(quality, dict) else {}
            rights_summary = {
                "unresolved_count": quality.get("visual_rights_unresolved_count"),
                "review_required_count": quality.get("visual_rights_review_required_count"),
                "rights_cleared_count": quality.get("visual_rights_cleared_count"),
                "fallback_visual_count": quality.get("fallback_visual_count"),
                "manifest_sha256": hashlib.sha256(downloaded["rights_manifest.json"]).hexdigest(),
                "manifest_parse_passed": isinstance(rights, (dict, list)),
            }
            result = {
                "schema": "ai-infinity-live-deployment-verification.v1",
                "status": "PASS",
                "expected_commit_sha": expected_sha.lower(),
                "deployed_commit_sha": str(health.get("deployment_revision") or "").lower(),
                "health_status": health.get("status"),
                "project_id": project_id,
                "project_status": project_state.get("status"),
                "final_media_sha256": _sha256(video_path),
                "final_media_bytes": video_path.stat().st_size,
                "media_inspection": media,
                "required_artifact_bytes": {name: len(data) for name, data in downloaded.items()},
                "package_entries_verified": True,
                "rights_evidence": rights_summary,
                "storage_health": (
                    ((health.get("capabilities") or {}).get("storage") or {})
                    if isinstance(health.get("capabilities"), dict) else {}
                ),
                "truthful": True,
            }
    except Exception as exc:
        result = {
            "schema": "ai-infinity-live-deployment-verification.v1",
            "status": "FAIL",
            "expected_commit_sha": expected_sha.lower(),
            "deployed_commit_sha": str((health or {}).get("deployment_revision") or "").lower(),
            "project_id": project_id or None,
            "reason": _safe_error(exc) if not str(exc).startswith("live_") else str(exc),
            "truthful": True,
        }

    _write(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the exact live Render revision and one real creator production.")
    parser.add_argument("--base-url", default=os.getenv("AI_INFINITY_LIVE_BASE_URL", ""))
    parser.add_argument("--expected-sha", default=os.getenv("AI_INFINITY_EXPECTED_COMMIT_SHA", ""))
    parser.add_argument("--output", type=Path, default=Path("acceptance-evidence/live-deployment-verification.json"))
    parser.add_argument("--wait-seconds", type=int, default=900)
    args = parser.parse_args()
    result = verify(args.base_url, args.expected_sha, args.output, args.wait_seconds)
    print("LIVE_DEPLOYMENT_VERIFICATION=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
    # A gate result is carried in JSON; the certificate builder is the release
    # authority and will refuse any status other than PASS.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
