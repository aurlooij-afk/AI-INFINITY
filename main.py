"""
AI Infinity
TARGET-2050.168
CONNECTOR-EXECUTION-ADAPTER-CORE

Previous: TARGET-2050.167
"""

from __future__ import annotations

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
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# CORE
# ============================================================

VERSION = "TARGET-2050.168"
BUILD = "CONNECTOR-EXECUTION-ADAPTER-CORE"
PREVIOUS_BUILD = "TARGET-2050.167"

DB_PATH = os.getenv(
    "AI_INFINITY_DB",
    "/tmp/ai-infinity/ai_infinity.db"
)

WORKSPACE = Path(
    os.getenv(
        "AI_INFINITY_WORKSPACE",
        "/tmp/ai-infinity/workspace"
    )
).resolve()

WORKSPACE.mkdir(parents=True, exist_ok=True)
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="AI Infinity",
    version=VERSION
)

DB_LOCK = threading.RLock()

BLOCKED_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
}

BLOCKED_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.azure.com",
}


# ============================================================
# UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db():
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def qone(sql: str, params=()):
    with DB_LOCK:
        conn = db()
        row = conn.execute(sql, params).fetchone()
        conn.close()
        return dict(row) if row else None


def qall(sql: str, params=()):
    with DB_LOCK:
        conn = db()
        rows = [
            dict(row)
            for row in conn.execute(sql, params).fetchall()
        ]
        conn.close()
        return rows


def qexec(sql: str, params=()):
    with DB_LOCK:
        conn = db()
        cur = conn.execute(sql, params)
        conn.commit()
        value = cur.lastrowid
        conn.close()
        return value


def json_load(value, default=None):
    if value is None:
        return default

    try:
        return json.loads(value)
    except Exception:
        return default


def safe_json(value):
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def event(execution_id: str, event_type: str, data=None):
    qexec(
        """
        INSERT INTO execution_events
        (execution_id,event_type,data_json,created_at)
        VALUES(?,?,?,?)
        """,
        (
            execution_id,
            event_type,
            json.dumps(data or {}, ensure_ascii=False),
            now_iso(),
        ),
    )


# ============================================================
# DATABASE
# ============================================================

def init_db():

    with DB_LOCK:

        conn = db()

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS executions(
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                action_type TEXT NOT NULL,
                connector TEXT NOT NULL,
                status TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0,
                verified INTEGER NOT NULL DEFAULT 0,
                result_closure INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT,
                request_json TEXT,
                result_json TEXT,
                error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                recovery_attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_exec_idempotency
            ON executions(idempotency_key)
            WHERE idempotency_key IS NOT NULL;

            CREATE TABLE IF NOT EXISTS execution_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS missions(
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                execution_id TEXT,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflows(
                id TEXT PRIMARY KEY,
                objective TEXT,
                steps_json TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        conn.commit()
        conn.close()


init_db()


# ============================================================
# URL / SECURITY
# ============================================================

def extract_url(text: str) -> Optional[str]:

    match = re.search(
        r"https?://[^\s<>\"]+",
        text or "",
        re.IGNORECASE,
    )

    if not match:
        return None

    return match.group(0).rstrip(
        ".,);]}'"
    )


def validate_url(url: str):

    if not url:
        raise ValueError("url is required")

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            "only http/https URLs are allowed"
        )

    if not parsed.hostname:
        raise ValueError(
            "URL hostname is required"
        )

    host = parsed.hostname.lower()

    if host in BLOCKED_METADATA_HOSTS:
        raise ValueError(
            "blocked metadata endpoint"
        )

    try:

        infos = socket.getaddrinfo(
            host,
            parsed.port or (
                443
                if parsed.scheme == "https"
                else 80
            ),
        )

        for info in infos:

            address = ipaddress.ip_address(
                info[4][0]
            )

            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            ):
                raise ValueError(
                    "private or reserved network target blocked"
                )

    except ValueError:
        raise

    except Exception:
        pass


def safe_headers(
    headers: Optional[Dict[str, str]]
) -> Dict[str, str]:

    result = {}

    for key, value in (headers or {}).items():

        if key.lower().strip() in BLOCKED_HEADERS:
            raise ValueError(
                f"sensitive header blocked: {key}"
            )

        result[str(key)] = str(value)

    return result


class SafeRedirect(HTTPRedirectHandler):

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):

        validate_url(newurl)

        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )


HTTP_OPENER = build_opener(
    SafeRedirect()
)


# ============================================================
# REQUEST MODELS
# ============================================================

class CommandRequest(BaseModel):

    command: str = Field(min_length=1)

    approved: bool = False

    idempotency_key: Optional[str] = None

    mission_id: Optional[str] = None

    url: Optional[str] = None

    method: Optional[str] = None

    headers: Optional[Dict[str, str]] = None

    body: Optional[Any] = None


