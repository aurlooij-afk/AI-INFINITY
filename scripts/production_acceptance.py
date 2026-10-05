#!/usr/bin/env python3
from __future__ import annotations

import http.cookiejar
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("AI_INFINITY_ACCEPTANCE_DATA_DIR", tempfile.mkdtemp(prefix="ai-infinity-acceptance-")))
PORT = int(os.environ.get("AI_INFINITY_ACCEPTANCE_PORT", "8123"))
BASE = f"http://127.0.0.1:{PORT}"
TIMEOUT = int(os.environ.get("AI_INFINITY_ACCEPTANCE_TIMEOUT", "900"))

ENV = os.environ.copy()
ENV.update(
    {
        "AI_INFINITY_DATA_DIR": str(DATA),
        "AI_INFINITY_EXTERNAL_WORKER": "true",
        "AI_INFINITY_STORAGE_MODE": "local",
        "AI_INFINITY_PERSISTENCE_MODE": "portable-volume",
        "AI_INFINITY_FAST_MODE": "true",
        "AI_INFINITY_STUDIO_SMOKE": "false",
        "AI_INFINITY_FFMPEG_THREADS": "1",
        "AI_INFINITY_FFMPEG_FILTER_THREADS": "1",
        "AI_INFINITY_MEMORY_GUARD_PERCENT": "0.80",
        "AI_INFINITY_MEMORY_RESERVE_MB": "256",
        "AI_INFINITY_QUEUE_LEASE_SECONDS": "300",
        "AI_INFINITY_MAX_RETRIES": "2",
    }
)

jar = http.cookiejar.CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request(path: str, method: str = "GET", payload=None, timeout: int = 30):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with client.open(req, timeout=timeout) as resp:
        raw = resp.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            body = json.loads(text)
        except Exception:
            body = text
        return resp.status, body


def wait_health(deadline: float):
    while time.time() < deadline:
        try:
            status, body = request("/infinity/3708/health", timeout=5)
            if status == 200 and body.get("status") == "healthy":
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("runtime health did not become healthy")


def terminate(proc):
    if not proc or proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
        proc.wait(timeout=5)


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    web = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "runtime_main_3700:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        cwd=ROOT,
        env=ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    worker = subprocess.Popen(
        [sys.executable, "production_worker.py"],
        cwd=ROOT,
        env=ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + TIMEOUT
    try:
        wait_health(deadline)

        status, command = request(
            "/infinity/studio/director",
            "POST",
            {
                "command": "Create a 30 second vertical video about practical renewable energy for communities in Afghanistan, with clear narration, captions and a polished visual story.",
                "duration": 30,
                "format": "short",
                "aspect_ratio": "9:16",
                "content_type": "video",
                "quality_preset": "high",
                "language": "English",
            },
            timeout=30,
        )
        if status != 200 or not command.get("project_id"):
            raise RuntimeError(f"director did not queue a project: {command}")
        project_id = command["project_id"]
        print("Queued project:", project_id)

        finished = None
        while time.time() < deadline:
            _, project = request(f"/infinity/studio/project/{project_id}", timeout=20)
            state = project.get("status")
            print("Project:", state, project.get("stage"), project.get("progress"))
            if state in {"completed", "completed_with_qc_warnings"}:
                finished = project
                break
            if state in {"failed", "cancelled"}:
                raise RuntimeError(f"production ended in {state}: {project.get('error')}")
            time.sleep(3)

        if finished is None:
            raise RuntimeError("30-second production did not finish within the acceptance window")

        result = finished.get("result") or {}
        if result.get("status") != "completed":
            raise RuntimeError(f"production result is not a clean PASS: {result.get('status')}")
        if float(result.get("duration_seconds") or 0) < 25:
            raise RuntimeError(f"render duration too short: {result.get('duration_seconds')}")
        quality = result.get("quality") or {}
        if not quality.get("passed"):
            raise RuntimeError("quality gate did not pass")

        _, manifest = request(f"/infinity/studio/production-fabric/project/{project_id}", timeout=20)
        artifacts = {x.get("asset_name"): x for x in manifest.get("artifacts") or []}
        required = {
            "final.mp4",
            "thumbnail.jpg",
            "captions.srt",
            "script.md",
            "timeline.json",
            "package.zip",
            "manifest.json",
        }
        missing = sorted(required - set(artifacts))
        if missing:
            raise RuntimeError(f"required artifacts missing: {missing}")
        if not manifest.get("artifact_integrity_passed"):
            raise RuntimeError("artifact integrity verification failed")

        _, timeline = request(f"/infinity/studio/project/{project_id}/timeline", timeout=20)
        if not timeline.get("materialized"):
            raise RuntimeError("canonical timeline was not materialized")

        # Persistence gate: restart only the web process and reopen the same
        # completed project from the same durable data directory.
        terminate(web)
        web = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "runtime_main_3700:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
            ],
            cwd=ROOT,
            env=ENV,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        wait_health(time.time() + 45)
        _, reopened = request(f"/infinity/studio/project/{project_id}", timeout=20)
        if reopened.get("status") not in {"completed", "completed_with_qc_warnings"}:
            raise RuntimeError("completed project did not survive web restart")

        print("ACCEPTANCE PASS")
        print(json.dumps({
            "project_id": project_id,
            "duration_seconds": result.get("duration_seconds"),
            "quality_passed": quality.get("passed"),
            "artifact_count": manifest.get("artifact_count"),
            "timeline_materialized": timeline.get("materialized"),
            "restart_reopen_passed": True,
        }, indent=2))
        return 0
    finally:
        terminate(web)
        terminate(worker)


if __name__ == "__main__":
    raise SystemExit(main())
