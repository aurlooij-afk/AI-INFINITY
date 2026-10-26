"""Isolated, read-only bridge from AI Infinity research to API Anything.

This first integration deliberately exposes only the pinned bundled YouTube search
operation, uses the plain-HTTP tier, refuses user/registry overrides, and gives each
invocation a fresh private API Anything home so external sessions cannot cross users.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

UPSTREAM_COMMIT = "69bef64f5b3628f448d3d9c2435c455bcb6ea38d"
MAX_QUERY_CHARS = 250
MAX_OUTPUT_CHARS = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 12


def status() -> dict[str, Any]:
    executable = shutil.which("api-anything")
    return {
        "provider": "api-anything",
        "status": "installed" if executable else "not_installed",
        "available": bool(executable),
        "enabled_operation": "youtube.search",
        "mode": "bundled_read_only_plain_http",
        "browser_learning_enabled": False,
        "upstream_commit": UPSTREAM_COMMIT,
        "truthful": True,
    }


def _run_json(
    runner: Callable[..., Any],
    executable: str,
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: int,
) -> tuple[Any | None, str | None]:
    try:
        completed = runner(
            [executable, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            close_fds=True,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "execution_failed"

    stdout = str(getattr(completed, "stdout", "") or "")
    if len(stdout) > MAX_OUTPUT_CHARS:
        return None, "output_too_large"
    if getattr(completed, "returncode", 1) != 0:
        return None, "command_failed"
    try:
        return json.loads(stdout), None
    except (TypeError, ValueError):
        return None, "invalid_json"


def _clean_text(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:limit]


def youtube_search(query: str, limit: int = 10) -> dict[str, Any]:
    """Search public YouTube results through the bundled, read-only API Anything spec."""
    q = _clean_text(query, MAX_QUERY_CHARS)
    if not q:
        return {"status": "invalid_input", "items": [], "truthful": True}

    executable = shutil.which("api-anything")
    if not executable:
        return {"status": "not_configured", "items": [], "truthful": True}

    try:
        timeout = max(3, min(20, int(os.getenv("AI_INFINITY_API_ANYTHING_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))))
        safe_limit = max(1, min(10, int(limit or 10)))
    except (TypeError, ValueError, OverflowError):
        return {"status": "invalid_configuration", "items": [], "truthful": True}
    started = time.monotonic()

    try:
        # Do not inherit application API keys or a user's browser/session state into this tool.
        with tempfile.TemporaryDirectory(prefix="ai-infinity-api-anything-") as temporary:
            root = Path(temporary).resolve()
            home = root / "home"
            api_home = root / "api-anything"
            home.mkdir(mode=0o700)
            api_home.mkdir(mode=0o700)
            env = {
                "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": str(home),
                "API_ANYTHING_HOME": str(api_home),
                "TMPDIR": str(root),
                "NODE_ENV": "production",
            }

            sites, error = _run_json(
                subprocess.run, executable, ["sites"], cwd=str(root), env=env, timeout=timeout
            )
            if error:
                return {"status": error, "items": [], "truthful": True}
            site = next(
                (item for item in sites if isinstance(item, dict) and item.get("name") == "youtube"),
                None,
            ) if isinstance(sites, list) else None
            if not site or site.get("source") != "bundled":
                return {"status": "untrusted_site_spec", "items": [], "truthful": True}

            operations, error = _run_json(
                subprocess.run, executable, ["ops", "youtube"], cwd=str(root), env=env, timeout=timeout
            )
            if error:
                return {"status": error, "items": [], "truthful": True}
            ops = operations.get("operations") if isinstance(operations, dict) else None
            operation = next(
                (item for item in ops if isinstance(item, dict) and item.get("name") == "search"),
                None,
            ) if isinstance(ops, list) else None
            if not operation or operation.get("readOnly") is not True:
                return {"status": "operation_not_verified_read_only", "items": [], "truthful": True}

            args = [
                "call", "youtube", "search",
                "--json", json.dumps({"q": q}, ensure_ascii=False, separators=(",", ":")),
                "--max-tier", "1",
            ]
            result, error = _run_json(
                subprocess.run, executable, args, cwd=str(root), env=env, timeout=timeout
            )
            if error:
                return {"status": error, "items": [], "truthful": True}
            if not isinstance(result, dict) or result.get("ok") is not True:
                return {"status": "operation_failed", "items": [], "truthful": True}

            raw_items = result.get("data")
            if not isinstance(raw_items, list):
                return {"status": "invalid_result_shape", "items": [], "truthful": True}

            items = []
            for row in raw_items:
                if not isinstance(row, dict):
                    continue
                video_id = _clean_text(row.get("id"), 32)
                title = _clean_text(row.get("title"), 300)
                if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id) or not title:
                    continue
                items.append({
                    "id": video_id,
                    "title": title,
                    "channel": _clean_text(row.get("channel"), 160),
                    "views": _clean_text(row.get("views"), 80),
                    # Construct links only from a validated identifier on a fixed HTTPS host.
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "source": "API Anything / YouTube",
                })
                if len(items) >= safe_limit:
                    break

            return {
                "status": "ready" if items else "empty",
                "items": items,
                "query": q,
                "site": "youtube",
                "operation": "search",
                "transport_tier": result.get("tier"),
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "truthful": True,
            }
    except Exception as exc:
        # Do not return arbitrary stderr, URLs or process environment details to the user.
        return {"status": "internal_error", "error_type": type(exc).__name__, "items": [], "truthful": True}
