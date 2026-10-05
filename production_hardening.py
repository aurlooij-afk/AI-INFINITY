from __future__ import annotations

"""
AI Infinity production hardening layer.

Goals:
- keep heavy media work isolated to one ffmpeg process at a time;
- cap ffmpeg/filter parallelism so cgroup memory cannot spike from codec threads;
- watch the container cgroup while ffmpeg is running and fail the job cleanly
  before the kernel OOM-kills the whole web service;
- clean completed-job intermediates so long-running creator work does not fill
  the persistent volume;
- expose a machine-readable runtime hardening health surface;
- remain portable across hosting environments.
"""

import importlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional


VERSION = "TARGET-2050.3707"
BUILD = "PRODUCTION-HARDENING-RESOURCE-GUARD"

# Conservative defaults. They are intentionally safe on both the old 512 MiB
# Shared-CPU instances and larger production instances.
FFMPEG_THREADS = max(1, min(2, int(os.getenv("AI_INFINITY_FFMPEG_THREADS", "1"))))
FILTER_THREADS = max(1, min(2, int(os.getenv("AI_INFINITY_FFMPEG_FILTER_THREADS", "1"))))
MEMORY_GUARD_PERCENT = max(
    0.60, min(0.90, float(os.getenv("AI_INFINITY_MEMORY_GUARD_PERCENT", "0.75")))
)
MEMORY_RESERVE_MB = max(
    64, min(256, int(os.getenv("AI_INFINITY_MEMORY_RESERVE_MB", "128")))
)
MAX_HEAVY_JOBS = max(
    1, min(2, int(os.getenv("AI_INFINITY_MAX_HEAVY_JOBS", "1")))
)
PROCESS_POLL_SECONDS = max(
    0.05, min(0.5, float(os.getenv("AI_INFINITY_PROCESS_POLL_SECONDS", "0.20")))
)
CLEANUP_AGE_SECONDS = max(
    3600, int(os.getenv("AI_INFINITY_INTERMEDIATE_RETENTION_SECONDS", "21600"))
)

HEAVY_GATE = threading.BoundedSemaphore(MAX_HEAVY_JOBS)
STATE_LOCK = threading.RLock()
STATE: dict[str, Any] = {
    "active_ffmpeg": 0,
    "guard_trips": 0,
    "completed_cleanups": 0,
    "last_error": None,
    "applied": False,
}
ORIGINALS: dict[str, Callable[..., Any]] = {}


def _set_runtime_env() -> None:
    # Prevent hidden native-library thread explosions.
    values = {
        "OMP_NUM_THREADS": str(FFMPEG_THREADS),
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "BLIS_NUM_THREADS": "1",
        "MALLOC_ARENA_MAX": "2",
        "PYTHONUNBUFFERED": "1",
    }
    for key, value in values.items():
        os.environ.setdefault(key, value)


def _read_number(path: Path) -> Optional[int]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw or raw == "max":
            return None
        return int(raw)
    except Exception:
        return None


