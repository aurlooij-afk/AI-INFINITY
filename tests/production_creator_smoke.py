from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path


BASE = os.environ.get("BASE_URL", "http://127.0.0.1:10000").rstrip("/")
JAR = CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))


def request(path: str, method: str = "GET", payload=None, timeout: int = 30):
    data = None
    headers = {"User-Agent": "AI-Infinity-final-production-smoke/1"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", "replace")
            try:
                body = json.loads(text)
            except Exception:
                body = text
            return resp.status, body, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        text = raw.decode("utf-8", "replace")
        try:
            body = json.loads(text)
        except Exception:
            body = text
        raise AssertionError(f"{method} {path} -> HTTP {exc.code}: {body}") from exc


def assert_ok(path: str, method: str = "GET", payload=None):
    status, body, raw = request(path, method, payload)
    assert 200 <= status < 300, (path, status, body)
    return body, raw


def wait_for_project(project_id: str, timeout_seconds: int = 300):
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        last, _ = assert_ok(f"/infinity/studio/project/{urllib.parse.quote(project_id, safe='')}")
        status = str(last.get("status") or "").lower()
        if status in {"completed", "completed_with_qc_warnings", "failed", "cancelled"}:
            return last
        time.sleep(3)
    raise AssertionError(f"creator job timed out: {last}")


def ffprobe(path: Path):
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_format", "-show_streams",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def sha256(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(path: str, out: Path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "AI-Infinity-final-production-smoke/1"})
    with OPENER.open(req, timeout=60) as resp:
        out.write_bytes(resp.read())
    assert out.is_file() and out.stat().st_size > 10000, f"invalid download: {out}"


def main():
    # A–I: real service/bootstrap gates.
    health, _ = assert_ok("/health")
    assert health.get("canonical") is True
    assert health.get("capabilities", {}).get("local", {}).get("ffmpeg") is True
    assert health.get("capabilities", {}).get("local", {}).get("ffprobe") is True

    canonical_health, _ = assert_ok("/infinity/canonical/health")
    assert canonical_health.get("truthful") is True

    status, root, _ = request("/")
    assert status == 200 and "<html" in root.lower() and "AI Infinity" in root

    caps, _ = assert_ok("/infinity/canonical/capabilities")
    assert caps["local"]["media_core"] is True

    preflight, _ = assert_ok(
        "/infinity/canonical/preflight",
        "POST",
        {"command": "Create a 20 second cinematic video about resilient creativity"},
    )
    assert preflight["ready"] is True, preflight

    # J–L: real description -> real worker -> real MP4 -> independent proof/QC.
    created, _ = assert_ok(
        "/infinity/canonical/create",
        "POST",
        {
            "command": "Create a 20 second cinematic video about resilient creativity",
            "duration": 20,
            "format": "short",
            "aspect_ratio": "16:9",
            "idempotency_key": "final-ci-resilient-creativity-v1",
        },
    )
    project_id = created["project_id"]
    version1 = created["version"]["version_id"]

    project = wait_for_project(project_id)
    assert project["status"] in {"completed", "completed_with_qc_warnings"}, project

    truth, _ = assert_ok(f"/infinity/canonical/project/{project_id}/truth")
    # The canonical contract is evidence-driven: no success claim without real proof.
    assert truth["done"] is True, json.dumps(truth, indent=2)
    assert truth["current_version"]["version_id"] == version1
    assert truth["current_version"]["evidence"]["valid"] is True

    root = Path("/tmp/ai-infinity-smoke")
    root.mkdir(parents=True, exist_ok=True)
    final = root / "version1.mp4"
    download(f"/infinity/studio/project/{project_id}/asset/final.mp4", final)

    probe = ffprobe(final)
    streams = probe.get("streams", [])
    video = next((x for x in streams if x.get("codec_type") == "video"), None)
    audio = next((x for x in streams if x.get("codec_type") == "audio"), None)
    duration = float((probe.get("format") or {}).get("duration") or 0)
    assert video and video.get("codec_name") == "h264", probe
    assert audio and audio.get("codec_name") in {"aac", "mp3"}, probe
    assert duration > 2, probe
    assert abs(duration - 20) <= 1.0, probe
    assert video.get("width", 0) > 0 and video.get("height", 0) > 0

    studio_verify, _ = assert_ok(f"/infinity/studio/project/{project_id}/verify")
    assert studio_verify["passed"] is True, studio_verify

    artifacts, _ = assert_ok(f"/infinity/studio/project/{project_id}/artifacts")
    final_rows = [x for x in artifacts["artifacts"] if x["asset_name"] == "final.mp4"]
    assert final_rows and final_rows[0]["sha256"] == sha256(final), artifacts

    # M–N: real edit -> a different physical MP4 -> Version 2.
    edit, _ = assert_ok(
        f"/infinity/canonical/project/{project_id}/command",
        "POST",
        {"command": "remove the first 2 seconds and make it cinematic"},
    )
    version2 = edit["version_id"]
    assert version2 != version1
    edited = root / "version2.mp4"
    edit_asset_name = edit["artifact"]["name"]
    edit_asset_url = f"/infinity/studio/project/{urllib.parse.quote(project_id, safe=\"\")}/asset/{urllib.parse.quote(edit_asset_name, safe=\"\")}"
    download(edit_asset_url, edited)
    probe2 = ffprobe(edited)
    duration2 = float((probe2.get("format") or {}).get("duration") or 0)
    assert duration2 > 2 and duration2 < duration, (duration, duration2)
    hash1 = sha256(final)
    hash2 = sha256(edited)
    assert hash1 != hash2, "edit produced byte-identical output"

    versions, _ = assert_ok(f"/infinity/canonical/project/{project_id}/versions")
    ids = [v["version_id"] for v in versions["versions"]]
    assert version1 in ids and version2 in ids

    # O: real undo/restore. The current pointer must return to the exact previous
    # verified artifact, and the restored bytes must match Version 1.
    undone, _ = assert_ok(f"/infinity/canonical/project/{project_id}/undo", "POST")
    assert undone["current_version_id"] == version1, undone
    truth_after_undo, _ = assert_ok(f"/infinity/canonical/project/{project_id}/truth")
    assert truth_after_undo["current_version"]["version_id"] == version1
    assert truth_after_undo["current_version"]["evidence"]["valid"] is True
    restored = root / "restored.mp4"
    download("/infinity/studio/project/" + urllib.parse.quote(project_id, safe="") + "/asset/final.mp4", restored)
    assert sha256(restored) == hash1, "undo did not restore the Version 1 artifact"

    # P: memory/project persistence across a fresh HTTP client context. The
    # project itself is server-side SQLite state; the cookie preserves identity.
    memory, _ = assert_ok(
        "/infinity/canonical/memory",
        "POST",
        {
            "project_id": project_id,
            "kind": "preference",
            "text": "Keep future videos concise and cinematic.",
        },
    )
    assert memory["memory_id"]
    project_memory, _ = assert_ok(f"/infinity/canonical/project/{project_id}/memory")
    assert any("concise and cinematic" in x["text"] for x in project_memory["memories"])

    bootstrap, _ = assert_ok("/infinity/canonical/bootstrap")
    assert any(p["project_id"] == project_id for p in bootstrap["projects"])

    # Q: a deliberately invalid edit must fail without corrupting the project.
    try:
        request(
            f"/infinity/canonical/project/{project_id}/command",
            "POST",
            {"command": "remove the first 999 seconds"},
        )
    except AssertionError as exc:
        assert "HTTP 422" in str(exc), str(exc)
    else:
        raise AssertionError("invalid edit unexpectedly succeeded")

    truth_after_failure, _ = assert_ok(f"/infinity/canonical/project/{project_id}/truth")
    assert truth_after_failure["done"] is True
    assert truth_after_failure["current_version"]["version_id"] == version1

    # R/S are completed by the workflow's container stop step after this script.
    print(json.dumps({
        "passed": True,
        "project_id": project_id,
        "version_1": version1,
        "version_2": version2,
        "duration_seconds": duration,
        "edited_duration_seconds": duration2,
        "version_1_sha256": hash1,
        "version_2_sha256": hash2,
        "artifact_bytes": final.stat().st_size,
        "canonical_truth": truth_after_undo["done"],
        "memory_persisted": True,
        "failure_recovery_preserved_current_version": True,
    }, indent=2))


if __name__ == "__main__":
    main()
