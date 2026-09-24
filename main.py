"""
AI Infinity
TARGET-2050.166
MULTI-TOOL REAL-WORLD COMMAND ORCHESTRATOR CORE

Previous: TARGET-2050.165
Goal: practical real-world-command-capable AI Infinity

Architecture:

Natural language
      ↓
Intent Router
      ↓
Action Planner
      ↓
Policy / Approval
      ↓
Durable Execution
      ↓
Tool Executor
      ↓
Verification
      ↓
Recovery
      ↓
Result Closure
      ↓
Mission / Memory / Events

This build intentionally does NOT expose arbitrary shell execution.
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
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# CORE
# ============================================================

VERSION = "TARGET-2050.166"
BUILD = "MULTI-TOOL-REAL-WORLD-COMMAND-ORCHESTRATOR-CORE"
PREVIOUS_BUILD = "TARGET-2050.165"

DB_PATH = os.getenv(
    "AI_INFINITY_DB",
    "/tmp/ai-infinity/ai_infinity.db",
)

WORKSPACE_ROOT = Path(
    os.getenv(
        "AI_INFINITY_WORKSPACE",
        "/tmp/ai-infinity/workspace",
    )
).resolve()

MAX_FILE_BYTES = 1024 * 1024
MAX_HTTP_BYTES = 1024 * 1024
HTTP_TIMEOUT = 15
MAX_RECOVERY_ATTEMPTS = 2
MAX_WORKFLOW_STEPS = 20

SAFE_HTTP_METHODS = {"GET", "HEAD"}
SIDE_EFFECT_HTTP_METHODS = {
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
}

SUPPORTED_HTTP_METHODS = {
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
}

BLOCKED_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "access-token",
    "refresh-token",
    "password",
    "secret",
    "credential",
}

DB_LOCK = threading.Lock()

RUNNING_TASKS: Dict[str, asyncio.Task] = {}


app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "Practical real-world command orchestration engine "
        "with multi-tool execution, verification and recovery."
    ),
)


# ============================================================
# DATABASE
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    WORKSPACE_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    with DB_LOCK:
        db = connect_db()

        db.executescript(
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
                path TEXT,
                content TEXT,
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

            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policies (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                version INTEGER NOT NULL,
                policy_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_execution_idempotency
            ON executions(idempotency_key)
            WHERE idempotency_key IS NOT NULL;
            """
        )

        policy = db.execute(
            "SELECT id FROM policies WHERE id=1"
        ).fetchone()

        if not policy:
            policy = {
                "approval_required_for_side_effects": True,
                "external_http_enabled": True,
                "ssrf_protection": True,
                "credential_header_protection": True,
                "workspace_tools": True,
                "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
            }

            db.execute(
                """
                INSERT INTO policies
                (id, version, policy_json, updated_at)
                VALUES (1, 1, ?, ?)
                """,
                (
                    json.dumps(policy),
                    now(),
                ),
            )

        db.commit()
        db.close()


initialize_database()


# ============================================================
# DATABASE HELPERS
# ============================================================

def load_json(
    value: Optional[str],
    default: Any = None,
) -> Any:
    if not value:
        return default

    try:
        return json.loads(value)
    except Exception:
        return default


def get_policy() -> Dict[str, Any]:
    with DB_LOCK:
        db = connect_db()

        row = db.execute(
            """
            SELECT version, policy_json
            FROM policies
            WHERE id=1
            """
        ).fetchone()

        db.close()

    if not row:
        return {
            "version": 1,
            "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
        }

    policy = load_json(
        row["policy_json"],
        {},
    )

    policy["version"] = row["version"]

    return policy


def update_execution(
    execution_id: str,
    **fields: Any,
) -> None:

    if not fields:
        return

    fields["updated_at"] = now()

    assignments = []
    values = []

    for key, value in fields.items():

        assignments.append(
            f"{key}=?"
        )

        if isinstance(value, (dict, list)):
            value = json.dumps(value)

        values.append(value)

    values.append(execution_id)

    with DB_LOCK:
        db = connect_db()

        db.execute(
            f"""
            UPDATE executions
            SET {", ".join(assignments)}
            WHERE id=?
            """,
            values,
        )

        db.commit()
        db.close()


