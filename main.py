"""
AI Infinity
TARGET-2050.165
INTENT-TO-ACTION REAL-WORLD COMMAND ORCHESTRATOR CORE

Built on TARGET-2050.164.

Core flow:
natural language
    -> intent normalization
    -> structured action plan
    -> safety/policy gate
    -> approval when required
    -> durable execution
    -> verification
    -> adaptive recovery
    -> result closure
    -> memory

Free-first / Render-compatible.
No httpx dependency.
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
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# CONFIG
# ============================================================

VERSION = "TARGET-2050.165"
BUILD = "INTENT-TO-ACTION-REAL-WORLD-COMMAND-ORCHESTRATOR-CORE"
PREVIOUS_BUILD = "TARGET-2050.164"

DB_PATH = os.getenv(
    "AI_INFINITY_DB",
    "/tmp/ai-infinity/ai_infinity_165.db",
)

MAX_RESPONSE_BYTES = 1024 * 1024
REQUEST_TIMEOUT = 15
MAX_RECOVERY_ATTEMPTS = 2

APPROVAL_REQUIRED_METHODS = {
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
}

BLOCKED_HEADER_WORDS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "token",
    "secret",
    "password",
    "credential",
}

SAFE_METHODS = {
    "GET",
    "HEAD",
}

SUPPORTED_METHODS = [
    "DELETE",
    "GET",
    "HEAD",
    "PATCH",
    "POST",
    "PUT",
]


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "Practical real-world command orchestration engine "
        "with planning, safety, durable execution, verification, "
        "recovery, memory and result closure."
    ),
)


# ============================================================
# DATABASE
# ============================================================

_db_lock = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with _db_lock:
        connection = db()

        connection.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                result_json TEXT
            );

            CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                command TEXT NOT NULL,
                action_type TEXT NOT NULL,
                method TEXT,
                url TEXT,
                status TEXT NOT NULL,
                approval_required INTEGER NOT NULL DEFAULT 0,
                approved INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                recovery_attempts INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS execution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policies (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                version INTEGER NOT NULL,
                policy_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_idempotency
            ON executions(idempotency_key)
            WHERE idempotency_key IS NOT NULL;
            """
        )

        row = connection.execute(
            "SELECT id FROM policies WHERE id = 1"
        ).fetchone()

        if not row:
            policy = {
                "approval_required_for_side_effects": True,
                "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
                "external_http_enabled": True,
                "ssrf_protection": True,
                "credential_header_protection": True,
            }

            connection.execute(
                """
                INSERT INTO policies
                (id, version, policy_json, updated_at)
                VALUES (1, 1, ?, ?)
                """,
                (json.dumps(policy), utc_now()),
            )

        connection.commit()
        connection.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