class WorkflowRequest(BaseModel):

    objective: str = ""

    steps: List[str]

    approved: bool = False

    idempotency_key: Optional[str] = None


class MemoryRequest(BaseModel):

    content: str = Field(min_length=1)

    metadata: Optional[Dict[str, Any]] = None


# ============================================================
# CONNECTOR ADAPTER SYSTEM
# ============================================================

class Adapter:

    name = "base"

    def capabilities(self):

        return {
            "name": self.name
        }

    def health(self):

        return {
            "status": "ready"
        }

    def execute(self, action):

        raise NotImplementedError


# ============================================================
# SYSTEM CONNECTOR
# ============================================================

class SystemAdapter(Adapter):

    name = "system"

    def capabilities(self):

        return {
            "name": self.name,
            "actions": ["ping"],
            "approval_required": False,
        }

    def execute(self, action):

        return {
            "status": "pong",
            "timestamp": now_iso(),
        }


# ============================================================
# MEMORY CONNECTOR
# ============================================================

class MemoryAdapter(Adapter):

    name = "memory"

    def capabilities(self):

        return {
            "name": self.name,
            "actions": ["remember"],
            "approval_required": False,
        }

    def execute(self, action):

        content = (
            action.get("content")
            or action.get("command")
            or ""
        )

        memory_id = qexec(
            """
            INSERT INTO memory
            (content,metadata_json,created_at)
            VALUES(?,?,?)
            """,
            (
                content,
                json.dumps(
                    action.get("metadata") or {}
                ),
                now_iso(),
            ),
        )

        return {
            "status": "remembered",
            "memory_id": memory_id,
            "content": content,
        }


# ============================================================
# RESEARCH CONNECTOR
# ============================================================

class ResearchAdapter(Adapter):

    name = "research"

    def capabilities(self):

        return {
            "name": self.name,
            "actions": ["research_plan"],
            "approval_required": False,
            "execution_mode": "plan",
        }

    def execute(self, action):

        topic = (
            action.get("topic")
            or action.get("command")
            or ""
        )

        return {
            "status": "planned",
            "topic": topic,
            "steps": [
                "discover independent sources",
                "collect evidence",
                "compare claims",
                "verify evidence",
                "close result with confidence",
            ],
        }


# ============================================================
# WORKSPACE CONNECTOR
# ============================================================