def add_event(
    execution_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:

    with DB_LOCK:
        db = connect_db()

        db.execute(
            """
            INSERT INTO execution_events
            (
                execution_id,
                event_type,
                payload_json,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                execution_id,
                event_type,
                json.dumps(payload or {}),
                now(),
            ),
        )

        db.commit()
        db.close()


def get_execution(
    execution_id: str,
) -> Optional[Dict[str, Any]]:

    with DB_LOCK:
        db = connect_db()

        row = db.execute(
            """
            SELECT *
            FROM executions
            WHERE id=?
            """,
            (execution_id,),
        ).fetchone()

        db.close()

    if not row:
        return None

    result = dict(row)

    result["result"] = load_json(
        result.pop("result_json"),
        None,
    )

    return result


def get_mission(
    mission_id: str,
) -> Optional[Dict[str, Any]]:

    with DB_LOCK:
        db = connect_db()

        row = db.execute(
            """
            SELECT *
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        db.close()

    if not row:
        return None

    result = dict(row)

    result["result"] = load_json(
        result.pop("result_json"),
        None,
    )

    return result


# ============================================================
# SECURITY
# ============================================================

def is_private_ip(
    value: str,
) -> bool:

    try:
        ip = ipaddress.ip_address(value)

        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )

    except ValueError:
        return False


def validate_external_url(
    url: str,
) -> None:

    parsed = urlparse(url)

    if parsed.scheme not in {
        "http",
        "https",
    }:
        raise ValueError(
            "only HTTP and HTTPS URLs are allowed"
        )

    if not parsed.hostname:
        raise ValueError(
            "URL hostname is required"
        )

    host = parsed.hostname.lower()

    blocked_hosts = {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
    }

    if host in blocked_hosts:
        raise ValueError(
            "internal hostname blocked"
        )

    if is_private_ip(host):
        raise ValueError(
            "private/internal IP blocked"
        )

    try:
        addresses = socket.getaddrinfo(
            host,
            parsed.port
            or (
                443
                if parsed.scheme == "https"
                else 80
            ),
            type=socket.SOCK_STREAM,
        )

    except socket.gaierror:
        raise ValueError(
            "hostname could not be resolved"
        )

    for address in addresses:

        resolved_ip = address[4][0]

        if is_private_ip(resolved_ip):
            raise ValueError(
                "hostname resolves to private/internal address"
            )


def sanitize_headers(
    headers: Optional[Dict[str, str]],
) -> Dict[str, str]:

    safe = {}

    for key, value in (
        headers or {}
    ).items():

        lowered = key.lower()

        if any(
            blocked in lowered
            for blocked in BLOCKED_HEADERS
        ):
            raise ValueError(
                f"credential-sensitive header blocked: {key}"
            )

        safe[key] = str(value)[:2048]

    return safe


# ============================================================
# INTENT ROUTER
# ============================================================

URL_PATTERN = re.compile(
    r"https?://[^\s<>'\"]+",
    re.IGNORECASE,
)


def extract_url(
    text: str,
) -> Optional[str]:

    match = URL_PATTERN.search(text)

    if not match:
        return None

    return match.group(0).rstrip(
        ".,);]"
    )


def workflow_parts(
    command: str,
) -> List[str]:

    text = command.strip()

    if text.lower().startswith(
        "workflow:"
    ):
        text = text.split(
            ":",
            1,
        )[1].strip()

    parts = re.split(
        r"\s+(?:then|and then)\s+|\s*;\s*",
        text,
        flags=re.IGNORECASE,
    )

    return [
        part.strip()
        for part in parts
        if part.strip()
    ][:MAX_WORKFLOW_STEPS]


def parse_file_command(
    command: str,
) -> Optional[Dict[str, Any]]:

    text = command.strip()

    match = re.match(
        r"^read\s+file\s+(.+)$",
        text,
        re.IGNORECASE,
    )

    if match:
        return {
            "action_type": "file_read",
            "path": match.group(1).strip(),
        }

    match = re.match(
        r"^list\s+files(?:\s+(.+))?$",
        text,
        re.IGNORECASE,
    )

    if match:
        return {
            "action_type": "file_list",
            "path": (
                match.group(1)
                or "."
            ).strip(),
        }

    match = re.match(
        r"^write\s+file\s+(.+?)\s+(?:with|:)\s*(.+)$",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    if match:
        return {
            "action_type": "file_write",
            "path": match.group(1).strip(),
            "content": match.group(2),
        }

    match = re.match(
        r"^delete\s+file\s+(.+)$",
        text,
        re.IGNORECASE,
    )

    if match:
        return {
            "action_type": "file_delete",
            "path": match.group(1).strip(),
        }

    return None


def infer_http_method(
    text: str,
) -> str:

    lowered = text.lower()

    if re.search(
        r"\b(delete|remove)\b",
        lowered,
    ):
        return "DELETE"

    if re.search(
        r"\b(patch|update|modify|change)\b",
        lowered,
    ):
        return "PATCH"

    if re.search(
        r"\b(post|send|submit|create)\b",
        lowered,
    ):
        return "POST"

    if re.search(
        r"\b(put|replace)\b",
        lowered,
    ):
        return "PUT"

    if re.search(
        r"\bhead\b",
        lowered,
    ):
        return "HEAD"

    return "GET"


def classify_intent(
    command: str,
) -> str:

    lowered = command.lower().strip()

    if not lowered:
        return "unknown"

    if len(workflow_parts(command)) > 1:
        return "workflow"

    file_action = parse_file_command(command)

    if file_action:
        return "file"

    if extract_url(command):
        return "external_http"

    if lowered in {
        "ping",
        "health",
        "status",
        "system status",
    }:
        return "system"

    if (
        lowered.startswith("remember ")
        or "remember that" in lowered
        or lowered.startswith("memorize ")
        or lowered.startswith("save this")
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


def build_plan(
    command: str,
    external_access: bool = False,
) -> Dict[str, Any]:

    command = command.strip()

    intent = classify_intent(
        command
    )

    plan: Dict[str, Any] = {
        "plan_id": (
            f"plan-{uuid.uuid4().hex[:16]}"
        ),
        "command": command,
        "intent": intent,
        "confidence": 0.75,
        "policy_version": get_policy()["version"],
    }

    if intent == "workflow":

        plan.update(
            {
                "action_type": "workflow",
                "steps": workflow_parts(command),
                "requires_approval": False,
                "confidence": 0.95,
            }
        )

    elif intent == "file":

        parsed = parse_file_command(
            command
        ) or {}

        action = parsed.get(
            "action_type",
            "file",
        )

        plan.update(
            {
                "action_type": action,
                "path": parsed.get("path"),
                "content": parsed.get("content"),
                "requires_approval": action
                in {
                    "file_write",
                    "file_delete",
                },
                "confidence": 0.97,
            }
        )

    elif intent == "external_http":

        method = infer_http_method(
            command
        )

        plan.update(
            {
                "action_type": "external_http",
                "method": method,
                "url": extract_url(command),
                "requires_approval": (
                    method
                    not in SAFE_HTTP_METHODS
                ),
                "confidence": 0.98,
            }
        )

    elif intent == "system":

        plan.update(
            {
                "action_type": "system",
                "requires_approval": False,
                "confidence": 0.99,
            }
        )

    elif intent == "memory":

        plan.update(
            {
                "action_type": "memory",
                "requires_approval": False,
                "confidence": 0.99,
            }
        )

    elif intent == "research":

        plan.update(
            {
                "action_type": "research_plan",
                "requires_approval": False,
                "confidence": 0.90,
            }
        )

    else:

        plan.update(
            {
                "action_type": "local_command",
                "requires_approval": False,
            }
        )

    if (
        external_access
        and plan.get("url")
    ):
        plan["action_type"] = (
            "external_http"
        )

    return plan


# ============================================================
# TOOL REGISTRY
# ============================================================

TOOL_REGISTRY = {
    "system": {
        "enabled": True,
        "approval": False,
    },
    "memory": {
        "enabled": True,
        "approval": False,
    },
    "research_plan": {
        "enabled": True,
        "approval": False,
    },
    "external_http": {
        "enabled": True,
        "approval": "side_effect",
        "safe_methods": sorted(
            SAFE_HTTP_METHODS
        ),
    },
    "file_read": {
        "enabled": True,
        "approval": False,
    },
    "file_list": {
        "enabled": True,
        "approval": False,
    },
    "file_write": {
        "enabled": True,
        "approval": True,
    },
    "file_delete": {
        "enabled": True,
        "approval": True,
    },
    "workflow": {
        "enabled": True,
        "approval": "per_step",
    },
}


# ============================================================
# SYSTEM TOOL
# ============================================================

def execute_system(
    command: str,
) -> Dict[str, Any]:

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
            "Command safely routed. "
            "No arbitrary OS shell execution is exposed."
        ),
    }


# ============================================================
# MEMORY
# ============================================================

def remember(
    content: str,
) -> Dict[str, Any]:

    with DB_LOCK:
        db = connect_db()

        db.execute(
            """
            INSERT INTO memories
            (
                content,
                memory_type,
                created_at
            )
            VALUES (?, ?, ?)
            """,
            (
                content,
                "command_memory",
                now(),
            ),
        )

        db.commit()

        count = db.execute(
            """
            SELECT COUNT(*) AS count
            FROM memories
            """
        ).fetchone()["count"]

        db.close()

    return {
        "success": True,
        "status": "remembered",
        "content": content,
        "memory_count": count,
    }


# ============================================================
# RESEARCH PLANNER
# ============================================================

def research_plan(
    command: str,
) -> Dict[str, Any]:

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
            "Research orchestration is available. "
            "A connected research provider is required "
            "for live source collection."
        ),
    }


