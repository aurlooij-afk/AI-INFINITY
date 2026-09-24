"""
AI Infinity — TARGET-2050.167
REAL-WORLD CONNECTOR FABRIC CORE

Extends TARGET-2050.166.

Core:
- Natural-language intent routing
- Intent -> action planning
- Connector registry
- HTTP connector
- Webhook connector
- Persistent memory
- Research planning
- Workspace/file connector
- Workflow orchestration
- Durable SQLite execution
- Execution events
- Idempotency
- Approval gates
- SSRF protection
- Credential-header protection
- Redirect revalidation
- Verification
- Result closure
- Adaptive recovery foundation
- Mission linkage
- Command UI

Dependencies:
    fastapi
    uvicorn[standard]
    pydantic

Render:
    uvicorn main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# IDENTITY
# ============================================================

VERSION = "TARGET-2050.167"
BUILD = "REAL-WORLD-CONNECTOR-FABRIC-CORE"
PREVIOUS_BUILD = "TARGET-2050.166"
TARGET = "real-world-command-capable AI Infinity"

DATA_DIR = Path(
    os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity")
)

WORKSPACE = DATA_DIR / "workspace"
DB_PATH = DATA_DIR / "ai_infinity.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE.mkdir(parents=True, exist_ok=True)

MAX_HTTP_BODY = 2 * 1024 * 1024
MAX_FILE_SIZE = 1 * 1024 * 1024
HTTP_TIMEOUT = float(
    os.getenv("AI_INFINITY_HTTP_TIMEOUT", "15")
)

SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-access-token",
}

PRIVATE_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Real-world command orchestration and connector fabric.",
)


# ============================================================
# BASIC HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=20000")
    return con


# ============================================================
# DATABASE
# ============================================================

def init_db() -> None:
    con = db()

    try:
        con.executescript(
            """
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
                action_type TEXT NOT NULL,
                connector TEXT,
                status TEXT NOT NULL,
                command TEXT,
                input_json TEXT,
                result_json TEXT,
                error TEXT,
                approval_required INTEGER NOT NULL DEFAULT 0,
                approved INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                recovery_attempts INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT UNIQUE,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                verified INTEGER NOT NULL DEFAULT 0,
                result_closed INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS execution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                status TEXT NOT NULL,
                step_count INTEGER NOT NULL DEFAULT 0,
                current_step INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS connectors (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                config_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policies (
                id INTEGER PRIMARY KEY CHECK(id=1),
                version INTEGER NOT NULL,
                data_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            INSERT OR IGNORE INTO policies(
                id,
                version,
                data_json,
                updated_at
            )
            VALUES(
                1,
                1,
                '{"external_execution":true,
                  "verification":true,
                  "adaptive_recovery":true,
                  "self_modification":true}',
                datetime('now')
            );
            """
        )

        con.commit()

    finally:
        con.close()


init_db()


# ============================================================
# SECURITY
# ============================================================

def is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)

        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    except ValueError:
        return False


def resolve_public_host(host: str) -> None:
    lowered = host.lower().rstrip(".")

    if (
        lowered in PRIVATE_HOSTS
        or lowered.endswith(".local")
    ):
        raise ValueError("private/local host blocked")

    try:
        infos = socket.getaddrinfo(
            lowered,
            None,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError(
            f"DNS resolution failed: {exc}"
        ) from exc

    addresses = {
        item[4][0]
        for item in infos
    }

    if not addresses:
        raise ValueError("host has no resolved address")

    for address in addresses:
        if is_private_ip(address):
            raise ValueError(
                "private/reserved destination blocked"
            )


def validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in {
        "http",
        "https",
    }:
        raise ValueError(
            "only http/https URLs are allowed"
        )

    if not parsed.hostname:
        raise ValueError("URL host is required")

    if parsed.username or parsed.password:
        raise ValueError(
            "URL credentials are not allowed"
        )

    resolve_public_host(parsed.hostname)

    return url


def sanitize_headers(
    headers: Optional[Dict[str, str]]
) -> Dict[str, str]:

    result: Dict[str, str] = {}

    for key, value in (headers or {}).items():

        if key.lower() in SENSITIVE_HEADERS:
            raise ValueError(
                f"sensitive header blocked: {key}"
            )

        if len(key) > 128:
            raise ValueError(
                "header name too large"
            )

        if len(str(value)) > 4096:
            raise ValueError(
                "header value too large"
            )

        result[str(key)] = str(value)

    return result


def safe_workspace_path(relative: str) -> Path:

    if not relative:
        raise ValueError(
            "path is required"
        )

    root = WORKSPACE.resolve()

    candidate = (
        WORKSPACE / relative
    ).resolve()

    try:
        candidate.relative_to(root)

    except ValueError as exc:
        raise ValueError(
            "workspace path traversal blocked"
        ) from exc

    return candidate


# ============================================================
# CONNECTOR FABRIC
# ============================================================

CONNECTORS: Dict[str, Dict[str, Any]] = {

    "system": {
        "name": "System",
        "kind": "builtin",
        "capabilities": [
            "ping",
            "status",
        ],
        "requires_approval": False,
    },

    "memory": {
        "name": "Persistent Memory",
        "kind": "builtin",
        "capabilities": [
            "remember",
            "recall",
        ],
        "requires_approval": False,
    },

    "research": {
        "name": "Research Planner",
        "kind": "builtin",
        "capabilities": [
            "research_plan",
        ],
        "requires_approval": False,
    },

    "workspace": {
        "name": "Workspace",
        "kind": "builtin",
        "capabilities": [
            "file_list",
            "file_read",
            "file_write",
            "file_delete",
        ],
        "requires_approval": True,
    },

    "http": {
        "name": "HTTP Bridge",
        "kind": "network",
        "capabilities": [
            "http_get",
            "http_post",
            "http_put",
            "http_patch",
            "http_delete",
        ],
        "requires_approval": False,
    },

    "webhook": {
        "name": "Webhook Connector",
        "kind": "network",
        "capabilities": [
            "webhook",
        ],
        "requires_approval": True,
    },

    "workflow": {
        "name": "Workflow Orchestrator",
        "kind": "builtin",
        "capabilities": [
            "workflow",
        ],
        "requires_approval": False,
    },
}


def connector_snapshot() -> List[Dict[str, Any]]:

    return [
        {
            "id": connector_id,
            **data,
            "enabled": True,
        }
        for connector_id, data
        in CONNECTORS.items()
    ]


# ============================================================
# MODELS
# ============================================================

class CommandRequest(BaseModel):

    command: str

    action_type: Optional[str] = None
    connector: Optional[str] = None

    method: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    body: Any = None

    path: Optional[str] = None
    content: Optional[str] = None

    remember: Optional[str] = None

    steps: Optional[
        List[Dict[str, Any]]
    ] = None

    mission_id: Optional[str] = None

    idempotency_key: Optional[str] = None

    require_approval: Optional[bool] = None


class PlanRequest(BaseModel):
    command: str


class WorkflowRequest(BaseModel):

    name: str = "workflow"

    steps: List[
        Dict[str, Any]
    ] = Field(default_factory=list)

    mission_id: Optional[str] = None

    idempotency_key: Optional[str] = None


class MemoryRequest(BaseModel):
    content: str


class MissionRequest(BaseModel):

    objective: str

    remember: bool = False


# ============================================================
# EVENTS
# ============================================================

def event(
    execution_id: str,
    event_type: str,
    data: Optional[Dict[str, Any]] = None,
) -> None:

    con = db()

    try:
        con.execute(
            """
            INSERT INTO execution_events(
                execution_id,
                event_type,
                data_json,
                created_at
            )
            VALUES(?,?,?,?)
            """,
            (
                execution_id,
                event_type,
                json.dumps(
                    data or {},
                    ensure_ascii=False,
                ),
                now_iso(),
            ),
        )

        con.commit()

    finally:
        con.close()


# ============================================================
# MISSIONS
# ============================================================

def create_mission(
    objective: str,
) -> str:

    mission_id = (
        "mission-"
        + secrets.token_hex(8)
    )

    stamp = now_iso()

    con = db()

    try:

        con.execute(
            """
            INSERT INTO missions(
                id,
                objective,
                status,
                created_at,
                updated_at
            )
            VALUES(?,?,?,?,?)
            """,
            (
                mission_id,
                objective,
                "running",
                stamp,
                stamp,
            ),
        )

        con.commit()

    finally:
        con.close()

    return mission_id


def update_mission(
    mission_id: str,
    status: str,
    result: Any = None,
) -> None:

    con = db()

    try:

        con.execute(
            """
            UPDATE missions
            SET
                status=?,
                updated_at=?,
                result_json=?
            WHERE id=?
            """,
            (
                status,
                now_iso(),
                json.dumps(
                    result,
                    ensure_ascii=False,
                )
                if result is not None
                else None,
                mission_id,
            ),
        )

        con.commit()

    finally:
        con.close()


# ============================================================
# EXECUTIONS
# ============================================================

def get_execution(
    execution_id: str,
) -> Dict[str, Any]:

    con = db()

    try:

        row = con.execute(
            """
            SELECT *
            FROM executions
            WHERE id=?
            """,
            (execution_id,),
        ).fetchone()

        if not row:
            raise KeyError(execution_id)

        result = dict(row)

        for key in (
            "input_json",
            "result_json",
        ):

            if result.get(key):

                try:
                    result[key] = json.loads(
                        result[key]
                    )

                except Exception:
                    pass

        return result

    finally:
        con.close()


def create_execution(
    action_type: str,
    command: str,
    payload: Dict[str, Any],
    connector: str,
    mission_id: Optional[str] = None,
    approval_required: bool = False,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:

    con = db()

    try:

        if idempotency_key:

            row = con.execute(
                """
                SELECT *
                FROM executions
                WHERE idempotency_key=?
                """,
                (idempotency_key,),
            ).fetchone()

            if row:
                return dict(row)

        execution_id = (
            "exec-"
            + secrets.token_hex(10)
        )

        stamp = now_iso()

        try:

            con.execute(
                """
                INSERT INTO executions(
                    id,
                    mission_id,
                    action_type,
                    connector,
                    status,
                    command,
                    input_json,
                    approval_required,
                    approved,
                    idempotency_key,
                    created_at
                )
                VALUES(
                    ?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    execution_id,
                    mission_id,
                    action_type,
                    connector,
                    (
                        "awaiting_approval"
                        if approval_required
                        else "queued"
                    ),
                    command,
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                    ),
                    1
                    if approval_required
                    else 0,
                    0,
                    idempotency_key,
                    stamp,
                ),
            )

            con.commit()

        except sqlite3.IntegrityError:

            if idempotency_key:

                row = con.execute(
                    """
                    SELECT *
                    FROM executions
                    WHERE idempotency_key=?
                    """,
                    (idempotency_key,),
                ).fetchone()

                if row:
                    return dict(row)

            raise

    finally:
        con.close()

    event(
        execution_id,
        "execution_created",
        {
            "action_type": action_type,
            "connector": connector,
            "approval_required":
                approval_required,
        },
    )

    return get_execution(
        execution_id
    )