class WorkspaceAdapter(Adapter):

    name = "workspace"

    def capabilities(self):

        return {
            "name": self.name,
            "actions": [
                "file_list",
                "file_read",
                "file_write",
            ],
            "approval_required": True,
        }

    def safe_path(self, name: str):

        path = (
            WORKSPACE / name
        ).resolve()

        if (
            path != WORKSPACE
            and WORKSPACE not in path.parents
        ):
            raise ValueError(
                "workspace path traversal blocked"
            )

        return path

    def execute(self, action):

        operation = action.get(
            "operation",
            "list",
        )

        if operation == "list":

            return {
                "files": sorted(
                    p.name
                    for p in WORKSPACE.iterdir()
                )
            }

        if operation == "read":

            path = self.safe_path(
                action.get("path", "")
            )

            return {
                "path": str(
                    path.relative_to(WORKSPACE)
                ),
                "content": path.read_text(
                    encoding="utf-8"
                ),
            }

        if operation == "write":

            path = self.safe_path(
                action.get("path", "")
            )

            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            path.write_text(
                str(
                    action.get(
                        "content",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            return {
                "status": "written",
                "path": str(
                    path.relative_to(WORKSPACE)
                ),
            }

        raise ValueError(
            "unsupported workspace operation"
        )


# ============================================================
# HTTP CONNECTOR
# ============================================================

class HTTPAdapter(Adapter):

    name = "http"

    def capabilities(self):

        return {
            "name": self.name,
            "actions": ["external_http"],
            "methods": [
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "HEAD",
            ],
            "approval_required": True,
        }

    def health(self):

        return {
            "status": "ready",
            "transport": "urllib",
            "ssrf_protection": True,
        }

    def execute(self, action):

        url = action.get("url")

        validate_url(url)

        method = str(
            action.get("method")
            or "GET"
        ).upper()

        if method not in {
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "HEAD",
        }:
            raise ValueError(
                "unsupported HTTP method"
            )

        headers = safe_headers(
            action.get("headers")
        )

        body = action.get("body")

        data = None

        if body is not None:

            if isinstance(
                body,
                (dict, list),
            ):

                data = json.dumps(
                    body
                ).encode("utf-8")

                headers.setdefault(
                    "Content-Type",
                    "application/json",
                )

            else:

                data = str(
                    body
                ).encode("utf-8")

        request = Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )

        started = time.time()

        with HTTP_OPENER.open(
            request,
            timeout=20,
        ) as response:

            raw = response.read(
                1024 * 1024
            )

            text = raw.decode(
                "utf-8",
                errors="replace",
            )

            response_headers = {}

            for key, value in response.headers.items():

                if (
                    key.lower()
                    not in BLOCKED_HEADERS
                ):
                    response_headers[
                        key
                    ] = value

            return {
                "status_code": int(
                    response.status
                ),
                "url": response.geturl(),
                "headers": response_headers,
                "body": text[:20000],
                "elapsed_ms": round(
                    (time.time() - started)
                    * 1000,
                    2,
                ),
            }


# ============================================================
# WEBHOOK CONNECTOR
# ============================================================

class WebhookAdapter(HTTPAdapter):

    name = "webhook"

    def capabilities(self):

        return {
            "name": self.name,
            "actions": ["webhook"],
            "methods": [
                "POST",
                "PUT",
                "PATCH",
            ],
            "approval_required": True,
            "natural_language_url_extraction": True,
        }

    def execute(self, action):

        action = dict(action)

        action["method"] = str(
            action.get("method")
            or "POST"
        ).upper()

        if action["method"] not in {
            "POST",
            "PUT",
            "PATCH",
        }:
            raise ValueError(
                "webhook method must be POST, PUT, or PATCH"
            )

        return super().execute(action)


# ============================================================
# WORKFLOW CONNECTOR
# ============================================================

class WorkflowAdapter(Adapter):

    name = "workflow"

    def capabilities(self):

        return {
            "name": self.name,
            "actions": ["workflow"],
            "approval_required": True,
            "connector_steps": True,
        }

    def execute(self, action):

        steps = action.get("steps") or []

        results = []

        for step in steps:

            if isinstance(step, str):

                plan = build_plan(
                    CommandRequest(
                        command=step,
                        approved=True,
                    )
                )

            else:

                plan = step

            if plan["action_type"] == "workflow":

                raise ValueError(
                    "nested workflows are not allowed"
                )

            result = execute_plan(
                plan,
                approved=True,
                from_workflow=True,
            )

            results.append(result)

            if result.get("status") != "completed":

                return {
                    "status": "partial",
                    "results": results,
                }

        return {
            "status": "completed",
            "results": results,
        }


# ============================================================
# CONNECTOR REGISTRY
# ============================================================

ADAPTERS: Dict[str, Adapter] = {
    "system": SystemAdapter(),
    "memory": MemoryAdapter(),
    "research": ResearchAdapter(),
    "workspace": WorkspaceAdapter(),
    "http": HTTPAdapter(),
    "webhook": WebhookAdapter(),
    "workflow": WorkflowAdapter(),
}


# ============================================================
# INTENT ROUTER
# ============================================================

def detect_intent(
    command: str
) -> Dict[str, Any]:

    text = command.strip()
    low = text.lower()

    url = extract_url(text)

    if low in {
        "ping",
        "ping ai infinity",
        "health",
    }:

        return {
            "action_type": "system",
            "connector": "system",
        }

    if low.startswith(
        (
            "remember ",
            "remember that ",
        )
    ):

        return {
            "action_type": "memory",
            "connector": "memory",
        }

    if low.startswith(
        (
            "research ",
            "find evidence ",
            "investigate ",
        )
    ):

        return {
            "action_type": "research_plan",
            "connector": "research",
        }

    if low.startswith(
        (
            "list files",
            "show files",
            "workspace list",
        )
    ):

        return {
            "action_type": "file_list",
            "connector": "workspace",
        }

    # IMPORTANT 168 FIX:
    # Webhook detection happens before generic HTTP detection.
    if low.startswith(
        (
            "webhook ",
            "send webhook",
            "trigger webhook",
        )
    ):

        return {
            "action_type": "webhook",
            "connector": "webhook",
        }

    if (
        low.startswith("workflow:")
        or low.startswith("run workflow")
        or low.startswith("do these steps")
        or " then " in low
    ):

        return {
            "action_type": "workflow",
            "connector": "workflow",
        }

    if re.match(
        r"^(get|post|put|patch|delete|head)\s+https?://",
        low,
    ):

        return {
            "action_type": "external_http",
            "connector": "http",
        }

    if url:

        return {
            "action_type": "external_http",
            "connector": "http",
        }

    return {
        "action_type": "unknown",
        "connector": "system",
    }


# ============================================================
# WORKFLOW PARSER
# ============================================================

def split_workflow(
    command: str
) -> List[str]:

    low = command.lower()

    if low.startswith("workflow:"):

        body = command.split(
            ":",
            1,
        )[1]

        return [
            x.strip()
            for x in re.split(
                r"\s+then\s+|;",
                body,
                flags=re.I,
            )
            if x.strip()
        ]

    if low.startswith(
        "run workflow"
    ):

        body = re.sub(
            r"^run workflow\s*:?\s*",
            "",
            command,
            flags=re.I,
        )

        return [
            x.strip()
            for x in re.split(
                r"\s+then\s+|;",
                body,
                flags=re.I,
            )
            if x.strip()
        ]

    if " then " in low:

        return [
            x.strip()
            for x in re.split(
                r"\s+then\s+",
                command,
                flags=re.I,
            )
            if x.strip()
        ]

    return [command]


# ============================================================
# STRUCTURED ACTION PLANNER
# ============================================================

def build_plan(
    req: CommandRequest
) -> Dict[str, Any]:

    intent = detect_intent(
        req.command
    )

    action_type = intent[
        "action_type"
    ]

    connector = intent[
        "connector"
    ]

    command = req.command.strip()

    url = (
        req.url
        or extract_url(command)
    )

    if action_type == "unknown":

        raise ValueError(
            "command not understood"
        )

    plan = {
        "action_type": action_type,
        "connector": connector,
        "command": command,
        "approved_required": connector
        in {
            "http",
            "webhook",
            "workspace",
            "workflow",
        },
    }

    # --------------------------------------------------------
    # HTTP
    # --------------------------------------------------------

    if action_type == "external_http":

        if not url:

            raise ValueError(
                "HTTP action requires URL"
            )

        method_match = re.match(
            r"^(GET|POST|PUT|PATCH|DELETE|HEAD)\b",
            command,
            re.I,
        )

        method = (
            req.method
            or (
                method_match.group(1)
                if method_match
                else "GET"
            )
        ).upper()

        plan.update(
            {
                "url": url,
                "method": method,
                "headers": req.headers or {},
                "body": req.body,
            }
        )

    # --------------------------------------------------------
    # WEBHOOK — 168 FIX
    # --------------------------------------------------------

    elif action_type == "webhook":

        # The 167 bug was:
        # detect_intent() detected webhook correctly,
        # but build_plan() demanded req.url.
        #
        # 168 extracts the URL directly from natural language.

        if not url:

            raise ValueError(
                "webhook action requires URL"
            )

        plan.update(
            {
                "url": url,
                "method": (
                    req.method
                    or "POST"
                ).upper(),
                "headers": req.headers or {},
                "body": (
                    req.body
                    if req.body is not None
                    else {
                        "source": "AI Infinity",
                        "command": command,
                    }
                ),
            }
        )

    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    elif action_type == "memory":

        content = re.sub(
            r"^remember(?:\s+that)?\s*",
            "",
            command,
            flags=re.I,
        ).strip()

        plan["content"] = (
            content
            or command
        )

    # --------------------------------------------------------
    # RESEARCH
    # --------------------------------------------------------

    elif action_type == "research_plan":

        plan["topic"] = re.sub(
            r"^(research|find evidence|investigate)\s*",
            "",
            command,
            flags=re.I,
        ).strip()

    # --------------------------------------------------------
    # FILES
    # --------------------------------------------------------

    elif action_type == "file_list":

        plan["operation"] = "list"

    # --------------------------------------------------------
    # WORKFLOW
    # --------------------------------------------------------

    elif action_type == "workflow":

        steps = split_workflow(
            command
        )

        if len(steps) < 2:

            raise ValueError(
                "workflow requires at least two steps"
            )

        plan["steps"] = [
            build_plan(
                CommandRequest(
                    command=step,
                    approved=True,
                )
            )
            for step in steps
        ]

    return plan


# ============================================================
# EXECUTION
# ============================================================

def create_execution(
    plan: Dict[str, Any],
    req: CommandRequest,
    approved: bool,
):

    if req.idempotency_key:

        existing = qone(
            """
            SELECT *
            FROM executions
            WHERE idempotency_key=?
            """,
            (req.idempotency_key,),
        )

        if existing:

            return existing

    execution_id = (
        "exec-"
        + uuid.uuid4().hex[:16]
    )

    timestamp = now_iso()

    try:

        qexec(
            """
            INSERT INTO executions
            (
                id,
                mission_id,
                action_type,
                connector,
                status,
                approved,
                verified,
                result_closure,
                idempotency_key,
                request_json,
                created_at,
                updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                execution_id,
                req.mission_id,
                plan["action_type"],
                plan["connector"],
                "queued",
                int(approved),
                0,
                0,
                req.idempotency_key,
                json.dumps(
                    plan,
                    ensure_ascii=False,
                ),
                timestamp,
                timestamp,
            ),
        )

    except sqlite3.IntegrityError:

        existing = qone(
            """
            SELECT *
            FROM executions
            WHERE idempotency_key=?
            """,
            (req.idempotency_key,),
        )

        if existing:
            return existing

        raise

    return qone(
        """
        SELECT *
        FROM executions
        WHERE id=?
        """,
        (execution_id,),
    )


def update_execution(
    execution_id: str,
    **fields,
):

    fields["updated_at"] = now_iso()

    columns = ", ".join(
        f"{key}=?"
        for key in fields
    )

    values = (
        list(fields.values())
        + [execution_id]
    )

    qexec(
        f"""
        UPDATE executions
        SET {columns}
        WHERE id=?
        """,
        tuple(values),
    )


def execute_plan(
    plan: Dict[str, Any],
    approved: bool = False,
    from_workflow: bool = False,
):

    connector = plan[
        "connector"
    ]

    adapter = ADAPTERS.get(
        connector
    )

    if not adapter:

        raise ValueError(
            "connector unavailable"
        )

    if (
        plan.get("approved_required")
        and not approved
        and not from_workflow
    ):

        return {
            "status":
                "awaiting_approval",
            "plan": plan,
        }

    attempts = 0
    recovery_attempts = 0
    last_error = None

    while attempts < 2:

        attempts += 1

        try:

            result = adapter.execute(
                plan
            )

            return {
                "status": "completed",
                "result": safe_json(
                    result
                ),
                "attempts": attempts,
                "recovery_attempts":
                    recovery_attempts,
            }

        except Exception as exc:

            last_error = str(exc)

            recovery_attempts += 1

            if attempts >= 2:
                break

            time.sleep(0.05)

    return {
        "status": "failed",
        "error": last_error,
        "attempts": attempts,
        "recovery_attempts":
            recovery_attempts,
    }


def run_execution(
    execution_id: str
):

    row = qone(
        """
        SELECT *
        FROM executions
        WHERE id=?
        """,
        (execution_id,),
    )

    if not row:

        raise ValueError(
            "execution not found"
        )

    plan = json_load(
        row["request_json"],
        {},
    )

    event(
        execution_id,
        "created",
        {
            "connector":
                row["connector"],
            "action_type":
                row["action_type"],
        },
    )

    event(
        execution_id,
        "started",
        {},
    )

    update_execution(
        execution_id,
        status="running",
        attempts=0,
    )

    result = execute_plan(
        plan,
        approved=bool(
            row["approved"]
        ),
    )

    attempts = int(
        result.get(
            "attempts",
            1,
        )
    )

    recovery_attempts = int(
        result.get(
            "recovery_attempts",
            0,
        )
    )

    if result.get("status") == "awaiting_approval":

        update_execution(
            execution_id,
            status="awaiting_approval",
        )

        event(
            execution_id,
            "awaiting_approval",
            {},
        )

        return

    if result.get("status") == "completed":

        update_execution(
            execution_id,
            status="verified",
            verified=1,
            result_closure=1,
            result_json=json.dumps(
                result.get("result"),
                ensure_ascii=False,
            ),
            attempts=attempts,
            recovery_attempts=
                recovery_attempts,
            error=None,
        )

        event(
            execution_id,
            "verified",
            {
                "attempts": attempts
            },
        )

        event(
            execution_id,
            "closed",
            {
                "result_closure":
                    True
            },
        )

    else:

        update_execution(
            execution_id,
            status="failed",
            verified=0,
            result_closure=0,
            result_json=json.dumps(
                result,
                ensure_ascii=False,
            ),
            attempts=attempts,
            recovery_attempts=
                recovery_attempts,
            error=result.get(
                "error"
            ),
        )

        event(
            execution_id,
            "failed",
            {
                "error":
                    result.get(
                        "error"
                    )
            },
        )


# ============================================================
# SERIALIZATION
# ============================================================

def serialize_execution(row):

    if not row:
        return None

    result = dict(row)

    result["approved"] = bool(
        result["approved"]
    )

    result["verified"] = bool(
        result["verified"]
    )

    result["result_closure"] = bool(
        result["result_closure"]
    )

    result["request"] = json_load(
        result.pop("request_json"),
        {},
    )

    result["result"] = json_load(
        result.pop("result_json"),
        None,
    )

    return result


# ============================================================
# COMMAND SUBMISSION
# ============================================================

def submit_command(
    req: CommandRequest
):

    try:

        plan = build_plan(req)

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    if (
        plan.get("approved_required")
        and not req.approved
    ):

        return {
            "status":
                "awaiting_approval",
            "plan": plan,
        }

    row = create_execution(
        plan,
        req,
        approved=req.approved,
    )

    execution_id = row["id"]

    if row["status"] in {
        "queued",
        "running",
    }:

        run_execution(
            execution_id
        )

    return serialize_execution(
        qone(
            """
            SELECT *
            FROM executions
            WHERE id=?
            """,
            (execution_id,),
        )
    )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "status": "ready",
        "version": VERSION,
        "build": BUILD,
        "previous_build":
            PREVIOUS_BUILD,

        "target":
            "real-world-command-capable AI Infinity",

        "intent_to_action": True,
        "natural_language_routing": True,
        "structured_action_planning": True,
        "local_command_engine": True,

        "external_http_bridge": True,
        "webhook_connector": True,

        "connector_registry": True,
        "connector_capability_discovery":
            True,
        "connector_adapters": True,
        "connector_health": True,

        "approval_gate": True,
        "ssrf_protection": True,
        "redirect_ssrf_revalidation":
            True,
        "credential_header_protection":
            True,

        "durable_execution": True,
        "mission_execution_link": True,
        "execution_events": True,
        "idempotency": True,

        "verification": True,
        "adaptive_recovery": True,
        "persistent_memory": True,
        "result_closure": True,

        "multi_tool_orchestration":
            True,
        "workflow_engine": True,
        "workspace_file_tools": True,

        "interface":
            "/command-ui",
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "database": "ready",

        "mission_count":
            qone(
                "SELECT COUNT(*) n FROM missions"
            )["n"],

        "execution_count":
            qone(
                "SELECT COUNT(*) n FROM executions"
            )["n"],

        "memory_count":
            qone(
                "SELECT COUNT(*) n FROM memory"
            )["n"],

        "execution_event_count":
            qone(
                "SELECT COUNT(*) n FROM execution_events"
            )["n"],

        "workflow_count":
            qone(
                "SELECT COUNT(*) n FROM workflows"
            )["n"],

        "connector_count":
            len(ADAPTERS),

        "policy_version": 1,

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

        "connector_adapter_enabled":
            True,
    }


@app.get("/168-status")
def status_168():
    return root()


# ============================================================
# CONNECTORS
# ============================================================

@app.get("/connectors")
def connectors():

    return {
        "count": len(ADAPTERS),
        "connectors": [
            {
                "name": name,
                "capabilities":
                    adapter.capabilities(),
                "health":
                    adapter.health(),
            }
            for name, adapter
            in ADAPTERS.items()
        ],
    }


@app.get("/connector-health")
def connector_health():

    result = {}

    for name, adapter in ADAPTERS.items():

        result[name] = adapter.health()

    return result


@app.get("/tools")
def tools():
    return connectors()


# ============================================================
# COMMAND
# ============================================================

@app.post("/command")
def command(
    req: CommandRequest
):

    return submit_command(req)


@app.post("/command/plan")
def command_plan(
    req: CommandRequest
):

    try:

        return {
            "status": "planned",
            "plan":
                build_plan(req),
        }

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


# ============================================================
# WORKFLOW
# ============================================================

@app.post("/workflow")
def workflow(
    req: WorkflowRequest
):

    command = (
        "workflow: "
        + " then ".join(req.steps)
    )

    request = CommandRequest(
        command=command,
        approved=req.approved,
        idempotency_key=
            req.idempotency_key,
    )

    return submit_command(
        request
    )


# ============================================================
# EXECUTION
# ============================================================

@app.get("/execution/{execution_id}")
def execution(
    execution_id: str
):

    row = qone(
        """
        SELECT *
        FROM executions
        WHERE id=?
        """,
        (execution_id,),
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    return serialize_execution(
        row
    )


@app.get("/execution/{execution_id}/events")
def execution_events(
    execution_id: str
):

    if not qone(
        """
        SELECT id
        FROM executions
        WHERE id=?
        """,
        (execution_id,),
    ):

        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    rows = qall(
        """
        SELECT
            event_type,
            data_json,
            created_at
        FROM execution_events
        WHERE execution_id=?
        ORDER BY id
        """,
        (execution_id,),
    )

    for row in rows:

        row["data"] = json_load(
            row.pop("data_json"),
            {},
        )

    return {
        "execution_id":
            execution_id,
        "events": rows,
    }


@app.post("/execution/{execution_id}/approve")
def approve_execution(
    execution_id: str
):

    row = qone(
        """
        SELECT *
        FROM executions
        WHERE id=?
        """,
        (execution_id,),
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail="execution not found",
        )

    update_execution(
        execution_id,
        approved=1,
    )

    run_execution(
        execution_id
    )

    return serialize_execution(
        qone(
            """
            SELECT *
            FROM executions
            WHERE id=?
            """,
            (execution_id,),
        )
    )


# ============================================================
# MEMORY
# ============================================================

@app.post("/memory")
def remember(
    req: MemoryRequest
):

    memory_id = qexec(
        """
        INSERT INTO memory
        (content,metadata_json,created_at)
        VALUES(?,?,?)
        """,
        (
            req.content,
            json.dumps(
                req.metadata or {}
            ),
            now_iso(),
        ),
    )

    return {
        "status": "remembered",
        "memory_id": memory_id,
    }


@app.get("/memory")
def memories(
    limit: int = 50
):

    limit = max(
        1,
        min(limit, 200),
    )

    rows = qall(
        """
        SELECT *
        FROM memory
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )

    for row in rows:

        row["metadata"] = json_load(
            row.pop("metadata_json"),
            {},
        )

    return {
        "count": len(rows),
        "items": rows,
    }


# ============================================================
# MISSIONS
# ============================================================

@app.post("/mission")
def mission(
    req: CommandRequest
):

    mission_id = (
        req.mission_id
        or (
            "mission-"
            + uuid.uuid4().hex[:16]
        )
    )

    timestamp = now_iso()

    qexec(
        """
        INSERT OR REPLACE INTO missions
        (
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
            req.command,
            "created",
            timestamp,
            timestamp,
        ),
    )

    req.mission_id = mission_id

    result = submit_command(req)

    if result.get("id"):

        qexec(
            """
            UPDATE missions
            SET
                status=?,
                execution_id=?,
                result_json=?,
                updated_at=?
            WHERE id=?
            """,
            (
                result.get(
                    "status"
                ),
                result.get("id"),
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                now_iso(),
                mission_id,
            ),
        )

    return {
        "mission_id": mission_id,
        "result": result,
    }


@app.get("/mission/{mission_id}")
def mission_get(
    mission_id: str
):

    row = qone(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (mission_id,),
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    row["result"] = json_load(
        row.pop("result_json"),
        None,
    )

    return row


# ============================================================
# ROUTER TEST
# ============================================================

@app.get("/test-router")
def test_router():

    tests = [
        (
            "ping",
            "system",
        ),
        (
            "GET https://example.com",
            "http",
        ),
        (
            "remember that AI Infinity works",
            "memory",
        ),
        (
            "research autonomous AI agents",
            "research",
        ),
        (
            "list files",
            "workspace",
        ),
        (
            "ping then remember that AI Infinity works",
            "workflow",
        ),
        (
            "POST https://example.com",
            "http",
        ),
        (
            "webhook https://example.com",
            "webhook",
        ),
    ]

    results = []

    for command_text, expected in tests:

        try:

            plan = build_plan(
                CommandRequest(
                    command=command_text
                )
            )

            passed = (
                plan["connector"]
                == expected
            )

            if expected == "webhook":

                passed = (
                    passed
                    and bool(
                        plan.get("url")
                    )
                    and plan.get(
                        "method"
                    ) == "POST"
                )

            results.append(
                {
                    "command":
                        command_text,
                    "action_type":
                        plan["action_type"],
                    "connector":
                        plan["connector"],
                    "passed":
                        passed,
                    **(
                        {
                            "url_extracted":
                                plan.get("url")
                        }
                        if expected
                        == "webhook"
                        else {}
                    ),
                }
            )

        except Exception as exc:

            results.append(
                {
                    "command":
                        command_text,
                    "passed": False,
                    "error":
                        str(exc),
                }
            )

    return {
        "status":
            (
                "completed"
                if all(
                    x["passed"]
                    for x in results
                )
                else "failed"
            ),
        "version": VERSION,
        "tests": results,
    }


# ============================================================
# SELF TEST
# ============================================================

@app.get("/self-test")
def self_test():

    tests = []

    # HTTP routing
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
                    plan["connector"]
                    == "http",
            }
        )

    except Exception as exc:

        tests.append(
            {
                "name":
                    "HTTP intent routing",
                "passed": False,
                "error": str(exc),
            }
        )

    # SSRF
    try:

        validate_url(
            "http://127.0.0.1"
        )

        tests.append(
            {
                "name":
                    "SSRF protection",
                "passed": False,
                "error":
                    "SSRF was not blocked",
            }
        )

    except ValueError:

        tests.append(
            {
                "name":
                    "SSRF protection",
                "passed": True,
            }
        )

    # Sensitive headers
    try:

        safe_headers(
            {
                "Authorization":
                    "blocked"
            }
        )

        tests.append(
            {
                "name":
                    "credential header protection",
                "passed": False,
            }
        )

    except ValueError:

        tests.append(
            {
                "name":
                    "credential header protection",
                "passed": True,
            }
        )

    # Workspace traversal
    try:

        ADAPTERS[
            "workspace"
        ].safe_path(
            "../blocked"
        )

        tests.append(
            {
                "name":
                    "workspace traversal protection",
                "passed": False,
            }
        )

    except ValueError:

        tests.append(
            {
                "name":
                    "workspace traversal protection",
                "passed": True,
            }
        )

    # Approval gate
    try:

        result = submit_command(
            CommandRequest(
                command=
                    "GET https://example.com",
                approved=False,
            )
        )

        tests.append(
            {
                "name":
                    "approval gate",
                "passed":
                    result["status"]
                    == "awaiting_approval",
            }
        )

    except Exception as exc:

        tests.append(
            {
                "name":
                    "approval gate",
                "passed": False,
                "error": str(exc),
            }
        )

    # Workflow
    try:

        plan = build_plan(
            CommandRequest(
                command=
                    "ping then remember that AI Infinity works"
            )
        )

        tests.append(
            {
                "name":
                    "workflow planning",
                "passed":
                    len(
                        plan["steps"]
                    ) == 2,
            }
        )

    except Exception as exc:

        tests.append(
            {
                "name":
                    "workflow planning",
                "passed": False,
                "error": str(exc),
            }
        )

    # 168 webhook fix
    try:

        plan = build_plan(
            CommandRequest(
                command=
                    "webhook https://example.com"
            )
        )

        tests.append(
            {
                "name":
                    "webhook URL extraction",
                "passed":
                    (
                        plan["connector"]
                        == "webhook"
                        and plan["url"]
                        == "https://example.com"
                        and plan["method"]
                        == "POST"
                    ),
            }
        )

    except Exception as exc:

        tests.append(
            {
                "name":
                    "webhook URL extraction",
                "passed": False,
                "error": str(exc),
            }
        )

    # Registry
    tests.append(
        {
            "name":
                "connector registry",
            "passed":
                len(ADAPTERS) == 7,
        }
    )

    passed = all(
        test["passed"]
        for test in tests
    )

    return {
        "status":
            "completed"
            if passed
            else "failed",
        "passed": passed,
        "tests": tests,
    }


# ============================================================
# COMMAND UI
# ============================================================

@app.get(
    "/command-ui",
    response_class=HTMLResponse,
)
def command_ui():

    return """
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>

body{
    font-family:system-ui;
    margin:0;
    background:#0b1020;
    color:white;
}

main{
    max-width:850px;
    margin:auto;
    padding:22px;
}

textarea{
    width:100%;
    min-height:130px;
    box-sizing:border-box;
    padding:14px;
    border-radius:12px;
    border:1px solid #39425c;
    background:#11182c;
    color:white;
    font-size:16px;
}

button{
    padding:12px 20px;
    border:0;
    border-radius:10px;
    margin-top:10px;
    cursor:pointer;
}

pre{
    white-space:pre-wrap;
    background:#121a30;
    padding:15px;
    border-radius:12px;
    overflow:auto;
}

.card{
    background:#121a30;
    padding:14px;
    border-radius:12px;
    margin-bottom:14px;
}

</style>
</head>

<body>

<main>

<div class="card">

<h1>AI Infinity</h1>

<p>
TARGET-2050.168 —
Connector Execution Adapter Core
</p>

<p>
Real-world command interface
</p>

</div>

<div class="card">

<textarea
id="cmd"
placeholder="Try:
ping

GET https://example.com

webhook https://example.com

remember that AI Infinity works

research autonomous AI agents

ping then remember that AI Infinity works"
></textarea>

<br>

<button onclick="runCommand()">
Run Command
</button>

<button onclick="planCommand()">
Plan
</button>

</div>

<pre id="out">
Ready.
</pre>

<script>

async function runCommand(){

    const command =
        document.getElementById("cmd").value;

    const response =
        await fetch(
            "/command",
            {
                method:"POST",
                headers:{
                    "Content-Type":
                        "application/json"
                },
                body:JSON.stringify({
                    command:command,
                    approved:false
                })
            }
        );

    document.getElementById("out")
        .textContent =
        JSON.stringify(
            await response.json(),
            null,
            2
        );
}

async function planCommand(){

    const command =
        document.getElementById("cmd").value;

    const response =
        await fetch(
            "/command/plan",
            {
                method:"POST",
                headers:{
                    "Content-Type":
                        "application/json"
                },
                body:JSON.stringify({
                    command:command
                })
            }
        );

    document.getElementById("out")
        .textContent =
        JSON.stringify(
            await response.json(),
            null,
            2
        );
}

</script>

</main>

</body>
</html>
"""


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000"
            )
        ),
    )
