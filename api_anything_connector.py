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
import threading
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
        "enabled_operations": ["youtube.search", "hacker-news.search"],
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


def _bundled_search(
    site_name: str,
    operation_name: str,
    query_parameter: str,
    query: str,
    limit: int,
    result_kind: str,
) -> dict[str, Any]:
    """Execute a fixed, allowlisted bundled read-only research operation in isolation."""
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
        # Never inherit application provider keys, browser cookies or other session state.
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
                (item for item in sites if isinstance(item, dict) and item.get("name") == site_name),
                None,
            ) if isinstance(sites, list) else None
            if not site or site.get("source") != "bundled":
                return {"status": "untrusted_site_spec", "items": [], "truthful": True}

            operations, error = _run_json(
                subprocess.run, executable, ["ops", site_name], cwd=str(root), env=env, timeout=timeout
            )
            if error:
                return {"status": error, "items": [], "truthful": True}
            ops = operations.get("operations") if isinstance(operations, dict) else None
            operation = next(
                (item for item in ops if isinstance(item, dict) and item.get("name") == operation_name),
                None,
            ) if isinstance(ops, list) else None
            if not operation or operation.get("readOnly") is not True:
                return {"status": "operation_not_verified_read_only", "items": [], "truthful": True}

            args = [
                "call", site_name, operation_name,
                "--json", json.dumps({query_parameter: q}, ensure_ascii=False, separators=(",", ":")),
                "--max-tier", "1",
            ]
            result, error = _run_json(
                subprocess.run, executable, args, cwd=str(root), env=env, timeout=timeout
            )
            if error:
                return {"status": error, "items": [], "truthful": True}
            if not isinstance(result, dict) or result.get("ok") is not True:
                return {"status": "operation_failed", "items": [], "truthful": True}
            if result.get("tier") != 1:
                return {"status": "unexpected_transport_tier", "items": [], "truthful": True}
            raw_items = result.get("data")
            if not isinstance(raw_items, list):
                return {"status": "invalid_result_shape", "items": [], "truthful": True}

            items = []
            for row in raw_items:
                if not isinstance(row, dict):
                    continue
                title = _clean_text(row.get("title") or row.get("name"), 300)
                if not title:
                    continue
                if result_kind == "youtube":
                    item_id = _clean_text(row.get("id"), 32)
                    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", item_id):
                        continue
                    url = f"https://www.youtube.com/watch?v={item_id}"
                    normalized = {
                        "id": item_id,
                        "title": title,
                        "channel": _clean_text(row.get("channel"), 160),
                        "views": _clean_text(row.get("views"), 80),
                        "url": url,
                        "source": "API Anything / YouTube",
                    }
                elif result_kind == "hacker-news":
                    url = _clean_text(row.get("url"), 1000)
                    parsed = urlparse(url)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                        continue
                    host = parsed.hostname.rstrip(".").lower()
                    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".localhost")):
                        continue
                    normalized = {
                        "id": _clean_text(row.get("objectID"), 80),
                        "title": title,
                        "url": url,
                        "author": _clean_text(row.get("author"), 120),
                        "points": _clean_text(row.get("points"), 40),
                        "comments": _clean_text(row.get("num_comments") or row.get("comments"), 40),
                        "source": "API Anything / Hacker News",
                    }
                else:
                    return {"status": "operation_not_allowlisted", "items": [], "truthful": True}
                items.append(normalized)
                if len(items) >= safe_limit:
                    break

            return {
                "status": "ready" if items else "empty",
                "items": items,
                "query": q,
                "site": site_name,
                "operation": operation_name,
                "transport_tier": result.get("tier"),
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "truthful": True,
            }
    except Exception as exc:
        return {"status": "internal_error", "error_type": type(exc).__name__, "items": [], "truthful": True}


# The API Anything CLI pacing is process-local. Keep a shared process-level gate/cooldown
# because every invocation intentionally uses a fresh isolated home.
_EXECUTION_GATE = threading.BoundedSemaphore(1)
_RATE_LOCK = threading.Lock()
_LAST_CALL_AT: dict[str, float] = {}
MIN_SITE_INTERVAL_SECONDS = 1.0

# This explicit allowlist is the capability boundary. We never execute arbitrary site/op names.
_ALLOWED_SEARCHES = {
    "youtube.search": ("youtube", "search", "q", "youtube"),
    "hacker-news.search": ("hacker-news", "search", "query", "hacker-news"),
}


def _rate_limited_search(capability: str, query: str, limit: int = 10) -> dict[str, Any]:
    config = _ALLOWED_SEARCHES.get(capability)
    if not config:
        return {"status": "operation_not_allowlisted", "items": [], "truthful": True}
    q = _clean_text(query, MAX_QUERY_CHARS)
    if not q:
        return {"status": "invalid_input", "items": [], "truthful": True}
    if not shutil.which("api-anything"):
        return {"status": "not_configured", "items": [], "truthful": True}
    try:
        max(3, min(20, int(os.getenv("AI_INFINITY_API_ANYTHING_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))))
        safe_limit = max(1, min(10, int(limit or 10)))
    except (TypeError, ValueError, OverflowError):
        return {"status": "invalid_configuration", "items": [], "truthful": True}
    site_name, operation_name, query_parameter, result_kind = config
    if not _EXECUTION_GATE.acquire(timeout=0.5):
        return {"status": "busy", "items": [], "truthful": True}
    try:
        now = time.monotonic()
        with _RATE_LOCK:
            last = _LAST_CALL_AT.get(site_name)
            if last is not None and now - last < MIN_SITE_INTERVAL_SECONDS:
                return {"status": "rate_limited", "items": [], "truthful": True}
            _LAST_CALL_AT[site_name] = now
        return _bundled_search(site_name, operation_name, query_parameter, q, safe_limit, result_kind)
    finally:
        _EXECUTION_GATE.release()


def youtube_search(query: str, limit: int = 10) -> dict[str, Any]:
    return _rate_limited_search("youtube.search", query, limit)


def hacker_news_search(query: str, limit: int = 10) -> dict[str, Any]:
    """Search Hacker News through its bundled, read-only, tier-one operation."""
    return _rate_limited_search("hacker-news.search", query, limit)


def search_candidates(query: str, limit: int = 10) -> dict[str, Any]:
    """Collect candidates from the explicitly allowed bundled sources, with per-source outcomes."""
    safe_limit = max(1, min(10, int(limit or 10)))
    sources = []
    statuses = {}
    for capability in ("youtube.search", "hacker-news.search"):
        result = _rate_limited_search(capability, query, safe_limit)
        statuses[capability] = result.get("status", "unknown")
        sources.extend(result.get("items") or [])
    return {
        "status": "ready" if sources else "degraded",
        "source_count": len(sources),
        "sources": sources[: safe_limit * 2],
        "operations": statuses,
        "truthful": True,
    }
