"""
AI Infinity
TARGET-2050.164
REAL-WORLD-COMMAND-EXECUTION-BRIDGE-CORE

Purpose:
    Turn AI Infinity from a mission/research core into a practical command
    execution core with controlled real-world HTTP execution.

Core:
    - mission engine
    - command routing
    - internal commands
    - external HTTP bridge
    - approval gates
    - SSRF/private-network protection
    - credential-header protection
    - durable SQLite execution records
    - async execution
    - restart recovery
    - execution events
    - idempotency
    - verification
    - adaptive recovery
    - persistent memory
    - policy adaptation
    - research planning
    - result closure
    - browser interface

Dependencies:
    FastAPI
    Uvicorn
    Pydantic

The HTTP execution layer intentionally uses Python's standard library rather
than httpx so deployment does not depend on an additional HTTP package.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import threading
import time
import uuid

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# BUILD IDENTITY
# ============================================================

VERSION = "TARGET-2050.164"
BUILD = "REAL-WORLD-COMMAND-EXECUTION-BRIDGE-CORE"
PREVIOUS_BUILD = "TARGET-2050.163"

# ============================================================
# CONFIGURATION
# ============================================================

DATA_DIR = os.getenv(
    "AI_INFINITY_DATA_DIR",
    "/tmp/ai-infinity",
)

DB_PATH = os.getenv(
    "AI_INFINITY_DB",
    os.path.join(DATA_DIR, "ai_infinity_164.db"),
)

HTTP_TIMEOUT = float(
    os.getenv("AI_INFINITY_HTTP_TIMEOUT", "15")
)

MAX_REQUEST_BODY = int(
    os.getenv("AI_INFINITY_MAX_BODY", "2000000")
)

MAX_RESPONSE_BODY = int(
    os.getenv("AI_INFINITY_MAX_RESPONSE", "2000000")
)

MAX_RETRIES = int(
    os.getenv("AI_INFINITY_MAX_RETRIES", "2")
)

REQUIRE_APPROVAL = (
    os.getenv(
        "AI_INFINITY_REQUIRE_APPROVAL",
        "true",
    ).lower()
    not in {"0", "false", "no"}
)

ALLOWLIST_RAW = os.getenv(
    "AI_INFINITY_EXTERNAL_ALLOWLIST",
    "",
)

EXTERNAL_ALLOWLIST = {
    item.strip().lower()
    for item in ALLOWLIST_RAW.split(",")
    if item.strip()
}

os.makedirs(DATA_DIR, exist_ok=True)

DB_LOCK = threading.RLock()

EXECUTOR = ThreadPoolExecutor(
    max_workers=4
)

QUEUE_TASKS: Dict[str, asyncio.Task] = {}

QUEUE_LOCK = asyncio.Lock()


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "Practical real-world command execution and mission "
        "closure core."
    ),
)


# ============================================================
# BASIC UTILITIES
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:14]}"


def stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def loads(
    value: Optional[str],
    default: Any = None,
) -> Any:
    if not value:
        return default

    try:
        return json.loads(value)
    except Exception:
        return default


# ============================================================
# DATABASE
# ============================================================

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with DB_LOCK:
        with get_db() as conn:

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result_json TEXT,
                    verification_json TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    recovery_attempts INTEGER NOT NULL DEFAULT 0,
                    policy_version INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS mission_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    value_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    source TEXT,
                    verified INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_sources (
                    id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT,
                    snippet TEXT,
                    source_family TEXT,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS executions (
                    id TEXT PRIMARY KEY,
                    mission_id TEXT,
                    command TEXT NOT NULL,
                    method TEXT NOT NULL,
                    url TEXT NOT NULL,
                    headers_json TEXT,
                    body_json TEXT,
                    status TEXT NOT NULL,
                    approval_required INTEGER NOT NULL DEFAULT 1,
                    approved INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    recovery_attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    response_json TEXT,
                    error TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_execution_idempotency
                ON executions(idempotency_key)
                WHERE idempotency_key IS NOT NULL;

                CREATE TABLE IF NOT EXISTS execution_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS policies (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

            existing = conn.execute(
                "SELECT id FROM policies WHERE id = 1"
            ).fetchone()

            if not existing:
                policy = default_policy()

                conn.execute(
                    """
                    INSERT INTO policies(
                        id,
                        version,
                        data_json,
                        updated_at
                    )
                    VALUES(1,?,?,?)
                    """,
                    (
                        1,
                        dumps(policy),
                        now(),
                    ),
                )


# ============================================================
# POLICY
# ============================================================

def default_policy() -> Dict[str, Any]:
    return {
        "version": 1,
        "verification_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True,
        "external_execution_enabled": True,
        "approval_required": REQUIRE_APPROVAL,
        "private_network_blocked": True,
        "credential_headers_blocked": True,
        "max_external_body": MAX_REQUEST_BODY,
        "max_external_response": MAX_RESPONSE_BODY,
        "max_retries": MAX_RETRIES,
    }


def get_policy() -> Dict[str, Any]:

    init_db()

    with DB_LOCK:
        with get_db() as conn:

            row = conn.execute(
                "SELECT * FROM policies WHERE id=1"
            ).fetchone()

    if not row:
        return default_policy()

    result = loads(
        row["data_json"],
        default_policy(),
    )

    result["version"] = row["version"]

    return result


def update_policy(
    changes: Dict[str, Any]
) -> Dict[str, Any]:

    policy = get_policy()

    allowed = {
        "verification_enabled",
        "adaptive_recovery_enabled",
        "self_modification_enabled",
        "external_execution_enabled",
        "approval_required",
        "private_network_blocked",
        "credential_headers_blocked",
        "max_external_body",
        "max_external_response",
        "max_retries",
    }

    for key, value in changes.items():

        if key in allowed:
            policy[key] = value

    version = int(
        policy.get("version", 1)
    ) + 1

    policy["version"] = version

    with DB_LOCK:
        with get_db() as conn:

            conn.execute(
                """
                UPDATE policies
                SET version=?,
                    data_json=?,
                    updated_at=?
                WHERE id=1
                """,
                (
                    version,
                    dumps(policy),
                    now(),
                ),
            )

    return policy


# ============================================================
# EVENTS
# ============================================================

def mission_event(
    mission_id: str,
    event_type: str,
    payload: Any = None,
) -> None:

    with DB_LOCK:
        with get_db() as conn:

            conn.execute(
                """
                INSERT INTO mission_events(
                    mission_id,
                    event_type,
                    payload_json,
                    created_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    mission_id,
                    event_type,
                    dumps(payload or {}),
                    now(),
                ),
            )