def cgroup_memory_limit_bytes() -> Optional[int]:
    value = _read_number(Path("/sys/fs/cgroup/memory.max"))
    if value is not None:
        return value
    return _read_number(Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"))


def cgroup_memory_current_bytes() -> Optional[int]:
    value = _read_number(Path("/sys/fs/cgroup/memory.current"))
    if value is not None:
        return value
    return _read_number(Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"))


def _memory_budget_bytes() -> Optional[int]:
    limit = cgroup_memory_limit_bytes()
    if not limit or limit < 128 * 1024 * 1024:
        return None
    pct_budget = int(limit * MEMORY_GUARD_PERCENT)
    reserve_budget = max(0, limit - MEMORY_RESERVE_MB * 1024 * 1024)
    budget = min(pct_budget, reserve_budget)
    return max(64 * 1024 * 1024, budget)


def _proc_rss_bytes(pid: int) -> int:
    try:
        status = Path(f"/proc/{pid}/status")
        for line in status.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0


def _terminate_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except Exception:
        try:
            process.terminate()
        except Exception:
            pass
    try:
        process.wait(timeout=2)
        return
    except Exception:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    try:
        process.wait(timeout=2)
    except Exception:
        pass


def _run_ffmpeg(*args: Any, timeout: int = 240) -> None:
    studio = importlib.import_module("studio_ultimate")
    project_id = getattr(getattr(studio, "ACTIVE_PROJECT", None), "project_id", None)

    acquired = HEAVY_GATE.acquire(timeout=max(5, min(int(timeout), 900)))
    if not acquired:
        raise RuntimeError("AI Infinity resource guard: media worker is busy")

    with STATE_LOCK:
        STATE["active_ffmpeg"] += 1

    try:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-threads",
            str(FFMPEG_THREADS),
            "-filter_threads",
            str(FILTER_THREADS),
            "-filter_complex_threads",
            str(FILTER_THREADS),
            *map(str, args),
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        registry = getattr(studio, "PROCESS_REGISTRY", None)
        process_lock = getattr(studio, "PROCESS_LOCK", None)
        if project_id and isinstance(registry, dict) and process_lock is not None:
            with process_lock:
                registry[project_id] = process

        deadline = time.monotonic() + max(5, int(timeout))
        budget = _memory_budget_bytes()
        guard_reason = None

        while process.poll() is None:
            if time.monotonic() > deadline:
                guard_reason = f"ffmpeg timeout after {int(timeout)}s"
                _terminate_group(process)
                break

            if budget is not None:
                current = cgroup_memory_current_bytes()
                if current is not None and current > budget:
                    guard_reason = (
                        "AI Infinity memory guard tripped before cgroup OOM: "
                        f"{current / 1048576:.0f} MiB > {budget / 1048576:.0f} MiB budget"
                    )
                    _terminate_group(process)
                    break

            time.sleep(PROCESS_POLL_SECONDS)

        stdout, stderr = process.communicate(timeout=3)
        if guard_reason:
            with STATE_LOCK:
                STATE["guard_trips"] += 1
                STATE["last_error"] = guard_reason
            raise RuntimeError(guard_reason)

        if process.returncode:
            detail = (stderr or stdout or "ffmpeg failed")[-4000:]
            with STATE_LOCK:
                STATE["last_error"] = detail
            raise RuntimeError(detail)
    finally:
        if project_id:
            registry = getattr(studio, "PROCESS_REGISTRY", None)
            process_lock = getattr(studio, "PROCESS_LOCK", None)
            if isinstance(registry, dict) and process_lock is not None:
                with process_lock:
                    registry.pop(project_id, None)
        with STATE_LOCK:
            STATE["active_ffmpeg"] = max(0, STATE["active_ffmpeg"] - 1)
        HEAVY_GATE.release()


def _cleanup_project_dir(project_dir: Path) -> int:
    if not project_dir.exists() or not project_dir.is_dir():
        return 0

    keep_exact = {
        "final.mp4",
        "package.zip",
        "thumbnail.jpg",
        "script.md",
        "captions.srt",
        "sources.json",
        "manifest.json",
        "feature_execution.json",
        "creator_experiments.json",
        "audio_master.mp3",
        "article.md",
        "social_campaign.md",
        "social_campaign.json",
        "seo.json",
        "accessibility.json",
        "provenance.json",
        "fact_check.json",
        "platform_manifest.json",
        "podcast_rss.xml",
    }
    removed = 0
    cutoff = time.time() - CLEANUP_AGE_SECONDS
    for path in project_dir.iterdir():
        try:
            if path.name in keep_exact:
                continue
            if path.is_dir():
                continue
            if path.stat().st_mtime > cutoff:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        except Exception:
            continue
    return removed


def _cleanup_completed_projects(studio: Any) -> int:
    root = Path(getattr(studio, "ROOT", "/tmp/ai-infinity/creator_studio"))
    removed = 0
    try:
        with getattr(studio, "DB_LOCK"), getattr(studio, "_connect")() as conn:
            rows = conn.execute(
                "SELECT project_id, status FROM studio_projects_3610 "
                "WHERE status IN ('completed','completed_with_qc_warnings','failed') "
                "ORDER BY updated_at DESC LIMIT 200"
            ).fetchall()
        for row in rows:
            project_id = str(row["project_id"])
            removed += _cleanup_project_dir(root / project_id)
    except Exception as exc:
        with STATE_LOCK:
            STATE["last_error"] = str(exc)[:500]
    if removed:
        with STATE_LOCK:
            STATE["completed_cleanups"] += removed
    return removed


def _janitor_loop() -> None:
    while True:
        try:
            studio = importlib.import_module("studio_ultimate")
            _cleanup_completed_projects(studio)
            root = Path(getattr(studio, "DATA_DIR", "/tmp/ai-infinity"))
            cutoff = time.time() - CLEANUP_AGE_SECONDS
            for dirname in ("storage-staging", "uploads"):
                base = root / dirname
                if not base.exists():
                    continue
                for item in base.rglob("*"):
                    try:
                        if item.is_file() and item.stat().st_mtime < cutoff:
                            item.unlink(missing_ok=True)
                    except Exception:
                        continue
        except Exception as exc:
            with STATE_LOCK:
                STATE["last_error"] = str(exc)[:500]
        time.sleep(900)


def runtime_health() -> dict[str, Any]:
    limit = cgroup_memory_limit_bytes()
    current = cgroup_memory_current_bytes()
    budget = _memory_budget_bytes()
    try:
        studio = importlib.import_module("studio_ultimate")
        data_dir = str(getattr(studio, "DATA_DIR", os.getenv("AI_INFINITY_DATA_DIR", "")))
        fast_mode = bool(getattr(studio, "FAST_MODE", True))
        active_project = getattr(getattr(studio, "ACTIVE_PROJECT", None), "project_id", None)
    except Exception:
        data_dir = os.getenv("AI_INFINITY_DATA_DIR", "")
        fast_mode = True
        active_project = None
    with STATE_LOCK:
        state = dict(STATE)
    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "resource_guard": True,
        "heavy_job_slots": MAX_HEAVY_JOBS,
        "ffmpeg_threads": FFMPEG_THREADS,
        "filter_threads": FILTER_THREADS,
        "memory_guard_percent": MEMORY_GUARD_PERCENT,
        "memory_limit_mb": round(limit / 1048576, 1) if limit else None,
        "memory_current_mb": round(current / 1048576, 1) if current else None,
        "memory_budget_mb": round(budget / 1048576, 1) if budget else None,
        "active_ffmpeg": state["active_ffmpeg"],
        "guard_trips": state["guard_trips"],
        "cleaned_intermediates": state["completed_cleanups"],
        "active_project": active_project,
        "fast_mode": fast_mode,
        "data_dir": data_dir,
        "persistence_mode": os.getenv("AI_INFINITY_PERSISTENCE_MODE", "unknown"),
        "truthful": True,
    }


def apply() -> dict[str, Any]:
    _set_runtime_env()
    studio = importlib.import_module("studio_ultimate")

    with STATE_LOCK:
        if STATE["applied"]:
            return runtime_health()

    original_ffmpeg = getattr(studio, "ffmpeg", None)
    if not callable(original_ffmpeg):
        raise RuntimeError("studio_ultimate.ffmpeg is unavailable")
    ORIGINALS["studio_ffmpeg"] = original_ffmpeg
    studio.ffmpeg = _run_ffmpeg

    try:
        factory = importlib.import_module("content_factory")
        original_factory = getattr(factory, "_ffmpeg", None)
        if callable(original_factory):
            ORIGINALS["factory_ffmpeg"] = original_factory

            def factory_ffmpeg(*args: Any, timeout: int = 240) -> None:
                return _run_ffmpeg(*args, timeout=timeout)

            factory._ffmpeg = factory_ffmpeg
    except Exception as exc:
        with STATE_LOCK:
            STATE["last_error"] = str(exc)[:500]

    original_run_project = getattr(studio, "run_project", None)
    if callable(original_run_project):
        ORIGINALS["studio_run_project"] = original_run_project

        def run_project_with_cleanup(*args: Any, **kwargs: Any) -> Any:
            result = original_run_project(*args, **kwargs)
            try:
                _cleanup_completed_projects(studio)
            except Exception as exc:
                with STATE_LOCK:
                    STATE["last_error"] = str(exc)[:500]
            return result

        studio.run_project = run_project_with_cleanup

    janitor = threading.Thread(
        target=_janitor_loop,
        name="ai-infinity-production-janitor",
        daemon=True,
    )
    janitor.start()

    with STATE_LOCK:
        STATE["applied"] = True
    return runtime_health()