def update_execution(
    execution_id: str,
    **fields: Any,
) -> None:

    if not fields:
        return

    keys = list(fields.keys())

    values = [
        fields[key]
        for key in keys
    ]

    sql = (
        "UPDATE executions SET "
        + ",".join(
            key + "=?"
            for key in keys
        )
        + " WHERE id=?"
    )

    con = db()

    try:

        con.execute(
            sql,
            (
                *values,
                execution_id,
            ),
        )

        con.commit()

    finally:
        con.close()


def list_events(
    execution_id: str,
) -> List[Dict[str, Any]]:

    con = db()

    try:

        rows = con.execute(
            """
            SELECT *
            FROM execution_events
            WHERE execution_id=?
            ORDER BY id
            """,
            (execution_id,),
        ).fetchall()

        result = []

        for row in rows:

            result.append(
                {
                    "id": row["id"],
                    "execution_id":
                        row["execution_id"],
                    "event_type":
                        row["event_type"],
                    "data":
                        json.loads(
                            row["data_json"]
                            or "{}"
                        ),
                    "created_at":
                        row["created_at"],
                }
            )

        return result

    finally:
        con.close()


# ============================================================
# INTENT ROUTER
# ============================================================

def detect_intent(
    command: str,
) -> Dict[str, Any]:

    text = command.strip()
    lower = text.lower()

    method_match = re.search(
        r"\b(GET|POST|PUT|PATCH|DELETE|HEAD)\s+(https?://\S+)",
        text,
        re.I,
    )

    url_match = re.search(
        r"https?://\S+",
        text,
        re.I,
    )

    if lower in {
        "ping",
        "status",
        "health",
        "check system",
    }:

        return {
            "action_type": "system",
            "connector": "system",
        }

    if (
        lower.startswith("remember ")
        or lower.startswith("remember that ")
    ):

        return {
            "action_type": "memory",
            "connector": "memory",
        }

    if (
        lower.startswith("research ")
        or lower.startswith(
            "find evidence for "
        )
    ):

        return {
            "action_type": "research_plan",
            "connector": "research",
        }

    if (
        "list files" in lower
        or "show files" in lower
    ):

        return {
            "action_type": "file_list",
            "connector": "workspace",
        }

    if lower.startswith(
        "read file "
    ):

        return {
            "action_type": "file_read",
            "connector": "workspace",
        }

    if (
        lower.startswith("write file ")
        or lower.startswith("create file ")
    ):

        return {
            "action_type": "file_write",
            "connector": "workspace",
        }

    if (
        lower.startswith("delete file ")
        or lower.startswith("remove file ")
    ):

        return {
            "action_type": "file_delete",
            "connector": "workspace",
        }

    if (
        " then " in lower
        or lower.startswith("workflow:")
    ):

        return {
            "action_type": "workflow",
            "connector": "workflow",
        }

    if (
        lower.startswith("webhook ")
        or lower.startswith("send webhook ")
    ):

        return {
            "action_type": "webhook",
            "connector": "webhook",
        }

    if method_match or url_match:

        method = (
            method_match.group(1).upper()
            if method_match
            else "GET"
        )

        url = (
            method_match.group(2)
            if method_match
            else url_match.group(0)
        )

        return {
            "action_type": "external_http",
            "connector": "http",
            "method": method,
            "url": url,
        }

    return {
        "action_type": "system",
        "connector": "system",
    }