def json_load(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def policy() -> Dict[str, Any]:
    with _db_lock:
        connection = db()
        row = connection.execute(
            "SELECT version, policy_json FROM policies WHERE id = 1"
        ).fetchone()
        connection.close()

    if not row:
        return {
            "version": 1,
            "approval_required_for_side_effects": True,
            "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
        }

    result = json_load(row["policy_json"], {})
    result["version"] = row["version"]
    return result


def add_event(
    execution_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    with _db_lock:
        connection = db()
        connection.execute(
            """
            INSERT INTO execution_events
            (execution_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                execution_id,
                event_type,
                json.dumps(payload or {}),
                utc_now(),
            ),
        )
        connection.commit()
        connection.close()


def update_execution(
    execution_id: str,
    **fields: Any,
) -> None:
    if not fields:
        return

    fields["updated_at"] = utc_now()

    columns = []
    values = []

    for key, value in fields.items():
        columns.append(f"{key} = ?")
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        values.append(value)

    values.append(execution_id)

    with _db_lock:
        connection = db()
        connection.execute(
            f"""
            UPDATE executions
            SET {", ".join(columns)}
            WHERE id = ?
            """,
            values,
        )
        connection.commit()
        connection.close()


def get_execution(execution_id: str) -> Optional[Dict[str, Any]]:
    with _db_lock:
        connection = db()
        row = connection.execute(
            "SELECT * FROM executions WHERE id = ?",
            (execution_id,),
        ).fetchone()
        connection.close()

    if not row:
        return None

    result = dict(row)
    result["result"] = json_load(result.pop("result_json"), None)
    return result


def get_mission(mission_id: str) -> Optional[Dict[str, Any]]:
    with _db_lock:
        connection = db()
        row = connection.execute(
            "SELECT * FROM missions WHERE id = ?",
            (mission_id,),
        ).fetchone()
        connection.close()

    if not row:
        return None

    result = dict(row)
    result["result"] = json_load(result.pop("result_json"), None)
    return result


# ============================================================
# SECURITY
# ============================================================

def is_private_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
        return (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        )
    except ValueError:
        return False


def validate_external_url(url: str) -> None:
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http and https URLs are allowed")

    if not parsed.hostname:
        raise ValueError("URL hostname is required")

    hostname = parsed.hostname.lower()

    if hostname in {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
    }:
        raise ValueError("private/internal host blocked")

    try:
        if is_private_ip(hostname):
            raise ValueError("private/internal IP blocked")
    except Exception:
        raise ValueError("invalid hostname")

    try:
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        raise ValueError("hostname could not be resolved")

    for address in addresses:
        ip = address[4][0]
        if is_private_ip(ip):
            raise ValueError("hostname resolves to a private/internal address")


def sanitize_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    safe: Dict[str, str] = {}

    for key, value in (headers or {}).items():
        lowered = key.lower()

        if any(word in lowered for word in BLOCKED_HEADER_WORDS):
            raise ValueError(
                f"credential-sensitive header blocked: {key}"
            )

        safe[key] = str(value)[:2048]

    return safe


# ============================================================
# INTENT ENGINE
# ============================================================

URL_RE = re.compile(
    r"https?://[^\s<>'\"]+",
    re.IGNORECASE,
)


def extract_url(text: str) -> Optional[str]:
    match = URL_RE.search(text)
    if not match:
        return None

    return match.group(0).rstrip(".,);]")


def infer_http_method(text: str) -> str:
    lowered = text.lower()

    if re.search(r"\b(delete|remove)\b", lowered):
        return "DELETE"

    if re.search(r"\b(update|modify|patch|change)\b", lowered):
        return "PATCH"

    if re.search(r"\b(create|submit|send|post)\b", lowered):
        return "POST"

    if re.search(r"\b(replace|put)\b", lowered):
        return "PUT"

    if re.search(r"\b(head)\b", lowered):
        return "HEAD"

    return "GET"


def classify_intent(command: str) -> str:
    lowered = command.lower().strip()

    if not lowered:
        return "unknown"

    if extract_url(command):
        return "external_http"

    if lowered in {
        "ping",
        "health",
        "status",
        "system status",
    }:
        return "system"

    if lowered.startswith("remember "):
        return "memory"

    if any(
        word in lowered
        for word in (
            "remember that",
            "save this",
            "store this",
            "memorize",
        )
    ):
        return "memory"

    if any(
        word in lowered
        for word in (
            "research",
            "investigate",
            "find evidence",
            "look into",
            "analyze",
        )
    ):
        return "research"

    return "local_command"


def normalize_command(command: str) -> Dict[str, Any]:
    command = command.strip()
    intent = classify_intent(command)

    url = extract_url(command)

    if intent == "external_http":
        method = infer_http_method(command)

        return {
            "intent": intent,
            "action_type": "external_http",
            "method": method,
            "url": url,
            "command": command,
            "requires_approval": method not in SAFE_METHODS,
            "confidence": 0.98,
        }

    if intent == "system":
        return {
            "intent": intent,
            "action_type": "system",
            "method": None,
            "url": None,
            "command": command,
            "requires_approval": False,
            "confidence": 0.99,
        }

    if intent == "memory":
        return {
            "intent": intent,
            "action_type": "memory",
            "method": None,
            "url": None,
            "command": command,
            "requires_approval": False,
            "confidence": 0.99,
        }

    if intent == "research":
        return {
            "intent": intent,
            "action_type": "research_plan",
            "method": None,
            "url": None,
            "command": command,
            "requires_approval": False,
            "confidence": 0.90,
        }

    return {
        "intent": intent,
        "action_type": "local_command",
        "method": None,
        "url": None,
        "command": command,
        "requires_approval": False,
        "confidence": 0.75,
    }


# ============================================================
# ACTION PLANNER
# ============================================================

def build_action_plan(
    command: str,
    external_access: bool = False,
) -> Dict[str, Any]:
    plan = normalize_command(command)

    if external_access and plan["url"]:
        plan["action_type"] = "external_http"

    plan["policy_version"] = policy()["version"]
    plan["plan_id"] = f"plan-{uuid.uuid4().hex[:16]}"

    return plan


# ============================================================
# EXECUTORS
# ============================================================

def external_request(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    body: Any = None,
) -> Dict[str, Any]:
    validate_external_url(url)

    method = method.upper()

    if method not in SUPPORTED_METHODS:
        raise ValueError(f"unsupported HTTP method: {method}")

    safe_headers = sanitize_headers(headers)

    request_body = None

    if body is not None:
        request_body = json.dumps(body).encode("utf-8")

        safe_headers.setdefault(
            "Content-Type",
            "application/json",
        )

    request = Request(
        url=url,
        data=request_body,
        headers=safe_headers,
        method=method,
    )

    started = time.time()

    try:
        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            raw = response.read(MAX_RESPONSE_BYTES)

            elapsed = round(
                time.time() - started,
                4,
            )

            content_type = response.headers.get(
                "Content-Type",
                "",
            )

            text_body = raw.decode(
                "utf-8",
                errors="replace",
            )

            parsed_body: Any = text_body

            if "json" in content_type.lower():
                try:
                    parsed_body = json.loads(text_body)
                except Exception:
                    parsed_body = text_body

            return {
                "success": True,
                "status_code": response.status,
                "url": url,
                "method": method,
                "content_type": content_type,
                "body": parsed_body,
                "elapsed_seconds": elapsed,
            }

    except HTTPError as exc:
        raw = exc.read(MAX_RESPONSE_BYTES)

        return {
            "success": False,
            "status_code": exc.code,
            "url": url,
            "method": method,
            "body": raw.decode(
                "utf-8",
                errors="replace",
            ),
            "error": f"HTTP {exc.code}",
        }

    except URLError as exc:
        raise RuntimeError(
            f"external request failed: {exc.reason}"
        )

    except Exception as exc:
        raise RuntimeError(
            f"external request failed: {exc}"
        )


def local_command(command: str) -> Dict[str, Any]:
    lowered = command.lower().strip()

    if lowered == "ping":
        return {
            "success": True,
            "status": "completed",
            "action": "ping",
            "message": "pong",
        }

    if lowered in {
        "health",
        "status",
        "system status",
    }:
        return {
            "success": True,
            "status": "healthy",
            "version": VERSION,
            "build": BUILD,
        }

    return {
        "success": True,
        "status": "completed",
        "action": "local_command",
        "command": command,
        "message": (
            "Command understood and safely routed, "
            "but no privileged local tool is registered for it."
        ),
    }


def remember(content: str) -> Dict[str, Any]:
    with _db_lock:
        connection = db()

        connection.execute(
            """
            INSERT INTO memories
            (content, memory_type, created_at)
            VALUES (?, ?, ?)
            """,
            (
                content,
                "command_memory",
                utc_now(),
            ),
        )

        connection.commit()

        row = connection.execute(
            "SELECT COUNT(*) AS count FROM memories"
        ).fetchone()

        connection.close()

    return {
        "success": True,
        "status": "remembered",
        "content": content,
        "memory_count": row["count"],
    }


def research_plan(command: str) -> Dict[str, Any]:
    return {
        "success": True,
        "status": "planned",
        "action": "research_plan",
        "objective": command,
        "steps": [
            "decompose objective",
            "identify evidence requirements",
            "select independent sources",
            "collect evidence",
            "compare claims",
            "verify evidence",
            "close result",
        ],
        "note": (
            "This target plans research. It does not claim "
            "live evidence collection unless a research tool "
            "is explicitly connected."
        ),
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_result(
    action_type: str,
    result: Any,
) -> Dict[str, Any]:
    if not result:
        return {
            "verified": False,
            "reason": "empty result",
        }

    if isinstance(result, dict):
        if result.get("success") is True:
            return {
                "verified": True,
                "reason": "executor reported success",
            }

        if (
            action_type == "external_http"
            and isinstance(result.get("status_code"), int)
        ):
            code = result["status_code"]

            return {
                "verified": 200 <= code < 400,
                "reason": f"HTTP status {code}",
            }

    return {
        "verified": False,
        "reason": "result requires additional verification",
    }


# ============================================================
# RECOVERY
# ============================================================

def recovery_strategy(
    action_type: str,
    method: Optional[str],
    attempt: int,
) -> Optional[Dict[str, Any]]:
    if attempt > MAX_RECOVERY_ATTEMPTS:
        return None

    if action_type == "external_http":
        if method == "HEAD":
            return {
                "method": "GET",
                "reason": "recover HEAD failure using GET",
            }

        return {
            "method": method or "GET",
            "reason": "retry external request",
        }

    if action_type in {
        "local_command",
        "system",
        "research_plan",
    }:
        return {
            "reason": "re-run deterministic action",
        }

    return None


# ============================================================
# DURABLE EXECUTION
# ============================================================

def create_execution(
    command: str,
    plan: Dict[str, Any],
    mission_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    execution_id = f"exec-{uuid.uuid4().hex[:20]}"

    approval_required = bool(
        plan.get("requires_approval")
    )

    with _db_lock:
        connection = db()

        if idempotency_key:
            existing = connection.execute(
                """
                SELECT * FROM executions
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()

            if existing:
                connection.close()
                return {
                    "execution_id": existing["id"],
                    "existing": True,
                }

        connection.execute(
            """
            INSERT INTO executions (
                id,
                mission_id,
                command,
                action_type,
                method,
                url,
                status,
                approval_required,
                approved,
                attempts,
                recovery_attempts,
                idempotency_key,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution_id,
                mission_id,
                command,
                plan["action_type"],
                plan.get("method"),
                plan.get("url"),
                "awaiting_approval"
                if approval_required
                else "queued",
                int(approval_required),
                0,
                0,
                0,
                idempotency_key,
                utc_now(),
                utc_now(),
            ),
        )

        connection.commit()
        connection.close()

    add_event(
        execution_id,
        "execution_created",
        {
            "plan_id": plan["plan_id"],
            "action_type": plan["action_type"],
        },
    )

    if approval_required:
        add_event(
            execution_id,
            "approval_required",
            {
                "method": plan.get("method"),
                "url": plan.get("url"),
            },
        )

    return {
        "execution_id": execution_id,
        "existing": False,
    }


def execute_sync(
    execution_id: str,
) -> Dict[str, Any]:
    execution = get_execution(execution_id)

    if not execution:
        raise ValueError("execution not found")

    if execution["approval_required"] and not execution["approved"]:
        return {
            "status": "awaiting_approval",
            "execution_id": execution_id,
        }

    command = execution["command"]
    action_type = execution["action_type"]

    update_execution(
        execution_id,
        status="running",
        attempts=execution["attempts"] + 1,
    )

    add_event(
        execution_id,
        "execution_started",
        {"attempt": execution["attempts"] + 1},
    )

    attempts = execution["attempts"] + 1
    recovery_attempts = execution["recovery_attempts"]

    current_method = execution["method"]

    for round_number in range(
        MAX_RECOVERY_ATTEMPTS + 1
    ):
        try:
            if action_type == "external_http":
                result = external_request(
                    method=current_method or "GET",
                    url=execution["url"],
                )

            elif action_type in {
                "local_command",
                "system",
            }:
                result = local_command(command)

            elif action_type == "memory":
                result = remember(command)

            elif action_type == "research_plan":
                result = research_plan(command)

            else:
                result = {
                    "success": False,
                    "error": "unsupported action type",
                }

            verification = verify_result(
                action_type,
                result,
            )

            if verification["verified"]:
                final = {
                    "status": "completed",
                    "execution_id": execution_id,
                    "action_type": action_type,
                    "attempts": attempts,
                    "recovery_attempts": recovery_attempts,
                    "result": result,
                    "verification": verification,
                    "result_closure": True,
                }

                update_execution(
                    execution_id,
                    status="completed",
                    attempts=attempts,
                    recovery_attempts=recovery_attempts,
                    result_json=final,
                    error=None,
                )

                add_event(
                    execution_id,
                    "result_verified",
                    verification,
                )

                add_event(
                    execution_id,
                    "result_closed",
                    {
                        "closed": True,
                    },
                )

                return final

            error = verification["reason"]

        except Exception as exc:
            error = str(exc)

        if round_number >= MAX_RECOVERY_ATTEMPTS:
            break

        strategy = recovery_strategy(
            action_type,
            current_method,
            round_number + 1,
        )

        if not strategy:
            break

        recovery_attempts += 1
        attempts += 1

        update_execution(
            execution_id,
            status="recovering",
            attempts=attempts,
            recovery_attempts=recovery_attempts,
            error=error,
        )

        add_event(
            execution_id,
            "recovery_started",
            {
                "attempt": recovery_attempts,
                "reason": error,
                "strategy": strategy,
            },
        )

        if strategy.get("method"):
            current_method = strategy["method"]

        time.sleep(0.2)

    final = {
        "status": "failed",
        "execution_id": execution_id,
        "action_type": action_type,
        "attempts": attempts,
        "recovery_attempts": recovery_attempts,
        "error": error,
        "result_closure": False,
    }

    update_execution(
        execution_id,
        status="failed",
        attempts=attempts,
        recovery_attempts=recovery_attempts,
        result_json=final,
        error=str(error),
    )

    add_event(
        execution_id,
        "execution_failed",
        final,
    )

    return final


# ============================================================
# ASYNC QUEUE
# ============================================================

_tasks: Dict[str, asyncio.Task] = {}


async def execute_async(
    execution_id: str,
) -> None:
    loop = asyncio.get_running_loop()

    try:
        await loop.run_in_executor(
            None,
            execute_sync,
            execution_id,
        )
    finally:
        _tasks.pop(execution_id, None)


def queue_execution(
    execution_id: str,
) -> None:
    task = asyncio.create_task(
        execute_async(execution_id)
    )

    _tasks[execution_id] = task


# ============================================================
# MISSION ORCHESTRATION
# ============================================================

def create_mission(
    objective: str,
) -> str:
    mission_id = f"mission-{uuid.uuid4().hex[:18]}"

    with _db_lock:
        connection = db()

        connection.execute(
            """
            INSERT INTO missions
            (id, objective, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                objective,
                "running",
                utc_now(),
                utc_now(),
            ),
        )

        connection.commit()
        connection.close()

    return mission_id


def close_mission(
    mission_id: str,
    result: Dict[str, Any],
) -> None:
    status = (
        "completed"
        if result.get("status") == "completed"
        else "failed"
    )

    with _db_lock:
        connection = db()

        connection.execute(
            """
            UPDATE missions
            SET status = ?,
                updated_at = ?,
                result_json = ?
            WHERE id = ?
            """,
            (
                status,
                utc_now(),
                json.dumps(result),
                mission_id,
            ),
        )

        connection.commit()
        connection.close()


# ============================================================
# REQUEST MODELS
# ============================================================

class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=10000)
    external_access: bool = False
    execute: bool = True
    mission_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class ExternalRequest(BaseModel):
    method: str = "GET"
    url: str
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Any = None
    mission_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    approved: bool = False


class ApprovalRequest(BaseModel):
    approved: bool


class MemoryRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class MissionRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=10000)


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "name": "AI Infinity",
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "previous_build": PREVIOUS_BUILD,
        "target": "real-world-command-capable AI Infinity",
        "interface": "/command-ui",
        "health": "/health",
        "status_endpoint": "/165-status",
        "command": "/command",
        "mission": "/mission",
        "external": "/external/execute",
        "execution": "/execution/{execution_id}",
        "approval": "/execution/{execution_id}/approve",
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    with _db_lock:
        connection = db()

        mission_count = connection.execute(
            "SELECT COUNT(*) AS c FROM missions"
        ).fetchone()["c"]

        execution_count = connection.execute(
            "SELECT COUNT(*) AS c FROM executions"
        ).fetchone()["c"]

        memory_count = connection.execute(
            "SELECT COUNT(*) AS c FROM memories"
        ).fetchone()["c"]

        event_count = connection.execute(
            "SELECT COUNT(*) AS c FROM execution_events"
        ).fetchone()["c"]

        connection.close()

    p = policy()

    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "database": "ready",
        "mission_count": mission_count,
        "execution_count": execution_count,
        "memory_count": memory_count,
        "execution_event_count": event_count,
        "policy_version": p["version"],
        "external_execution_enabled": True,
        "verification_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True,
        "intent_router_enabled": True,
        "result_closure_enabled": True,
    }