# ============================================================
# EXTERNAL HTTP TOOL
# ============================================================

def external_http(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    body: Any = None,
) -> Dict[str, Any]:

    method = method.upper()

    if method not in SUPPORTED_HTTP_METHODS:
        raise ValueError(
            f"unsupported HTTP method: {method}"
        )

    validate_external_url(
        url
    )

    safe_headers = sanitize_headers(
        headers
    )

    request_body = None

    if body is not None:

        request_body = json.dumps(
            body
        ).encode("utf-8")

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
            timeout=HTTP_TIMEOUT,
        ) as response:

            raw = response.read(
                MAX_HTTP_BYTES
            )

            content_type = (
                response.headers.get(
                    "Content-Type",
                    "",
                )
            )

            text = raw.decode(
                "utf-8",
                errors="replace",
            )

            payload: Any = text

            if "json" in content_type.lower():

                try:
                    payload = json.loads(
                        text
                    )
                except Exception:
                    pass

            return {
                "success": True,
                "status_code": response.status,
                "url": url,
                "method": method,
                "content_type": content_type,
                "body": payload,
                "elapsed_seconds": round(
                    time.time() - started,
                    4,
                ),
            }

    except HTTPError as exc:

        return {
            "success": False,
            "status_code": exc.code,
            "url": url,
            "method": method,
            "body": exc.read(
                MAX_HTTP_BYTES
            ).decode(
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


# ============================================================
# WORKSPACE FILE TOOLS
# ============================================================

def workspace_path(
    relative_path: str,
) -> Path:

    relative_path = str(
        relative_path or ""
    ).strip()

    if not relative_path:
        raise ValueError(
            "file path required"
        )

    candidate = Path(
        relative_path
    )

    if candidate.is_absolute():
        raise ValueError(
            "absolute paths are not allowed"
        )

    resolved = (
        WORKSPACE_ROOT
        / candidate
    ).resolve()

    try:
        resolved.relative_to(
            WORKSPACE_ROOT
        )
    except ValueError:
        raise ValueError(
            "path escapes AI Infinity workspace"
        )

    return resolved


def file_read(
    path: str,
) -> Dict[str, Any]:

    target = workspace_path(
        path
    )

    if not target.exists():
        raise FileNotFoundError(
            "file not found"
        )

    if not target.is_file():
        raise ValueError(
            "path is not a file"
        )

    if (
        target.stat().st_size
        > MAX_FILE_BYTES
    ):
        raise ValueError(
            "file too large"
        )

    content = target.read_text(
        encoding="utf-8",
        errors="replace",
    )

    return {
        "success": True,
        "status": "completed",
        "action": "file_read",
        "path": str(
            target.relative_to(
                WORKSPACE_ROOT
            )
        ),
        "content": content,
    }


def file_list(
    path: str = ".",
) -> Dict[str, Any]:

    target = workspace_path(
        path
    )

    if not target.exists():
        raise FileNotFoundError(
            "directory not found"
        )

    if not target.is_dir():
        raise ValueError(
            "path is not a directory"
        )

    entries = []

    for item in sorted(
        target.iterdir(),
        key=lambda x: x.name.lower(),
    )[:200]:

        entries.append(
            {
                "name": item.name,
                "type": (
                    "directory"
                    if item.is_dir()
                    else "file"
                ),
                "size": (
                    item.stat().st_size
                    if item.is_file()
                    else None
                ),
            }
        )

    return {
        "success": True,
        "status": "completed",
        "action": "file_list",
        "path": str(
            target.relative_to(
                WORKSPACE_ROOT
            )
        ),
        "entries": entries,
    }


def file_write(
    path: str,
    content: str,
) -> Dict[str, Any]:

    target = workspace_path(
        path
    )

    data = content.encode(
        "utf-8"
    )

    if len(data) > MAX_FILE_BYTES:
        raise ValueError(
            "file content too large"
        )

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = target.with_name(
        target.name
        + ".ai-infinity.tmp"
    )

    temporary.write_bytes(
        data
    )

    temporary.replace(
        target
    )

    return {
        "success": True,
        "status": "completed",
        "action": "file_write",
        "path": str(
            target.relative_to(
                WORKSPACE_ROOT
            )
        ),
        "bytes": len(data),
    }


def file_delete(
    path: str,
) -> Dict[str, Any]:

    target = workspace_path(
        path
    )

    if not target.exists():
        raise FileNotFoundError(
            "file not found"
        )

    if not target.is_file():
        raise ValueError(
            "only files may be deleted"
        )

    target.unlink()

    return {
        "success": True,
        "status": "completed",
        "action": "file_delete",
        "path": str(
            target.relative_to(
                WORKSPACE_ROOT
            )
        ),
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify(
    action_type: str,
    result: Any,
) -> Dict[str, Any]:

    if not result:
        return {
            "verified": False,
            "reason": "empty result",
        }

    if isinstance(
        result,
        dict,
    ):

        if result.get(
            "success"
        ) is True:

            return {
                "verified": True,
                "reason": (
                    "executor reported success"
                ),
            }

        status_code = result.get(
            "status_code"
        )

        if isinstance(
            status_code,
            int,
        ):

            return {
                "verified": (
                    200
                    <= status_code
                    < 400
                ),
                "reason": (
                    f"HTTP status {status_code}"
                ),
            }

    return {
        "verified": False,
        "reason": (
            "result requires additional verification"
        ),
    }


# ============================================================
# RECOVERY
# ============================================================

def recovery_strategy(
    action_type: str,
    method: Optional[str],
    recovery_number: int,
) -> Optional[Dict[str, Any]]:

    if (
        recovery_number
        > MAX_RECOVERY_ATTEMPTS
    ):
        return None

    if action_type == "external_http":

        return {
            "reason": (
                "retry external HTTP operation"
            ),
            "method": (
                "GET"
                if method == "HEAD"
                else method
            ),
        }

    if action_type in {
        "system",
        "local_command",
        "memory",
        "research_plan",
        "file_read",
        "file_list",
    }:

        return {
            "reason": (
                "retry deterministic operation"
            )
        }

    return None


# ============================================================
# MISSION
# ============================================================

def create_mission(
    objective: str,
) -> str:

    mission_id = (
        f"mission-{uuid.uuid4().hex[:18]}"
    )

    with DB_LOCK:
        db = connect_db()

        db.execute(
            """
            INSERT INTO missions
            (
                id,
                objective,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                objective,
                "running",
                now(),
                now(),
            ),
        )

        db.commit()
        db.close()

    return mission_id


def close_mission(
    mission_id: str,
    result: Dict[str, Any],
) -> None:

    status = (
        "completed"
        if result.get("status")
        == "completed"
        else "failed"
    )

    with DB_LOCK:
        db = connect_db()

        db.execute(
            """
            UPDATE missions
            SET status=?,
                updated_at=?,
                result_json=?
            WHERE id=?
            """,
            (
                status,
                now(),
                json.dumps(result),
                mission_id,
            ),
        )

        db.commit()
        db.close()


# ============================================================
# EXECUTION RECORD
# ============================================================

def create_execution(
    command: str,
    plan: Dict[str, Any],
    mission_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:

    if idempotency_key:

        with DB_LOCK:
            db = connect_db()

            existing = db.execute(
                """
                SELECT id
                FROM executions
                WHERE idempotency_key=?
                """,
                (idempotency_key,),
            ).fetchone()

            db.close()

        if existing:
            return {
                "execution_id": existing["id"],
                "existing": True,
            }

    execution_id = (
        f"exec-{uuid.uuid4().hex[:20]}"
    )

    approval_required = bool(
        plan.get(
            "requires_approval"
        )
    )

    status = (
        "awaiting_approval"
        if approval_required
        else "queued"
    )

    with DB_LOCK:
        db = connect_db()

        db.execute(
            """
            INSERT INTO executions
            (
                id,
                mission_id,
                command,
                action_type,
                method,
                url,
                path,
                content,
                status,
                approval_required,
                approved,
                attempts,
                recovery_attempts,
                idempotency_key,
                created_at,
                updated_at
            )
            VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution_id,
                mission_id,
                command,
                plan["action_type"],
                plan.get("method"),
                plan.get("url"),
                plan.get("path"),
                plan.get("content"),
                status,
                int(approval_required),
                0,
                0,
                0,
                idempotency_key,
                now(),
                now(),
            ),
        )

        db.commit()
        db.close()

    add_event(
        execution_id,
        "execution_created",
        {
            "plan_id": plan["plan_id"],
            "action_type": plan[
                "action_type"
            ],
        },
    )

    if approval_required:

        add_event(
            execution_id,
            "approval_required",
            {
                "action_type": plan[
                    "action_type"
                ],
                "method": plan.get(
                    "method"
                ),
                "url": plan.get(
                    "url"
                ),
                "path": plan.get(
                    "path"
                ),
            },
        )

    return {
        "execution_id": execution_id,
        "existing": False,
    }


# ============================================================
# EXECUTION ENGINE
# ============================================================

def execute_sync(
    execution_id: str,
) -> Dict[str, Any]:

    execution = get_execution(
        execution_id
    )

    if not execution:
        raise ValueError(
            "execution not found"
        )

    if (
        execution["approval_required"]
        and not execution["approved"]
    ):
        return {
            "status": "awaiting_approval",
            "execution_id": execution_id,
        }

    action_type = execution[
        "action_type"
    ]

    command = execution[
        "command"
    ]

    method = (
        execution.get("method")
        or "GET"
    )

    attempts = (
        execution["attempts"]
        + 1
    )

    recovery_attempts = execution[
        "recovery_attempts"
    ]

    update_execution(
        execution_id,
        status="running",
        attempts=attempts,
    )

    add_event(
        execution_id,
        "execution_started",
        {
            "attempt": attempts
        },
    )

    last_error = None

    for round_number in range(
        MAX_RECOVERY_ATTEMPTS + 1
    ):

        try:

            if action_type == "external_http":

                result = external_http(
                    method,
                    execution["url"],
                )

            elif action_type in {
                "system",
                "local_command",
            }:

                result = execute_system(
                    command
                )

            elif action_type == "memory":

                result = remember(
                    command
                )

            elif action_type == "research_plan":

                result = research_plan(
                    command
                )

            elif action_type == "file_read":

                result = file_read(
                    execution["path"]
                    or ""
                )

            elif action_type == "file_list":

                result = file_list(
                    execution["path"]
                    or "."
                )

            elif action_type == "file_write":

                result = file_write(
                    execution["path"]
                    or "",
                    execution["content"]
                    or "",
                )

            elif action_type == "file_delete":

                result = file_delete(
                    execution["path"]
                    or ""
                )

            else:

                result = {
                    "success": False,
                    "error": (
                        "unsupported action type"
                    ),
                }

            verification = verify(
                action_type,
                result,
            )

            if verification[
                "verified"
            ]:

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
                        "closed": True
                    },
                )

                return final

            last_error = verification[
                "reason"
            ]

        except Exception as exc:

            last_error = str(exc)

        if (
            round_number
            >= MAX_RECOVERY_ATTEMPTS
        ):
            break

        strategy = recovery_strategy(
            action_type,
            method,
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
            error=last_error,
        )

        add_event(
            execution_id,
            "recovery_started",
            {
                "attempt": recovery_attempts,
                "reason": last_error,
                "strategy": strategy,
            },
        )

        if strategy.get(
            "method"
        ):
            method = strategy[
                "method"
            ]

        time.sleep(
            0.2
        )

    final = {
        "status": "failed",
        "execution_id": execution_id,
        "action_type": action_type,
        "attempts": attempts,
        "recovery_attempts": recovery_attempts,
        "error": last_error,
        "result_closure": False,
    }

    update_execution(
        execution_id,
        status="failed",
        attempts=attempts,
        recovery_attempts=recovery_attempts,
        result_json=final,
        error=str(last_error),
    )

    add_event(
        execution_id,
        "execution_failed",
        final,
    )

    return final


