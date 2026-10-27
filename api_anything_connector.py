"""API Anything integration for public research and user-learned site operations.

The adapter discovers bundled, registry, and user-scoped operations, learns operations from
public HTTPS triggers, and runs reads or explicitly authorized writes with tier and dry-run evidence.
"""
from __future__ import annotations

import json
import os
import hashlib
import ipaddress
import math
import selectors
import re
import shutil
import subprocess
import tempfile
import time
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

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
        "convenience_search_operations": ["youtube.search", "hacker-news.search", "goodreads.searchBooks", "rottentomatoes.searchMovies", "amazon.search"],
        "generic_bundled_read_only_operations": True,
        "generic_bundled_write_operations": False,
        "user_learned_operations": True,
        "registry_operations": True,
        "user_learned_write_operations": True,
        "supported_transport_tiers": [1, 2, 3],
        "write_execution_requires_explicit_allowance": True,
        "write_execution_requires_matching_dry_run": True,
        "mode": "bundled_registry_and_user_scoped_read_write",
        "browser_learning_supported": True,
        "browser_learning_runtime_verified": False,
        "durable_profile_storage_verified": False,
        "upstream_commit": UPSTREAM_COMMIT,
        "truthful": True,
    }


_ORIGINAL_SUBPROCESS_RUN = subprocess.run