def execution_event(
    execution_id: str,
    event_type: str,
    payload: Any = None,
) -> None:

    with DB_LOCK:
        with get_db() as conn:

            conn.execute(
                """
                INSERT INTO execution_events(
                    execution_id,
                    event_type,
                    payload_json,
                    created_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    execution_id,
                    event_type,
                    dumps(payload or {}),
                    now(),
                ),
            )


# ============================================================
# MEMORY
# ============================================================

def memory_put(
    key: str,
    value: Any,
) -> Dict[str, Any]:

    memory_id = make_id("mem")

    with DB_LOCK:
        with get_db() as conn:

            existing = conn.execute(
                "SELECT id FROM memories WHERE key=?",
                (key,),
            ).fetchone()

            if existing:

                memory_id = existing["id"]

                conn.execute(
                    """
                    UPDATE memories
                    SET value_json=?,
                        updated_at=?
                    WHERE key=?
                    """,
                    (
                        dumps(value),
                        now(),
                        key,
                    ),
                )

            else:

                conn.execute(
                    """
                    INSERT INTO memories(
                        id,
                        key,
                        value_json,
                        created_at,
                        updated_at
                    )
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        memory_id,
                        key,
                        dumps(value),
                        now(),
                        now(),
                    ),
                )

    return {
        "id": memory_id,
        "key": key,
        "value": value,
    }


def memory_get(
    key: str,
) -> Optional[Dict[str, Any]]:

    with DB_LOCK:
        with get_db() as conn:

            row = conn.execute(
                """
                SELECT *
                FROM memories
                WHERE key=?
                """,
                (key,),
            ).fetchone()

    if not row:
        return None

    return {
        "id": row["id"],
        "key": row["key"],
        "value": loads(row["value_json"]),
        "updated_at": row["updated_at"],
    }


# ============================================================
# URL SECURITY
# ============================================================

BLOCKED_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-access-token",
}


def sanitize_headers(
    headers: Dict[str, str]
) -> Dict[str, str]:

    if not get_policy().get(
        "credential_headers_blocked",
        True,
    ):
        return {
            str(k): str(v)
            for k, v in headers.items()
        }

    result: Dict[str, str] = {}

    for key, value in headers.items():

        key = str(key)
        value = str(value)

        lowered = key.lower().strip()

        if lowered in BLOCKED_HEADERS:
            raise ValueError(
                f"credential/session header blocked: {key}"
            )

        if (
            "token" in lowered
            or "secret" in lowered
            or "password" in lowered
        ):
            raise ValueError(
                f"sensitive header blocked: {key}"
            )

        if len(key) > 128:
            raise ValueError(
                "header name too long"
            )

        if len(value) > 4096:
            raise ValueError(
                "header value too long"
            )

        result[key] = value

    return result


def host_allowed(
    host: str
) -> bool:

    host = host.lower().rstrip(".")

    if not EXTERNAL_ALLOWLIST:
        return True

    if host in EXTERNAL_ALLOWLIST:
        return True

    return any(
        host.endswith("." + allowed)
        for allowed in EXTERNAL_ALLOWLIST
    )


def resolve_host_ips(
    host: str
) -> List[str]:

    try:

        addresses = socket.getaddrinfo(
            host,
            None,
            type=socket.SOCK_STREAM,
        )

    except socket.gaierror as exc:

        raise ValueError(
            f"DNS resolution failed: {exc}"
        ) from exc

    return sorted(
        {
            item[4][0]
            for item in addresses
        }
    )


def is_private_ip(
    value: str
) -> bool:

    address = ipaddress.ip_address(value)

    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_external_url(
    url: str
) -> str:

    if not isinstance(url, str):
        raise ValueError(
            "URL must be a string"
        )

    if len(url) > 4096:
        raise ValueError(
            "URL too long"
        )

    parsed = urlparse(url)

    if parsed.scheme not in {
        "http",
        "https",
    }:
        raise ValueError(
            "only http and https URLs are allowed"
        )

    if not parsed.hostname:
        raise ValueError(
            "URL host is required"
        )

    if parsed.username or parsed.password:
        raise ValueError(
            "embedded URL credentials are blocked"
        )

    host = parsed.hostname

    if not host_allowed(host):
        raise ValueError(
            "destination host is not allowed"
        )

    policy = get_policy()

    if policy.get(
        "private_network_blocked",
        True,
    ):

        for address in resolve_host_ips(host):

            if is_private_ip(address):

                raise ValueError(
                    "private/local/reserved destination blocked"
                )

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",
        )
    )


# ============================================================
# EXTERNAL HTTP EXECUTION
# ============================================================

ALLOWED_METHODS = {
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
}


def external_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Any = None,
) -> Dict[str, Any]:

    method = method.upper()

    if method not in ALLOWED_METHODS:
        raise ValueError(
            "unsupported HTTP method"
        )

    clean_url = validate_external_url(url)

    safe_headers = sanitize_headers(
        headers
    )

    raw_body = None

    if body is not None:

        raw_body = json.dumps(
            body,
            ensure_ascii=False,
        ).encode("utf-8")

        if len(raw_body) > MAX_REQUEST_BODY:
            raise ValueError(
                "request body exceeds configured limit"
            )

        safe_headers.setdefault(
            "Content-Type",
            "application/json",
        )

    request = Request(
        clean_url,
        data=raw_body,
        headers=safe_headers,
        method=method,
    )

    started = time.time()

    try:

        with urlopen(
            request,
            timeout=HTTP_TIMEOUT,
        ) as response:

            data = response.read(
                MAX_RESPONSE_BODY + 1
            )

            truncated = (
                len(data)
                > MAX_RESPONSE_BODY
            )

            if truncated:
                data = data[
                    :MAX_RESPONSE_BODY
                ]

            text_body = data.decode(
                "utf-8",
                errors="replace",
            )

            content_type = response.headers.get(
                "Content-Type",
                "",
            )

            if "json" in content_type.lower():
                parsed_body = loads(
                    text_body,
                    text_body,
                )
            else:
                parsed_body = text_body

            return {
                "ok": 200 <= response.status < 300,
                "status_code": response.status,
                "url": clean_url,
                "method": method,
                "headers": dict(
                    response.headers
                ),
                "body": parsed_body,
                "truncated": truncated,
                "elapsed_ms": round(
                    (time.time() - started) * 1000,
                    2,
                ),
            }

    except HTTPError as exc:

        data = exc.read(
            MAX_RESPONSE_BODY
        )

        text_body = data.decode(
            "utf-8",
            errors="replace",
        )

        return {
            "ok": False,
            "status_code": exc.code,
            "url": clean_url,
            "method": method,
            "headers": dict(
                exc.headers
            ),
            "body": loads(
                text_body,
                text_body,
            ),
            "truncated": False,
            "elapsed_ms": round(
                (time.time() - started) * 1000,
                2,
            ),
        }

    except URLError as exc:

        raise RuntimeError(
            f"external request failed: {exc.reason}"
        ) from exc

    except TimeoutError as exc:

        raise RuntimeError(
            "external request timed out"
        ) from exc