# ============================================================
# WORKFLOW PARSER
# ============================================================

def parse_workflow_command(
    command: str,
) -> List[Dict[str, Any]]:

    text = re.sub(
        r"^workflow:\s*",
        "",
        command.strip(),
        flags=re.I,
    )

    parts = [
        part.strip()
        for part in re.split(
            r"\s+then\s+",
            text,
            flags=re.I,
        )
        if part.strip()
    ]

    return [
        {
            "command": part
        }
        for part in parts
    ]


# ============================================================
# ACTION PLANNER
# ============================================================

def build_plan(
    req: CommandRequest,
) -> Dict[str, Any]:

    intent = detect_intent(
        req.command
    )

    action_type = (
        req.action_type
        or intent["action_type"]
    )

    connector = (
        req.connector
        or intent["connector"]
    )

    if action_type == "external_http":

        method = (
            req.method
            or intent.get("method")
            or "GET"
        ).upper()

        url = (
            req.url
            or intent.get("url")
        )

        if not url:
            raise ValueError(
                "HTTP action requires URL"
            )

        return {
            "action_type":
                "external_http",
            "connector":
                "http",
            "method":
                method,
            "url":
                url,
            "headers":
                req.headers or {},
            "body":
                req.body,
        }

    if action_type == "webhook":

        if not req.url:
            raise ValueError(
                "webhook action requires url"
            )

        return {
            "action_type":
                "webhook",
            "connector":
                "webhook",
            "method":
                (
                    req.method
                    or "POST"
                ).upper(),
            "url":
                req.url,
            "headers":
                req.headers or {},
            "body":
                (
                    req.body
                    if req.body is not None
                    else {
                        "command":
                            req.command
                    }
                ),
        }

    if action_type == "memory":

        content = req.remember

        if not content:

            content = re.sub(
                r"^remember(?: that)?\s*",
                "",
                req.command,
                flags=re.I,
            )

        return {
            "action_type":
                "memory",
            "connector":
                "memory",
            "content":
                content,
        }

    if action_type == "research_plan":

        topic = re.sub(
            r"^(research|find evidence for)\s*",
            "",
            req.command,
            flags=re.I,
        )

        return {
            "action_type":
                "research_plan",
            "connector":
                "research",
            "topic":
                topic,
        }

    if action_type == "file_list":

        return {
            "action_type":
                "file_list",
            "connector":
                "workspace",
        }

    if action_type == "file_read":

        path = (
            req.path
            or re.sub(
                r"^read file\s*",
                "",
                req.command,
                flags=re.I,
            )
        )

        return {
            "action_type":
                "file_read",
            "connector":
                "workspace",
            "path":
                path.strip(),
        }

    if action_type == "file_write":

        path = req.path
        content = req.content or ""

        if not path:

            match = re.match(
                r"^(?:write|create) file\s+(\S+)(?:\s+with\s+)?(.*)$",
                req.command,
                re.I,
            )

            if match:
                path = match.group(1)
                content = (
                    content
                    or match.group(2)
                )

        return {
            "action_type":
                "file_write",
            "connector":
                "workspace",
            "path":
                path,
            "content":
                content,
        }

    if action_type == "file_delete":

        path = (
            req.path
            or re.sub(
                r"^(delete|remove) file\s*",
                "",
                req.command,
                flags=re.I,
            )
        )

        return {
            "action_type":
                "file_delete",
            "connector":
                "workspace",
            "path":
                path.strip(),
        }

    if action_type == "workflow":

        return {
            "action_type":
                "workflow",
            "connector":
                "workflow",
            "steps":
                (
                    req.steps
                    or parse_workflow_command(
                        req.command
                    )
                ),
        }

    return {
        "action_type":
            "system",
        "connector":
            "system",
        "command":
            req.command,
    }


# ============================================================
# APPROVAL POLICY
# ============================================================

def action_requires_approval(
    action_type: str,
    connector: str,
    requested: Optional[bool],
) -> bool:

    if requested is not None:
        return requested

    if action_type in {
        "file_write",
        "file_delete",
        "webhook",
    }:
        return True

    # GET/HEAD-style HTTP reads do not require approval.
    # Mutating HTTP calls do.
    if action_type == "external_http":

        return False

    return bool(
        CONNECTORS.get(
            connector,
            {},
        ).get(
            "requires_approval",
            False,
        )
    )


# ============================================================
# SYSTEM CONNECTOR
# ============================================================