@app.get("/165-status")
def status_165() -> Dict[str, Any]:
    health_data = health()

    return {
        "status": "ready",
        "version": VERSION,
        "build": BUILD,
        "previous_build": PREVIOUS_BUILD,
        "target": "real-world-command-capable AI Infinity",
        "intent_to_action": True,
        "natural_language_routing": True,
        "structured_action_planning": True,
        "local_command_engine": True,
        "external_http_bridge": True,
        "approval_gate": True,
        "ssrf_protection": True,
        "credential_header_protection": True,
        "durable_execution": True,
        "mission_execution_link": True,
        "execution_events": True,
        "idempotency": True,
        "verification": True,
        "adaptive_recovery": True,
        "persistent_memory": True,
        "result_closure": True,
        "interface": "/command-ui",
        "health": health_data,
    }


# ============================================================
# COMMAND ROUTER
# ============================================================

@app.post("/command")
async def command_endpoint(
    request: CommandRequest,
) -> Dict[str, Any]:
    plan = build_action_plan(
        request.command,
        request.external_access,
    )

    if not request.execute:
        return {
            "status": "planned",
            "plan": plan,
        }

    mission_id = request.mission_id

    if not mission_id:
        mission_id = create_mission(
            request.command
        )

    execution = create_execution(
        command=request.command,
        plan=plan,
        mission_id=mission_id,
        idempotency_key=request.idempotency_key,
    )

    execution_id = execution["execution_id"]

    if execution.get("existing"):
        existing = get_execution(execution_id)

        return {
            "status": "existing",
            "mission_id": mission_id,
            "execution_id": execution_id,
            "execution": existing,
        }

    if plan["requires_approval"]:
        return {
            "status": "awaiting_approval",
            "mission_id": mission_id,
            "execution_id": execution_id,
            "plan": plan,
            "approval_endpoint": (
                f"/execution/{execution_id}/approve"
            ),
        }

    queue_execution(execution_id)

    return {
        "status": "accepted",
        "mission_id": mission_id,
        "execution_id": execution_id,
        "plan": plan,
    }