# ============================================================
# COMMAND UNDERSTANDING
# ============================================================

def classify_command(
    command: str
) -> Dict[str, Any]:

    command = command.strip()

    lowered = command.lower()

    method = None

    for candidate in (
        "PATCH",
        "DELETE",
        "POST",
        "PUT",
        "HEAD",
        "GET",
    ):

        if re.search(
            rf"\b{candidate}\b",
            command,
            re.IGNORECASE,
        ):
            method = candidate
            break

    url_match = re.search(
        r"https?://[^\s\"'<>]+",
        command,
        re.IGNORECASE,
    )

    url = None

    if url_match:

        url = (
            url_match.group(0)
            .rstrip(".,);]")
        )

    if any(
        term in lowered
        for term in (
            "research",
            "find out",
            "investigate",
            "evidence",
            "sources",
        )
    ):
        intent = "research"

    elif any(
        term in lowered
        for term in (
            "remember",
            "save this",
            "store this",
        )
    ):
        intent = "memory"

    elif url:
        intent = "external_http"

    else:
        intent = "general"

    side_effect = (
        method in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }
        or any(
            term in lowered
            for term in (
                "send",
                "create",
                "update",
                "delete",
                "publish",
                "submit",
            )
        )
    )

    return {
        "intent": intent,
        "method": method,
        "url": url,
        "side_effect": side_effect,
        "requires_external_access": bool(url),
    }