def _run_json(
    runner: Callable[..., Any],
    executable: str,
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: int,
) -> tuple[Any | None, str | None]:
    # Production uses incremental pipe reads so a remote response cannot be fully
    # buffered in RAM before MAX_OUTPUT_CHARS is enforced. Tests can inject a runner.
    if runner is _ORIGINAL_SUBPROCESS_RUN:
        process = None
        chunks = []
        total = 0
        deadline = time.monotonic() + timeout
        try:
            process = subprocess.Popen(
                [executable, *args],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            assert process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        process.kill()
                        process.wait()
                        return None, "timeout"
                    events = selector.select(remaining)
                    if not events:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                            return None, "timeout"
                        continue
                    for key, _ in events:
                        chunk = os.read(key.fd, min(65536, MAX_OUTPUT_CHARS - total + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        total += len(chunk)
                        if total > MAX_OUTPUT_CHARS:
                            process.kill()
                            process.wait()
                            return None, "output_too_large"
                        chunks.append(chunk)
            remaining = max(0.01, deadline - time.monotonic())
            try:
                code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return None, "timeout"
            if code != 0:
                return None, "command_failed"
            stdout = b"".join(chunks).decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            return None, "timeout"
        except OSError:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            return None, "execution_failed"
        except Exception:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            return None, "execution_failed"
    else:
        # Injected test runner; preserve deterministic test control and the same caps.
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
                    try:
                        port = parsed.port
                    except ValueError:
                        continue
                    if port not in {None, 443}:
                        continue
                    try:
                        address = ipaddress.ip_address(host)
                    except ValueError:
                        address = None
                    if address is not None and not address.is_global:
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
                elif result_kind == "goodreads":
                    book_id = _clean_text(row.get("bookId") or row.get("id"), 24)
                    if not re.fullmatch(r"\d{1,20}", book_id):
                        continue
                    normalized = {
                        "id": book_id,
                        "title": title,
                        "author": _clean_text(row.get("author"), 160),
                        "rating": _clean_text(row.get("avgRating"), 40),
                        "ratings_count": _clean_text(row.get("ratingsCount"), 60),
                        "url": f"https://www.goodreads.com/book/show/{book_id}",
                        "source": "API Anything / Goodreads",
                    }
                elif result_kind == "rottentomatoes":
                    raw_url = _clean_text(row.get("url"), 500)
                    parsed = urlparse(raw_url)
                    host = (parsed.hostname or "").rstrip(".").lower()
                    path = parsed.path.strip("/")
                    parts = path.split("/")
                    if (
                        parsed.scheme not in {"https", ""}
                        or (host and host != "www.rottentomatoes.com")
                        or len(parts) != 2 or parts[0] != "m"
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", parts[1])
                        or parsed.username or parsed.password
                    ):
                        continue
                    slug = parts[1]
                    normalized = {
                        "id": slug,
                        "title": title,
                        "year": _clean_text(row.get("year"), 20),
                        "tomatometer": _clean_text(row.get("tomatometer"), 40),
                        "cast": _clean_text(row.get("cast"), 300),
                        "url": f"https://www.rottentomatoes.com/m/{slug}",
                        "source": "API Anything / Rotten Tomatoes",
                    }
                elif result_kind == "amazon":
                    asin = _clean_text(row.get("asin") or row.get("itemKey") or row.get("id"), 10).upper()
                    if not re.fullmatch(r"[A-Z0-9]{10}", asin):
                        continue
                    normalized = {
                        "id": asin,
                        "title": title,
                        "price": _clean_text(row.get("price"), 80),
                        "url": f"https://www.amazon.com/dp/{asin}",
                        "source": "API Anything / Amazon",
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
    "goodreads.searchBooks": ("goodreads", "searchBooks", "q", "goodreads"),
    "rottentomatoes.searchMovies": ("rottentomatoes", "searchMovies", "q", "rottentomatoes"),
    "amazon.search": ("amazon", "search", "k", "amazon"),
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
    """Search broad sources plus context-matched bundled public research operations."""
    safe_limit = max(1, min(10, int(limit or 10)))
    topic = _clean_text(query, MAX_QUERY_CHARS).lower()
    capabilities = ["youtube.search", "hacker-news.search"]
    # Avoid slow and irrelevant calls: use specialist sites only when the brief supports them.
    if re.search(r"\b(book|books|author|novel|literature|reading|fiction|nonfiction|biography|memoir)\b", topic):
        capabilities.append("goodreads.searchBooks")
    if re.search(r"\b(movie|movies|film|films|cinema|cinematic references|actors|directors|tomatometer|critic score)\b", topic):
        capabilities.append("rottentomatoes.searchMovies")
    if re.search(r"\b(product|products|price comparison|shopping|laptop|camera|microphone|headphones|smartphone|tablet|gear|equipment)\b", topic):
        capabilities.append("amazon.search")
    sources = []
    statuses = {}
    for capability in capabilities:
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


# Generic bundled-operation access widens capability beyond a five-operation demo.
# This is deliberately capability-open but execution-closed: only operations
# advertised as bundled + readOnly by the pinned CLI are callable, with declared
# scalar inputs, isolated credentials, and plain-HTTP tier 1 transport.
_MAX_GENERIC_PARAMS = 12
_MAX_GENERIC_RESULT_ITEMS = 20
_SENSITIVE_OUTPUT_KEY = re.compile(r"(?:password|cookie|authorization|token|secret|api[_-]?key|session)", re.I)
_SAFE_SITE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_SAFE_OPERATION_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_GENERIC_COOLDOWNS: dict[str, float] = {}


def _isolated_cli_env(root: Path) -> dict[str, str]:
    home = root / "home"
    api_home = root / "api-anything"
    home.mkdir(mode=0o700, exist_ok=True)
    api_home.mkdir(mode=0o700, exist_ok=True)
    # Do not inherit application secrets, browser credentials, proxy settings,
    # or caller-controlled Node options into the third-party process.
    return {
        "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "API_ANYTHING_HOME": str(api_home),
        "TMPDIR": str(root),
        "NODE_ENV": "production",
    }


def _declared_parameter_names(operation: dict[str, Any]) -> set[str]:
    schema: Any = None
    for field in ("parameters", "params", "inputs", "arguments"):
        if field in operation:
            schema = operation[field]
            break
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            return {str(name) for name in props if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", str(name))}
        return {str(name) for name in schema if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", str(name))}
    if isinstance(schema, list):
        names = set()
        for row in schema:
            if isinstance(row, str):
                # The pinned API Anything CLI summarizes params as
                # "name:type", optionally followed by ?, hints, and examples.
                candidate = row.split(":", 1)[0].strip()
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", candidate):
                    names.add(candidate)
            elif isinstance(row, dict):
                name = row.get("name") or row.get("key") or row.get("parameter")
                if isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", name):
                    names.add(name)
        return names
    return set()


def _safe_public_data(value: Any, depth: int = 0) -> Any:
    """Bound generic upstream data before it enters the UI, event logs, or prompt context."""
    if depth >= 6:
        return "[depth limit]"
    if isinstance(value, str):
        return value.replace(chr(0), "")[:2000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_safe_public_data(item, depth + 1) for item in value[:_MAX_GENERIC_RESULT_ITEMS]]
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:80]:
            label = str(key)[:100]
            out[label] = "[redacted]" if _SENSITIVE_OUTPUT_KEY.search(label) else _safe_public_data(item, depth + 1)
        return out
    return _clean_text(value, 500)


def list_bundled_operations(site_name: str = "", limit: int = 100, include_writes: bool = True) -> dict[str, Any]:
    """Discover pinned bundled operations, separating reads from explicitly declared writes."""
    executable = shutil.which("api-anything")
    if not executable:
        return {"status": "not_configured", "sites": [], "operations": [], "truthful": True}
    requested_site = _clean_text(site_name, 64).lower()
    if requested_site and not _SAFE_SITE_NAME.fullmatch(requested_site):
        return {"status": "invalid_site", "sites": [], "operations": [], "truthful": True}
    try:
        safe_limit = max(1, min(100, int(limit or 100)))
        timeout = max(3, min(10, int(os.getenv("AI_INFINITY_API_ANYTHING_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))))
        with tempfile.TemporaryDirectory(prefix="ai-infinity-api-catalog-") as temporary:
            root = Path(temporary).resolve()
            env = _isolated_cli_env(root)

            def run(args: list[str]) -> tuple[Any | None, str | None]:
                return _run_json(subprocess.run, executable, args, cwd=str(root), env=env, timeout=timeout)

            site_rows, error = run(["sites"])
            if error or not isinstance(site_rows, list):
                return {"status": error or "invalid_catalog", "sites": [], "operations": [], "truthful": True}
            sites = [
                row for row in site_rows
                if isinstance(row, dict) and row.get("source") == "bundled"
                and isinstance(row.get("name"), str) and _SAFE_SITE_NAME.fullmatch(row["name"])
                and (not requested_site or row["name"] == requested_site)
            ][:30]
            operations: list[dict[str, Any]] = []
            for site in sites:
                payload, op_error = run(["ops", site["name"]])
                rows = payload.get("operations") if isinstance(payload, dict) else None
                if op_error or not isinstance(rows, list):
                    continue
                for operation in rows:
                    if not isinstance(operation, dict) or operation.get("readOnly") not in (True, False):
                        continue
                    is_read = operation.get("readOnly") is True
                    if not is_read and not include_writes:
                        continue
                    min_tier = operation.get("minTier", 1)
                    if type(min_tier) is not int or min_tier != 1:
                        continue
                    name = operation.get("name")
                    if not isinstance(name, str) or not _SAFE_OPERATION_NAME.fullmatch(name):
                        continue
                    operations.append({
                        "site": site["name"],
                        "operation": name,
                        "read_only": is_read,
                        "write": not is_read,
                        "parameters": sorted(_declared_parameter_names(operation)),
                        "description": _clean_text(operation.get("summary") or operation.get("description") or "", 300),
                        "notes": _clean_text(operation.get("notes") or "", 500),
                        "transport": "tier_1_plain_http_only",
                        "execution_mode": "read" if is_read else "write_requires_explicit_allowance",
                    })
                    if len(operations) >= safe_limit:
                        break
                if len(operations) >= safe_limit:
                    break
            return {
                "status": "ready" if operations else "empty",
                "site_count": len(sites),
                "operation_count": len(operations),
                "read_operation_count": sum(1 for op in operations if op["read_only"]),
                "write_operation_count": sum(1 for op in operations if op["write"]),
                "sites": [row["name"] for row in sites],
                "operations": operations,
                "executed": False,
                "upstream_commit": UPSTREAM_COMMIT,
                "truthful": True,
            }
    except Exception as exc:
        return {"status": "internal_error", "error_type": type(exc).__name__, "sites": [], "operations": [], "truthful": True}


def list_bundled_read_only_operations(site_name: str = "", limit: int = 100) -> dict[str, Any]:
    """Backwards-compatible read-only catalog for research tools."""
    result = list_bundled_operations(site_name=site_name, limit=limit, include_writes=False)
    result["generic_bundled_read_only_operations"] = True
    return result


def execute_bundled_operation(
    site_name: str,
    operation_name: str,
    parameters: dict[str, Any] | None = None,
    *,
    allow_writes: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute a pinned bundled read or explicitly authorized write on tier-one HTTP."""
    site_name = _clean_text(site_name, 64).lower()
    operation_name = _clean_text(operation_name, 64)
    if not _SAFE_SITE_NAME.fullmatch(site_name) or not _SAFE_OPERATION_NAME.fullmatch(operation_name):
        return {"status": "invalid_operation", "executed": False, "truthful": True}
    params = parameters if isinstance(parameters, dict) else {}
    if len(params) > _MAX_GENERIC_PARAMS:
        return {"status": "too_many_parameters", "executed": False, "truthful": True}
    safe_params: dict[str, Any] = {}
    for key, value in params.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key):
            return {"status": "invalid_parameter_name", "executed": False, "truthful": True}
        if _SENSITIVE_OUTPUT_KEY.search(key):
            return {"status": "sensitive_parameter_not_allowed", "executed": False, "truthful": True}
        if isinstance(value, str):
            if len(value) > MAX_QUERY_CHARS or chr(0) in value:
                return {"status": "invalid_parameter_value", "executed": False, "truthful": True}
            safe_params[key] = value
        elif type(value) in (int, bool):
            safe_params[key] = value
        elif type(value) is float and math.isfinite(value):
            safe_params[key] = value
        else:
            return {"status": "scalar_parameters_required", "executed": False, "truthful": True}
    if len(json.dumps(safe_params, ensure_ascii=False)) > 4000:
        return {"status": "parameters_too_large", "executed": False, "truthful": True}
    executable = shutil.which("api-anything")
    if not executable:
        return {"status": "not_configured", "executed": False, "truthful": True}
    if not _EXECUTION_GATE.acquire(timeout=0.5):
        return {"status": "busy", "executed": False, "truthful": True}
    started = time.monotonic()
    try:
        now = time.monotonic()
        with _RATE_LOCK:
            last = _GENERIC_COOLDOWNS.get(site_name)
            if last is not None and now - last < MIN_SITE_INTERVAL_SECONDS:
                return {"status": "rate_limited", "executed": False, "truthful": True}
            _GENERIC_COOLDOWNS[site_name] = now
        try:
            timeout = max(3, min(20, int(os.getenv("AI_INFINITY_API_ANYTHING_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))))
        except (TypeError, ValueError, OverflowError):
            return {"status": "invalid_configuration", "executed": False, "truthful": True}
        with tempfile.TemporaryDirectory(prefix="ai-infinity-api-anything-call-") as temporary:
            root = Path(temporary).resolve()
            env = _isolated_cli_env(root)

            def run(args: list[str]) -> tuple[Any | None, str | None]:
                return _run_json(subprocess.run, executable, args, cwd=str(root), env=env, timeout=timeout)

            sites, error = run(["sites"])
            if error:
                return {"status": error, "executed": False, "truthful": True}
            site = next(
                (row for row in sites if isinstance(row, dict) and row.get("name") == site_name and row.get("source") == "bundled"),
                None,
            ) if isinstance(sites, list) else None
            if not site:
                return {"status": "untrusted_site_spec", "executed": False, "truthful": True}
            catalog, error = run(["ops", site_name])
            operations = catalog.get("operations") if isinstance(catalog, dict) else None
            if error or not isinstance(operations, list):
                return {"status": error or "invalid_operation_catalog", "executed": False, "truthful": True}
            operation = next(
                (row for row in operations if isinstance(row, dict) and row.get("name") == operation_name),
                None,
            )
            if not operation or operation.get("readOnly") not in (True, False):
                return {"status": "operation_not_explicitly_classified", "executed": False, "truthful": True}
            is_read = operation.get("readOnly") is True
            if not is_read and not allow_writes and not dry_run:
                return {
                    "status": "write_confirmation_required", "executed": False,
                    "site": site_name, "operation": operation_name, "write": True,
                    "required_confirmation": "Re-run this exact operation with --allow-writes after checking the target and content.",
                    "truthful": True,
                }
            min_tier = operation.get("minTier", 1)
            if type(min_tier) is not int or min_tier != 1:
                return {"status": "tier_one_transport_required", "executed": False, "truthful": True}
            declared = _declared_parameter_names(operation)
            unknown = sorted(set(safe_params) - declared)
            if unknown:
                return {
                    "status": "undeclared_parameters", "executed": False,
                    "allowed_parameters": sorted(declared), "rejected_parameters": unknown[:12], "truthful": True,
                }
            if not declared and safe_params:
                return {"status": "parameter_schema_unavailable", "executed": False, "truthful": True}
            args = [
                "call", site_name, operation_name,
                "--json", json.dumps(safe_params, ensure_ascii=False, separators=(",", ":")),
                "--max-tier", "1",
            ]
            if not is_read:
                args.append("--allow-writes")
            if dry_run:
                args.append("--dry")
            result, error = run(args)
            if error:
                return {"status": error, "executed": False, "truthful": True}
            if not isinstance(result, dict):
                return {"status": "invalid_result_shape", "executed": False, "truthful": True}
            if dry_run:
                return {
                    "status": "dry_run_ready" if result.get("ok") is True else "dry_run_failed",
                    "provider": "api-anything", "site": site_name, "operation": operation_name,
                    "read_only": is_read, "write": not is_read, "dry_run": True,
                    "transport_tier": result.get("tier"),
                    "request_preview": _safe_public_data(result.get("request") or result.get("preview") or result.get("data")),
                    "executed": False, "truthful": True,
                }
            if result.get("ok") is not True:
                safe_failure = {
                    "status": "operation_failed", "provider": "api-anything",
                    "site": site_name, "operation": operation_name,
                    "read_only": is_read, "write": not is_read,
                    "error_class": _clean_text(result.get("class"), 60),
                    "reason": _clean_text(result.get("reason"), 300),
                    "write_outcome": _clean_text(result.get("write"), 40) if not is_read else None,
                    "executed": not is_read and result.get("write") in {"accepted", "may_have_applied", "applied"},
                    "truthful": True,
                }
                return safe_failure
            returned_site = result.get("site")
            returned_op = result.get("op")
            if returned_site and returned_site != site_name:
                return {"status": "result_identity_mismatch", "executed": False, "truthful": True}
            if returned_op and returned_op != operation_name:
                return {"status": "result_identity_mismatch", "executed": False, "truthful": True}
            if result.get("tier") != 1:
                return {"status": "unexpected_transport_tier", "executed": False, "truthful": True}
            data = _safe_public_data(result.get("data"))
            encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            if len(encoded) > 50000:
                data = encoded[:50000] + "…[truncated]"
            write_outcome = _clean_text(result.get("write"), 40) if not is_read else None
            if is_read:
                status_name = "completed"
            elif write_outcome == "applied":
                status_name = "completed"
            elif write_outcome == "accepted":
                status_name = "write_accepted_not_independently_verified"
            elif write_outcome == "may_have_applied":
                status_name = "write_outcome_ambiguous_check_target_do_not_retry"
            elif write_outcome == "not_applied":
                status_name = "write_not_applied"
            else:
                status_name = "write_acknowledgement_unverified"
            return {
                "status": status_name, "provider": "api-anything",
                "site": site_name, "operation": operation_name,
                "read_only": is_read, "write": not is_read, "write_outcome": write_outcome,
                "transport_tier": result.get("tier"),
                "parameters_sha256": __import__("hashlib").sha256(
                    json.dumps(safe_params, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest(),
                "result": data,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "upstream_commit": UPSTREAM_COMMIT,
                "executed": True, "truthful": True,
            }
    except Exception as exc:
        return {"status": "internal_error", "error_type": type(exc).__name__, "executed": False, "truthful": True}
    finally:
        _EXECUTION_GATE.release()


def execute_bundled_read_only(
    site_name: str,
    operation_name: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Backwards-compatible entry point for read-only research operations."""
    return execute_bundled_operation(site_name, operation_name, parameters, allow_writes=False, dry_run=False)


def _user_profile_env(root: Path, user_id: str) -> dict[str, str]:
    """Persist learned specs and logins in a private, deterministic per-user API Anything home."""
    identity = _clean_text(user_id, 256) or "anonymous"
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    configured = os.getenv("AI_INFINITY_API_ANYTHING_DATA_DIR")
    base = Path(configured).expanduser() if configured else (
        Path(os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity")).expanduser() / "api-anything"
    )
    base.mkdir(parents=True, mode=0o700, exist_ok=True)
    profile = base.resolve() / key
    profile.mkdir(mode=0o700, exist_ok=True)
    try:
        profile.chmod(0o700)
    except OSError:
        pass
    home = profile / "home"
    home.mkdir(mode=0o700, exist_ok=True)
    return {
        "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "API_ANYTHING_HOME": str(profile),
        "TMPDIR": str(root),
        "NODE_ENV": "production",
    }


def list_api_anything_operations(
    site_name: str = "", limit: int = 100, *, user_id: str = "anonymous", include_writes: bool = True
) -> dict[str, Any]:
    """Discover bundled plus this user's saved operations, including read/write and tier metadata."""
    executable = shutil.which("api-anything")
    if not executable:
        return {"status": "not_configured", "sites": [], "operations": [], "truthful": True}
    requested_site = _clean_text(site_name, 64).lower()
    if requested_site and not _SAFE_SITE_NAME.fullmatch(requested_site):
        return {"status": "invalid_site", "sites": [], "operations": [], "truthful": True}
    try:
        safe_limit = max(1, min(200, int(limit or 100)))
        timeout = max(3, min(20, int(os.getenv("AI_INFINITY_API_ANYTHING_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))))
        with tempfile.TemporaryDirectory(prefix="ai-infinity-api-catalog-") as temporary:
            root = Path(temporary).resolve()
            env = _user_profile_env(root, user_id)

            def run(args: list[str]) -> tuple[Any | None, str | None]:
                return _run_json(subprocess.run, executable, args, cwd=str(root), env=env, timeout=timeout)

            site_rows, error = run(["sites"])
            if error or not isinstance(site_rows, list):
                return {"status": error or "invalid_catalog", "sites": [], "operations": [], "truthful": True}
            sites = [
                row for row in site_rows
                if isinstance(row, dict) and row.get("source") in {"bundled", "user", "registry"}
                and isinstance(row.get("name"), str) and _SAFE_SITE_NAME.fullmatch(row["name"])
                and (not requested_site or row["name"] == requested_site)
            ][:50]
            operations: list[dict[str, Any]] = []
            for site in sites:
                payload, op_error = run(["ops", site["name"]])
                rows = payload.get("operations") if isinstance(payload, dict) else None
                if op_error or not isinstance(rows, list):
                    continue
                for operation in rows:
                    if not isinstance(operation, dict) or operation.get("readOnly") not in (True, False):
                        continue
                    read_only = operation.get("readOnly") is True
                    if not read_only and not include_writes:
                        continue
                    min_tier = operation.get("minTier", 1)
                    name = operation.get("name")
                    if type(min_tier) is not int or min_tier not in (1, 2, 3):
                        continue
                    if not isinstance(name, str) or not _SAFE_OPERATION_NAME.fullmatch(name):
                        continue
                    operations.append({
                        "site": site["name"], "source": site["source"], "operation": name,
                        "read_only": read_only, "write_capable": not read_only,
                        "min_tier": min_tier,
                        "parameters": sorted(_declared_parameter_names(operation)),
                        "description": _clean_text(operation.get("summary") or operation.get("description") or "", 300),
                        "notes": _clean_text(operation.get("notes") or "", 500),
                        "transport": f"tier_{min_tier}_or_higher",
                    })
                    if len(operations) >= safe_limit:
                        break
                if len(operations) >= safe_limit:
                    break
            return {
                "status": "ready" if operations else "empty",
                "site_count": len(sites), "operation_count": len(operations),
                "read_operation_count": sum(1 for op in operations if op["read_only"]),
                "write_operation_count": sum(1 for op in operations if op["write_capable"]),
                "sites": [row["name"] for row in sites], "operations": operations,
                "executed": False, "upstream_commit": UPSTREAM_COMMIT,
                "profile_scope": "user", "truthful": True,
            }
    except Exception as exc:
        return {"status": "internal_error", "error_type": type(exc).__name__, "sites": [], "operations": [], "truthful": True}


def _validate_api_argument(value: Any, depth: int = 0) -> Any:
    """Accept JSON parameters needed by real APIs while bounding shape and rejecting prototype keys."""
    if depth > 6:
        raise ValueError("parameter nesting too deep")
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, str):
        if len(value) > MAX_QUERY_CHARS or chr(0) in value:
            raise ValueError("invalid string argument")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite numeric argument")
        return value
    if isinstance(value, list):
        if len(value) > 100:
            raise ValueError("array argument too large")
        return [_validate_api_argument(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("object argument too large")
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key):
                raise ValueError("invalid object key")
            if key in {"__proto__", "prototype", "constructor"}:
                raise ValueError("unsafe object key")
            out[key] = _validate_api_argument(item, depth + 1)
        return out
    raise ValueError("argument must be JSON-compatible")


def _write_preview_key(site_name: str, operation_name: str, parameters: dict[str, Any], max_tier: int) -> str:
    payload = json.dumps(
        {"site": site_name, "operation": operation_name, "parameters": parameters, "max_tier": max_tier},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def execute_api_anything_operation(
    site_name: str, operation_name: str, parameters: dict[str, Any] | None = None, *,
    user_id: str = "anonymous", allow_writes: bool = False, max_tier: int = 3,
    dry_run: bool = False, idempotency_key: str = "", again: bool = False,
    auto_preflight: bool = False,
) -> dict[str, Any]:
    """Run a bundled or learned API Anything operation with tier selection and write reporting."""
    site_name = _clean_text(site_name, 64).lower()
    operation_name = _clean_text(operation_name, 64)
    if not _SAFE_SITE_NAME.fullmatch(site_name) or not _SAFE_OPERATION_NAME.fullmatch(operation_name):
        return {"status": "invalid_operation", "executed": False, "truthful": True}
    if type(max_tier) is not int or max_tier not in (1, 2, 3):
        return {"status": "invalid_transport_tier", "executed": False, "truthful": True}
    params = parameters if isinstance(parameters, dict) else {}
    if len(params) > _MAX_GENERIC_PARAMS:
        return {"status": "too_many_parameters", "executed": False, "truthful": True}
    safe_params: dict[str, Any] = {}
    for key, value in params.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key):
            return {"status": "invalid_parameter_name", "executed": False, "truthful": True}
        try:
            safe_params[key] = _validate_api_argument(value)
        except ValueError:
            return {"status": "invalid_parameter_value", "executed": False, "truthful": True}
    if len(json.dumps(safe_params, ensure_ascii=False, allow_nan=False)) > 12000:
        return {"status": "parameters_too_large", "executed": False, "truthful": True}
    executable = shutil.which("api-anything")
    if not executable:
        return {"status": "not_configured", "executed": False, "truthful": True}
    if not _EXECUTION_GATE.acquire(timeout=0.5):
        return {"status": "busy", "executed": False, "truthful": True}
    started = time.monotonic()
    try:
        now = time.monotonic()
        with _RATE_LOCK:
            last = _GENERIC_COOLDOWNS.get(site_name)
            if last is not None and now - last < MIN_SITE_INTERVAL_SECONDS:
                return {"status": "rate_limited", "executed": False, "truthful": True}
            _GENERIC_COOLDOWNS[site_name] = now
        try:
            timeout = max(3, min(20, int(os.getenv("AI_INFINITY_API_ANYTHING_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))))
        except (TypeError, ValueError, OverflowError):
            return {"status": "invalid_configuration", "executed": False, "truthful": True}
        with tempfile.TemporaryDirectory(prefix="ai-infinity-api-call-") as temporary:
            root = Path(temporary).resolve()
            env = _user_profile_env(root, user_id)

            def run(args: list[str]) -> tuple[Any | None, str | None]:
                return _run_json(subprocess.run, executable, args, cwd=str(root), env=env, timeout=timeout)

            sites, error = run(["sites"])
            if error:
                return {"status": error, "executed": False, "truthful": True}
            site = next(
                (row for row in sites if isinstance(row, dict) and row.get("name") == site_name and row.get("source") in {"bundled", "user", "registry"}),
                None,
            ) if isinstance(sites, list) else None
            if not site:
                return {"status": "unknown_site_for_profile", "executed": False, "truthful": True}
            catalog, error = run(["ops", site_name])
            operations = catalog.get("operations") if isinstance(catalog, dict) else None
            if error or not isinstance(operations, list):
                return {"status": error or "invalid_operation_catalog", "executed": False, "truthful": True}
            operation = next((row for row in operations if isinstance(row, dict) and row.get("name") == operation_name), None)
            if not operation:
                return {"status": "unknown_operation", "executed": False, "truthful": True}
            read_only = operation.get("readOnly")
            if read_only is True:
                is_write = False
            elif read_only is False:
                is_write = True
                if not allow_writes and not dry_run:
                    return {
                        "status": "write_confirmation_required", "executed": False,
                        "site": site_name, "operation": operation_name, "write_capable": True,
                        "required_confirmation": "Repeat this operation with api_anything_allow_writes=true after verifying the exact target and content.",
                        "truthful": True,
                    }
            else:
                return {"status": "operation_read_only_status_unknown", "executed": False, "truthful": True}
            min_tier = operation.get("minTier", 1)
            if type(min_tier) is not int or min_tier not in (1, 2, 3):
                return {"status": "invalid_operation_transport_tier", "executed": False, "truthful": True}
            if max_tier < min_tier:
                return {"status": "transport_tier_too_low", "executed": False, "min_tier": min_tier, "max_tier": max_tier, "truthful": True}
            declared = _declared_parameter_names(operation)
            unknown = sorted(set(safe_params) - declared)
            if unknown:
                return {
                    "status": "undeclared_parameters", "executed": False,
                    "allowed_parameters": sorted(declared), "rejected_parameters": unknown[:12], "truthful": True,
                }
            if not declared and safe_params:
                return {"status": "parameter_schema_unavailable", "executed": False, "truthful": True}
            preview_dir = Path(env["API_ANYTHING_HOME"]) / ".ai-infinity-write-previews"
            preview_path = preview_dir / (_write_preview_key(site_name, operation_name, safe_params, max_tier) + ".json")
            preview_missing = is_write and allow_writes and not dry_run and not preview_path.is_file()
            if preview_missing and not auto_preflight:
                return {
                    "status": "dry_run_required", "executed": False,
                    "site": site_name, "operation": operation_name, "write_capable": True,
                    "next_action": "Run this exact operation with api_anything_dry_run=true, inspect the request preview, then repeat the exact operation.",
                    "truthful": True,
                }
            args = [
                "call", site_name, operation_name, "--json",
                json.dumps(safe_params, ensure_ascii=False, separators=(",", ":")),
                "--max-tier", str(max_tier),
            ]
            if is_write:
                args.append("--allow-writes")
            if dry_run:
                args.append("--dry")
            if again:
                args.append("--again")
            if idempotency_key:
                key = _clean_text(idempotency_key, 128)
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", key):
                    return {"status": "invalid_idempotency_key", "executed": False, "truthful": True}
                args.extend(["--idempotency-key", key])
            if preview_missing and auto_preflight:
                preflight_args = [*args, "--dry"]
                preview_result, preview_error = run(preflight_args)
                if (
                    preview_error
                    or not isinstance(preview_result, dict)
                    or preview_result.get("ok") is not True
                    or preview_result.get("dry") is not True
                ):
                    return {
                        "status": "dry_run_failed", "executed": False,
                        "site": site_name, "operation": operation_name,
                        "error_type": preview_error or "invalid_preview_response",
                        "truthful": True,
                    }
                try:
                    preview_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                    preview_path.write_text(json.dumps({
                        "site": site_name, "operation": operation_name,
                        "parameters_sha256": hashlib.sha256(
                            json.dumps(safe_params, sort_keys=True, ensure_ascii=False).encode("utf-8")
                        ).hexdigest(),
                        "max_tier": max_tier, "created_at": int(time.time()),
                    }), encoding="utf-8")
                    preview_path.chmod(0o600)
                except OSError:
                    return {"status": "preview_receipt_persist_failed", "executed": False, "truthful": True}

            if is_write and allow_writes and not dry_run:
                try:
                    preview_path.unlink()
                except FileNotFoundError:
                    return {"status": "dry_run_required", "executed": False, "truthful": True}
                except OSError:
                    return {"status": "preview_receipt_consume_failed", "executed": False, "truthful": True}
            result, error = run(args)
            if error:
                if is_write and not dry_run:
                    return {
                        "status": "write_outcome_ambiguous_check_target_do_not_retry",
                        "provider": "api-anything", "site": site_name, "operation": operation_name,
                        "write_capable": True, "write_outcome": "unknown",
                        "execution_attempted": True, "executed": False,
                        "next_action": "Read the target through a separate read operation before retrying.",
                        "error_type": error, "truthful": True,
                    }
                return {"status": error, "executed": False, "truthful": True}
            if not isinstance(result, dict):
                if is_write and not dry_run:
                    return {
                        "status": "write_outcome_ambiguous_check_target_do_not_retry",
                        "provider": "api-anything", "site": site_name, "operation": operation_name,
                        "write_capable": True, "write_outcome": "unknown",
                        "execution_attempted": True, "executed": False,
                        "next_action": "Read the target through a separate read operation before retrying.",
                        "truthful": True,
                    }
                return {"status": "invalid_result_shape", "executed": False, "truthful": True}
            safe_result = _safe_public_data(result)
            if dry_run:
                preview_ready = result.get("ok") is True and result.get("dry") is True
                if preview_ready and is_write:
                    try:
                        preview_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                        preview_path.write_text(json.dumps({
                            "site": site_name, "operation": operation_name,
                            "parameters_sha256": hashlib.sha256(
                                json.dumps(safe_params, sort_keys=True, ensure_ascii=False).encode("utf-8")
                            ).hexdigest(),
                            "max_tier": max_tier, "created_at": int(time.time()),
                        }), encoding="utf-8")
                        preview_path.chmod(0o600)
                    except OSError:
                        return {"status": "preview_receipt_persist_failed", "executed": False, "truthful": True}
                return {
                    "status": "dry_run_ready" if preview_ready else "dry_run_failed",
                    "provider": "api-anything", "site": site_name, "operation": operation_name,
                    "read_only": not is_write, "write_capable": is_write, "dry_run": True,
                    "request_preview": _safe_public_data(result.get("request") or result.get("preview") or result.get("data")),
                    "preview_receipt_saved": bool(preview_ready and is_write),
                    "executed": False, "truthful": True,
                }
            if result.get("ok") is not True:
                write_outcome = _clean_text(result.get("write"), 40) if is_write else None
                if is_write and write_outcome in {"accepted", "may_have_applied", "applied"}:
                    return {
                        "status": "write_outcome_ambiguous_check_target_do_not_retry",
                        "provider": "api-anything", "site": site_name, "operation": operation_name,
                        "write_capable": True, "write_outcome": write_outcome,
                        "execution_attempted": True, "executed": False,
                        "error_class": _clean_text(result.get("class"), 60),
                        "reason": _clean_text(result.get("reason"), 300),
                        "retry": "no", "result": safe_result,
                        "next_action": "Read the target through a separate read operation before retrying.",
                        "truthful": True,
                    }
                return {
                    "status": "write_not_applied" if is_write and write_outcome == "not_applied" else "operation_failed",
                    "provider": "api-anything",
                    "site": site_name, "operation": operation_name,
                    "read_only": not is_write, "write_capable": is_write,
                    "error_class": _clean_text(result.get("class"), 60),
                    "reason": _clean_text(result.get("reason"), 300),
                    "write_outcome": write_outcome,
                    "retry": _clean_text(result.get("retry"), 40),
                    "executed": False, "result": safe_result, "truthful": True,
                }
            returned_site = result.get("site")
            returned_operation = result.get("op") or result.get("operation")
            if (returned_site and returned_site != site_name) or (returned_operation and returned_operation != operation_name):
                if is_write:
                    return {
                        "status": "write_outcome_ambiguous_check_target_do_not_retry",
                        "provider": "api-anything", "site": site_name, "operation": operation_name,
                        "write_capable": True, "write_outcome": "unknown",
                        "execution_attempted": True, "executed": False, "truthful": True,
                    }
                return {"status": "result_identity_mismatch", "executed": False, "truthful": True}
            returned_tier = result.get("tier")
            if type(returned_tier) is not int or returned_tier < min_tier or returned_tier > max_tier:
                if is_write:
                    return {
                        "status": "write_outcome_ambiguous_check_target_do_not_retry",
                        "provider": "api-anything", "site": site_name, "operation": operation_name,
                        "write_capable": True, "write_outcome": "unknown",
                        "execution_attempted": True, "executed": False, "truthful": True,
                    }
                return {"status": "unexpected_transport_tier", "executed": False, "truthful": True}
            data = _safe_public_data(result.get("data"))
            encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            if len(encoded) > 50000:
                data = encoded[:50000] + "…[truncated]"
            write_outcome = _clean_text(result.get("write"), 40) if is_write else None
            status_name = (
                "completed" if not is_write or write_outcome == "applied"
                else "write_accepted_not_independently_verified" if write_outcome == "accepted"
                else "write_outcome_ambiguous_check_target_do_not_retry" if write_outcome == "may_have_applied"
                else "write_not_applied" if write_outcome == "not_applied"
                else "write_acknowledgement_unverified"
            )
            return {
                "status": status_name, "provider": "api-anything",
                "site": site_name, "operation": operation_name, "source": site.get("source"),
                "read_only": not is_write, "write_capable": is_write, "write_outcome": write_outcome,
                "transport_tier": returned_tier, "result": data,
                "parameters_sha256": hashlib.sha256(
                    json.dumps(safe_params, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest(),
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "upstream_commit": UPSTREAM_COMMIT, "executed": True, "truthful": True,
            }
    except Exception as exc:
        return {"status": "internal_error", "error_type": type(exc).__name__, "executed": False, "truthful": True}
    finally:
        _EXECUTION_GATE.release()


def learn_api_anything_operation(
    site_name: str, operation_name: str, trigger_url: str,
    example: dict[str, Any], example2: dict[str, Any], *,
    user_id: str = "anonymous", write: bool = False,
    match: dict[str, str] | None = None,
    steps: list[dict[str, Any]] | None = None,
    soft_from: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Learn a site operation from two different examples, with optional browser interaction steps."""
    site_name = _clean_text(site_name, 64).lower()
    operation_name = _clean_text(operation_name, 64)
    if not _SAFE_SITE_NAME.fullmatch(site_name) or not _SAFE_OPERATION_NAME.fullmatch(operation_name) or ".." in site_name:
        return {"status": "invalid_operation", "executed": False, "truthful": True}

    def public_https(value: str) -> bool:
        parsed_value = urlparse(value)
        host_value = (parsed_value.hostname or "").rstrip(".").lower()
        try:
            port_value = parsed_value.port
        except ValueError:
            return False
        if (
            parsed_value.scheme != "https" or not host_value or parsed_value.username or parsed_value.password
            or port_value not in (None, 443)
            or host_value in {"localhost", "localhost.localdomain"}
            or host_value.endswith((".local", ".internal", ".localhost", ".test", ".invalid"))
        ):
            return False
        try:
            address_value = ipaddress.ip_address(host_value)
            if not address_value.is_global:
                return False
        except ValueError:
            pass
        return True

    trigger_url = _clean_text(trigger_url, 1200)
    soft_from = _clean_text(soft_from, 1200)
    description = _clean_text(description, 300)
    if not public_https(trigger_url) or (soft_from and not public_https(soft_from)):
        return {"status": "public_https_url_required", "executed": False, "truthful": True}
    if not isinstance(example, dict) or not isinstance(example2, dict) or not example or not example2:
        return {"status": "two_examples_required", "executed": False, "truthful": True}
    if set(example) != set(example2):
        return {"status": "example_parameters_must_match", "executed": False, "truthful": True}
    if example == example2:
        return {"status": "examples_must_differ", "executed": False, "truthful": True}

    def validate_example(row: dict[str, Any]) -> dict[str, str] | None:
        safe: dict[str, str] = {}
        for key, value in row.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key):
                return None
            if not isinstance(value, (str, int, float, bool)) or isinstance(value, str) and (len(value) > MAX_QUERY_CHARS or chr(0) in value):
                return None
            if isinstance(value, float) and not math.isfinite(value):
                return None
            safe[key] = str(value)
        return safe

    first = validate_example(example)
    second = validate_example(example2)
    if first is None or second is None:
        return {"status": "invalid_example", "executed": False, "truthful": True}

    safe_steps: list[dict[str, Any]] = []
    if steps is not None:
        if not isinstance(steps, list) or len(steps) > 20:
            return {"status": "invalid_steps", "executed": False, "truthful": True}
        for step in steps:
            if not isinstance(step, dict) or set(step) - {"action", "selector", "value", "ms"}:
                return {"status": "invalid_steps", "executed": False, "truthful": True}
            action = step.get("action")
            if action not in {"click", "fill", "press", "wait", "goto"}:
                return {"status": "invalid_steps", "executed": False, "truthful": True}
            safe_step: dict[str, Any] = {"action": action}
            for field in ("selector", "value"):
                val = step.get(field)
                if val is not None:
                    if not isinstance(val, str) or len(val) > 800 or chr(0) in val:
                        return {"status": "invalid_steps", "executed": False, "truthful": True}
                    if field == "value" and action == "goto" and val and not public_https(val):
                        return {"status": "public_https_url_required", "executed": False, "truthful": True}
                    safe_step[field] = val
            if action in {"click", "fill", "press"} and not safe_step.get("selector"):
                return {"status": "invalid_steps", "executed": False, "truthful": True}
            if action == "wait":
                ms = step.get("ms", 250)
                if type(ms) not in (int, float) or not math.isfinite(ms) or ms < 0 or ms > 10000:
                    return {"status": "invalid_steps", "executed": False, "truthful": True}
                safe_step["ms"] = ms
            safe_steps.append(safe_step)

    all_template_fields = trigger_url + "\n" + json.dumps(safe_steps, ensure_ascii=False)
    if not all("{" + key + "}" in all_template_fields for key in first):
        return {"status": "trigger_or_steps_must_include_each_parameter_placeholder", "executed": False, "truthful": True}

    safe_match = match if isinstance(match, dict) else {}
    if any(key not in {"method", "host", "path", "operationName"} for key in safe_match):
        return {"status": "invalid_match_fields", "executed": False, "truthful": True}
    if any(not isinstance(value, str) or len(value) > 180 or chr(0) in value for value in safe_match.values()):
        return {"status": "invalid_match_value", "executed": False, "truthful": True}

    if not _EXECUTION_GATE.acquire(timeout=0.5):
        return {"status": "busy", "executed": False, "truthful": True}
    try:
        executable = shutil.which("api-anything")
        if not executable:
            return {"status": "not_configured", "executed": False, "truthful": True}
        try:
            timeout = max(3, min(60, int(os.getenv("AI_INFINITY_API_ANYTHING_LEARN_TIMEOUT_SECONDS", "45"))))
        except (TypeError, ValueError, OverflowError):
            return {"status": "invalid_configuration", "executed": False, "truthful": True}
        with tempfile.TemporaryDirectory(prefix="ai-infinity-api-learn-") as temporary:
            root = Path(temporary).resolve()
            env = _user_profile_env(root, user_id)
            args = ["add", site_name, operation_name, "--trigger", trigger_url]
            for key, value in first.items():
                args.extend(["--example", f"{key}={value}"])
            for key, value in second.items():
                args.extend(["--example2", f"{key}={value}"])
            if safe_steps:
                args.extend(["--steps", json.dumps(safe_steps, ensure_ascii=False, separators=(",", ":"))])
            if soft_from:
                args.extend(["--soft-from", soft_from])
            if description:
                args.extend(["--description", description])
            for key, value in safe_match.items():
                args.extend(["--match", f"{key}={value}"])
            if write:
                args.append("--write")
            result, error = _run_json(subprocess.run, executable, args, cwd=str(root), env=env, timeout=timeout)
            if error:
                return {"status": error, "executed": False, "truthful": True}
            if not isinstance(result, dict):
                return {"status": "invalid_learning_result", "executed": False, "truthful": True}
            ok = result.get("ok") is True or result.get("status") in {"ready", "learned", "completed"}
            return {
                "status": "learned" if ok else "learning_failed",
                "site": site_name, "operation": operation_name,
                "write_operation": bool(write), "learning_aborts_write_requests": bool(write),
                "has_browser_steps": bool(safe_steps), "has_soft_navigation": bool(soft_from),
                "result": _safe_public_data(result),
                "executed": False,
                "next_action": "Inspect the saved operation, verify both examples, and dry-run a write before its first execution." if write else "Inspect and verify the learned operation before relying on it.",
                "truthful": True,
            }
    except Exception as exc:
        return {"status": "internal_error", "error_type": type(exc).__name__, "executed": False, "truthful": True}
    finally:
        _EXECUTION_GATE.release()