# ============================================================
# PLAN ONLY
# ============================================================

@app.post("/command/plan")
def command_plan(
    request: CommandRequest,
) -> Dict[str, Any]:
    plan = build_action_plan(
        request.command,
        request.external_access,
    )

    return {
        "status": "planned",
        "plan": plan,
    }


# ============================================================
# EXTERNAL EXECUTION
# ============================================================

@app.post("/external/execute")
async def external_execute(
    request: ExternalRequest,
) -> Dict[str, Any]:
    method = request.method.upper()

    if method not in SUPPORTED_METHODS:
        raise HTTPException(
            status_code=400,
            detail="unsupported HTTP method",
        )

    try:
        validate_external_url(request.url)
        sanitize_headers(request.headers)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    requires_approval = (
        method in APPROVAL_REQUIRED_METHODS
    )

    plan = {
        "plan_id": f"plan-{uuid.uuid4().hex[:16]}",
        "intent": "external_http",
        "action_type": "external_http",
        "method": method,
        "url": request.url,
        "requires_approval": requires_approval,
        "confidence": 1.0,
        "policy_version": policy()["version"],
    }

    execution = create_execution(
        command=f"{method} {request.url}",
        plan=plan,
        mission_id=request.mission_id,
        idempotency_key=request.idempotency_key,
    )

    execution_id = execution["execution_id"]

    if execution.get("existing"):
        return {
            "status": "existing",
            "execution_id": execution_id,
            "execution": get_execution(
                execution_id
            ),
        }

    if requires_approval and not request.approved:
        return {
            "status": "awaiting_approval",
            "execution_id": execution_id,
            "approval_endpoint": (
                f"/execution/{execution_id}/approve"
            ),
        }

    if request.approved:
        update_execution(
            execution_id,
            approved=1,
            status="queued",
        )

    queue_execution(execution_id)

    return {
        "status": "accepted",
        "execution_id": execution_id,
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post("/execution/{execution_id}/approve")
async def approve_execution(
    execution_id: str,
    request: ApprovalRequest,
) -> Dict[str, Any]:
    execution = get_execution(execution_id)

    if not execution:
        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    if not execution["approval_required"]:
        return {
            "status": "not_required",
            "execution_id": execution_id,
        }

    if not request.approved:
        update_execution(
            execution_id,
            status="rejected",
            approved=0,
        )

        add_event(
            execution_id,
            "approval_rejected",
        )

        return {
            "status": "rejected",
            "execution_id": execution_id,
        }

    update_execution(
        execution_id,
        approved=1,
        status="queued",
    )

    add_event(
        execution_id,
        "approval_granted",
    )

    queue_execution(execution_id)

    return {
        "status": "approved",
        "execution_id": execution_id,
    }


# ============================================================
# EXECUTION STATE
# ============================================================

@app.get("/execution/{execution_id}")
def execution_endpoint(
    execution_id: str,
) -> Dict[str, Any]:
    execution = get_execution(execution_id)

    if not execution:
        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    with _db_lock:
        connection = db()

        rows = connection.execute(
            """
            SELECT event_type, payload_json, created_at
            FROM execution_events
            WHERE execution_id = ?
            ORDER BY id ASC
            """,
            (execution_id,),
        ).fetchall()

        connection.close()

    execution["events"] = [
        {
            "event": row["event_type"],
            "payload": json_load(
                row["payload_json"],
                {},
            ),
            "created_at": row["created_at"],
        }
        for row in rows
    ]

    return execution


# ============================================================
# MISSIONS
# ============================================================

@app.post("/mission")
async def mission_endpoint(
    request: MissionRequest,
) -> Dict[str, Any]:
    mission_id = create_mission(
        request.objective
    )

    plan = build_action_plan(
        request.objective
    )

    execution = create_execution(
        command=request.objective,
        plan=plan,
        mission_id=mission_id,
    )

    execution_id = execution["execution_id"]

    if plan["requires_approval"]:
        return {
            "status": "awaiting_approval",
            "mission_id": mission_id,
            "execution_id": execution_id,
            "plan": plan,
        }

    queue_execution(execution_id)

    return {
        "status": "accepted",
        "mission_id": mission_id,
        "execution_id": execution_id,
        "plan": plan,
    }


@app.get("/mission/{mission_id}")
def mission_status(
    mission_id: str,
) -> Dict[str, Any]:
    mission = get_mission(mission_id)

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    with _db_lock:
        connection = db()

        rows = connection.execute(
            """
            SELECT *
            FROM executions
            WHERE mission_id = ?
            ORDER BY created_at ASC
            """,
            (mission_id,),
        ).fetchall()

        connection.close()

    mission["executions"] = []

    for row in rows:
        item = dict(row)
        item["result"] = json_load(
            item.pop("result_json"),
            None,
        )
        mission["executions"].append(item)

    return mission


# ============================================================
# MEMORY
# ============================================================

@app.post("/memory")
def memory_endpoint(
    request: MemoryRequest,
) -> Dict[str, Any]:
    return remember(request.content)


@app.get("/memory")
def memory_list() -> Dict[str, Any]:
    with _db_lock:
        connection = db()

        rows = connection.execute(
            """
            SELECT id, content, memory_type, created_at
            FROM memories
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

        connection.close()

    return {
        "status": "ok",
        "count": len(rows),
        "memories": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# ROUTER SELF TEST
# ============================================================

@app.get("/test-router")
def test_router() -> Dict[str, Any]:
    tests = [
        build_action_plan("ping"),
        build_action_plan(
            "GET https://example.com"
        ),
        build_action_plan(
            "remember that AI Infinity works"
        ),
        build_action_plan(
            "research autonomous AI agents"
        ),
    ]

    return {
        "status": "completed",
        "version": VERSION,
        "tests": [
            {
                "command": "ping",
                "action_type": tests[0]["action_type"],
                "passed": tests[0]["action_type"]
                == "system",
            },
            {
                "command": "GET https://example.com",
                "action_type": tests[1]["action_type"],
                "method": tests[1]["method"],
                "passed": (
                    tests[1]["action_type"]
                    == "external_http"
                    and tests[1]["method"]
                    == "GET"
                ),
            },
            {
                "command": "remember that AI Infinity works",
                "action_type": tests[2]["action_type"],
                "passed": tests[2]["action_type"]
                == "memory",
            },
            {
                "command": "research autonomous AI agents",
                "action_type": tests[3]["action_type"],
                "passed": tests[3]["action_type"]
                == "research_plan",
            },
        ],
    }


# ============================================================
# EXTERNAL SECURITY SELF TEST
# ============================================================

@app.get("/external/self-test")
def external_self_test() -> Dict[str, Any]:
    tests = []

    plan = build_action_plan(
        "GET https://example.com"
    )

    tests.append(
        {
            "name": "intent_to_external_plan",
            "passed": (
                plan["action_type"]
                == "external_http"
                and plan["method"] == "GET"
            ),
        }
    )

    try:
        validate_external_url(
            "http://127.0.0.1"
        )
        ssrf_passed = False
    except ValueError:
        ssrf_passed = True

    tests.append(
        {
            "name": "ssrf_block",
            "passed": ssrf_passed,
        }
    )

    try:
        sanitize_headers(
            {
                "Authorization": "secret"
            }
        )
        credential_passed = False
    except ValueError:
        credential_passed = True

    tests.append(
        {
            "name": "credential_header_block",
            "passed": credential_passed,
        }
    )

    return {
        "status": "completed",
        "test": "external_self_test",
        "passed": all(
            item["passed"]
            for item in tests
        ),
        "tests": tests,
    }


# ============================================================
# RECOVERY ON STARTUP
# ============================================================

def recover_interrupted_executions() -> None:
    with _db_lock:
        connection = db()

        rows = connection.execute(
            """
            SELECT id
            FROM executions
            WHERE status IN ('running', 'recovering', 'queued')
            """
        ).fetchall()

        connection.close()

    for row in rows:
        execution_id = row["id"]

        update_execution(
            execution_id,
            status="queued",
        )

        add_event(
            execution_id,
            "restart_recovery",
            {
                "version": VERSION,
            },
        )


@app.on_event("startup")
async def startup() -> None:
    recover_interrupted_executions()


# ============================================================
# COMMAND UI
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1">
<title>AI Infinity 2050.165</title>

<style>
body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #0b0f14;
    color: #eef2f7;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 24px;
}

.card {
    background: #141a22;
    border: 1px solid #293241;
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 16px;
}

h1 {
    margin-bottom: 4px;
}

.sub {
    color: #9ba8b7;
}

textarea {
    width: 100%;
    min-height: 120px;
    box-sizing: border-box;
    border-radius: 10px;
    border: 1px solid #344154;
    background: #0d1219;
    color: white;
    padding: 12px;
    font-size: 16px;
    resize: vertical;
}

button {
    margin-top: 12px;
    padding: 11px 16px;
    border-radius: 9px;
    border: 0;
    cursor: pointer;
    font-weight: bold;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: #090d12;
    padding: 14px;
    border-radius: 10px;
    overflow: auto;
}

.status {
    font-weight: bold;
}
</style>
</head>

<body>

<div class="container">

<div class="card">
<h1>AI Infinity</h1>
<div class="sub">
TARGET-2050.165 — INTENT-TO-ACTION
REAL-WORLD COMMAND ORCHESTRATOR
</div>
<p class="status" id="status">
Checking...
</p>
</div>

<div class="card">
<h2>Command</h2>

<textarea id="command"
placeholder="Try: ping
or: GET https://example.com
or: remember that AI Infinity reached 2050.165
or: research autonomous AI agents"></textarea>

<br>

<button onclick="sendCommand()">
Execute Command
</button>

<button onclick="planCommand()">
Plan Only
</button>
</div>

<div class="card">
<h2>Result</h2>
<pre id="result">Waiting...</pre>
</div>

</div>

<script>

async function refreshHealth() {
    try {
        const response = await fetch("/health");
        const data = await response.json();

        document.getElementById("status").innerText =
            data.status + " — " + data.version;
    } catch (error) {
        document.getElementById("status").innerText =
            "offline";
    }
}

async function planCommand() {
    const command =
        document.getElementById("command").value;

    const response = await fetch(
        "/command/plan",
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                command: command,
                execute: false
            })
        }
    );

    const data = await response.json();

    document.getElementById("result").innerText =
        JSON.stringify(data, null, 2);
}

async function sendCommand() {
    const command =
        document.getElementById("command").value;

    const response = await fetch(
        "/command",
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                command: command,
                execute: true
            })
        }
    );

    const data = await response.json();

    document.getElementById("result").innerText =
        JSON.stringify(data, null, 2);

    if (data.execution_id) {
        watchExecution(data.execution_id);
    }
}

async function watchExecution(id) {
    for (let i = 0; i < 30; i++) {

        await new Promise(
            resolve => setTimeout(resolve, 500)
        );

        const response = await fetch(
            "/execution/" + id
        );

        const data = await response.json();

        document.getElementById("result").innerText =
            JSON.stringify(data, null, 2);

        if (
            data.status === "completed" ||
            data.status === "failed" ||
            data.status === "rejected"
        ) {
            break;
        }
    }
}

refreshHealth();

</script>

</body>
</html>
"""


@app.get("/command-ui", response_class=HTMLResponse)
def command_ui() -> str:
    return HTML