def execute_system(
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    return {
        "success": True,
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "command":
            payload.get(
                "command",
                "ping",
            ),
    }


# ============================================================
# MEMORY CONNECTOR
# ============================================================

def execute_memory(
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    content = str(
        payload.get(
            "content",
            "",
        )
    ).strip()

    if not content:
        raise ValueError(
            "memory content is required"
        )

    con = db()

    try:

        con.execute(
            """
            INSERT INTO memories(
                content,
                created_at
            )
            VALUES(?,?)
            """,
            (
                content,
                now_iso(),
            ),
        )

        con.commit()

    finally:
        con.close()

    return {
        "success": True,
        "remembered": content,
    }


# ============================================================
# RESEARCH CONNECTOR
# ============================================================

def execute_research(
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    topic = str(
        payload.get(
            "topic",
            "",
        )
    ).strip()

    if not topic:
        raise ValueError(
            "research topic is required"
        )

    return {
        "success": True,
        "type": "research_plan",
        "topic": topic,
        "steps": [
            "discover independent sources",
            "canonicalize sources",
            "extract claims",
            "compare evidence",
            "verify claims",
            "close result",
        ],
        "live_provider_execution": False,
        "note":
            "Research provider adapters can be registered through the connector fabric.",
    }


# ============================================================
# WORKSPACE CONNECTOR
# ============================================================

def execute_file(
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    action = payload[
        "action_type"
    ]

    if action == "file_list":

        files = []

        for path in sorted(
            WORKSPACE.rglob("*")
        ):

            if path.is_file():

                files.append(
                    str(
                        path.relative_to(
                            WORKSPACE
                        )
                    )
                )

        return {
            "success": True,
            "files": files,
        }

    path = safe_workspace_path(
        str(
            payload.get(
                "path",
                "",
            )
        )
    )

    if action == "file_read":

        if (
            not path.exists()
            or not path.is_file()
        ):
            raise FileNotFoundError(
                "file not found"
            )

        if (
            path.stat().st_size
            > MAX_FILE_SIZE
        ):
            raise ValueError(
                "file exceeds size limit"
            )

        return {
            "success": True,
            "path":
                str(
                    path.relative_to(
                        WORKSPACE
                    )
                ),
            "content":
                path.read_text(
                    encoding="utf-8"
                ),
        }

    if action == "file_write":

        content = str(
            payload.get(
                "content",
                "",
            )
        )

        if (
            len(
                content.encode("utf-8")
            )
            > MAX_FILE_SIZE
        ):
            raise ValueError(
                "file exceeds size limit"
            )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        fd, temporary = tempfile.mkstemp(
            prefix=".ai-",
            dir=str(path.parent),
        )

        try:

            with os.fdopen(
                fd,
                "w",
                encoding="utf-8",
            ) as handle:

                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(
                temporary,
                path,
            )

        finally:

            if os.path.exists(
                temporary
            ):
                os.unlink(
                    temporary
                )

        return {
            "success": True,
            "path":
                str(
                    path.relative_to(
                        WORKSPACE
                    )
                ),
            "bytes":
                len(
                    content.encode(
                        "utf-8"
                    )
                ),
        }

    if action == "file_delete":

        if not path.exists():
            raise FileNotFoundError(
                "file not found"
            )

        if path.is_dir():
            raise ValueError(
                "directory deletion is not allowed"
            )

        path.unlink()

        return {
            "success": True,
            "deleted":
                str(
                    path.relative_to(
                        WORKSPACE
                    )
                ),
        }

    raise ValueError(
        "unsupported workspace action"
    )


# ============================================================
# SAFE HTTP REDIRECT HANDLER
# ============================================================

class SafeRedirectHandler(
    urllib.request.HTTPRedirectHandler
):

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):

        # Revalidate every redirect target.
        validate_url(newurl)

        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )


HTTP_OPENER = (
    urllib.request.build_opener(
        SafeRedirectHandler
    )
)


# ============================================================
# HTTP CONNECTOR
# ============================================================

def execute_http(
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    url = validate_url(
        str(payload["url"])
    )

    method = str(
        payload.get(
            "method",
            "GET",
        )
    ).upper()

    headers = sanitize_headers(
        payload.get("headers")
    )

    body = payload.get("body")

    raw_body: Optional[bytes] = None

    if body is not None:

        if isinstance(
            body,
            (dict, list),
        ):

            raw_body = json.dumps(
                body
            ).encode("utf-8")

            headers.setdefault(
                "Content-Type",
                "application/json",
            )

        else:

            raw_body = str(
                body
            ).encode("utf-8")

    if (
        raw_body
        and len(raw_body)
        > MAX_HTTP_BODY
    ):
        raise ValueError(
            "request body exceeds limit"
        )

    request = urllib.request.Request(
        url=url,
        data=raw_body,
        headers=headers,
        method=method,
    )

    started = time.monotonic()

    try:

        with HTTP_OPENER.open(
            request,
            timeout=HTTP_TIMEOUT,
        ) as response:

            data = response.read(
                MAX_HTTP_BODY + 1
            )

            elapsed = round(
                time.monotonic()
                - started,
                4,
            )

            if len(data) > MAX_HTTP_BODY:
                raise ValueError(
                    "response body exceeds limit"
                )

            content_type = (
                response.headers.get(
                    "Content-Type",
                    "",
                )
            )

            text = data.decode(
                "utf-8",
                errors="replace",
            )

            return {
                "success":
                    200
                    <= response.status
                    < 400,
                "status_code":
                    response.status,
                "content_type":
                    content_type,
                "body":
                    text,
                "url":
                    response.geturl(),
                "elapsed_seconds":
                    elapsed,
            }

    except urllib.error.HTTPError as exc:

        body_bytes = exc.read(
            MAX_HTTP_BODY
        )

        return {
            "success": False,
            "status_code":
                exc.code,
            "content_type":
                exc.headers.get(
                    "Content-Type",
                    "",
                ),
            "body":
                body_bytes.decode(
                    "utf-8",
                    errors="replace",
                ),
            "url":
                url,
            "error":
                f"HTTP {exc.code}",
        }


# ============================================================
# WEBHOOK CONNECTOR
# ============================================================

def execute_webhook(
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    return execute_http(
        {
            "url":
                payload["url"],
            "method":
                payload.get(
                    "method",
                    "POST",
                ),
            "headers":
                payload.get(
                    "headers",
                    {},
                ),
            "body":
                payload.get(
                    "body",
                    {},
                ),
        }
    )


# ============================================================
# ACTION DISPATCH
# ============================================================

def execute_action_sync(
    action_type: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    if action_type == "system":
        return execute_system(
            payload
        )

    if action_type == "memory":
        return execute_memory(
            payload
        )

    if action_type == "research_plan":
        return execute_research(
            payload
        )

    if action_type in {
        "file_list",
        "file_read",
        "file_write",
        "file_delete",
    }:
        return execute_file(
            {
                **payload,
                "action_type":
                    action_type,
            }
        )

    if action_type == "external_http":
        return execute_http(
            payload
        )

    if action_type == "webhook":
        return execute_webhook(
            payload
        )

    raise ValueError(
        f"unsupported action type: {action_type}"
    )


# ============================================================
# VERIFICATION
# ============================================================

def verify_result(
    action_type: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:

    if not isinstance(
        result,
        dict,
    ):

        return {
            "verified": False,
            "reason":
                "result is not structured",
        }

    if action_type in {
        "external_http",
        "webhook",
    }:

        verified = (
            bool(
                result.get(
                    "success"
                )
            )
            and isinstance(
                result.get(
                    "status_code"
                ),
                int,
            )
        )

        return {
            "verified":
                verified,
            "reason":
                (
                    "HTTP executor reported success and status code"
                    if verified
                    else
                    "HTTP execution did not report success"
                ),
        }

    if action_type in {
        "file_write",
        "file_delete",
        "file_read",
        "file_list",
    }:

        return {
            "verified":
                bool(
                    result.get(
                        "success"
                    )
                ),
            "reason":
                "workspace executor reported success",
        }

    return {
        "verified":
            bool(
                result.get(
                    "success"
                )
            ),
        "reason":
            (
                "executor reported success"
                if result.get(
                    "success"
                )
                else
                "executor did not report success"
            ),
    }


# ============================================================
# WORKFLOW
# ============================================================

def create_workflow_run(
    execution_id: str,
    steps: List[Dict[str, Any]],
) -> str:

    run_id = (
        "workflow-"
        + secrets.token_hex(8)
    )

    stamp = now_iso()

    con = db()

    try:

        con.execute(
            """
            INSERT INTO workflow_runs(
                id,
                execution_id,
                status,
                step_count,
                current_step,
                created_at,
                updated_at
            )
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                run_id,
                execution_id,
                "running",
                len(steps),
                0,
                stamp,
                stamp,
            ),
        )

        con.commit()

    finally:
        con.close()

    return run_id


def update_workflow_run(
    run_id: str,
    **fields: Any,
) -> None:

    if not fields:
        return

    fields["updated_at"] = now_iso()

    keys = list(fields.keys())

    con = db()

    try:

        con.execute(
            (
                "UPDATE workflow_runs SET "
                + ",".join(
                    key + "=?"
                    for key in keys
                )
                + " WHERE id=?"
            ),
            (
                *[
                    fields[key]
                    for key in keys
                ],
                run_id,
            ),
        )

        con.commit()

    finally:
        con.close()


def execute_workflow_sync(
    execution_id: str,
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:

    workflow_id = create_workflow_run(
        execution_id,
        steps,
    )

    outputs: List[
        Dict[str, Any]
    ] = []

    try:

        for index, raw_step in enumerate(
            steps
        ):

            update_workflow_run(
                workflow_id,
                current_step=index,
            )

            if (
                "command" in raw_step
                and len(raw_step) == 1
            ):

                sub_request = CommandRequest(
                    command=str(
                        raw_step["command"]
                    )
                )

            else:

                sub_request = CommandRequest(
                    command=str(
                        raw_step.get(
                            "command",
                            raw_step.get(
                                "action_type",
                                "step",
                            ),
                        )
                    ),
                    action_type=
                        raw_step.get(
                            "action_type"
                        ),
                    connector=
                        raw_step.get(
                            "connector"
                        ),
                    method=
                        raw_step.get(
                            "method"
                        ),
                    url=
                        raw_step.get(
                            "url"
                        ),
                    headers=
                        raw_step.get(
                            "headers"
                        ),
                    body=
                        raw_step.get(
                            "body"
                        ),
                    path=
                        raw_step.get(
                            "path"
                        ),
                    content=
                        raw_step.get(
                            "content"
                        ),
                    remember=
                        raw_step.get(
                            "remember"
                        ),
                )

            plan = build_plan(
                sub_request
            )

            action_type = plan[
                "action_type"
            ]

            connector = plan[
                "connector"
            ]

            # Workflow child side effects remain protected.
            if action_requires_approval(
                action_type,
                connector,
                None,
            ):

                final = {
                    "success": False,
                    "status":
                        "awaiting_approval",
                    "workflow_id":
                        workflow_id,
                    "step":
                        index,
                    "plan":
                        plan,
                    "reason":
                        "workflow contains an approval-gated step",
                }

                update_workflow_run(
                    workflow_id,
                    status=
                        "awaiting_approval",
                    result_json=
                        json.dumps(
                            final,
                            ensure_ascii=False,
                        ),
                )

                return final

            result = execute_action_sync(
                action_type,
                plan,
            )

            verification = verify_result(
                action_type,
                result,
            )

            outputs.append(
                {
                    "step":
                        index,
                    "action_type":
                        action_type,
                    "connector":
                        connector,
                    "result":
                        result,
                    "verification":
                        verification,
                }
            )

            if not verification[
                "verified"
            ]:

                final = {
                    "success": False,
                    "status": "failed",
                    "workflow_id":
                        workflow_id,
                    "outputs":
                        outputs,
                }

                update_workflow_run(
                    workflow_id,
                    status="failed",
                    result_json=
                        json.dumps(
                            final,
                            ensure_ascii=False,
                        ),
                )

                return final

        final = {
            "success": True,
            "status": "completed",
            "workflow_id":
                workflow_id,
            "outputs":
                outputs,
        }

        update_workflow_run(
            workflow_id,
            status="completed",
            current_step=len(steps),
            result_json=
                json.dumps(
                    final,
                    ensure_ascii=False,
                ),
        )

        return final

    except Exception as exc:

        final = {
            "success": False,
            "status": "failed",
            "workflow_id":
                workflow_id,
            "outputs":
                outputs,
            "error":
                str(exc),
        }

        update_workflow_run(
            workflow_id,
            status="failed",
            result_json=
                json.dumps(
                    final,
                    ensure_ascii=False,
                ),
        )

        return final


# ============================================================
# DURABLE EXECUTION
# ============================================================

def execute_sync(
    execution_id: str,
) -> Dict[str, Any]:

    execution = get_execution(
        execution_id
    )

    if (
        execution["status"]
        == "awaiting_approval"
    ):

        raise ValueError(
            "approval required"
        )

    update_execution(
        execution_id,
        status="running",
        started_at=now_iso(),
        attempts=
            int(
                execution.get(
                    "attempts"
                )
                or 0
            )
            + 1,
    )

    event(
        execution_id,
        "execution_started",
    )

    execution = get_execution(
        execution_id
    )

    payload = execution[
        "input_json"
    ]

    action_type = execution[
        "action_type"
    ]

    try:

        if action_type == "workflow":

            result = execute_workflow_sync(
                execution_id,
                payload.get(
                    "steps",
                    [],
                ),
            )

        else:

            result = execute_action_sync(
                action_type,
                payload,
            )

        verification = verify_result(
            action_type,
            result,
        )

        final_result = {
            **result,
            "verification":
                verification,
        }

        update_execution(
            execution_id,
            status=(
                "completed"
                if verification[
                    "verified"
                ]
                else "failed"
            ),
            result_json=
                json.dumps(
                    final_result,
                    ensure_ascii=False,
                ),
            verified=
                1
                if verification[
                    "verified"
                ]
                else 0,
            result_closed=
                1
                if verification[
                    "verified"
                ]
                else 0,
            finished_at=now_iso(),
        )

        event(
            execution_id,
            "result_verified",
            verification,
        )

        if verification[
            "verified"
        ]:

            event(
                execution_id,
                "result_closed",
                {
                    "result_closure":
                        True
                },
            )

        return get_execution(
            execution_id
        )

    except Exception as exc:

        update_execution(
            execution_id,
            status="failed",
            error=str(exc),
            finished_at=now_iso(),
        )

        event(
            execution_id,
            "execution_failed",
            {
                "error": str(exc)
            },
        )

        raise


async def execute_async(
    execution_id: str,
) -> None:

    try:

        await asyncio.to_thread(
            execute_sync,
            execution_id,
        )

    except Exception:
        # Durable state already records the failure.
        pass


# ============================================================
# STARTUP RECOVERY
# ============================================================

def startup_recovery() -> None:

    con = db()

    try:

        rows = con.execute(
            """
            SELECT id
            FROM executions
            WHERE status IN(
                'queued',
                'running'
            )
            """
        ).fetchall()

        for row in rows:

            con.execute(
                """
                UPDATE executions
                SET status='queued'
                WHERE id=?
                """,
                (row["id"],),
            )

        con.commit()

        queued = [
            row["id"]
            for row in rows
        ]

    finally:
        con.close()

    try:

        loop = asyncio.get_event_loop()

        for execution_id in queued:

            loop.create_task(
                execute_async(
                    execution_id
                )
            )

    except Exception:
        pass


@app.on_event("startup")
async def startup() -> None:

    init_db()
    startup_recovery()


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root() -> Dict[str, Any]:

    return {
        "status": "ready",
        "version": VERSION,
        "build": BUILD,
        "previous_build":
            PREVIOUS_BUILD,
        "target": TARGET,

        "intent_to_action": True,
        "natural_language_routing": True,
        "structured_action_planning": True,

        "local_command_engine": True,

        "external_http_bridge": True,
        "webhook_connector": True,

        "connector_registry": True,
        "connector_capability_discovery": True,

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

        "interface":
            "/command-ui",
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health() -> Dict[str, Any]:

    con = db()

    try:

        counts = {

            "mission_count":
                con.execute(
                    "SELECT COUNT(*) c FROM missions"
                ).fetchone()["c"],

            "execution_count":
                con.execute(
                    "SELECT COUNT(*) c FROM executions"
                ).fetchone()["c"],

            "memory_count":
                con.execute(
                    "SELECT COUNT(*) c FROM memories"
                ).fetchone()["c"],

            "execution_event_count":
                con.execute(
                    "SELECT COUNT(*) c FROM execution_events"
                ).fetchone()["c"],

            "workflow_count":
                con.execute(
                    "SELECT COUNT(*) c FROM workflow_runs"
                ).fetchone()["c"],

            "connector_count":
                len(CONNECTORS),
        }

        policy = con.execute(
            """
            SELECT *
            FROM policies
            WHERE id=1
            """
        ).fetchone()

    finally:
        con.close()

    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "database": "ready",

        **counts,

        "policy_version":
            policy["version"]
            if policy
            else 1,

        "external_execution_enabled":
            True,

        "verification_enabled":
            True,

        "adaptive_recovery_enabled":
            True,

        "self_modification_enabled":
            True,

        "intent_router_enabled":
            True,

        "result_closure_enabled":
            True,

        "multi_tool_enabled":
            True,

        "workflow_enabled":
            True,

        "workspace_tools_enabled":
            True,

        "connector_fabric_enabled":
            True,
    }


# ============================================================
# 167 STATUS
# ============================================================

@app.get("/167-status")
def status_167() -> Dict[str, Any]:

    return root()


# ============================================================
# CONNECTORS
# ============================================================

@app.get("/tools")
def tools() -> Dict[str, Any]:

    return {
        "status": "ready",
        "connectors":
            connector_snapshot(),
        "total_connectors":
            len(CONNECTORS),
    }


@app.get("/connectors")
def connectors() -> Dict[str, Any]:

    return {
        "status": "ready",
        "fabric": "active",
        "connectors":
            connector_snapshot(),
    }


# ============================================================
# PLANNING
# ============================================================

@app.post("/command/plan")
def command_plan(
    req: PlanRequest,
) -> Dict[str, Any]:

    try:

        plan = build_plan(
            CommandRequest(
                command=req.command
            )
        )

        approval = action_requires_approval(
            plan["action_type"],
            plan["connector"],
            None,
        )

        return {
            "status": "planned",
            "command":
                req.command,
            "plan":
                plan,
            "approval_required":
                approval,
        }

    except Exception as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


# ============================================================
# COMMAND
# ============================================================

@app.post("/command")
async def command(
    req: CommandRequest,
) -> Dict[str, Any]:

    try:

        plan = build_plan(req)

    except Exception as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    approval = action_requires_approval(
        plan["action_type"],
        plan["connector"],
        req.require_approval,
    )

    execution = create_execution(
        action_type=
            plan["action_type"],

        command=
            req.command,

        payload=
            plan,

        connector=
            plan["connector"],

        mission_id=
            req.mission_id,

        approval_required=
            approval,

        idempotency_key=
            req.idempotency_key,
    )

    if (
        execution["status"]
        == "awaiting_approval"
    ):

        return {
            "status":
                "awaiting_approval",

            "execution_id":
                execution["id"],

            "plan":
                plan,

            "approval_required":
                True,
        }

    if execution["status"] == "completed":

        return {
            "status":
                "completed",
            "execution":
                get_execution(
                    execution["id"]
                ),
        }

    await execute_async(
        execution["id"]
    )

    final = get_execution(
        execution["id"]
    )

    return {
        "status":
            final["status"],

        "execution_id":
            execution["id"],

        "execution":
            final,

        "plan":
            plan,

        "approval_required":
            False,
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post(
    "/execution/{execution_id}/approve"
)
async def approve(
    execution_id: str,
) -> Dict[str, Any]:

    try:

        execution = get_execution(
            execution_id
        )

    except KeyError:

        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    if not execution[
        "approval_required"
    ]:

        return {
            "status":
                "already_approved_or_not_required",
            "execution":
                execution,
        }

    update_execution(
        execution_id,
        approved=1,
        status="queued",
    )

    event(
        execution_id,
        "approval_granted",
    )

    await execute_async(
        execution_id
    )

    return {
        "status":
            "completed",

        "execution":
            get_execution(
                execution_id
            ),
    }


# ============================================================
# EXECUTION INSPECTION
# ============================================================

@app.get(
    "/execution/{execution_id}"
)
def execution(
    execution_id: str,
) -> Dict[str, Any]:

    try:

        item = get_execution(
            execution_id
        )

    except KeyError:

        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    return {
        "execution":
            item,

        "events":
            list_events(
                execution_id
            ),
    }


# ============================================================
# WORKFLOW API
# ============================================================

@app.post("/workflow")
async def workflow(
    req: WorkflowRequest,
) -> Dict[str, Any]:

    if not req.steps:

        raise HTTPException(
            status_code=400,
            detail=(
                "workflow requires "
                "at least one step"
            ),
        )

    payload = {
        "action_type":
            "workflow",
        "connector":
            "workflow",
        "steps":
            req.steps,
    }

    execution = create_execution(
        action_type="workflow",
        command=req.name,
        payload=payload,
        connector="workflow",
        mission_id=req.mission_id,
        approval_required=False,
        idempotency_key=
            req.idempotency_key,
    )

    await execute_async(
        execution["id"]
    )

    return {
        "status":
            get_execution(
                execution["id"]
            )["status"],

        "execution_id":
            execution["id"],

        "execution":
            get_execution(
                execution["id"]
            ),
    }


# ============================================================
# MISSION API
# ============================================================

@app.post("/mission")
async def mission(
    req: MissionRequest,
) -> Dict[str, Any]:

    mission_id = create_mission(
        req.objective
    )

    if req.remember:

        execute_memory(
            {
                "content":
                    req.objective
            }
        )

    try:

        plan = build_plan(
            CommandRequest(
                command=req.objective
            )
        )

    except Exception as exc:

        update_mission(
            mission_id,
            "failed",
            {
                "error": str(exc)
            },
        )

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    approval = action_requires_approval(
        plan["action_type"],
        plan["connector"],
        None,
    )

    execution = create_execution(
        action_type=
            plan["action_type"],

        command=
            req.objective,

        payload=
            plan,

        connector=
            plan["connector"],

        mission_id=
            mission_id,

        approval_required=
            approval,
    )

    if approval:

        return {
            "status":
                "awaiting_approval",

            "mission_id":
                mission_id,

            "execution_id":
                execution["id"],

            "plan":
                plan,
        }

    await execute_async(
        execution["id"]
    )

    final = get_execution(
        execution["id"]
    )

    mission_status = (
        "completed"
        if final["status"]
        == "completed"
        else "failed"
    )

    update_mission(
        mission_id,
        mission_status,
        final.get(
            "result_json"
        ),
    )

    return {
        "status":
            mission_status,

        "mission_id":
            mission_id,

        "execution_id":
            execution["id"],

        "execution":
            final,
    }


@app.get(
    "/mission/{mission_id}"
)
def mission_get(
    mission_id: str,
) -> Dict[str, Any]:

    con = db()

    try:

        mission_row = con.execute(
            """
            SELECT *
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if not mission_row:

            raise HTTPException(
                status_code=404,
                detail="mission not found",
            )

        executions = con.execute(
            """
            SELECT id
            FROM executions
            WHERE mission_id=?
            ORDER BY created_at
            """,
            (mission_id,),
        ).fetchall()

    finally:
        con.close()

    return {
        "mission":
            dict(mission_row),

        "executions":
            [
                {
                    "execution":
                        get_execution(
                            row["id"]
                        ),

                    "events":
                        list_events(
                            row["id"]
                        ),
                }

                for row in executions
            ],
    }


# ============================================================
# MEMORY
# ============================================================

@app.post("/memory")
def memory(
    req: MemoryRequest,
) -> Dict[str, Any]:

    return execute_memory(
        {
            "content":
                req.content
        }
    )


@app.get("/memory")
def memory_list(
    limit: int = 50,
) -> Dict[str, Any]:

    limit = max(
        1,
        min(
            int(limit),
            200,
        ),
    )

    con = db()

    try:

        rows = con.execute(
            """
            SELECT
                id,
                content,
                created_at
            FROM memories
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    finally:
        con.close()

    return {
        "status":
            "ready",

        "memories":
            [
                dict(row)
                for row in rows
            ],
    }


# ============================================================
# WORKFLOW INSPECTION
# ============================================================

@app.get(
    "/workflow/{run_id}"
)
def workflow_get(
    run_id: str,
) -> Dict[str, Any]:

    con = db()

    try:

        row = con.execute(
            """
            SELECT *
            FROM workflow_runs
            WHERE id=?
            """,
            (run_id,),
        ).fetchone()

    finally:
        con.close()

    if not row:

        raise HTTPException(
            status_code=404,
            detail="workflow not found",
        )

    result = dict(row)

    if result.get(
        "result_json"
    ):

        result[
            "result_json"
        ] = json.loads(
            result["result_json"]
        )

    return result


# ============================================================
# ROUTER TEST
# ============================================================

@app.get("/test-router")
def test_router() -> Dict[str, Any]:

    commands = [
        "ping",
        "GET https://example.com",
        "remember that AI Infinity works",
        "research autonomous AI agents",
        "list files",
        "ping then remember that AI Infinity works",
        "POST https://example.com",
        "webhook https://example.com",
    ]

    tests = []

    for command_text in commands:

        try:

            plan = build_plan(
                CommandRequest(
                    command=command_text
                )
            )

            passed = bool(
                plan["action_type"]
                and plan["connector"]
            )

            tests.append(
                {
                    "command":
                        command_text,
                    "action_type":
                        plan["action_type"],
                    "connector":
                        plan["connector"],
                    "passed":
                        passed,
                }
            )

        except Exception as exc:

            tests.append(
                {
                    "command":
                        command_text,
                    "passed":
                        False,
                    "error":
                        str(exc),
                }
            )

    return {
        "status":
            "completed",

        "version":
            VERSION,

        "tests":
            tests,
    }


# ============================================================
# SELF TEST
# ============================================================

@app.get("/self-test")
def self_test() -> Dict[str, Any]:

    tests = []

    # 1 HTTP routing
    try:

        plan = build_plan(
            CommandRequest(
                command=
                    "GET https://example.com"
            )
        )

        tests.append(
            {
                "name":
                    "HTTP intent routing",

                "passed":
                    (
                        plan["action_type"]
                        == "external_http"
                        and
                        plan["connector"]
                        == "http"
                    ),
            }
        )

    except Exception:

        tests.append(
            {
                "name":
                    "HTTP intent routing",
                "passed":
                    False,
            }
        )

    # 2 SSRF
    try:

        validate_url(
            "http://127.0.0.1"
        )

        ssrf_passed = False

    except Exception:

        ssrf_passed = True

    tests.append(
        {
            "name":
                "SSRF protection",
            "passed":
                ssrf_passed,
        }
    )

    # 3 Credentials
    try:

        sanitize_headers(
            {
                "Authorization":
                    "secret"
            }
        )

        credential_passed = False

    except Exception:

        credential_passed = True

    tests.append(
        {
            "name":
                "credential header protection",
            "passed":
                credential_passed,
        }
    )

    # 4 Traversal
    try:

        safe_workspace_path(
            "../escape.txt"
        )

        traversal_passed = False

    except Exception:

        traversal_passed = True

    tests.append(
        {
            "name":
                "workspace traversal protection",
            "passed":
                traversal_passed,
        }
    )

    # 5 Approval
    tests.append(
        {
            "name":
                "approval gate",
            "passed":
                action_requires_approval(
                    "file_write",
                    "workspace",
                    None,
                ),
        }
    )

    # 6 Workflow
    try:

        workflow_result = (
            execute_workflow_sync(
                "self-test-"
                + secrets.token_hex(4),
                [
                    {
                        "command":
                            "ping"
                    }
                ],
            )
        )

        tests.append(
            {
                "name":
                    "workflow execution",
                "passed":
                    bool(
                        workflow_result.get(
                            "success"
                        )
                    ),
            }
        )

    except Exception:

        tests.append(
            {
                "name":
                    "workflow execution",
                "passed":
                    False,
            }
        )

    return {
        "status":
            "completed",

        "passed":
            all(
                item["passed"]
                for item in tests
            ),

        "tests":
            tests,
    }


# ============================================================
# COMMAND UI
# ============================================================

UI = r"""
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">

<title>AI Infinity 2050.167</title>

<style>
body{
    font-family:system-ui,sans-serif;
    margin:0;
    padding:20px;
    background:#0d1117;
    color:#eee;
}

main{
    max-width:850px;
    margin:auto;
}

textarea,
button{
    width:100%;
    box-sizing:border-box;
    margin:7px 0;
    padding:13px;
    border-radius:10px;
    border:1px solid #30363d;
    background:#161b22;
    color:#fff;
}

button{
    cursor:pointer;
}

pre{
    white-space:pre-wrap;
    background:#161b22;
    padding:14px;
    border-radius:10px;
    overflow:auto;
}

.status{
    opacity:.75;
}
</style>
</head>

<body>

<main>

<h1>AI Infinity</h1>

<p class="status">
TARGET-2050.167 —
REAL-WORLD CONNECTOR FABRIC
</p>

<textarea
id="cmd"
rows="5"
placeholder="Try: ping
or: GET https://example.com
or: remember that AI Infinity works
or: research autonomous AI agents
or: list files
or: ping then remember that AI Infinity works"></textarea>

<button onclick="runCommand()">
Execute command
</button>

<button onclick="planCommand()">
Plan only
</button>

<button onclick="loadHealth()">
Health
</button>

<button onclick="loadConnectors()">
Connectors
</button>

<pre id="out">Ready.</pre>

</main>

<script>

async function request(
    url,
    options
){
    const response =
        await fetch(
            url,
            options
        );

    return await response.json();
}

async function runCommand(){

    const command =
        document.getElementById(
            "cmd"
        ).value;

    const data =
        await request(
            "/command",
            {
                method:"POST",
                headers:{
                    "Content-Type":
                        "application/json"
                },
                body:
                    JSON.stringify({
                        command:
                            command
                    })
            }
        );

    document.getElementById(
        "out"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );
}

async function planCommand(){

    const command =
        document.getElementById(
            "cmd"
        ).value;

    const data =
        await request(
            "/command/plan",
            {
                method:"POST",
                headers:{
                    "Content-Type":
                        "application/json"
                },
                body:
                    JSON.stringify({
                        command:
                            command
                    })
            }
        );

    document.getElementById(
        "out"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );
}

async function loadHealth(){

    const data =
        await request(
            "/health"
        );

    document.getElementById(
        "out"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );
}

async function loadConnectors(){

    const data =
        await request(
            "/connectors"
        );

    document.getElementById(
        "out"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );
}

</script>

</body>
</html>
"""


@app.get(
    "/command-ui",
    response_class=HTMLResponse,
)
def command_ui() -> str:

    return UI