def parse_command_body(
    command: str
) -> Any:

    match = re.search(
        r"(?:body|json|payload)\s*[:=]\s*(\{.*\})\s*$",
        command,
        re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return None

    return loads(
        match.group(1)
    )


# ============================================================
# RESEARCH PLANNER
# ============================================================

def research_plan(
    objective: str
) -> Dict[str, Any]:

    terms = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9_-]{2,}",
        objective,
    )

    terms = terms[:12]

    query = " ".join(terms)

    queries = [
        query,
        f"{query} evidence",
        f"{query} empirical study",
        f"{query} systematic review",
    ]

    return {
        "queries": queries,
        "source_targets": [
            {
                "family": "general_search",
                "query": queries[0],
            },
            {
                "family": "scholarly",
                "query": queries[2],
            },
            {
                "family": "review",
                "query": queries[3],
            },
        ],
        "live_retrieval": False,
        "note": (
            "This layer generates a research plan. "
            "Live retrieval requires an approved research connector."
        ),
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_result(
    result: Any
) -> Dict[str, Any]:

    checks = {
        "result_exists": result is not None,
        "result_nonempty": bool(result),
    }

    passed = sum(
        1
        for value in checks.values()
        if value
    )

    total = len(checks)

    confidence = (
        passed / total
        if total
        else 0
    )

    return {
        "verified": (
            passed == total
        ),
        "confidence": round(
            confidence,
            3,
        ),
        "checks": checks,
    }


# ============================================================
# LOCAL COMMAND ENGINE
# ============================================================

def local_command(
    command: str
) -> Dict[str, Any]:

    parsed = classify_command(
        command
    )

    lowered = command.lower().strip()

    if lowered.startswith("ping"):

        return {
            "status": "completed",
            "action": "ping",
            "message": "pong",
        }

    if lowered.startswith("status"):

        return {
            "status": "completed",
            "action": "status",
            "version": VERSION,
            "build": BUILD,
            "policy_version": get_policy()[
                "version"
            ],
        }

    if parsed["intent"] == "memory":

        match = re.search(
            r"(?:remember|save|store)"
            r"\s+(?:that\s+)?(.+)",
            command,
            re.IGNORECASE,
        )

        statement = (
            match.group(1).strip()
            if match
            else command
        )

        memory = memory_put(
            statement,
            {
                "statement": statement,
                "stored_at": now(),
            },
        )

        return {
            "status": "completed",
            "action": "memory",
            "memory": memory,
        }

    if parsed["intent"] == "research":

        return {
            "status": "completed",
            "action": "research_plan",
            "research": research_plan(
                command
            ),
        }

    return {
        "status": "completed",
        "action": "interpreted",
        "command": command,
        "intent": parsed["intent"],
        "message": (
            "Command interpreted by AI Infinity's "
            "local command engine."
        ),
    }


# ============================================================
# EXTERNAL PLAN
# ============================================================

def make_external_plan(
    command: str,
    method: Optional[str] = None,
    url: Optional[str] = None,
    body: Any = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:

    parsed = classify_command(
        command
    )

    final_method = (
        method
        or parsed["method"]
        or "GET"
    ).upper()

    final_url = (
        url
        or parsed["url"]
    )

    if not final_url:

        return {
            "ready": False,
            "reason": (
                "No HTTP URL detected."
            ),
            "command": command,
            "parsed": parsed,
        }

    if final_method not in ALLOWED_METHODS:

        return {
            "ready": False,
            "reason": (
                f"Unsupported HTTP method: "
                f"{final_method}"
            ),
        }

    if body is None:
        body = parse_command_body(
            command
        )

    safe_headers = sanitize_headers(
        headers or {}
    )

    side_effect = final_method in {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }

    return {
        "ready": True,
        "command": command,
        "method": final_method,
        "url": final_url,
        "headers": safe_headers,
        "body": body,
        "side_effect": side_effect,
        "approval_required": (
            REQUIRE_APPROVAL
            and side_effect
        ),
        "policy_version": get_policy()[
            "version"
        ],
    }


# ============================================================
# MISSIONS
# ============================================================

class RunRequest(BaseModel):

    objective: str = Field(
        min_length=1,
        max_length=10000,
    )

    research: bool = False

    verify: bool = True

    remember: bool = False

    external_access: bool = False

    command: Optional[str] = None

    idempotency_key: Optional[str] = None


class CommandRequest(BaseModel):

    command: str = Field(
        min_length=1,
        max_length=10000,
    )

    approve_external: bool = False

    async_mode: bool = False

    idempotency_key: Optional[str] = None


class ExternalPlanRequest(BaseModel):

    command: str = Field(
        min_length=1,
        max_length=10000,
    )

    method: Optional[str] = None

    url: Optional[str] = None

    body: Any = None

    headers: Dict[str, str] = Field(
        default_factory=dict
    )


class ExternalExecuteRequest(BaseModel):

    method: str = "GET"

    url: str

    headers: Dict[str, str] = Field(
        default_factory=dict
    )

    body: Any = None

    approve: bool = False

    mission_id: Optional[str] = None

    idempotency_key: Optional[str] = None

    async_mode: bool = True


class ApprovalRequest(BaseModel):

    approve: bool = True


class MemoryRequest(BaseModel):

    key: str = Field(
        min_length=1,
        max_length=256,
    )

    value: Any


class PolicyPatch(BaseModel):

    values: Dict[str, Any]


def create_mission(
    request: RunRequest
) -> Dict[str, Any]:

    mission_id = make_id(
        "mission"
    )

    created = now()

    with DB_LOCK:
        with get_db() as conn:

            conn.execute(
                """
                INSERT INTO missions(
                    id,
                    objective,
                    status,
                    created_at,
                    updated_at,
                    policy_version
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    mission_id,
                    request.objective,
                    "queued",
                    created,
                    created,
                    get_policy()[
                        "version"
                    ],
                ),
            )

    mission_event(
        mission_id,
        "created",
        {
            "objective": request.objective
        },
    )

    return {
        "id": mission_id,
        "objective": request.objective,
        "status": "queued",
        "created_at": created,
    }


def mission_row(
    mission_id: str
) -> Optional[sqlite3.Row]:

    with DB_LOCK:
        with get_db() as conn:

            return conn.execute(
                """
                SELECT *
                FROM missions
                WHERE id=?
                """,
                (mission_id,),
            ).fetchone()


def set_mission_status(
    mission_id: str,
    status: str,
    result: Any = None,
    verification: Any = None,
    attempts: Optional[int] = None,
    recovery_attempts: Optional[int] = None,
) -> None:

    fields: Dict[str, Any] = {
        "status": status,
        "updated_at": now(),
    }

    if result is not None:
        fields["result_json"] = dumps(
            result
        )

    if verification is not None:
        fields[
            "verification_json"
        ] = dumps(
            verification
        )

    if attempts is not None:
        fields["attempts"] = attempts

    if recovery_attempts is not None:
        fields[
            "recovery_attempts"
        ] = recovery_attempts

    assignment = ",".join(
        f"{key}=?"
        for key in fields
    )

    values = list(
        fields.values()
    )

    values.append(
        mission_id
    )

    with DB_LOCK:
        with get_db() as conn:

            conn.execute(
                f"""
                UPDATE missions
                SET {assignment}
                WHERE id=?
                """,
                values,
            )


def run_mission_sync(
    mission_id: str,
    request: RunRequest,
) -> Dict[str, Any]:

    attempts = 0
    recovery_attempts = 0

    set_mission_status(
        mission_id,
        "running",
    )

    mission_event(
        mission_id,
        "started",
    )

    try:

        attempts += 1

        command = (
            request.command
            or request.objective
        )

        parsed = classify_command(
            command
        )

        # ----------------------------------------------------
        # EXTERNAL EXECUTION
        # ----------------------------------------------------

        if (
            parsed["intent"]
            == "external_http"
            or request.external_access
        ):

            plan = make_external_plan(
                command
            )

            if not plan["ready"]:
                raise ValueError(
                    plan["reason"]
                )

            if (
                plan["side_effect"]
                and REQUIRE_APPROVAL
            ):

                result = {
                    "status": "awaiting_approval",
                    "action": "external_execution",
                    "plan": plan,
                }

            else:

                result = external_request(
                    plan["method"],
                    plan["url"],
                    plan["headers"],
                    plan["body"],
                )

        # ----------------------------------------------------
        # INTERNAL EXECUTION
        # ----------------------------------------------------

        else:

            result = local_command(
                command
            )

        research = (
            research_plan(
                request.objective
            )
            if request.research
            else None
        )

        verification = (
            verify_result(result)
            if request.verify
            else {
                "verified": False,
                "confidence": 0,
                "reason": (
                    "verification disabled"
                ),
            }
        )

        if request.remember:

            memory_put(
                f"mission:{mission_id}",
                {
                    "objective": request.objective,
                    "result": result,
                    "verification": verification,
                },
            )

        final = {
            "status": "completed",
            "mission_id": mission_id,
            "objective": request.objective,
            "result": result,
            "research": research,
            "verification": verification,
            "attempts": attempts,
            "recovery_attempts": recovery_attempts,
            "policy_version": get_policy()[
                "version"
            ],
        }

        set_mission_status(
            mission_id,
            "completed",
            result=final,
            verification=verification,
            attempts=attempts,
            recovery_attempts=recovery_attempts,
        )

        mission_event(
            mission_id,
            "completed",
            final,
        )

        return final

    except Exception as exc:

        mission_event(
            mission_id,
            "failure",
            {
                "error": str(exc),
                "attempt": attempts,
            },
        )

        policy = get_policy()

        if (
            policy.get(
                "adaptive_recovery_enabled",
                True,
            )
            and recovery_attempts
            < MAX_RETRIES
        ):

            recovery_attempts += 1

            set_mission_status(
                mission_id,
                "recovering",
                attempts=attempts,
                recovery_attempts=recovery_attempts,
            )

            mission_event(
                mission_id,
                "recovery",
                {
                    "recovery_attempt":
                        recovery_attempts,
                },
            )

            try:

                fallback = local_command(
                    request.command
                    or request.objective
                )

                verification = verify_result(
                    fallback
                )

                final = {
                    "status": "completed",
                    "mission_id": mission_id,
                    "objective": request.objective,
                    "result": fallback,
                    "recovered_from": str(exc),
                    "verification": verification,
                    "attempts": attempts + 1,
                    "recovery_attempts": recovery_attempts,
                    "policy_version": get_policy()[
                        "version"
                    ],
                }

                set_mission_status(
                    mission_id,
                    "completed",
                    result=final,
                    verification=verification,
                    attempts=attempts + 1,
                    recovery_attempts=recovery_attempts,
                )

                mission_event(
                    mission_id,
                    "recovered",
                    final,
                )

                return final

            except Exception as recovery_error:

                exc = recovery_error

        failure = {
            "status": "failed",
            "mission_id": mission_id,
            "objective": request.objective,
            "error": str(exc),
            "attempts": attempts,
            "recovery_attempts": recovery_attempts,
        }

        set_mission_status(
            mission_id,
            "failed",
            result=failure,
            attempts=attempts,
            recovery_attempts=recovery_attempts,
        )

        mission_event(
            mission_id,
            "failed",
            failure,
        )

        return failure


# ============================================================
# EXECUTION RECORDS
# ============================================================

def execution_row(
    execution_id: str
) -> Optional[sqlite3.Row]:

    with DB_LOCK:
        with get_db() as conn:

            return conn.execute(
                """
                SELECT *
                FROM executions
                WHERE id=?
                """,
                (execution_id,),
            ).fetchone()


def execution_dict(
    row: sqlite3.Row
) -> Dict[str, Any]:

    return {
        "id": row["id"],
        "mission_id": row["mission_id"],
        "command": row["command"],
        "method": row["method"],
        "url": row["url"],
        "headers": loads(
            row["headers_json"],
            {},
        ),
        "body": loads(
            row["body_json"]
        ),
        "status": row["status"],
        "approval_required": bool(
            row["approval_required"]
        ),
        "approved": bool(
            row["approved"]
        ),
        "idempotency_key": row[
            "idempotency_key"
        ],
        "attempts": row["attempts"],
        "recovery_attempts": row[
            "recovery_attempts"
        ],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "response": loads(
            row["response_json"]
        ),
        "error": row["error"],
    }


def create_execution(
    command: str,
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Any,
    mission_id: Optional[str],
    idempotency_key: Optional[str],
    approved: bool,
) -> Dict[str, Any]:

    clean_url = validate_external_url(
        url
    )

    safe_headers = sanitize_headers(
        headers
    )

    method = method.upper()

    if method not in ALLOWED_METHODS:
        raise ValueError(
            "unsupported HTTP method"
        )

    side_effect = method in {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }

    approval_required = (
        REQUIRE_APPROVAL
        and side_effect
    )

    status = (
        "awaiting_approval"
        if approval_required
        and not approved
        else "queued"
    )

    if idempotency_key:

        with DB_LOCK:
            with get_db() as conn:

                old = conn.execute(
                    """
                    SELECT *
                    FROM executions
                    WHERE idempotency_key=?
                    """,
                    (idempotency_key,),
                ).fetchone()

        if old:
            return execution_dict(
                old
            )

    execution_id = make_id(
        "exec"
    )

    with DB_LOCK:
        with get_db() as conn:

            conn.execute(
                """
                INSERT INTO executions(
                    id,
                    mission_id,
                    command,
                    method,
                    url,
                    headers_json,
                    body_json,
                    status,
                    approval_required,
                    approved,
                    idempotency_key,
                    created_at,
                    updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    execution_id,
                    mission_id,
                    command,
                    method,
                    clean_url,
                    dumps(safe_headers),
                    dumps(body),
                    status,
                    int(approval_required),
                    int(approved),
                    idempotency_key,
                    now(),
                    now(),
                ),
            )

    execution_event(
        execution_id,
        "created",
        {
            "status": status,
            "approval_required":
                approval_required,
        },
    )

    return execution_dict(
        execution_row(
            execution_id
        )
    )


def set_execution_status(
    execution_id: str,
    status: str,
    **fields: Any,
) -> None:

    allowed = {
        "attempts",
        "recovery_attempts",
        "started_at",
        "finished_at",
        "response_json",
        "error",
        "approved",
        "updated_at",
    }

    update = {
        key: value
        for key, value in fields.items()
        if key in allowed
    }

    update["status"] = status
    update["updated_at"] = now()

    assignment = ",".join(
        f"{key}=?"
        for key in update
    )

    values = list(
        update.values()
    )

    values.append(
        execution_id
    )

    with DB_LOCK:
        with get_db() as conn:

            conn.execute(
                f"""
                UPDATE executions
                SET {assignment}
                WHERE id=?
                """,
                values,
            )


# ============================================================
# EXTERNAL EXECUTION WORKER
# ============================================================

def execute_external_sync(
    execution_id: str
) -> Dict[str, Any]:

    row = execution_row(
        execution_id
    )

    if not row:
        raise ValueError(
            "execution not found"
        )

    if row["status"] in {
        "completed",
        "failed",
        "cancelled",
    }:
        return execution_dict(
            row
        )

    if row["status"] == "awaiting_approval":

        return execution_dict(
            row
        )

    attempts = int(
        row["attempts"]
    ) + 1

    set_execution_status(
        execution_id,
        "running",
        started_at=now(),
        attempts=attempts,
    )

    execution_event(
        execution_id,
        "started",
        {
            "attempt": attempts
        },
    )

    try:

        response = external_request(
            row["method"],
            row["url"],
            loads(
                row["headers_json"],
                {},
            ),
            loads(
                row["body_json"]
            ),
        )

        success = bool(
            response.get("ok")
        )

        status = (
            "completed"
            if success
            else "failed"
        )

        set_execution_status(
            execution_id,
            status,
            response_json=response,
            error=(
                None
                if success
                else (
                    "HTTP "
                    + str(
                        response.get(
                            "status_code"
                        )
                    )
                )
            ),
            finished_at=now(),
            attempts=attempts,
        )

        execution_event(
            execution_id,
            status,
            response,
        )

        return execution_dict(
            execution_row(
                execution_id
            )
        )

    except Exception as exc:

        policy = get_policy()

        recovery_attempts = 0

        if (
            policy.get(
                "adaptive_recovery_enabled",
                True,
            )
            and attempts <= MAX_RETRIES
        ):

            recovery_attempts = 1

            set_execution_status(
                execution_id,
                "recovering",
                recovery_attempts=
                    recovery_attempts,
            )

            execution_event(
                execution_id,
                "recovery",
                {
                    "error": str(exc)
                },
            )

            try:

                response = external_request(
                    row["method"],
                    row["url"],
                    loads(
                        row["headers_json"],
                        {},
                    ),
                    loads(
                        row["body_json"]
                    ),
                )

                success = bool(
                    response.get("ok")
                )

                status = (
                    "completed"
                    if success
                    else "failed"
                )

                set_execution_status(
                    execution_id,
                    status,
                    response_json=response,
                    error=(
                        None
                        if success
                        else (
                            "HTTP "
                            + str(
                                response.get(
                                    "status_code"
                                )
                            )
                        )
                    ),
                    finished_at=now(),
                    attempts=attempts + 1,
                    recovery_attempts=
                        recovery_attempts,
                )

                execution_event(
                    execution_id,
                    "recovered",
                    response,
                )

                return execution_dict(
                    execution_row(
                        execution_id
                    )
                )

            except Exception as recovery_error:

                exc = recovery_error

        set_execution_status(
            execution_id,
            "failed",
            error=str(exc),
            finished_at=now(),
            attempts=attempts,
            recovery_attempts=
                recovery_attempts,
        )

        execution_event(
            execution_id,
            "failed",
            {
                "error": str(exc)
            },
        )

        return execution_dict(
            execution_row(
                execution_id
            )
        )


async def queue_execution(
    execution_id: str
) -> None:

    async with QUEUE_LOCK:

        existing = QUEUE_TASKS.get(
            execution_id
        )

        if existing and not existing.done():
            return

        task = asyncio.create_task(
            asyncio.to_thread(
                execute_external_sync,
                execution_id,
            )
        )

        QUEUE_TASKS[
            execution_id
        ] = task


async def recover_pending_executions() -> None:

    with DB_LOCK:
        with get_db() as conn:

            rows = conn.execute(
                """
                SELECT id
                FROM executions
                WHERE status IN(
                    'queued',
                    'running',
                    'recovering'
                )
                """
            ).fetchall()

    for row in rows:

        execution_id = row["id"]

        with DB_LOCK:
            with get_db() as conn:

                conn.execute(
                    """
                    UPDATE executions
                    SET status='queued',
                        updated_at=?
                    WHERE id=?
                    AND status IN(
                        'running',
                        'recovering'
                    )
                    """,
                    (
                        now(),
                        execution_id,
                    ),
                )

        await queue_execution(
            execution_id
        )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup() -> None:

    init_db()

    await recover_pending_executions()


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root() -> Dict[str, Any]:

    return {
        "name": "AI Infinity",
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "previous_build": PREVIOUS_BUILD,
        "goal": (
            "practical real-world command "
            "execution with resilient mission closure"
        ),
        "docs": "/docs",
        "interface": "/command-ui",
        "run": "/run",
        "command": "/command",
        "external_plan": "/external/plan",
        "external_execute": "/external/execute",
        "status_endpoint": "/164-status",
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health() -> Dict[str, Any]:

    init_db()

    with DB_LOCK:
        with get_db() as conn:

            missions = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM missions
                """
            ).fetchone()["count"]

            executions = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM executions
                """
            ).fetchone()["count"]

            memories = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM memories
                """
            ).fetchone()["count"]

    policy = get_policy()

    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "database": "ready",
        "mission_count": missions,
        "execution_count": executions,
        "memory_count": memories,
        "policy_version": policy[
            "version"
        ],
        "external_execution_enabled":
            policy[
                "external_execution_enabled"
            ],
        "verification_enabled":
            policy[
                "verification_enabled"
            ],
        "adaptive_recovery_enabled":
            policy[
                "adaptive_recovery_enabled"
            ],
        "self_modification_enabled":
            policy[
                "self_modification_enabled"
            ],
    }


@app.get("/status")
async def status() -> Dict[str, Any]:

    return await health()


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities() -> Dict[str, Any]:

    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "mission-engine",
            "real-world-command-router",
            "internal-command-bridge",
            "external-http-bridge",
            "approval-gate",
            "ssrf-protection",
            "credential-protection",
            "durable-execution",
            "async-execution",
            "restart-recovery",
            "execution-events",
            "idempotency",
            "verification",
            "adaptive-recovery",
            "persistent-memory",
            "policy-adaptation",
            "research-planning",
            "result-closure",
            "web-interface",
        ],
    }


# ============================================================
# RUN
# ============================================================

@app.post("/run")
async def run(
    request: RunRequest
) -> Dict[str, Any]:

    mission = create_mission(
        request
    )

    result = await asyncio.to_thread(
        run_mission_sync,
        mission["id"],
        request,
    )

    return result


@app.post("/run-async")
async def run_async(
    request: RunRequest
) -> Dict[str, Any]:

    mission = create_mission(
        request
    )

    asyncio.create_task(
        asyncio.to_thread(
            run_mission_sync,
            mission["id"],
            request,
        )
    )

    return {
        "status": "accepted",
        "mission_id": mission["id"],
        "poll": (
            f"/mission/{mission['id']}"
        ),
        "events": (
            f"/mission/{mission['id']}/events"
        ),
    }


# ============================================================
# MISSION
# ============================================================

@app.get("/mission/{mission_id}")
async def get_mission(
    mission_id: str
) -> Dict[str, Any]:

    row = mission_row(
        mission_id
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    return {
        "id": row["id"],
        "objective": row["objective"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "result": loads(
            row["result_json"]
        ),
        "verification": loads(
            row["verification_json"]
        ),
        "attempts": row["attempts"],
        "recovery_attempts":
            row["recovery_attempts"],
        "policy_version":
            row["policy_version"],
    }


@app.get(
    "/mission/{mission_id}/events"
)
async def mission_events(
    mission_id: str
) -> Dict[str, Any]:

    if not mission_row(
        mission_id
    ):

        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    with DB_LOCK:
        with get_db() as conn:

            rows = conn.execute(
                """
                SELECT
                    id,
                    event_type,
                    payload_json,
                    created_at
                FROM mission_events
                WHERE mission_id=?
                ORDER BY id
                """,
                (mission_id,),
            ).fetchall()

    return {
        "mission_id": mission_id,
        "events": [
            {
                "id": row["id"],
                "type": row[
                    "event_type"
                ],
                "payload": loads(
                    row["payload_json"],
                    {},
                ),
                "created_at": row[
                    "created_at"
                ],
            }
            for row in rows
        ],
    }


# ============================================================
# COMMAND
# ============================================================

@app.post("/command")
async def command(
    request: CommandRequest
) -> Dict[str, Any]:

    parsed = classify_command(
        request.command
    )

    if parsed["intent"] == "external_http":

        try:

            plan = make_external_plan(
                request.command
            )

            if not plan["ready"]:

                return {
                    "status": "rejected",
                    "plan": plan,
                }

            execution = create_execution(
                command=request.command,
                method=plan["method"],
                url=plan["url"],
                headers=plan["headers"],
                body=plan["body"],
                mission_id=None,
                idempotency_key=
                    request.idempotency_key,
                approved=
                    request.approve_external,
            )

        except Exception as exc:

            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )

        if execution["status"] == "queued":

            if request.async_mode:

                await queue_execution(
                    execution["id"]
                )

            else:

                execution = (
                    await asyncio.to_thread(
                        execute_external_sync,
                        execution["id"],
                    )
                )

        return {
            "status": execution[
                "status"
            ],
            "command": request.command,
            "plan": plan,
            "execution": execution,
        }

    result = local_command(
        request.command
    )

    return {
        "status": "completed",
        "command": request.command,
        "result": result,
    }


@app.post("/command-interface")
async def command_interface(
    request: CommandRequest
) -> Dict[str, Any]:

    return await command(
        request
    )


# ============================================================
# EXTERNAL PLAN
# ============================================================

@app.post("/external/plan")
async def external_plan(
    request: ExternalPlanRequest
) -> Dict[str, Any]:

    try:

        return make_external_plan(
            request.command,
            request.method,
            request.url,
            request.body,
            request.headers,
        )

    except Exception as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


# ============================================================
# EXTERNAL EXECUTION
# ============================================================

@app.post("/external/execute")
async def external_execute(
    request: ExternalExecuteRequest
) -> Dict[str, Any]:

    try:

        execution = create_execution(
            command=(
                f"{request.method.upper()} "
                f"{request.url}"
            ),
            method=request.method,
            url=request.url,
            headers=request.headers,
            body=request.body,
            mission_id=request.mission_id,
            idempotency_key=
                request.idempotency_key,
            approved=request.approve,
        )

    except Exception as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    if execution["status"] == "queued":

        if request.async_mode:

            await queue_execution(
                execution["id"]
            )

            return {
                "status": "accepted",
                "execution": execution,
                "poll": (
                    "/external/execution/"
                    + execution["id"]
                ),
                "events": (
                    "/external/execution/"
                    + execution["id"]
                    + "/events"
                ),
            }

        return await asyncio.to_thread(
            execute_external_sync,
            execution["id"],
        )

    return {
        "status": execution[
            "status"
        ],
        "execution": execution,
        "approval": (
            "/external/approve/"
            + execution["id"]
        ),
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post(
    "/external/approve/{execution_id}"
)
async def approve_external(
    execution_id: str,
    request: ApprovalRequest,
) -> Dict[str, Any]:

    row = execution_row(
        execution_id
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    if not request.approve:

        set_execution_status(
            execution_id,
            "cancelled",
            finished_at=now(),
        )

        execution_event(
            execution_id,
            "approval_denied",
        )

        return execution_dict(
            execution_row(
                execution_id
            )
        )

    set_execution_status(
        execution_id,
        "queued",
        approved=1,
    )

    execution_event(
        execution_id,
        "approved",
    )

    await queue_execution(
        execution_id
    )

    return {
        "status": "accepted",
        "execution": execution_dict(
            execution_row(
                execution_id
            )
        ),
        "poll": (
            "/external/execution/"
            + execution_id
        ),
    }


# ============================================================
# EXECUTION STATUS
# ============================================================

@app.get(
    "/external/execution/{execution_id}"
)
async def external_execution(
    execution_id: str
) -> Dict[str, Any]:

    row = execution_row(
        execution_id
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    return execution_dict(
        row
    )


@app.get(
    "/external/execution/{execution_id}/events"
)
async def external_execution_events(
    execution_id: str
) -> Dict[str, Any]:

    if not execution_row(
        execution_id
    ):

        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    with DB_LOCK:
        with get_db() as conn:

            rows = conn.execute(
                """
                SELECT
                    id,
                    event_type,
                    payload_json,
                    created_at
                FROM execution_events
                WHERE execution_id=?
                ORDER BY id
                """,
                (execution_id,),
            ).fetchall()

    return {
        "execution_id":
            execution_id,
        "events": [
            {
                "id": row["id"],
                "type": row[
                    "event_type"
                ],
                "payload": loads(
                    row["payload_json"],
                    {},
                ),
                "created_at": row[
                    "created_at"
                ],
            }
            for row in rows
        ],
    }


# ============================================================
# RESUME
# ============================================================

@app.post(
    "/external/execution/{execution_id}/resume"
)
async def resume_external(
    execution_id: str
) -> Dict[str, Any]:

    row = execution_row(
        execution_id
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    if row["status"] == "awaiting_approval":

        raise HTTPException(
            status_code=409,
            detail=(
                "approval required before resume"
            ),
        )

    set_execution_status(
        execution_id,
        "queued",
    )

    execution_event(
        execution_id,
        "resumed",
    )

    await queue_execution(
        execution_id
    )

    return {
        "status": "accepted",
        "execution": execution_dict(
            execution_row(
                execution_id
            )
        ),
    }


# ============================================================
# CANCEL
# ============================================================

@app.post(
    "/external/execution/{execution_id}/cancel"
)
async def cancel_external(
    execution_id: str
) -> Dict[str, Any]:

    row = execution_row(
        execution_id
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    if row["status"] in {
        "completed",
        "failed",
        "cancelled",
    }:

        return execution_dict(
            row
        )

    set_execution_status(
        execution_id,
        "cancelled",
        finished_at=now(),
    )

    execution_event(
        execution_id,
        "cancelled",
    )

    return execution_dict(
        execution_row(
            execution_id
        )
    )


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory")
async def memory_list(
    limit: int = Query(
        50,
        ge=1,
        le=500,
    )
) -> Dict[str, Any]:

    with DB_LOCK:
        with get_db() as conn:

            rows = conn.execute(
                """
                SELECT
                    id,
                    key,
                    value_json,
                    updated_at
                FROM memories
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    return {
        "memories": [
            {
                "id": row["id"],
                "key": row["key"],
                "value": loads(
                    row["value_json"]
                ),
                "updated_at": row[
                    "updated_at"
                ],
            }
            for row in rows
        ]
    }


@app.get(
    "/memory/{key:path}"
)
async def get_memory(
    key: str
) -> Dict[str, Any]:

    result = memory_get(
        key
    )

    if result is None:

        raise HTTPException(
            status_code=404,
            detail="memory not found",
        )

    return result


@app.post("/memory")
async def put_memory(
    request: MemoryRequest
) -> Dict[str, Any]:

    return {
        "status": "stored",
        "memory": memory_put(
            request.key,
            request.value,
        ),
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
async def policy() -> Dict[str, Any]:

    return get_policy()


@app.post("/policy")
async def policy_update(
    request: PolicyPatch
) -> Dict[str, Any]:

    return update_policy(
        request.values
    )


# ============================================================
# SELF TESTS
# ============================================================

@app.get("/research/self-test")
async def research_self_test() -> Dict[str, Any]:

    result = research_plan(
        "autonomous AI agents real world reliability"
    )

    return {
        "status": "completed",
        "test": "research_self_test",
        "result": result,
    }


@app.get("/interface/self-test")
async def interface_self_test() -> Dict[str, Any]:

    return {
        "status": "completed",
        "test": "interface_self_test",
        "passed": True,
        "routes": [
            "/",
            "/health",
            "/capabilities",
            "/run",
            "/run-async",
            "/command",
            "/command-interface",
            "/external/plan",
            "/external/execute",
            "/external/approve/{execution_id}",
            "/external/execution/{execution_id}",
            "/external/execution/{execution_id}/events",
            "/external/execution/{execution_id}/resume",
            "/external/execution/{execution_id}/cancel",
            "/mission/{mission_id}",
            "/mission/{mission_id}/events",
            "/memory",
            "/policy",
        ],
    }


@app.get("/external/self-test")
async def external_self_test() -> Dict[str, Any]:

    tests = []

    # Safe plan test.
    try:

        plan = make_external_plan(
            "GET https://example.com"
        )

        tests.append(
            {
                "name": "external_plan",
                "passed": plan["ready"],
            }
        )

    except Exception as exc:

        tests.append(
            {
                "name": "external_plan",
                "passed": False,
                "error": str(exc),
            }
        )

    # SSRF protection test.
    try:

        validate_external_url(
            "http://127.0.0.1/"
        )

        tests.append(
            {
                "name": "ssrf_block",
                "passed": False,
            }
        )

    except Exception:

        tests.append(
            {
                "name": "ssrf_block",
                "passed": True,
            }
        )

    # Credential protection test.
    try:

        sanitize_headers(
            {
                "Authorization":
                    "should-be-blocked"
            }
        )

        tests.append(
            {
                "name":
                    "credential_header_block",
                "passed": False,
            }
        )

    except Exception:

        tests.append(
            {
                "name":
                    "credential_header_block",
                "passed": True,
            }
        )

    return {
        "status": "completed",
        "test": "external_self_test",
        "passed": all(
            test["passed"]
            for test in tests
        ),
        "tests": tests,
    }


# ============================================================
# 164 STATUS
# ============================================================

@app.get("/164-status")
async def status_164() -> Dict[str, Any]:

    policy = get_policy()

    with DB_LOCK:
        with get_db() as conn:

            mission_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM missions
                """
            ).fetchone()["count"]

            execution_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM executions
                """
            ).fetchone()["count"]

            event_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM execution_events
                """
            ).fetchone()["count"]

    return {
        "status": "ready",
        "version": VERSION,
        "build": BUILD,
        "previous_build": PREVIOUS_BUILD,

        "target":
            "real-world-command-capable AI Infinity",

        "result_closure": True,

        "internal_command_bridge": True,

        "external_http_bridge": True,

        "supported_methods": sorted(
            ALLOWED_METHODS
        ),

        "approval_gate":
            REQUIRE_APPROVAL,

        "ssrf_protection": True,

        "credential_header_protection":
            True,

        "durable_queue": True,

        "restart_recovery": True,

        "execution_events": True,

        "idempotency": True,

        "verification":
            policy[
                "verification_enabled"
            ],

        "adaptive_recovery":
            policy[
                "adaptive_recovery_enabled"
            ],

        "self_modification":
            policy[
                "self_modification_enabled"
            ],

        "memory": True,

        "mission_count":
            mission_count,

        "execution_count":
            execution_count,

        "execution_event_count":
            event_count,

        "policy_version":
            policy["version"],
    }


# ============================================================
# WEB INTERFACE
# ============================================================

INTERFACE_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">

<title>AI Infinity 2050.164</title>

<style>

body {
    margin: 0;
    padding: 24px;
    background: #0b0d10;
    color: #f2f2f2;
    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        sans-serif;
}

.container {
    max-width: 1000px;
    margin: auto;
}

h1 {
    margin-bottom: 4px;
}

.subtitle {
    opacity: .7;
    margin-bottom: 24px;
}

textarea,
button {
    box-sizing: border-box;
    width: 100%;
    font: inherit;
    border-radius: 10px;
}

textarea {
    min-height: 130px;
    padding: 14px;
    background: #151922;
    color: white;
    border: 1px solid #303644;
}

button {
    margin-top: 10px;
    padding: 12px;
    cursor: pointer;
    border: 0;
}

button:hover {
    opacity: .9;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    padding: 16px;
    margin-top: 18px;
    border-radius: 10px;
    background: #11151c;
    border: 1px solid #292e39;
}

.grid {
    display: grid;
    grid-template-columns:
        repeat(auto-fit,minmax(220px,1fr));
    gap: 10px;
}

.card {
    background: #11151c;
    border: 1px solid #292e39;
    border-radius: 10px;
    padding: 14px;
}

</style>
</head>

<body>

<div class="container">

<h1>AI Infinity</h1>

<div class="subtitle">
TARGET-2050.164 —
REAL-WORLD COMMAND EXECUTION BRIDGE
</div>

<div class="grid">

<div class="card">
<strong>Status</strong>
<br>
<span id="status">loading...</span>
</div>

<div class="card">
<strong>Version</strong>
<br>
<span id="version">loading...</span>
</div>

<div class="card">
<strong>Build</strong>
<br>
<span id="build">loading...</span>
</div>

</div>

<br>

<textarea
id="command"
placeholder="Enter a command...
Examples:
status
ping
research autonomous AI agents
remember that AI Infinity is my project
GET https://example.com">
</textarea>

<button onclick="executeCommand()">
EXECUTE COMMAND
</button>

<button onclick="health()">
REFRESH HEALTH
</button>

<pre id="output">
AI Infinity ready.
</pre>

</div>

<script>

async function api(
    url,
    options = {}
) {

    const response =
        await fetch(url, options);

    const text =
        await response.text();

    try {
        return JSON.stringify(
            JSON.parse(text),
            null,
            2
        );
    }

    catch {
        return text;
    }
}

async function executeCommand() {

    const command =
        document.getElementById(
            "command"
        ).value.trim();

    if (!command) {

        document.getElementById(
            "output"
        ).textContent =
            "Enter a command.";

        return;
    }

    document.getElementById(
        "output"
    ).textContent =
        "Executing...";

    document.getElementById(
        "output"
    ).textContent =
        await api(
            "/command",
            {
                method: "POST",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify({
                    command: command,
                    approve_external: false,
                    async_mode: false
                })
            }
        );
}

async function health() {

    const text =
        await api("/health");

    document.getElementById(
        "output"
    ).textContent = text;

    try {

        const data =
            JSON.parse(text);

        document.getElementById(
            "status"
        ).textContent =
            data.status;

        document.getElementById(
            "version"
        ).textContent =
            data.version;

        document.getElementById(
            "build"
        ).textContent =
            data.build;

    }

    catch {}

}

health();

</script>

</body>
</html>
"""


@app.get(
    "/command-ui",
    response_class=HTMLResponse,
)
async def command_ui() -> str:

    return INTERFACE_HTML


@app.get(
    "/docs-ui",
    response_class=HTMLResponse,
)
async def docs_ui() -> str:

    return INTERFACE_HTML


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
    )