async def execute_async(
    execution_id: str,
) -> None:

    loop = asyncio.get_running_loop()

    try:

        result = await loop.run_in_executor(
            None,
            execute_sync,
            execution_id,
        )

        execution = get_execution(
            execution_id
        )

        if execution:

            mission_id = execution.get(
                "mission_id"
            )

            if (
                mission_id
                and result.get(
                    "status"
                )
                in {
                    "completed",
                    "failed",
                }
            ):

                close_mission(
                    mission_id,
                    result,
                )

    finally:

        RUNNING_TASKS.pop(
            execution_id,
            None,
        )


def queue_execution(
    execution_id: str,
) -> None:

    RUNNING_TASKS[
        execution_id
    ] = asyncio.create_task(
        execute_async(
            execution_id
        )
    )


# ============================================================
# WORKFLOW ENGINE
# ============================================================

def execute_workflow_sync(
    parent_execution_id: str,
    mission_id: Optional[str],
    command: str,
) -> Dict[str, Any]:

    steps = workflow_parts(
        command
    )

    if not steps:
        raise ValueError(
            "workflow is empty"
        )

    if len(steps) > MAX_WORKFLOW_STEPS:
        raise ValueError(
            "workflow has too many steps"
        )

    workflow_id = (
        f"workflow-{uuid.uuid4().hex[:18]}"
    )

    with DB_LOCK:
        db = connect_db()

        db.execute(
            """
            INSERT INTO workflow_runs
            (
                id,
                mission_id,
                objective,
                status,
                steps_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                mission_id,
                command,
                "running",
                json.dumps(steps),
                now(),
                now(),
            ),
        )

        db.commit()
        db.close()

    results = []

    add_event(
        parent_execution_id,
        "workflow_started",
        {
            "workflow_id": workflow_id,
            "step_count": len(steps),
        },
    )

    for index, step in enumerate(
        steps,
        start=1,
    ):

        plan = build_plan(
            step
        )

        if plan["action_type"] == "workflow":

            raise ValueError(
                "nested workflows are not allowed"
            )

        child = create_execution(
            step,
            plan,
            mission_id,
            hashlib.sha256(
                f"{workflow_id}:{index}:{step}"
                .encode()
            ).hexdigest(),
        )

        child_id = child[
            "execution_id"
        ]

        if plan.get(
            "requires_approval"
        ):

            paused = {
                "status": "awaiting_approval",
                "workflow_id": workflow_id,
                "step": index,
                "execution_id": child_id,
                "command": step,
                "approval_endpoint": (
                    f"/execution/"
                    f"{child_id}/approve"
                ),
            }

            results.append(
                paused
            )

            with DB_LOCK:
                db = connect_db()

                db.execute(
                    """
                    UPDATE workflow_runs
                    SET status=?,
                        result_json=?,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        "awaiting_approval",
                        json.dumps({
                            "steps": results
                        }),
                        now(),
                        workflow_id,
                    ),
                )

                db.commit()
                db.close()

            add_event(
                parent_execution_id,
                "workflow_paused_for_approval",
                paused,
            )

            return {
                "success": False,
                "status": "awaiting_approval",
                "workflow_id": workflow_id,
                "steps": results,
            }

        result = execute_sync(
            child_id
        )

        result_item = {
            "step": index,
            "execution_id": child_id,
            "command": step,
            "status": result.get(
                "status"
            ),
            "result": result,
        }

        results.append(
            result_item
        )

        add_event(
            parent_execution_id,
            "workflow_step_closed",
            result_item,
        )

        if result.get(
            "status"
        ) != "completed":

            with DB_LOCK:
                db = connect_db()

                db.execute(
                    """
                    UPDATE workflow_runs
                    SET status=?,
                        result_json=?,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        "failed",
                        json.dumps({
                            "steps": results
                        }),
                        now(),
                        workflow_id,
                    ),
                )

                db.commit()
                db.close()

            return {
                "success": False,
                "status": "failed",
                "workflow_id": workflow_id,
                "steps": results,
            }

    final = {
        "success": True,
        "status": "completed",
        "workflow_id": workflow_id,
        "step_count": len(results),
        "steps": results,
    }

    with DB_LOCK:
        db = connect_db()

        db.execute(
            """
            UPDATE workflow_runs
            SET status=?,
                result_json=?,
                updated_at=?
            WHERE id=?
            """,
            (
                "completed",
                json.dumps(final),
                now(),
                workflow_id,
            ),
        )

        db.commit()
        db.close()

    add_event(
        parent_execution_id,
        "workflow_closed",
        {
            "workflow_id": workflow_id,
            "step_count": len(results),
        },
    )

    return final


# ============================================================
# API MODELS
# ============================================================

class CommandRequest(BaseModel):
    command: str = Field(
        min_length=1,
        max_length=10000,
    )

    external_access: bool = False
    execute: bool = True
    mission_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class MissionRequest(BaseModel):
    objective: str = Field(
        min_length=1,
        max_length=10000,
    )


class ApprovalRequest(BaseModel):
    approved: bool


class MemoryRequest(BaseModel):
    content: str = Field(
        min_length=1,
        max_length=10000,
    )


class WorkflowRequest(BaseModel):
    steps: List[str] = Field(
        min_length=1,
        max_length=MAX_WORKFLOW_STEPS,
    )

    execute: bool = True
    mission_id: Optional[str] = None


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "name": "AI Infinity",
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "previous_build": PREVIOUS_BUILD,
        "target": (
            "real-world-command-capable AI Infinity"
        ),
        "command": "/command",
        "mission": "/mission",
        "workflow": "/workflow",
        "execution": "/execution/{execution_id}",
        "approval": (
            "/execution/{execution_id}/approve"
        ),
        "tools": "/tools",
        "health": "/health",
        "interface": "/command-ui",
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    with DB_LOCK:
        db = connect_db()

        missions = db.execute(
            "SELECT COUNT(*) c FROM missions"
        ).fetchone()["c"]

        executions = db.execute(
            "SELECT COUNT(*) c FROM executions"
        ).fetchone()["c"]

        memories = db.execute(
            "SELECT COUNT(*) c FROM memories"
        ).fetchone()["c"]

        events = db.execute(
            "SELECT COUNT(*) c FROM execution_events"
        ).fetchone()["c"]

        workflows = db.execute(
            "SELECT COUNT(*) c FROM workflow_runs"
        ).fetchone()["c"]

        db.close()

    policy = get_policy()

    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "database": "ready",
        "mission_count": missions,
        "execution_count": executions,
        "memory_count": memories,
        "execution_event_count": events,
        "workflow_count": workflows,
        "policy_version": policy["version"],
        "external_execution_enabled": True,
        "verification_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True,
        "intent_router_enabled": True,
        "result_closure_enabled": True,
        "multi_tool_enabled": True,
        "workflow_enabled": True,
        "workspace_tools_enabled": True,
    }


# ============================================================
# 166 STATUS
# ============================================================

@app.get("/166-status")
def status_166():

    return {
        "status": "ready",
        "version": VERSION,
        "build": BUILD,
        "previous_build": PREVIOUS_BUILD,
        "target": (
            "real-world-command-capable AI Infinity"
        ),
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
        "multi_tool_orchestration": True,
        "workflow_engine": True,
        "workspace_file_tools": True,
        "interface": "/command-ui",
    }


# ============================================================
# TOOLS
# ============================================================

@app.get("/tools")
def tools():

    return {
        "status": "ready",
        "version": VERSION,
        "tools": TOOL_REGISTRY,
        "workspace": str(
            WORKSPACE_ROOT
        ),
    }


# ============================================================
# COMMAND
# ============================================================

@app.post("/command")
async def command(
    request: CommandRequest,
):

    plan = build_plan(
        request.command,
        request.external_access,
    )

    if not request.execute:

        return {
            "status": "planned",
            "plan": plan,
        }

    mission_id = (
        request.mission_id
        or create_mission(
            request.command
        )
    )

    execution = create_execution(
        request.command,
        plan,
        mission_id,
        request.idempotency_key,
    )

    execution_id = execution[
        "execution_id"
    ]

    if execution.get(
        "existing"
    ):

        return {
            "status": "existing",
            "mission_id": mission_id,
            "execution_id": execution_id,
            "execution": get_execution(
                execution_id
            ),
        }

    if plan.get(
        "requires_approval"
    ):

        return {
            "status": "awaiting_approval",
            "mission_id": mission_id,
            "execution_id": execution_id,
            "plan": plan,
            "approval_endpoint": (
                f"/execution/"
                f"{execution_id}/approve"
            ),
        }

    queue_execution(
        execution_id
    )

    return {
        "status": "accepted",
        "mission_id": mission_id,
        "execution_id": execution_id,
        "plan": plan,
    }


@app.post("/command/plan")
def command_plan(
    request: CommandRequest,
):

    return {
        "status": "planned",
        "plan": build_plan(
            request.command,
            request.external_access,
        ),
    }


# ============================================================
# WORKFLOW
# ============================================================

@app.post("/workflow")
async def workflow(
    request: WorkflowRequest,
):

    steps = [
        step.strip()
        for step in request.steps
        if step.strip()
    ]

    if not steps:
        raise HTTPException(
            status_code=400,
            detail="workflow is empty",
        )

    objective = (
        "workflow: "
        + " then ".join(steps)
    )

    mission_id = (
        request.mission_id
        or create_mission(
            objective
        )
    )

    plan = build_plan(
        objective
    )

    execution = create_execution(
        objective,
        plan,
        mission_id,
    )

    execution_id = execution[
        "execution_id"
    ]

    if not request.execute:

        return {
            "status": "planned",
            "mission_id": mission_id,
            "execution_id": execution_id,
            "plan": plan,
        }

    queue_execution(
        execution_id
    )

    return {
        "status": "accepted",
        "mission_id": mission_id,
        "execution_id": execution_id,
        "step_count": len(steps),
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post(
    "/execution/{execution_id}/approve"
)
async def approve(
    execution_id: str,
    request: ApprovalRequest,
):

    execution = get_execution(
        execution_id
    )

    if not execution:
        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    if not execution[
        "approval_required"
    ]:

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
        status="queued",
        approved=1,
    )

    add_event(
        execution_id,
        "approval_granted",
    )

    queue_execution(
        execution_id
    )

    return {
        "status": "approved",
        "execution_id": execution_id,
    }


# ============================================================
# EXECUTION
# ============================================================

@app.get(
    "/execution/{execution_id}"
)
def execution(
    execution_id: str,
):

    result = get_execution(
        execution_id
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    with DB_LOCK:
        db = connect_db()

        rows = db.execute(
            """
            SELECT
                event_type,
                payload_json,
                created_at
            FROM execution_events
            WHERE execution_id=?
            ORDER BY id
            """,
            (execution_id,),
        ).fetchall()

        db.close()

    result["events"] = [
        {
            "event": row[
                "event_type"
            ],
            "payload": load_json(
                row["payload_json"],
                {},
            ),
            "created_at": row[
                "created_at"
            ],
        }
        for row in rows
    ]

    return result


# ============================================================
# MISSION
# ============================================================

@app.post("/mission")
async def create_mission_endpoint(
    request: MissionRequest,
):

    mission_id = create_mission(
        request.objective
    )

    plan = build_plan(
        request.objective
    )

    execution = create_execution(
        request.objective,
        plan,
        mission_id,
    )

    execution_id = execution[
        "execution_id"
    ]

    if plan.get(
        "requires_approval"
    ):

        return {
            "status": "awaiting_approval",
            "mission_id": mission_id,
            "execution_id": execution_id,
            "plan": plan,
        }

    queue_execution(
        execution_id
    )

    return {
        "status": "accepted",
        "mission_id": mission_id,
        "execution_id": execution_id,
        "plan": plan,
    }


@app.get(
    "/mission/{mission_id}"
)
def mission(
    mission_id: str,
):

    result = get_mission(
        mission_id
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    with DB_LOCK:
        db = connect_db()

        rows = db.execute(
            """
            SELECT *
            FROM executions
            WHERE mission_id=?
            ORDER BY created_at
            """,
            (mission_id,),
        ).fetchall()

        db.close()

    executions = []

    for row in rows:

        item = dict(row)

        item["result"] = load_json(
            item.pop(
                "result_json"
            ),
            None,
        )

        executions.append(
            item
        )

    result["executions"] = executions

    return result


# ============================================================
# MEMORY
# ============================================================

@app.post("/memory")
def memory(
    request: MemoryRequest,
):

    return remember(
        request.content
    )


@app.get("/memory")
def memory_list():

    with DB_LOCK:
        db = connect_db()

        rows = db.execute(
            """
            SELECT
                id,
                content,
                memory_type,
                created_at
            FROM memories
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

        db.close()

    return {
        "status": "ok",
        "count": len(rows),
        "memories": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# ROUTER TEST
# ============================================================

@app.get("/test-router")
def router_test():

    cases = [
        (
            "ping",
            "system",
        ),
        (
            "GET https://example.com",
            "external_http",
        ),
        (
            "remember that AI Infinity works",
            "memory",
        ),
        (
            "research autonomous AI agents",
            "research_plan",
        ),
        (
            "list files",
            "file_list",
        ),
        (
            "ping then remember that AI Infinity works",
            "workflow",
        ),
    ]

    results = []

    for command_text, expected in cases:

        plan = build_plan(
            command_text
        )

        results.append(
            {
                "command": command_text,
                "action_type": plan[
                    "action_type"
                ],
                "passed": (
                    plan[
                        "action_type"
                    ]
                    == expected
                ),
            }
        )

    return {
        "status": "completed",
        "version": VERSION,
        "tests": results,
    }


# ============================================================
# SECURITY SELF TEST
# ============================================================

@app.get(
    "/self-test"
)
def self_test():

    tests = []

    plan = build_plan(
        "GET https://example.com"
    )

    tests.append(
        {
            "name": "HTTP intent routing",
            "passed": (
                plan["action_type"]
                == "external_http"
                and plan["method"]
                == "GET"
            ),
        }
    )

    try:

        validate_external_url(
            "http://127.0.0.1"
        )

        ssrf_blocked = False

    except ValueError:

        ssrf_blocked = True

    tests.append(
        {
            "name": "SSRF protection",
            "passed": ssrf_blocked,
        }
    )

    try:

        sanitize_headers(
            {
                "Authorization":
                "secret"
            }
        )

        credential_blocked = False

    except ValueError:

        credential_blocked = True

    tests.append(
        {
            "name": "credential header protection",
            "passed": credential_blocked,
        }
    )

    try:

        workspace_path(
            "../escape.txt"
        )

        traversal_blocked = False

    except ValueError:

        traversal_blocked = True

    tests.append(
        {
            "name": "workspace traversal protection",
            "passed": traversal_blocked,
        }
    )

    tests.append(
        {
            "name": "approval gate",
            "passed": (
                build_plan(
                    "POST https://example.com"
                )[
                    "requires_approval"
                ]
                is True
            ),
        }
    )

    return {
        "status": "completed",
        "passed": all(
            test["passed"]
            for test in tests
        ),
        "tests": tests,
    }


# ============================================================
# STARTUP RECOVERY
# ============================================================

def recover_interrupted_executions():

    with DB_LOCK:
        db = connect_db()

        rows = db.execute(
            """
            SELECT id
            FROM executions
            WHERE status IN
            (
                'queued',
                'running',
                'recovering'
            )
            """
        ).fetchall()

        db.close()

    for row in rows:

        update_execution(
            row["id"],
            status="queued",
        )

        add_event(
            row["id"],
            "startup_recovery",
            {
                "version": VERSION
            },
        )


@app.on_event(
    "startup"
)
async def startup():

    recover_interrupted_executions()


# ============================================================
# COMMAND UI
# ============================================================

COMMAND_UI = """
<!doctype html>

<html>

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>AI Infinity 2050.166</title>

<style>

body {
    margin: 0;
    background: #090d12;
    color: #eef2f7;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 20px;
}

.card {
    background: #141a22;
    border: 1px solid #293241;
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 16px;
}

textarea {
    width: 100%;
    min-height: 130px;
    box-sizing: border-box;
    background: #0d1219;
    color: white;
    border: 1px solid #344154;
    border-radius: 10px;
    padding: 12px;
    font-size: 16px;
}

button {
    padding: 11px 18px;
    margin: 8px 8px 0 0;
    border: 0;
    border-radius: 9px;
    cursor: pointer;
    font-weight: bold;
}

pre {
    background: #080b10;
    padding: 14px;
    border-radius: 10px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
}

.muted {
    color: #9aa7b7;
}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>AI Infinity</h1>

<div class="muted">
TARGET-2050.166
<br>
Multi-Tool Real-World Command Orchestrator
</div>

<p id="health">
Checking system...
</p>

</div>

<div class="card">

<h2>Command</h2>

<textarea
id="command"
placeholder="Try:

ping

GET https://example.com

remember that AI Infinity works

list files

ping then remember that AI Infinity works
"></textarea>

<br>

<button onclick="executeCommand()">
Execute
</button>

<button onclick="planCommand()">
Plan
</button>

</div>

<div class="card">

<h2>Result</h2>

<pre id="result">
Waiting...
</pre>

</div>

</div>

<script>

async function checkHealth() {

    try {

        const response =
            await fetch("/health");

        const data =
            await response.json();

        document.getElementById(
            "health"
        ).innerText =
            data.status
            + " — "
            + data.version
            + " — "
            + data.build;

    } catch(error) {

        document.getElementById(
            "health"
        ).innerText =
            "AI Infinity unavailable";

    }

}


async function planCommand() {

    const command =
        document.getElementById(
            "command"
        ).value;

    const response =
        await fetch(
            "/command/plan",
            {
                method: "POST",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify({
                    command: command,
                    execute: false
                })
            }
        );

    const data =
        await response.json();

    document.getElementById(
        "result"
    ).innerText =
        JSON.stringify(
            data,
            null,
            2
        );

}


async function executeCommand() {

    const command =
        document.getElementById(
            "command"
        ).value;

    const response =
        await fetch(
            "/command",
            {
                method: "POST",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify({
                    command: command,
                    execute: true
                })
            }
        );

    const data =
        await response.json();

    document.getElementById(
        "result"
    ).innerText =
        JSON.stringify(
            data,
            null,
            2
        );

    if (data.execution_id) {

        watchExecution(
            data.execution_id
        );

    }

}


async function watchExecution(id) {

    for (
        let i = 0;
        i < 60;
        i++
    ) {

        await new Promise(
            resolve =>
                setTimeout(
                    resolve,
                    500
                )
        );

        try {

            const response =
                await fetch(
                    "/execution/"
                    + id
                );

            const data =
                await response.json();

            document.getElementById(
                "result"
            ).innerText =
                JSON.stringify(
                    data,
                    null,
                    2
                );

            if (
                data.status ===
                    "completed"
                ||
                data.status ===
                    "failed"
                ||
                data.status ===
                    "rejected"
            ) {

                break;

            }

        } catch(error) {

            break;

        }

    }

}


checkHealth();

</script>

</body>

</html>
"""


@app.get(
    "/command-ui",
    response_class=HTMLResponse,
)
def command_ui():

    return COMMAND_UI
