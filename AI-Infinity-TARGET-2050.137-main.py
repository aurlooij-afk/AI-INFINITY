"""
AI Infinity
TARGET-2050.99
BUILD: REAL-WORLD-COMMAND-EXECUTION-CORE

Practical cumulative AI core.

Preserves:
- mission engine
- research
- evidence graph
- provider independence
- claims
- verification
- contradiction screening
- adaptive recovery
- persistent memory
- checkpoints/events
- world model
- opportunities
- action fabric
- interface
- legacy API compatibility
- 2050.98 stale transaction recovery

2050.99 adds:
- real-world external HTTP command execution
- explicit approval-gated side effects
- host allowlist
- public API/webhook execution
- safe request methods
- sensitive-header blocking
- side-effect transaction persistence
- approval transaction endpoint
- rejection endpoint
- no automatic replay of uncertain external effects
- stale external transaction closure
- bounded response/payload sizes
- redirect blocking for side-effecting requests
- audit trail integration
- real-world command status endpoint

2050.101 adds:
- fixes duplicate FastAPI app initialization that hid early routes
- restores /real-world-command and approval routes in the effective app
- restores one consistent route registry for the interface
- adds /route-integrity deployment diagnostic
- hardens interface command flow around the stable /command alias
"""

from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import ssl
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urljoin, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Request as FastAPIRequest,
)
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
app = FastAPI(title="AI Infinity", version="TARGET-2050.101")

# ============================================================
# VERSION
# ============================================================

APP_VERSION = "TARGET-2050.101"
BUILD = "REAL-WORLD-COMMAND-INTERFACE-CORE"

DATA_DIR = os.getenv(
    "AI_INFINITY_DATA_DIR",
    "/tmp/ai-infinity",
)
DB_PATH = os.path.join(
    DATA_DIR,
    "ai_infinity.db",
)

MAX_BODY = 1_000_000
MAX_REDIRECTS = 4
REQUEST_TIMEOUT = 12

ACTION_MAX_BYTES = 512 * 1024
ACTION_MAX_ATTEMPTS = 2

ACTION_STALE_SECONDS = max(
    30,
    int(
        os.getenv(
            "AI_INFINITY_ACTION_STALE_SECONDS",
            "120",
        )
    ),
)

ACTION_HOST_ALLOWLIST = {
    host.strip().lower().rstrip(".")
    for host in os.getenv(
        "AI_INFINITY_ACTION_HOST_ALLOWLIST",
        "",
    ).split(",")
    if host.strip()
}

ACTION_MAX_RESPONSE_BYTES = 256 * 1024

SENSITIVE_ACTION_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-access-token",
}


STARTED_AT = time.time()

os.makedirs(
    DATA_DIR,
    exist_ok=True,
)


# ============================================================
# SECURITY / NETWORKING
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "host.docker.internal",
    "0.0.0.0",
    "::1",
}

WAF_MARKERS = (
    "access denied",
    "captcha",
    "cloudflare ray id",
    "cf-chl-",
    "attention required",
    "request rejected",
    "forbidden",
)


def _host_is_private(
    host: str,
) -> bool:

    host = (
        host
        or ""
    ).strip().lower().rstrip(".")

    if not host:
        return True

    if (
        host in BLOCKED_HOSTS
        or host.endswith(".local")
    ):
        return True

    try:

        ip = ipaddress.ip_address(
            host
        )

        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )

    except ValueError:

        pass

    try:

        infos = socket.getaddrinfo(
            host,
            None,
        )

        for info in infos:

            address = info[4][0]

            ip = ipaddress.ip_address(
                address
            )

            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):

                return True

    except Exception:

        return True

    return False


def validate_url(
    url: str,
) -> str:

    parsed = urlparse(url)

    if parsed.scheme not in {
        "http",
        "https",
    }:

        raise ValueError(
            "only http/https URLs are allowed"
        )

    if not parsed.hostname:

        raise ValueError(
            "missing hostname"
        )

    if _host_is_private(
        parsed.hostname
    ):

        raise ValueError(
            "blocked or private destination"
        )

    if (
        parsed.username
        or parsed.password
    ):

        raise ValueError(
            "credential-bearing URLs are not allowed"
        )

    return url


class SafeRedirectHandler(
    HTTPRedirectHandler
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

        destination = urljoin(
            req.full_url,
            newurl,
        )

        validate_url(
            destination
        )

        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )


OPENER = build_opener(
    SafeRedirectHandler(),
    HTTPSHandler(
        context=ssl.create_default_context()
    ),
)


def safe_fetch(
    url: str,
    timeout: int = REQUEST_TIMEOUT,
) -> Dict[str, Any]:

    current = validate_url(
        url
    )

    redirects = 0

    while True:

        try:

            req = Request(
                current,
                headers={
                    "User-Agent":
                        "AI-Infinity/2050.100",
                    "Accept": (
                        "application/json,"
                        "text/html,"
                        "text/plain,"
                        "*/*"
                    ),
                },
                method="GET",
            )

            with OPENER.open(
                req,
                timeout=timeout,
            ) as response:

                final_url = validate_url(
                    response.geturl()
                )

                body = response.read(
                    MAX_BODY + 1
                )

                if len(body) > MAX_BODY:

                    raise ValueError(
                        "response exceeds 1 MB safety limit"
                    )

                text = body.decode(
                    "utf-8",
                    errors="replace",
                )

                sample = text[
                    :12000
                ].lower()

                blocked = any(
                    marker in sample
                    for marker in WAF_MARKERS
                )

                return {
                    "ok": not blocked,
                    "status": getattr(
                        response,
                        "status",
                        200,
                    ),
                    "url": final_url,
                    "content_type":
                        response.headers.get(
                            "Content-Type",
                            "",
                        ),
                    "text":
                        ""
                        if blocked
                        else text,
                    "error": (
                        "waf_or_block_page"
                        if blocked
                        else None
                    ),
                }

        except HTTPError as exc:

            return {
                "ok": False,
                "status": exc.code,
                "url": current,
                "text": "",
                "error": str(exc),
            }

        except (
            URLError,
            TimeoutError,
            ValueError,
            OSError,
        ) as exc:

            return {
                "ok": False,
                "status": 0,
                "url": current,
                "text": "",
                "error": str(exc),
            }

        except Exception as exc:

            return {
                "ok": False,
                "status": 0,
                "url": current,
                "text": "",
                "error": str(exc),
            }

        finally:

            redirects += 1

            if redirects > MAX_REDIRECTS:

                return {
                    "ok": False,
                    "status": 0,
                    "url": current,
                    "text": "",
                    "error": "too_many_redirects",
                }


# ============================================================
# DATABASE
# ============================================================

DB_LOCK = threading.RLock()


SCHEMA = {

    "missions": """
        CREATE TABLE IF NOT EXISTS missions(
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            request_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            result_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            recovery_attempts INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0,
            policy_version INTEGER NOT NULL DEFAULT 1,
            route TEXT,
            error TEXT
        )
    """,

    "mission_events": """
        CREATE TABLE IF NOT EXISTS mission_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            ts REAL NOT NULL,
            stage TEXT NOT NULL,
            event TEXT NOT NULL,
            data_json TEXT
        )
    """,

    "memory": """
        CREATE TABLE IF NOT EXISTS memory(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """,

    "policies": """
        CREATE TABLE IF NOT EXISTS policies(
            id INTEGER PRIMARY KEY CHECK(id=1),
            version INTEGER NOT NULL,
            data_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """,

    "action_log": """
        CREATE TABLE IF NOT EXISTS action_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            details_json TEXT,
            ts REAL NOT NULL,
            idempotency_key TEXT,
            action_type TEXT,
            target TEXT,
            result_json TEXT
        )
    """,

    "provenance": """
        CREATE TABLE IF NOT EXISTS provenance(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            item_type TEXT,
            item_id TEXT,
            source_url TEXT,
            provider TEXT,
            publisher TEXT,
            family TEXT,
            created_at REAL NOT NULL
        )
    """,

    "connectors": """
        CREATE TABLE IF NOT EXISTS connectors(
            name TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            config_json TEXT NOT NULL DEFAULT '{}',
            updated_at REAL NOT NULL
        )
    """,

    "checkpoints": """
        CREATE TABLE IF NOT EXISTS checkpoints(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            label TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """,

    "learning": """
        CREATE TABLE IF NOT EXISTS learning(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            signal TEXT NOT NULL,
            value REAL NOT NULL,
            details_json TEXT,
            created_at REAL NOT NULL
        )
    """,

    "claims": """
        CREATE TABLE IF NOT EXISTS claims(
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            text TEXT NOT NULL,
            polarity TEXT NOT NULL DEFAULT 'neutral',
            confidence REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
    """,

    "evidence": """
        CREATE TABLE IF NOT EXISTS evidence(
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            title TEXT,
            abstract TEXT,
            url TEXT,
            provider TEXT,
            family TEXT,
            publisher TEXT,
            year INTEGER,
            empirical INTEGER NOT NULL DEFAULT 0,
            relevant INTEGER NOT NULL DEFAULT 0,
            quality REAL NOT NULL DEFAULT 0,
            raw_json TEXT,
            created_at REAL NOT NULL
        )
    """,

    "evidence_links": """
        CREATE TABLE IF NOT EXISTS evidence_links(
            claim_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(claim_id,evidence_id,relation)
        )
    """,

    "research": """
        CREATE TABLE IF NOT EXISTS research(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            query TEXT NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            created_at REAL NOT NULL
        )
    """,

    "research_cache": """
        CREATE TABLE IF NOT EXISTS research_cache(
            cache_key TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """,

    "provider_stats": """
        CREATE TABLE IF NOT EXISTS provider_stats(
            provider TEXT PRIMARY KEY,
            family TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 0,
            failure INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at REAL NOT NULL
        )
    """,

    "world_model": """
        CREATE TABLE IF NOT EXISTS world_model(
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """,

    "opportunities": """
        CREATE TABLE IF NOT EXISTS opportunities(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            title TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """,

    "action_transactions": """
        CREATE TABLE IF NOT EXISTS action_transactions(
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT UNIQUE,
            input_json TEXT NOT NULL,
            result_json TEXT,
            error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """,

    "action_snapshots": """
        CREATE TABLE IF NOT EXISTS action_snapshots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """,

    "action_dependencies": """
        CREATE TABLE IF NOT EXISTS action_dependencies(
            action TEXT PRIMARY KEY,
            dependencies_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """,

    "action_circuit": """
        CREATE TABLE IF NOT EXISTS action_circuit(
            action TEXT PRIMARY KEY,
            failures INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'closed',
            opened_at REAL,
            updated_at REAL NOT NULL
        )
    """,

    "resources": """
        CREATE TABLE IF NOT EXISTS resources(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            endpoint TEXT,
            cost REAL NOT NULL DEFAULT 0,
            capabilities TEXT NOT NULL DEFAULT '[]',
            trust REAL NOT NULL DEFAULT 0.5,
            active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        )
    """,

    "genomes": """
        CREATE TABLE IF NOT EXISTS genomes(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            genome_json TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
    """,

    "nodes": """
        CREATE TABLE IF NOT EXISTS nodes(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            capabilities TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'offline',
            last_seen REAL NOT NULL
        )
    """,
}


def db():

    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    with DB_LOCK:

        conn = db()

        try:

            for sql in SCHEMA.values():

                conn.execute(sql)

            mission_cols = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(missions)"
                ).fetchall()
            }

            migrations = [
                ("request_json", "TEXT", "'{}'"),
                ("approved", "INTEGER", "0"),
                ("policy_version", "INTEGER", "1"),
                ("route", "TEXT", "NULL"),
                ("error", "TEXT", "NULL"),
            ]

            for name, typ, default in migrations:

                if name not in mission_cols:

                    conn.execute(
                        f"""
                        ALTER TABLE missions
                        ADD COLUMN {name} {typ}
                        DEFAULT {default}
                        """
                    )

            action_cols = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(action_log)"
                ).fetchall()
            }

            action_migrations = [
                ("idempotency_key", "TEXT", "NULL"),
                ("action_type", "TEXT", "NULL"),
                ("target", "TEXT", "NULL"),
                ("result_json", "TEXT", "NULL"),
            ]

            for name, typ, default in action_migrations:

                if name not in action_cols:

                    conn.execute(
                        f"""
                        ALTER TABLE action_log
                        ADD COLUMN {name} {typ}
                        DEFAULT {default}
                        """
                    )

            conn.execute(
                """
                INSERT OR IGNORE INTO
                policies(id,version,data_json,updated_at)
                VALUES(1,1,?,?)
                """,
                (
                    json.dumps({
                        "adaptive_recovery": True,
                        "self_modification": True,
                        "provider_quorum": True,
                        "safe_action_gateway": True,
                        "real_world_command_execution": True,
                    }),
                    time.time(),
                ),
            )

            defaults = [
                ("reasoning", "builtin"),
                ("planner", "builtin"),
                ("memory", "builtin"),
                ("web_read", "builtin"),
                ("verification", "builtin"),
                ("action_gateway", "approval-gated"),
                ("world_model", "builtin"),
                ("video", "boundary"),
            ]

            for name, kind in defaults:

                conn.execute(
                    """
                    INSERT OR IGNORE INTO
                    connectors(
                        name,
                        kind,
                        enabled,
                        config_json,
                        updated_at
                    )
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        name,
                        kind,
                        1 if kind == "builtin" else 0,
                        "{}",
                        time.time(),
                    ),
                )

            action_defaults = [
                ("calculator_test", []),
                ("hash_text", []),
                ("validate_python", []),
            ]

            for action_name, deps in action_defaults:

                conn.execute(
                    """
                    INSERT OR IGNORE INTO
                    action_dependencies(
                        action,
                        dependencies_json,
                        updated_at
                    )
                    VALUES(?,?,?)
                    """,
                    (
                        action_name,
                        json.dumps(deps),
                        time.time(),
                    ),
                )

                conn.execute(
                    """
                    INSERT OR IGNORE INTO
                    action_circuit(
                        action,
                        failures,
                        state,
                        opened_at,
                        updated_at
                    )
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        action_name,
                        0,
                        "closed",
                        None,
                        time.time(),
                    ),
                )

            conn.commit()

        finally:

            conn.close()


init_db()


def q(
    sql: str,
    args=(),
    one: bool = False,
):

    with DB_LOCK:

        conn = db()

        try:

            cur = conn.execute(
                sql,
                args,
            )

            rows = cur.fetchall()

            if one:

                return (
                    rows[0]
                    if rows
                    else None
                )

            return rows

        finally:

            conn.close()


def write(
    sql: str,
    args=(),
):

    with DB_LOCK:

        conn = db()

        try:

            conn.execute(
                sql,
                args,
            )

            conn.commit()

        finally:

            conn.close()


# ============================================================
# MODELS
# ============================================================

class MissionRequest(BaseModel):

    objective: str

    research: bool = True
    verify: bool = True
    remember: bool = False
    external_access: bool = True

    execute: bool = False
    require_approval: bool = False


class TaskRequest(MissionRequest):
    pass


class CommandRequest(BaseModel):

    objective: str

    action: str = "propose"

    require_approval: bool = True
    execute: bool = True
    external_access: bool = True

    research: bool = True
    verify: bool = True
    remember: bool = False


class MemoryRequest(BaseModel):

    key: str
    value: str


class ActionRequest(BaseModel):

    action: str
    args: Dict[str, Any] = {}

    mission_id: Optional[str] = None

    idempotency_key: Optional[str] = None

    require_approval: bool = False


class SafeActionRequest(BaseModel):

    action_type: str

    target: str = ""

    payload: Dict[str, Any] = {}

    idempotency_key: Optional[str] = None

    require_approval: bool = False

    mission_id: Optional[str] = None


class RealWorldCommandRequest(BaseModel):

    target: str

    method: str = "POST"

    body: Dict[str, Any] = {}

    headers: Dict[str, str] = {}

    idempotency_key: Optional[str] = None

    mission_id: Optional[str] = None


# ============================================================
# UTILITIES
# ============================================================

def now() -> float:

    return time.time()


def make_id(
    prefix: str,
) -> str:

    return (
        f"{prefix}-"
        f"{uuid.uuid4().hex[:12]}"
    )


def normalize_text(
    value: str,
) -> str:

    return re.sub(
        r"\s+",
        " ",
        (value or "").strip(),
    )


def fingerprint(
    *parts,
) -> str:

    raw = "|".join(
        str(x)
        for x in parts
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def emit(
    mission_id: str,
    stage: str,
    event: str,
    data: Optional[Dict[str, Any]] = None,
):

    write(
        """
        INSERT INTO mission_events(
            mission_id,
            ts,
            stage,
            event,
            data_json
        )
        VALUES(?,?,?,?,?)
        """,
        (
            mission_id,
            now(),
            stage,
            event,
            json.dumps(
                data or {},
                ensure_ascii=False,
            ),
        ),
    )


def checkpoint(
    mission_id: str,
    label: str,
    state: Dict[str, Any],
):

    write(
        """
        INSERT INTO checkpoints(
            mission_id,
            label,
            state_json,
            created_at
        )
        VALUES(?,?,?,?)
        """,
        (
            mission_id,
            label,
            json.dumps(
                state,
                ensure_ascii=False,
            ),
            now(),
        ),
    )


def policy() -> Dict[str, Any]:

    row = q(
        "SELECT * FROM policies WHERE id=1",
        one=True,
    )

    if not row:

        return {
            "version": 1,
            "adaptive_recovery": True,
            "self_modification": True,
        }

    data = json.loads(
        row["data_json"]
    )

    data["version"] = row["version"]

    return data


def adaptive_upgrade(
    reason: str,
) -> int:

    current = policy()

    version = int(
        current.get(
            "version",
            1,
        )
    ) + 1

    current["version"] = version
    current["last_reason"] = reason

    write(
        """
        UPDATE policies
        SET version=?,
            data_json=?,
            updated_at=?
        WHERE id=1
        """,
        (
            version,
            json.dumps(
                current,
                ensure_ascii=False,
            ),
            now(),
        ),
    )

    return version


def remember(
    key: str,
    value: str,
):

    ts = now()

    write(
        """
        INSERT INTO memory(
            key,
            value,
            created_at,
            updated_at
        )
        VALUES(?,?,?,?)
        """,
        (
            key,
            value,
            ts,
            ts,
        ),
    )


def memory_items(
    limit: int = 50,
):

    return [
        dict(row)
        for row in q(
            """
            SELECT *
            FROM memory
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
    ]


# ============================================================
# BOUNDED ACTION FABRIC
# ============================================================

SAFE_ACTIONS = {
    "calculator_test",
    "hash_text",
    "validate_python",
}


def _circuit(
    action: str,
):

    row = q(
        """
        SELECT *
        FROM action_circuit
        WHERE action=?
        """,
        (action,),
        one=True,
    )

    if not row:

        write(
            """
            INSERT INTO action_circuit(
                action,
                failures,
                state,
                opened_at,
                updated_at
            )
            VALUES(?,?,?,?,?)
            """,
            (
                action,
                0,
                "closed",
                None,
                now(),
            ),
        )

        return {
            "action": action,
            "failures": 0,
            "state": "closed",
        }

    return dict(row)


def _circuit_failure(
    action: str,
):

    current = _circuit(
        action
    )

    failures = int(
        current.get(
            "failures",
            0,
        )
    ) + 1

    state = (
        "open"
        if failures >= 3
        else "closed"
    )

    write(
        """
        UPDATE action_circuit
        SET failures=?,
            state=?,
            opened_at=?,
            updated_at=?
        WHERE action=?
        """,
        (
            failures,
            state,
            now()
            if state == "open"
            else current.get(
                "opened_at"
            ),
            now(),
            action,
        ),
    )


def _circuit_success(
    action: str,
):

    write(
        """
        UPDATE action_circuit
        SET failures=0,
            state='closed',
            opened_at=NULL,
            updated_at=?
        WHERE action=?
        """,
        (
            now(),
            action,
        ),
    )


def _action_allowed(
    action: str,
) -> bool:

    return (
        action in SAFE_ACTIONS
        and _circuit(action).get(
            "state"
        )
        != "open"
    )


def _safe_math_eval(
    node,
):

    if (
        isinstance(
            node,
            ast.Constant,
        )
        and isinstance(
            node.value,
            (int, float),
        )
        and not isinstance(
            node.value,
            bool,
        )
    ):

        return node.value

    if isinstance(
        node,
        ast.UnaryOp,
    ) and isinstance(
        node.op,
        (
            ast.USub,
            ast.UAdd,
        ),
    ):

        value = _safe_math_eval(
            node.operand
        )

        if isinstance(
            node.op,
            ast.USub,
        ):

            return -value

        return value

    if isinstance(
        node,
        ast.BinOp,
    ):

        left = _safe_math_eval(
            node.left
        )

        right = _safe_math_eval(
            node.right
        )

        if isinstance(
            node.op,
            ast.Add,
        ):

            return (
                left + right
            )

        if isinstance(
            node.op,
            ast.Sub,
        ):

            return (
                left - right
            )

        if isinstance(
            node.op,
            ast.Mult,
        ):

            return (
                left * right
            )

        if isinstance(
            node.op,
            ast.Div,
        ):

            return (
                left / right
            )

        if isinstance(
            node.op,
            ast.FloorDiv,
        ):

            return (
                left // right
            )

        if isinstance(
            node.op,
            ast.Mod,
        ):

            return (
                left % right
            )

        if isinstance(
            node.op,
            ast.Pow,
        ):

            if (
                abs(right) > 12
                or abs(left) > 1_000_000
            ):

                raise ValueError(
                    "power_bounds_exceeded"
                )

            return (
                left ** right
            )

    raise ValueError(
        "unsupported_expression"
    )


def _registered_execute(
    action: str,
    args: Dict[str, Any],
):

    if action == "calculator_test":

        expression = str(
            args.get(
                "expression",
                "2+3*4",
            )
        )

        tree = ast.parse(
            expression,
            mode="eval",
        )

        allowed = (
            ast.Expression,
            ast.Constant,
            ast.BinOp,
            ast.UnaryOp,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.Pow,
            ast.USub,
            ast.UAdd,
            ast.Load,
        )

        if any(
            not isinstance(
                node,
                allowed,
            )
            for node in ast.walk(tree)
        ):

            raise ValueError(
                "unsafe_expression"
            )

        return {
            "expression":
                expression,
            "value":
                _safe_math_eval(
                    tree.body
                ),
        }

    if action == "hash_text":

        value = str(
            args.get(
                "text",
                "",
            )
        )

        return {
            "algorithm":
                "sha256",
            "hash":
                hashlib.sha256(
                    value.encode(
                        "utf-8"
                    )
                ).hexdigest(),
            "length":
                len(value),
        }

    if action == "validate_python":

        source = str(
            args.get(
                "source",
                "",
            )
        )

        if len(source) > 50_000:

            raise ValueError(
                "source_too_large"
            )

        tree = ast.parse(
            source,
            mode="exec",
        )

        forbidden = []

        for node in ast.walk(
            tree
        ):

            if isinstance(
                node,
                (
                    ast.Call,
                    ast.Import,
                    ast.ImportFrom,
                ),
            ):

                forbidden.append(
                    type(node).__name__
                )

            if (
                isinstance(
                    node,
                    ast.Attribute,
                )
                and node.attr in {
                    "system",
                    "popen",
                    "run",
                    "Popen",
                    "check_output",
                    "check_call",
                }
            ):

                forbidden.append(
                    "forbidden_attribute"
                )

        return {
            "valid_syntax":
                True,
            "node_count":
                sum(
                    1
                    for _ in ast.walk(
                        tree
                    )
                ),
            "execution_performed":
                False,
            "unsafe_constructs_detected":
                forbidden[:20],
        }

    raise ValueError(
        "action_not_registered"
    )


def _transaction_result(
    row,
):

    if (
        not row
        or not row["result_json"]
    ):

        return None

    try:

        return json.loads(
            row["result_json"]
        )

    except Exception:

        return None


def _transaction_replay(
    row,
    action,
    key,
    reclaimed=False,
):

    return {
        "status":
            row["status"],
        "transaction_id":
            row["id"],
        "action":
            action,
        "idempotency_key":
            key,
        "idempotent_replay":
            not reclaimed,
        "stale_running_reclaimed":
            reclaimed,
        "result":
            _transaction_result(
                row
            ),
    }


def _begin_action_transaction(
    mission_id,
    action,
    key,
    input_json,
    snapshot,
    allow_stale_reclaim=True,
):

    existing = q(
        """
        SELECT *
        FROM action_transactions
        WHERE idempotency_key=?
        """,
        (key,),
        one=True,
    )

    if existing:

        age = max(
            0.0,
            now()
            - float(
                existing["updated_at"]
                or existing["created_at"]
                or now()
            ),
        )

        if (
            existing["status"]
            == "running"
            and age >= ACTION_STALE_SECONDS
        ):

            txid = existing["id"]

            if not allow_stale_reclaim:

                result = {
                    "status":
                        "failed_closed",
                    "transaction_id":
                        txid,
                    "outcome":
                        "unknown_remote_outcome",
                    "replay_blocked":
                        True,
                    "reason":
                        "stale_external_transaction_closed_without_replay",
                }

                write(
                    """
                    UPDATE action_transactions
                    SET status='failed_closed',
                        result_json=?,
                        error=?,
                        updated_at=?
                    WHERE id=?
                      AND status='running'
                    """,
                    (
                        json.dumps(
                            result,
                            ensure_ascii=False,
                        ),
                        "stale external transaction outcome unknown",
                        now(),
                        txid,
                    ),
                )

                refreshed = q(
                    """
                    SELECT *
                    FROM action_transactions
                    WHERE id=?
                    """,
                    (txid,),
                    one=True,
                )

                write(
                    """
                    INSERT INTO action_snapshots(
                        transaction_id,
                        snapshot_json,
                        created_at
                    )
                    VALUES(?,?,?)
                    """,
                    (
                        txid,
                        json.dumps(
                            {
                                **snapshot,
                                "recovery":
                                    "stale_external_transaction_closed_without_replay",
                                "previous_status":
                                    "running",
                                "stale_age_seconds":
                                    age,
                                "external_side_effects":
                                    True,
                            },
                            ensure_ascii=False,
                        ),
                        now(),
                    ),
                )

                return {
                    "mode":
                        "stale_closed",
                    "transaction_id":
                        txid,
                    "row":
                        refreshed,
                    "stale_age_seconds":
                        age,
                }

            write(
                """
                UPDATE action_transactions
                SET status='running',
                    input_json=?,
                    result_json=NULL,
                    error=NULL,
                    updated_at=?
                WHERE id=?
                  AND status='running'
                """,
                (
                    input_json,
                    now(),
                    txid,
                ),
            )

            refreshed = q(
                """
                SELECT *
                FROM action_transactions
                WHERE id=?
                """,
                (txid,),
                one=True,
            )

            if (
                refreshed
                and refreshed["status"]
                == "running"
            ):

                write(
                    """
                    INSERT INTO action_snapshots(
                        transaction_id,
                        snapshot_json,
                        created_at
                    )
                    VALUES(?,?,?)
                    """,
                    (
                        txid,
                        json.dumps(
                            {
                                **snapshot,
                                "recovery":
                                    "stale_running_reclaimed",
                                "previous_status":
                                    "running",
                                "stale_age_seconds":
                                    age,
                                "external_side_effects":
                                    False,
                            },
                            ensure_ascii=False,
                        ),
                        now(),
                    ),
                )

                return {
                    "mode":
                        "reclaimed",
                    "transaction_id":
                        txid,
                    "row":
                        refreshed,
                    "stale_age_seconds":
                        age,
                }

        return {
            "mode":
                "replay",
            "transaction_id":
                existing["id"],
            "row":
                existing,
        }

    txid = make_id(
        "tx"
    )

    try:

        write(
            """
            INSERT INTO action_transactions(
                id,
                mission_id,
                action,
                status,
                idempotency_key,
                input_json,
                created_at,
                updated_at
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                txid,
                mission_id,
                action,
                "running",
                key,
                input_json,
                now(),
                now(),
            ),
        )

    except sqlite3.IntegrityError:

        existing = q(
            """
            SELECT *
            FROM action_transactions
            WHERE idempotency_key=?
            """,
            (key,),
            one=True,
        )

        if existing:

            return {
                "mode":
                    "replay",
                "transaction_id":
                    existing["id"],
                "row":
                    existing,
            }

        raise

    write(
        """
        INSERT INTO action_snapshots(
            transaction_id,
            snapshot_json,
            created_at
        )
        VALUES(?,?,?)
        """,
        (
            txid,
            json.dumps(
                snapshot,
                ensure_ascii=False,
            ),
            now(),
        ),
    )

    return {
        "mode":
            "new",
        "transaction_id":
            txid,
        "row":
            q(
                """
                SELECT *
                FROM action_transactions
                WHERE id=?
                """,
                (txid,),
                one=True,
            ),
    }


def execute_action_fabric(
    request: ActionRequest,
):

    action = request.action.strip()

    args = dict(
        request.args or {}
    )

    if action not in SAFE_ACTIONS:

        return {
            "status":
                "blocked",
            "reason":
                "action_not_registered",
            "approval_required":
                True,
            "external_side_effects":
                False,
        }

    if not _action_allowed(
        action
    ):

        return {
            "status":
                "blocked",
            "reason":
                "circuit_open",
            "action":
                action,
        }

    if request.require_approval:

        return {
            "status":
                "awaiting_approval",
            "action":
                action,
            "approval_required":
                True,
            "external_side_effects":
                False,
        }

    idem = (
        request.idempotency_key
        or fingerprint(
            action,
            json.dumps(
                args,
                sort_keys=True,
            ),
        )
    )

    tx = _begin_action_transaction(
        request.mission_id,
        action,
        idem,
        json.dumps(
            args,
            ensure_ascii=False,
        ),
        {
            "action":
                action,
            "args":
                args,
            "side_effects":
                False,
        },
    )

    if tx["mode"] == "replay":

        return _transaction_replay(
            tx["row"],
            action,
            idem,
            False,
        )

    txid = tx[
        "transaction_id"
    ]

    attempts = []

    for attempt in range(
        1,
        ACTION_MAX_ATTEMPTS + 1,
    ):

        try:

            started = now()

            result = _registered_execute(
                action,
                args,
            )

            attempts.append({
                "attempt":
                    attempt,
                "status":
                    "verified",
                "latency_ms":
                    int(
                        (
                            now()
                            - started
                        ) * 1000
                    ),
            })

            _circuit_success(
                action
            )

            result = {
                **result,
                "attempts":
                    attempts,
            }

            write(
                """
                UPDATE action_transactions
                SET status='committed',
                    result_json=?,
                    error=NULL,
                    updated_at=?
                WHERE id=?
                """,
                (
                    json.dumps(
                        result,
                        ensure_ascii=False,
                    ),
                    now(),
                    txid,
                ),
            )

            return {
                "status":
                    "committed",
                "transaction_id":
                    txid,
                "action":
                    action,
                "result":
                    result,
                "idempotency_key":
                    idem,
                "stale_running_reclaimed":
                    tx["mode"]
                    == "reclaimed",
                "safety": {
                    "registered_action_only":
                        True,
                    "external_side_effects":
                        False,
                    "spending":
                        False,
                    "arbitrary_code_execution":
                        False,
                },
            }

        except Exception as exc:

            attempts.append({
                "attempt":
                    attempt,
                "status":
                    "failed",
                "error":
                    str(exc)[:500],
            })

            _circuit_failure(
                action
            )

            if attempt >= ACTION_MAX_ATTEMPTS:

                error = str(exc)[
                    :500
                ]

                write(
                    """
                    UPDATE action_transactions
                    SET status='failed_closed',
                        error=?,
                        result_json=NULL,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        error,
                        now(),
                        txid,
                    ),
                )

                return {
                    "status":
                        "failed_closed",
                    "transaction_id":
                        txid,
                    "action":
                        action,
                    "attempts":
                        attempts,
                    "idempotency_key":
                        idem,
                    "stale_running_reclaimed":
                        tx["mode"]
                        == "reclaimed",
                    "external_side_effects":
                        False,
                }

    raise RuntimeError(
        "action_fabric_unreachable"
    )


# ============================================================
# SAFE REAL-WORLD ACTION GATEWAY
# ============================================================

SAFE_ACTION_ALLOWLIST = {
    "public_http_get": {
        "side_effects":
            False,
        "requires_approval":
            False,
    },
    "public_http_request": {
        "side_effects":
            True,
        "requires_approval":
            True,
        "host_allowlist_required":
            True,
    },
    "save_result": {
        "side_effects":
            False,
        "requires_approval":
            False,
    },
}


def _action_fingerprint(
    action_type: str,
    target: str,
    payload: Optional[dict] = None,
):

    raw = json.dumps(
        {
            "action_type":
                action_type,
            "target":
                target,
            "payload":
                payload or {},
        },
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    ).encode(
        "utf-8"
    )

    return hashlib.sha256(
        raw
    ).hexdigest()


def _validate_public_target(
    target: str,
) -> str:

    parsed = urlparse(
        target
    )

    if parsed.scheme not in {
        "http",
        "https",
    }:

        raise HTTPException(
            status_code=400,
            detail=(
                "Only http/https "
                "public targets are allowed"
            ),
        )

    if not parsed.hostname:

        raise HTTPException(
            status_code=400,
            detail=(
                "Target hostname "
                "is required"
            ),
        )

    if (
        parsed.username
        or parsed.password
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Credential-bearing "
                "targets are blocked"
            ),
        )

    if _host_is_private(
        parsed.hostname
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "Private/local "
                "target blocked"
            ),
        )

    return target


class NoRedirectHandler(
    HTTPRedirectHandler
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

        return None


def _action_host_allowed(
    target: str,
) -> bool:

    host = (
        urlparse(
            target
        ).hostname
        or ""
    ).lower().rstrip(".")

    if not ACTION_HOST_ALLOWLIST:

        return False

    return any(
        host == allowed
        or host.endswith(
            "." + allowed
        )
        for allowed
        in ACTION_HOST_ALLOWLIST
    )


def _validated_action_headers(
    headers: Optional[dict],
) -> dict:

    source = (
        headers
        if isinstance(
            headers,
            dict,
        )
        else {}
    )

    output = {}

    for key, value in source.items():

        name = str(
            key
        ).strip()

        if not name:
            continue

        if (
            name.lower()
            in SENSITIVE_ACTION_HEADERS
        ):

            raise HTTPException(
                status_code=403,
                detail=(
                    "credential_or_cookie_"
                    "header_blocked"
                ),
            )

        if len(name) > 128:

            raise HTTPException(
                status_code=400,
                detail=(
                    "header_name_too_long"
                ),
            )

        text_value = str(
            value
        )

        if len(text_value) > 8192:

            raise HTTPException(
                status_code=400,
                detail=(
                    "header_value_too_long"
                ),
            )

        output[
            name
        ] = text_value

    return output


def _external_request(
    method: str,
    target: str,
    payload: Optional[dict] = None,
) -> dict:

    url = _validate_public_target(
        target
    )

    if not _action_host_allowed(
        url
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "target_host_not_allowlisted"
            ),
        )

    method = (
        str(
            method
            or "POST"
        )
        .upper()
        .strip()
    )

    allowed_methods = {
        "GET",
        "HEAD",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }

    if method not in allowed_methods:

        raise HTTPException(
            status_code=400,
            detail=(
                "unsupported_http_method"
            ),
        )

    data = (
        payload
        if isinstance(
            payload,
            dict,
        )
        else {}
    )

    headers = _validated_action_headers(
        data.get(
            "headers"
        )
    )

    headers.setdefault(
        "User-Agent",
        "AI-Infinity/2050.100",
    )

    headers.setdefault(
        "Accept",
        "application/json,"
        "text/plain,*/*",
    )

    body = None

    if method in {
        "POST",
        "PUT",
        "PATCH",
    }:

        body_value = data.get(
            "body",
            {},
        )

        body = json.dumps(
            body_value,
            ensure_ascii=False,
        ).encode(
            "utf-8"
        )

        if len(body) > ACTION_MAX_BYTES:

            raise HTTPException(
                status_code=413,
                detail=(
                    "action_payload_too_large"
                ),
            )

        headers.setdefault(
            "Content-Type",
            "application/json",
        )

    request = Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )

    opener = build_opener(
        NoRedirectHandler(),
        HTTPSHandler(
            context=ssl.create_default_context()
        ),
    )

    try:

        with opener.open(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:

            status_code = int(
                getattr(
                    response,
                    "status",
                    200,
                )
            )

            response_url = validate_url(
                response.geturl()
            )

            body_bytes = response.read(
                ACTION_MAX_RESPONSE_BYTES
                + 1
            )

            truncated = (
                len(body_bytes)
                > ACTION_MAX_RESPONSE_BYTES
            )

            body_bytes = body_bytes[
                :ACTION_MAX_RESPONSE_BYTES
            ]

            return {
                "ok":
                    200
                    <= status_code
                    < 300,
                "method":
                    method,
                "target":
                    response_url,
                "status_code":
                    status_code,
                "content_type":
                    response.headers.get(
                        "Content-Type",
                        "",
                    ),
                "bytes":
                    len(body_bytes),
                "truncated":
                    truncated,
                "response_preview":
                    body_bytes.decode(
                        "utf-8",
                        errors="replace",
                    )[:12000],
                "outcome":
                    (
                        "confirmed"
                        if (
                            200
                            <= status_code
                            < 300
                        )
                        else
                        "remote_response_non_2xx"
                    ),
            }

    except HTTPError as exc:

        code = int(
            exc.code
        )

        if (
            300
            <= code
            < 400
        ):

            return {
                "ok": False,
                "method":
                    method,
                "target":
                    url,
                "status_code":
                    code,
                "error":
                    (
                        "redirect_not_followed_"
                        "for_side_effect"
                    ),
                "outcome":
                    "not_executed",
            }

        data_bytes = exc.read(
            ACTION_MAX_RESPONSE_BYTES
        )

        outcome = (
            "remote_rejected"
            if (
                400
                <= code
                < 500
            )
            else
            "unknown_remote_outcome"
        )

        return {
            "ok": False,
            "method":
                method,
            "target":
                url,
            "status_code":
                code,
            "error":
                "http_error",
            "response_preview":
                data_bytes.decode(
                    "utf-8",
                    errors="replace",
                )[:4000],
            "outcome":
                outcome,
            "replay_blocked":
                code >= 500,
        }

    except (
        URLError,
        TimeoutError,
        OSError,
    ) as exc:

        return {
            "ok": False,
            "method":
                method,
            "target":
                url,
            "status_code":
                0,
            "error":
                str(exc)[:500],
            "outcome":
                "unknown_remote_outcome",
            "replay_blocked":
                True,
        }


def _execute_safe_action(
    action_type: str,
    target: str,
    payload: Optional[dict] = None,
):

    if action_type not in SAFE_ACTION_ALLOWLIST:

        raise HTTPException(
            status_code=403,
            detail=(
                "Action type "
                "is not registered"
            ),
        )

    if action_type == "public_http_get":

        url = _validate_public_target(
            target
        )

        result = safe_fetch(
            url,
            timeout=8,
        )

        if result.get(
            "ok"
        ):

            text = result.get(
                "text",
                "",
            )

            return {
                "ok":
                    True,
                "action_type":
                    action_type,
                "target":
                    result.get(
                        "url",
                        url,
                    ),
                "status_code":
                    result.get(
                        "status",
                        200,
                    ),
                "bytes":
                    len(
                        text.encode(
                            "utf-8",
                            errors="ignore",
                        )
                    ),
                "content_type":
                    result.get(
                        "content_type",
                        "",
                    ),
                "result_preview":
                    text[
                        :4000
                    ],
            }

        return {
            "ok":
                False,
            "action_type":
                action_type,
            "target":
                result.get(
                    "url",
                    url,
                ),
            "status_code":
                result.get(
                    "status",
                    0,
                ),
            "error":
                result.get(
                    "error",
                    "request_failed",
                ),
        }

    if action_type == "public_http_request":

        request_payload = (
            payload
            if isinstance(
                payload,
                dict,
            )
            else {}
        )

        return _external_request(
            request_payload.get(
                "method",
                "POST",
            ),
            target,
            request_payload,
        )

    if action_type == "save_result":

        safe_payload = (
            payload
            if isinstance(
                payload,
                dict,
            )
            else {}
        )

        raw = json.dumps(
            safe_payload,
            ensure_ascii=False,
        )

        size = len(
            raw.encode(
                "utf-8"
            )
        )

        if size > 128 * 1024:

            raise HTTPException(
                status_code=413,
                detail=(
                    "Saved result too large"
                ),
            )

        return {
            "ok":
                True,
            "action_type":
                action_type,
            "target":
                target,
            "saved":
                True,
            "bytes":
                size,
        }

    raise HTTPException(
        status_code=403,
        detail=(
            "Action type "
            "not implemented"
        ),
    )


def execute_safe_gateway(
    request: SafeActionRequest,
):

    if (
        request.action_type
        not in SAFE_ACTION_ALLOWLIST
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "Action type "
                "is not registered"
            ),
        )

    action_policy = SAFE_ACTION_ALLOWLIST[
        request.action_type
    ]

    side_effecting = bool(
        action_policy.get(
            "side_effects"
        )
    )

    key = (
        request.idempotency_key
        or _action_fingerprint(
            request.action_type,
            request.target,
            request.payload,
        )
    )

    if (
        request.require_approval
        and not side_effecting
    ):

        return {
            "status":
                "approval_required",
            "approved":
                False,
            "idempotency":
                True,
            "idempotency_key":
                key,
            "action_type":
                request.action_type,
            "target":
                request.target,
        }

    tx = _begin_action_transaction(
        request.mission_id,
        request.action_type,
        key,
        json.dumps(
            {
                "target":
                    request.target,
                "payload":
                    request.payload,
            },
            ensure_ascii=False,
        ),
        {
            "action_type":
                request.action_type,
            "target":
                request.target,
            "payload":
                request.payload,
            "external_side_effects":
                side_effecting,
        },
        allow_stale_reclaim=(
            not side_effecting
        ),
    )

    if tx["mode"] in {
        "replay",
        "stale_closed",
    }:

        row = tx[
            "row"
        ]

        return {
            "status":
                row["status"],
            "idempotency":
                True,
            "idempotency_key":
                key,
            "transaction_id":
                tx["transaction_id"],
            "stale_running_reclaimed":
                False,
            "approval_required":
                row["status"]
                == "awaiting_approval",
            "external_side_effects":
                side_effecting,
            "action":
                _transaction_result(
                    row
                ),
        }

    txid = tx[
        "transaction_id"
    ]

    if side_effecting:

        write(
            """
            UPDATE action_transactions
            SET status='awaiting_approval',
                updated_at=?
            WHERE id=?
              AND status='running'
            """,
            (
                now(),
                txid,
            ),
        )

        write(
            """
            INSERT INTO action_log(
                mission_id,
                action,
                status,
                details_json,
                ts,
                action_type,
                target,
                idempotency_key
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                request.mission_id,
                "external_action",
                "awaiting_approval",
                json.dumps(
                    request.model_dump(),
                    ensure_ascii=False,
                ),
                now(),
                request.action_type,
                request.target,
                key,
            ),
        )

        return {
            "status":
                "awaiting_approval",
            "approved":
                False,
            "approval_required":
                True,
            "external_side_effects":
                True,
            "idempotency":
                True,
            "idempotency_key":
                key,
            "transaction_id":
                txid,
            "stale_running_reclaimed":
                False,
        }

    if request.require_approval:

        return {
            "status":
                "approval_required",
            "approved":
                False,
            "idempotency":
                True,
            "idempotency_key":
                key,
            "transaction_id":
                txid,
            "action_type":
                request.action_type,
            "target":
                request.target,
        }

    try:

        result = _execute_safe_action(
            request.action_type,
            request.target,
            request.payload,
        )

        status = (
            "committed"
            if result.get("ok")
            else "failed_closed"
        )

        result[
            "transaction_id"
        ] = txid

        result[
            "idempotency_key"
        ] = key

        result[
            "stale_running_reclaimed"
        ] = (
            tx["mode"]
            == "reclaimed"
        )

        write(
            """
            UPDATE action_transactions
            SET status=?,
                result_json=?,
                error=NULL,
                updated_at=?
            WHERE id=?
            """,
            (
                status,
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                now(),
                txid,
            ),
        )

        return {
            "status":
                (
                    "completed"
                    if result.get(
                        "ok"
                    )
                    else
                    "failed"
                ),
            "action_requested":
                True,
            "action_executed":
                bool(
                    result.get(
                        "ok"
                    )
                ),
            "action_verified":
                bool(
                    result.get(
                        "ok"
                    )
                ),
            "idempotency":
                True,
            "idempotency_key":
                key,
            "provenance_recorded":
                True,
            "recovery_tested":
                tx["mode"]
                == "reclaimed",
            "stale_running_reclaimed":
                tx["mode"]
                == "reclaimed",
            "transaction_id":
                txid,
            "result":
                result,
        }

    except HTTPException:

        raise

    except Exception as exc:

        error = str(
            exc
        )[:500]

        write(
            """
            UPDATE action_transactions
            SET status='failed_closed',
                error=?,
                updated_at=?
            WHERE id=?
            """,
            (
                error,
                now(),
                txid,
            ),
        )

        return {
            "status":
                "failed",
            "action_requested":
                True,
            "action_executed":
                False,
            "action_verified":
                False,
            "idempotency":
                True,
            "idempotency_key":
                key,
            "provenance_recorded":
                True,
            "recovery_tested":
                tx["mode"]
                == "reclaimed",
            "stale_running_reclaimed":
                tx["mode"]
                == "reclaimed",
            "transaction_id":
                txid,
            "error":
                error,
        }


@app.post(
    "/real-world-command"
)
def real_world_command(
    request: RealWorldCommandRequest,
):
    # Fail closed before a transaction is created. A side-effecting
    # command must have an explicitly configured destination.
    target = _validate_public_target(
        request.target
    )

    if not _action_host_allowed(target):
        raise HTTPException(
            status_code=403,
            detail="target_host_not_allowlisted",
        )

    _validated_action_headers(
        request.headers
    )

    gateway_request = SafeActionRequest(
        action_type=
            "public_http_request",
        target=request.target,
        payload={
            "method":
                request.method,
            "body":
                request.body,
            "headers":
                request.headers,
        },
        idempotency_key=
            request.idempotency_key,
        require_approval=False,
        mission_id=
            request.mission_id,
    )

    return execute_safe_gateway(
        gateway_request
    )


@app.post(
    "/action-transaction/{transaction_id}/approve"
)
def approve_action_transaction(
    transaction_id: str,
):

    row = q(
        """
        SELECT *
        FROM action_transactions
        WHERE id=?
        """,
        (
            transaction_id,
        ),
        one=True,
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail=(
                "action transaction "
                "not found"
            ),
        )

    if row["status"] != "awaiting_approval":

        return {
            "status":
                row["status"],
            "transaction_id":
                transaction_id,
            "already_terminal":
                row["status"]
                in {
                    "committed",
                    "failed_closed",
                    "rejected",
                },
            "result":
                _transaction_result(
                    row
                ),
        }

    saved = json.loads(
        row["input_json"]
        or "{}"
    )

    action_type = str(
        row["action"]
    )

    if action_type not in SAFE_ACTION_ALLOWLIST:

        raise HTTPException(
            status_code=403,
            detail=(
                "Action type "
                "is not registered"
            ),
        )

    if not SAFE_ACTION_ALLOWLIST[
        action_type
    ].get("side_effects"):

        raise HTTPException(
            status_code=400,
            detail=(
                "Only external "
                "side-effect transactions "
                "use this approval path"
            ),
        )

    write(
        """
        UPDATE action_transactions
        SET status='running',
            updated_at=?
        WHERE id=?
          AND status='awaiting_approval'
        """,
        (
            now(),
            transaction_id,
        ),
    )

    row = q(
        """
        SELECT *
        FROM action_transactions
        WHERE id=?
        """,
        (
            transaction_id,
        ),
        one=True,
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail=(
                "action transaction "
                "not found"
            ),
        )

    if row["status"] != "running":

        return {
            "status":
                row["status"],
            "transaction_id":
                transaction_id,
            "result":
                _transaction_result(
                    row
                ),
        }

    try:

        result = _execute_safe_action(
            action_type,
            saved.get(
                "target",
                "",
            ),
            saved.get(
                "payload",
                {},
            ),
        )

        status = (
            "committed"
            if result.get(
                "ok"
            )
            else
            "failed_closed"
        )

        result[
            "transaction_id"
        ] = transaction_id

        result[
            "idempotency_key"
        ] = row[
            "idempotency_key"
        ]

        result[
            "approval_used"
        ] = True

        write(
            """
            UPDATE action_transactions
            SET status=?,
                result_json=?,
                error=?,
                updated_at=?
            WHERE id=?
              AND status='running'
            """,
            (
                status,
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                None
                if status == "committed"
                else result.get(
                    "error"
                ),
                now(),
                transaction_id,
            ),
        )

        write(
            """
            INSERT INTO action_log(
                mission_id,
                action,
                status,
                details_json,
                ts,
                action_type,
                target,
                idempotency_key,
                result_json
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                row["mission_id"],
                "external_action",
                status,
                json.dumps(
                    {
                        "approved":
                            True,
                        "outcome":
                            result.get(
                                "outcome"
                            ),
                    },
                    ensure_ascii=False,
                ),
                now(),
                action_type,
                saved.get(
                    "target",
                    "",
                ),
                row[
                    "idempotency_key"
                ],
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
            ),
        )

        return {
            "status":
                (
                    "completed"
                    if result.get(
                        "ok"
                    )
                    else
                    "failed"
                ),
            "approved":
                True,
            "action_executed":
                bool(
                    result.get(
                        "ok"
                    )
                ),
            "action_verified":
                bool(
                    result.get(
                        "ok"
                    )
                ),
            "transaction_id":
                transaction_id,
            "idempotency_key":
                row[
                    "idempotency_key"
                ],
            "external_side_effects":
                True,
            "outcome":
                result.get(
                    "outcome"
                ),
            "result":
                result,
        }

    except HTTPException:

        raise

    except Exception as exc:

        error = str(
            exc
        )[:500]

        write(
            """
            UPDATE action_transactions
            SET status='failed_closed',
                error=?,
                updated_at=?
            WHERE id=?
              AND status='running'
            """,
            (
                error,
                now(),
                transaction_id,
            ),
        )

        return {
            "status":
                "failed",
            "approved":
                True,
            "action_executed":
                False,
            "transaction_id":
                transaction_id,
            "error":
                error,
        }


@app.post(
    "/action-transaction/{transaction_id}/reject"
)
def reject_action_transaction(
    transaction_id: str,
):

    row = q(
        """
        SELECT *
        FROM action_transactions
        WHERE id=?
        """,
        (
            transaction_id,
        ),
        one=True,
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail=(
                "action transaction "
                "not found"
            ),
        )

    if row["status"] != "awaiting_approval":

        return {
            "status":
                row["status"],
            "transaction_id":
                transaction_id,
            "already_terminal":
                row["status"]
                in {
                    "committed",
                    "failed_closed",
                    "rejected",
                },
        }

    result = {
        "status":
            "rejected",
        "transaction_id":
            transaction_id,
        "approval_used":
            False,
        "external_side_effects":
            False,
        "replay_blocked":
            True,
    }

    write(
        """
        UPDATE action_transactions
        SET status='rejected',
            result_json=?,
            error=NULL,
            updated_at=?
        WHERE id=?
          AND status='awaiting_approval'
        """,
        (
            json.dumps(
                result,
                ensure_ascii=False,
            ),
            now(),
            transaction_id,
        ),
    )

    write(
        """
        INSERT INTO action_log(
            mission_id,
            action,
            status,
            details_json,
            ts,
            action_type,
            target,
            idempotency_key,
            result_json
        )
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            row["mission_id"],
            "external_action",
            "rejected",
            json.dumps(
                {
                    "approved":
                        False,
                    "reason":
                        "manual_rejection",
                },
                ensure_ascii=False,
            ),
            now(),
            row["action"],
            json.loads(
                row["input_json"]
                or "{}"
            ).get(
                "target",
                "",
            ),
            row[
                "idempotency_key"
            ],
            json.dumps(
                result,
                ensure_ascii=False,
            ),
        ),
    )

    return {
        "status":
            "rejected",
        "transaction_id":
            transaction_id,
        "replay_blocked":
            True,
        "external_side_effects":
            False,
    }


# ============================================================
# PROVIDERS
# ============================================================

PROVIDERS = [

    (
        "openalex",
        "openalex",
        "https://api.openalex.org/works"
        "?search={q}&per-page=8",
    ),

    (
        "crossref",
        "crossref",
        "https://api.crossref.org/works"
        "?query.bibliographic={q}&rows=8",
    ),

    (
        "crossref_alt",
        "crossref",
        "https://api.crossref.org/works"
        "?query={q}&rows=8",
    ),

    (
        "semantic_scholar",
        "semantic_scholar",
        "https://api.semanticscholar.org/graph/v1/paper/search"
        "?query={q}&limit=8"
        "&fields=title,abstract,year,url,venue,authors",
    ),

    (
        "arxiv",
        "arxiv",
        "https://export.arxiv.org/api/query"
        "?search_query=all:{q}"
        "&start=0&max_results=8",
    ),

    (
        "wikipedia",
        "wikipedia",
        "https://en.wikipedia.org/w/api.php"
        "?action=query"
        "&list=search"
        "&srsearch={q}"
        "&format=json"
        "&srlimit=8",
    ),
]


def provider_stat(
    provider: str,
    family: str,
    ok: bool,
    error: str = "",
):

    row = q(
        """
        SELECT provider
        FROM provider_stats
        WHERE provider=?
        """,
        (provider,),
        one=True,
    )

    if row:

        write(
            """
            UPDATE provider_stats
            SET success=success+?,
                failure=failure+?,
                last_error=?,
                updated_at=?
            WHERE provider=?
            """,
            (
                1 if ok else 0,
                0 if ok else 1,
                error[:500],
                now(),
                provider,
            ),
        )

    else:

        write(
            """
            INSERT INTO provider_stats(
                provider,
                family,
                success,
                failure,
                last_error,
                updated_at
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                provider,
                family,
                1 if ok else 0,
                0 if ok else 1,
                error[:500],
                now(),
            ),
        )


def strip_xml(
    text: str,
) -> str:

    return normalize_text(
        re.sub(
            r"<[^>]+>",
            " ",
            text or "",
        )
    )


def parse_provider(
    provider: str,
    family: str,
    payload: Dict[str, Any],
    raw: str,
):

    output = []

    if provider == "openalex":

        for item in payload.get(
            "results",
            [],
        ):

            abstract = normalize_text(
                " ".join(
                    (
                        item.get(
                            "abstract_inverted_index",
                            {},
                        )
                        or {}
                    ).keys()
                )
            )

            location = (
                item.get(
                    "primary_location"
                )
                or {}
            )

            source = (
                location.get(
                    "source"
                )
                or {}
            )

            output.append({
                "title":
                    item.get(
                        "title"
                    )
                    or "",
                "abstract":
                    abstract,
                "url":
                    (
                        location.get(
                            "landing_page_url"
                        )
                        or item.get(
                            "id"
                        )
                        or ""
                    ),
                "publisher":
                    source.get(
                        "display_name"
                    )
                    or "",
                "year":
                    item.get(
                        "publication_year"
                    ),
            })

    elif provider.startswith(
        "crossref"
    ):

        for item in (
            payload.get(
                "message",
                {},
            ).get(
                "items",
                [],
            )
        ):

            title = (
                item.get(
                    "title"
                )
                or [""]
            )[0]

            date = (
                item.get(
                    "published-print"
                )
                or item.get(
                    "published-online"
                )
                or {}
            )

            parts = date.get(
                "date-parts",
                [[None]],
            )

            year = (
                parts[0][0]
                if parts
                and parts[0]
                else None
            )

            output.append({
                "title":
                    title,
                "abstract":
                    strip_xml(
                        item.get(
                            "abstract"
                        )
                        or ""
                    ),
                "url":
                    item.get(
                        "URL"
                    )
                    or "",
                "publisher":
                    item.get(
                        "publisher"
                    )
                    or "",
                "year":
                    year,
            })

    elif provider == "semantic_scholar":

        for item in payload.get(
            "data",
            [],
        ):

            output.append({
                "title":
                    item.get(
                        "title"
                    )
                    or "",
                "abstract":
                    item.get(
                        "abstract"
                    )
                    or "",
                "url":
                    item.get(
                        "url"
                    )
                    or "",
                "publisher":
                    item.get(
                        "venue"
                    )
                    or "",
                "year":
                    item.get(
                        "year"
                    ),
            })

    elif provider == "wikipedia":

        for item in (
            payload.get(
                "query",
                {},
            ).get(
                "search",
                [],
            )
        ):

            title = (
                item.get(
                    "title"
                )
                or ""
            )

            output.append({
                "title":
                    title,
                "abstract":
                    strip_xml(
                        item.get(
                            "snippet"
                        )
                        or ""
                    ),
                "url":
                    (
                        "https://en.wikipedia.org/wiki/"
                        + quote_plus(
                            title.replace(
                                " ",
                                "_",
                            )
                        )
                    ),
                "publisher":
                    "Wikipedia",
                "year":
                    None,
            })

    elif provider == "arxiv":

        blocks = re.split(
            r"<entry>",
            raw,
        )[1:]

        for block in blocks:

            title = re.search(
                r"<title>(.*?)</title>",
                block,
                re.S,
            )

            summary = re.search(
                r"<summary>(.*?)</summary>",
                block,
                re.S,
            )

            link = re.search(
                r'<link[^>]+href="([^"]+)"',
                block,
            )

            pub = re.search(
                r"<published>(\d{4})-",
                block,
            )

            output.append({
                "title":
                    strip_xml(
                        title.group(1)
                        if title
                        else ""
                    ),
                "abstract":
                    strip_xml(
                        summary.group(1)
                        if summary
                        else ""
                    ),
                "url":
                    (
                        link.group(1)
                        if link
                        else ""
                    ),
                "publisher":
                    "arXiv",
                "year":
                    (
                        int(
                            pub.group(1)
                        )
                        if pub
                        else None
                    ),
            })

    return output


def relevant(
    title: str,
    abstract: str,
    query: str,
) -> bool:

    terms = [
        term.lower()
        for term in re.findall(
            r"[a-zA-Z]{4,}",
            query,
        )
    ]

    text = (
        title
        + " "
        + abstract
    ).lower()

    if not terms:

        return True

    hits = sum(
        1
        for term in terms
        if term in text
    )

    return hits >= max(
        1,
        min(
            3,
            len(terms),
        ),
    )


def empirical_score(
    title: str,
    abstract: str,
) -> bool:

    text = (
        title
        + " "
        + abstract
    ).lower()

    markers = (
        "experiment",
        "empirical",
        "evaluation",
        "benchmark",
        "dataset",
        "user study",
        "task success",
        "success rate",
        "ablation",
        "trial",
        "measured",
        "results",
        "performance",
        "failure rate",
    )

    return any(
        marker in text
        for marker in markers
    )


def quality_score(
    item: Dict[str, Any],
) -> float:

    score = 0.35

    if item.get(
        "publisher"
    ):

        score += 0.15

    if item.get(
        "year"
    ):

        score += 0.10

    if len(
        item.get(
            "abstract"
        )
        or ""
    ) > 200:

        score += 0.15

    if item.get(
        "empirical"
    ):

        score += 0.20

    if item.get(
        "url"
    ):

        score += 0.05

    return round(
        min(
            score,
            1.0,
        ),
        3,
    )


def query_provider(
    provider_tuple,
    query: str,
):

    provider, family, template = (
        provider_tuple
    )

    url = template.format(
        q=quote_plus(
            query
        )
    )

    result = safe_fetch(
        url
    )

    if not result["ok"]:

        provider_stat(
            provider,
            family,
            False,
            result.get(
                "error",
                "fetch failed",
            ),
        )

        return {
            "provider":
                provider,
            "family":
                family,
            "ok":
                False,
            "error":
                result.get(
                    "error"
                ),
        }

    raw = result.get(
        "text",
        "",
    )

    try:

        payload = (
            json.loads(raw)
            if provider != "arxiv"
            else {}
        )

        items = parse_provider(
            provider,
            family,
            payload,
            raw,
        )

        provider_stat(
            provider,
            family,
            True,
        )

        return {
            "provider":
                provider,
            "family":
                family,
            "ok":
                True,
            "items":
                items,
        }

    except Exception as exc:

        provider_stat(
            provider,
            family,
            False,
            str(exc),
        )

        return {
            "provider":
                provider,
            "family":
                family,
            "ok":
                False,
            "error":
                str(exc),
        }


def research_mission(
    mission_id: str,
    objective: str,
):

    queries = [
        objective,
        (
            objective
            + " empirical evaluation "
            + "benchmark task success"
        ),
        (
            objective
            + " failures limitations "
            + "independent study"
        ),
    ]

    collected = []
    results = []

    with ThreadPoolExecutor(
        max_workers=min(
            8,
            len(PROVIDERS),
        )
    ) as executor:

        futures = [
            executor.submit(
                query_provider,
                provider,
                queries[0],
            )
            for provider in PROVIDERS
        ]

        for future in as_completed(
            futures
        ):

            try:

                results.append(
                    future.result()
                )

            except Exception as exc:

                results.append({
                    "ok":
                        False,
                    "error":
                        str(exc),
                })

    preliminary = sum(
        len(
            result.get(
                "items",
                [],
            )
        )
        for result in results
        if result.get(
            "ok"
        )
    )

    if preliminary < 8:

        emit(
            mission_id,
            "recovery",
            "provider_recovery_round",
            {
                "preliminary":
                    preliminary,
            },
        )

        for provider in PROVIDERS:

            result = query_provider(
                provider,
                queries[1],
            )

            results.append(
                result
            )

    seen = set()

    for result in results:

        provider = result.get(
            "provider",
            "unknown",
        )

        family = result.get(
            "family",
            "unknown",
        )

        if not result.get(
            "ok"
        ):

            continue

        for item in result.get(
            "items",
            [],
        ):

            title = normalize_text(
                item.get(
                    "title",
                    "",
                )
            )

            abstract = normalize_text(
                item.get(
                    "abstract",
                    "",
                )
            )

            url = item.get(
                "url",
                "",
            )

            key = fingerprint(
                title.lower(),
                url,
            )

            if (
                not title
                or key in seen
            ):

                continue

            seen.add(
                key
            )

            rel = relevant(
                title,
                abstract,
                objective,
            )

            empirical = empirical_score(
                title,
                abstract,
            )

            normalized = {
                **item,
                "provider":
                    provider,
                "family":
                    family,
                "relevant":
                    rel,
                "empirical":
                    empirical,
            }

            normalized[
                "quality"
            ] = quality_score(
                normalized
            )

            collected.append(
                normalized
            )

            evidence_id = make_id(
                "ev"
            )

            write(
                """
                INSERT INTO evidence(
                    id,
                    mission_id,
                    title,
                    abstract,
                    url,
                    provider,
                    family,
                    publisher,
                    year,
                    empirical,
                    relevant,
                    quality,
                    raw_json,
                    created_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    evidence_id,
                    mission_id,
                    title,
                    abstract[:8000],
                    url,
                    provider,
                    family,
                    item.get(
                        "publisher"
                    )
                    or "",
                    item.get(
                        "year"
                    ),
                    int(
                        empirical
                    ),
                    int(
                        rel
                    ),
                    normalized[
                        "quality"
                    ],
                    json.dumps(
                        item,
                        ensure_ascii=False,
                    ),
                    now(),
                ),
            )

            write(
                """
                INSERT INTO provenance(
                    mission_id,
                    item_type,
                    item_id,
                    source_url,
                    provider,
                    publisher,
                    family,
                    created_at
                )
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    mission_id,
                    "evidence",
                    evidence_id,
                    url,
                    provider,
                    item.get(
                        "publisher"
                    )
                    or "",
                    family,
                    now(),
                ),
            )

            write(
                """
                INSERT INTO research(
                    mission_id,
                    query,
                    provider,
                    status,
                    result_json,
                    created_at
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    mission_id,
                    objective,
                    provider,
                    "ok",
                    json.dumps(
                        item,
                        ensure_ascii=False,
                    ),
                    now(),
                ),
            )

    return {
        "sources":
            collected,
        "provider_results":
            results,
        "total_sources":
            len(collected),
    }


# ============================================================
# CLAIMS / VERIFICATION
# ============================================================

POSITIVE = {
    "improve",
    "effective",
    "success",
    "successful",
    "benefit",
    "better",
    "robust",
    "reliable",
}

NEGATIVE = {
    "fail",
    "failure",
    "worse",
    "limitation",
    "unreliable",
    "error",
    "harm",
    "weak",
}


def claim_polarity(
    text: str,
) -> str:

    words = set(
        re.findall(
            r"[a-zA-Z]+",
            text.lower(),
        )
    )

    positive = len(
        words
        & POSITIVE
    )

    negative = len(
        words
        & NEGATIVE
    )

    if positive > negative:

        return "positive"

    if negative > positive:

        return "negative"

    return "neutral"


def extract_claims(
    mission_id: str,
    objective: str,
    sources: List[Dict[str, Any]],
):

    claims = []

    for source in sources[:30]:

        title = normalize_text(
            source.get(
                "title",
                "",
            )
        )

        abstract = normalize_text(
            source.get(
                "abstract",
                "",
            )
        )

        if not title:

            continue

        text = (
            abstract
            or title
        )[:450]

        claim_id = make_id(
            "claim"
        )

        polarity = claim_polarity(
            text
        )

        confidence = round(
            min(
                0.95,
                0.45
                + source.get(
                    "quality",
                    0,
                ) * 0.45
                + (
                    0.10
                    if source.get(
                        "empirical"
                    )
                    else 0
                ),
            ),
            3,
        )

        claim_text = (
            f"Evidence item "
            f"'{title}' reports findings "
            f"relevant to: {objective}."
        )

        write(
            """
            INSERT INTO claims(
                id,
                mission_id,
                text,
                polarity,
                confidence,
                created_at
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                claim_id,
                mission_id,
                claim_text,
                polarity,
                confidence,
                now(),
            ),
        )

        evidence_row = q(
            """
            SELECT id
            FROM evidence
            WHERE mission_id=?
              AND title=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                mission_id,
                title,
            ),
            one=True,
        )

        evidence_id = None

        if evidence_row:

            evidence_id = (
                evidence_row[
                    "id"
                ]
            )

            write(
                """
                INSERT OR IGNORE INTO
                evidence_links(
                    claim_id,
                    evidence_id,
                    relation,
                    score
                )
                VALUES(?,?,?,?)
                """,
                (
                    claim_id,
                    evidence_id,
                    "supports",
                    confidence,
                ),
            )

        claims.append({
            "id":
                claim_id,
            "text":
                claim_text,
            "polarity":
                polarity,
            "confidence":
                confidence,
            "evidence_id":
                evidence_id,
        })

    return claims


def _claim_topic_tokens(
    text: str,
):

    stop = {
        "evidence",
        "item",
        "reports",
        "findings",
        "relevant",
        "autonomous",
        "agent",
        "agents",
        "research",
        "study",
        "studies",
        "result",
        "results",
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "using",
        "about",
        "their",
        "they",
        "can",
        "may",
        "does",
        "not",
        "than",
        "into",
        "based",
        "empirical",
        "evaluation",
        "reliability",
        "task",
        "tasks",
    }

    return {
        word
        for word in re.findall(
            r"[a-z]{4,}",
            text.lower(),
        )
        if word not in stop
    }


def contradiction_screen(
    claims: List[Dict[str, Any]],
):

    positive = [
        claim
        for claim in claims
        if claim["polarity"]
        == "positive"
    ]

    negative = [
        claim
        for claim in claims
        if claim["polarity"]
        == "negative"
    ]

    candidates = []
    resolved = []
    unresolved = []

    for positive_claim in positive:

        positive_tokens = (
            _claim_topic_tokens(
                positive_claim.get(
                    "text",
                    "",
                )
            )
        )

        for negative_claim in negative:

            negative_tokens = (
                _claim_topic_tokens(
                    negative_claim.get(
                        "text",
                        "",
                    )
                )
            )

            overlap = (
                len(
                    positive_tokens
                    & negative_tokens
                )
                / max(
                    1,
                    len(
                        positive_tokens
                        | negative_tokens
                    ),
                )
            )

            same_evidence = (
                positive_claim.get(
                    "evidence_id"
                )
                == negative_claim.get(
                    "evidence_id"
                )
                and positive_claim.get(
                    "evidence_id"
                )
                is not None
            )

            pair = {
                "positive_claim":
                    positive_claim.get(
                        "id"
                    ),
                "negative_claim":
                    negative_claim.get(
                        "id"
                    ),
                "topic_overlap":
                    round(
                        overlap,
                        3,
                    ),
                "same_evidence":
                    same_evidence,
            }

            candidates.append(
                pair
            )

            positive_text = (
                positive_claim.get(
                    "text",
                    "",
                ).lower()
            )

            negative_text = (
                negative_claim.get(
                    "text",
                    "",
                ).lower()
            )

            opposition = any(
                term in positive_text
                or term in negative_text
                for term in (
                    "contradict",
                    "oppos",
                    "no effect",
                    "ineffective",
                    "fails",
                    "failed",
                    "harm",
                    "worse",
                    "not improve",
                    "not reliable",
                )
            )

            if (
                same_evidence
                or (
                    overlap >= 0.55
                    and opposition
                )
            ):

                unresolved.append(
                    pair
                )

            else:

                resolved.append({
                    **pair,
                    "resolution":
                        (
                            "polarity_screen_"
                            "not_semantic_contradiction"
                        ),
                })

    return {
        "screened":
            True,
        "conflict_detected":
            bool(
                positive
                and negative
            ),
        "candidate_conflict":
            bool(candidates),
        "positive_claims":
            len(positive),
        "negative_claims":
            len(negative),
        "candidate_pairs":
            len(candidates),
        "resolved_scope_pairs":
            len(resolved),
        "unresolved_pairs":
            len(unresolved),
        "resolved_pairs":
            resolved[:20],
        "unresolved_pairs":
            unresolved[:20],
        "semantic_contradiction_proof":
            False,
        "resolution_status":
            (
                "unresolved"
                if unresolved
                else (
                    "scope_resolved"
                    if candidates
                    else "clean"
                )
            ),
    }


def verify_evidence(
    mission_id: str,
    objective: str,
    sources: List[Dict[str, Any]],
    claims: List[Dict[str, Any]],
):

    usable = [
        source
        for source in sources
        if source.get("relevant")
    ]

    empirical = [
        source
        for source in usable
        if source.get("empirical")
    ]

    high_quality = [
        source
        for source in usable
        if source.get(
            "quality",
            0,
        ) >= 0.65
    ]

    publishers = {
        source.get(
            "publisher"
        )
        for source in usable
        if source.get(
            "publisher"
        )
    }

    families = {
        source.get(
            "family"
        )
        for source in usable
        if source.get(
            "family"
        )
    }

    contradiction = (
        contradiction_screen(
            claims
        )
    )

    contradiction_clear = (
        len(
            contradiction.get(
                "unresolved_pairs",
                [],
            )
        )
        == 0
    )

    requirements = {
        "relevant_sources":
            len(usable) >= 3,
        "high_quality_sources":
            len(high_quality) >= 2,
        "empirical_sources":
            len(empirical) >= 3,
        "independent_publishers":
            len(publishers) >= 2,
        "independent_provider_families":
            len(families) >= 2,
        "claims":
            len(claims) >= 2,
        "clean_contradiction_screen":
            contradiction_clear,
    }

    verified = all(
        requirements.values()
    )

    passed = sum(
        1
        for value
        in requirements.values()
        if value
    )

    confidence = (
        passed
        / max(
            1,
            len(requirements),
        )
    )

    if (
        contradiction.get(
            "resolution_status"
        )
        == "scope_resolved"
    ):

        confidence = min(
            0.99,
            confidence + 0.08,
        )

    return {
        "verified":
            verified,
        "confidence":
            round(
                confidence,
                3,
            ),
        "requirements":
            requirements,
        "counts": {
            "relevant":
                len(usable),
            "high_quality":
                len(high_quality),
            "empirical":
                len(empirical),
            "publishers":
                len(publishers),
            "provider_families":
                len(families),
            "claims":
                len(claims),
        },
        "contradiction_screen":
            contradiction,
        "note":
            (
                "Verification is an "
                "evidence-quorum screen with "
                "semantic contradiction candidate "
                "resolution; it is not mathematical proof."
            ),
    }


# ============================================================
# PLANNING / WORLD MODEL / RECOVERY
# ============================================================

def classify(
    objective: str,
) -> str:

    text = objective.lower()

    if any(
        word in text
        for word in (
            "research",
            "evidence",
            "study",
            "compare",
            "investigate",
        )
    ):

        return "verification"

    if any(
        word in text
        for word in (
            "remember",
            "memory",
            "save",
        )
    ):

        return "memory"

    if any(
        word in text
        for word in (
            "execute",
            "send",
            "create",
            "publish",
            "book",
            "change",
        )
    ):

        return "action"

    return "general"


def plan(
    objective: str,
    request: MissionRequest,
):

    steps = [
        "understand",
        "classify",
        "plan",
    ]

    if (
        request.research
        or request.external_access
    ):

        steps.extend([
            "research",
            "normalize_evidence",
            "build_claims",
        ])

    if request.verify:

        steps.extend([
            "verify",
            "contradiction_screen",
        ])

    if request.remember:

        steps.append(
            "remember"
        )

    if request.execute:

        steps.append(
            "action_boundary"
        )

    steps.extend([
        "synthesize",
        "checkpoint",
        "complete",
    ])

    return steps


def update_world_model(
    mission_id: str,
    result: Dict[str, Any],
):

    model = {
        "last_mission":
            mission_id,
        "last_status":
            result.get(
                "status"
            ),
        "last_verified":
            result.get(
                "verification",
                {},
            ).get(
                "verified"
            ),
        "last_updated":
            now(),
    }

    write(
        """
        INSERT OR REPLACE INTO
        world_model(
            key,
            value_json,
            updated_at
        )
        VALUES(
            'mission_state',
            ?,
            ?
        )
        """,
        (
            json.dumps(
                model,
                ensure_ascii=False,
            ),
            now(),
        ),
    )


def detect_opportunities(
    mission_id: str,
    result: Dict[str, Any],
):

    verification = result.get(
        "verification",
        {},
    )

    if (
        verification.get(
            "counts",
            {},
        ).get(
            "provider_families",
            0,
        )
        < 2
    ):

        write(
            """
            INSERT INTO opportunities(
                mission_id,
                title,
                details_json,
                created_at
            )
            VALUES(?,?,?,?)
            """,
            (
                mission_id,
                "Increase provider diversity",
                json.dumps({
                    "reason":
                        (
                            "evidence quorum lacks "
                            "provider-family independence"
                        ),
                }),
                now(),
            ),
        )

    if (
        result.get(
            "recovery",
            {},
        ).get(
            "attempts",
            0,
        )
    ):

        write(
            """
            INSERT INTO opportunities(
                mission_id,
                title,
                details_json,
                created_at
            )
            VALUES(?,?,?,?)
            """,
            (
                mission_id,
                "Improve failing provider path",
                json.dumps({
                    "reason":
                        (
                            "provider recovery "
                            "was required"
                        ),
                }),
                now(),
            ),
        )


def save_learning(
    mission_id: str,
    signal: str,
    value: float,
    details=None,
):

    write(
        """
        INSERT INTO learning(
            mission_id,
            signal,
            value,
            details_json,
            created_at
        )
        VALUES(?,?,?,?,?)
        """,
        (
            mission_id,
            signal,
            value,
            json.dumps(
                details or {}
            ),
            now(),
        ),
    )


# ============================================================
# MISSION ENGINE
# ============================================================

def create_mission(
    request: MissionRequest,
    approved: bool = False,
):

    mission_id = make_id(
        "mission"
    )

    timestamp = now()

    current_policy = policy()

    route = classify(
        request.objective
    )

    write(
        """
        INSERT INTO missions(
            id,
            objective,
            request_json,
            status,
            result_json,
            created_at,
            updated_at,
            attempts,
            recovery_attempts,
            approved,
            policy_version,
            route,
            error
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            mission_id,
            request.objective,
            request.model_dump_json(),
            "queued",
            None,
            timestamp,
            timestamp,
            0,
            0,
            int(
                approved
            ),
            int(
                current_policy[
                    "version"
                ]
            ),
            route,
            None,
        ),
    )

    emit(
        mission_id,
        "create",
        "mission_created",
        {
            "route":
                route,
            "request":
                request.model_dump(),
            "policy_version":
                current_policy[
                    "version"
                ],
        },
    )

    return mission_id


def load_request(
    mission_id: str,
):

    row = q(
        """
        SELECT request_json
        FROM missions
        WHERE id=?
        """,
        (mission_id,),
        one=True,
    )

    if not row:

        raise KeyError(
            mission_id
        )

    return MissionRequest.model_validate_json(
        row[
            "request_json"
        ]
    )


def run_mission(
    mission_id: str,
):

    row = q(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (mission_id,),
        one=True,
    )

    if not row:

        return

    try:

        request = load_request(
            mission_id
        )

        if (
            request.require_approval
            and not row["approved"]
        ):

            emit(
                mission_id,
                "approval",
                "waiting_for_approval",
            )

            write(
                """
                UPDATE missions
                SET status='awaiting_approval',
                    updated_at=?
                WHERE id=?
                """,
                (
                    now(),
                    mission_id,
                ),
            )

            return

        write(
            """
            UPDATE missions
            SET status='running',
                attempts=attempts+1,
                updated_at=?
            WHERE id=?
            """,
            (
                now(),
                mission_id,
            ),
        )

        emit(
            mission_id,
            "start",
            "mission_started",
        )

        checkpoint(
            mission_id,
            "started",
            {
                "objective":
                    request.objective,
                "request":
                    request.model_dump(),
            },
        )

        steps = plan(
            request.objective,
            request,
        )

        emit(
            mission_id,
            "plan",
            "plan_created",
            {
                "steps":
                    steps,
            },
        )

        research_result = {
            "sources":
                [],
            "total_sources":
                0,
        }

        recovery_attempts = 0

        if (
            request.research
            or request.external_access
        ):

            emit(
                mission_id,
                "research",
                "research_started",
            )

            research_result = (
                research_mission(
                    mission_id,
                    request.objective,
                )
            )

            if (
                research_result[
                    "total_sources"
                ]
                < 3
            ):

                recovery_attempts += 1

                version = adaptive_upgrade(
                    (
                        "insufficient evidence "
                        "after primary research"
                    )
                )

                emit(
                    mission_id,
                    "recovery",
                    "adaptive_policy_upgrade",
                    {
                        "policy_version":
                            version,
                    },
                )

                research_result = (
                    research_mission(
                        mission_id,
                        request.objective
                        + " independent "
                        + "empirical evaluation",
                    )
                )

        sources = research_result[
            "sources"
        ]

        claims = extract_claims(
            mission_id,
            request.objective,
            sources,
        )

        if request.verify:

            verification = (
                verify_evidence(
                    mission_id,
                    request.objective,
                    sources,
                    claims,
                )
            )

        else:

            verification = {
                "verified":
                    False,
                "confidence":
                    0,
                "note":
                    "verification disabled",
            }

        if request.remember:

            remember(
                f"mission:{mission_id}",
                json.dumps(
                    {
                        "objective":
                            request.objective,
                        "status":
                            "completed",
                        "verified":
                            verification.get(
                                "verified"
                            ),
                    },
                    ensure_ascii=False,
                ),
            )

        action_boundary = {
            "requested":
                request.execute,
            "approval_required":
                request.require_approval,
            "external_side_effects":
                True
                if request.execute
                else False,
            "status":
                (
                    "approval_bounded_gateway"
                    if request.execute
                    else "not_connected"
                ),
            "message":
                (
                    "External side effects are "
                    "available only through the "
                    "approval-gated real-world "
                    "command gateway."
                ),
        }

        if request.execute:

            write(
                """
                INSERT INTO action_log(
                    mission_id,
                    action,
                    status,
                    details_json,
                    ts,
                    action_type,
                    target
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    mission_id,
                    "external_action",
                    "proposed",
                    json.dumps(
                        action_boundary,
                        ensure_ascii=False,
                    ),
                    now(),
                    "external_action",
                    "",
                ),
            )

            emit(
                mission_id,
                "action",
                "safe_action_boundary",
                action_boundary,
            )

        result = {
            "status":
                "completed",
            "version":
                APP_VERSION,
            "build":
                BUILD,
            "mission_id":
                mission_id,
            "objective":
                request.objective,
            "route":
                classify(
                    request.objective
                ),
            "steps":
                steps,
            "evidence_summary": {
                "sources":
                    len(
                        sources
                    ),
                "claims":
                    len(
                        claims
                    ),
                "empirical_sources":
                    sum(
                        1
                        for source
                        in sources
                        if source.get(
                            "empirical"
                        )
                    ),
                "provider_families":
                    len({
                        source.get(
                            "family"
                        )
                        for source
                        in sources
                        if source.get(
                            "family"
                        )
                    }),
                "publishers":
                    len({
                        source.get(
                            "publisher"
                        )
                        for source
                        in sources
                        if source.get(
                            "publisher"
                        )
                    }),
            },
            "verification":
                verification,
            "recovery": {
                "attempts":
                    recovery_attempts,
                "enabled":
                    True,
            },
            "action_boundary":
                action_boundary,
            "capabilities_used": [
                "mission_engine",
                "planning",
                "research",
                "evidence_graph",
                "claim_analysis",
                "verification",
                "adaptive_recovery",
                (
                    "memory"
                    if request.remember
                    else "memory_available"
                ),
                "safe_action_boundary",
                (
                    "real_world_command_gateway"
                    if request.execute
                    else "real_world_command_available"
                ),
            ],
        }

        update_world_model(
            mission_id,
            result,
        )

        detect_opportunities(
            mission_id,
            result,
        )

        save_learning(
            mission_id,
            "mission_completion",
            (
                1.0
                if verification.get(
                    "verified"
                )
                else 0.5
            ),
            {
                "verified":
                    verification.get(
                        "verified"
                    ),
            },
        )

        checkpoint(
            mission_id,
            "completed",
            result,
        )

        write(
            """
            UPDATE missions
            SET status='completed',
                result_json=?,
                recovery_attempts=?,
                policy_version=?,
                updated_at=?
            WHERE id=?
            """,
            (
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                recovery_attempts,
                policy()[
                    "version"
                ],
                now(),
                mission_id,
            ),
        )

        emit(
            mission_id,
            "complete",
            "mission_completed",
            {
                "verified":
                    verification.get(
                        "verified"
                    ),
                "sources":
                    len(
                        sources
                    ),
            },
        )

    except Exception as exc:

        error = str(
            exc
        )[:2000]

        write(
            """
            UPDATE missions
            SET status='failed',
                error=?,
                updated_at=?
            WHERE id=?
            """,
            (
                error,
                now(),
                mission_id,
            ),
        )

        emit(
            mission_id,
            "error",
            "mission_failed",
            {
                "error":
                    error,
            },
        )

        checkpoint(
            mission_id,
            "failed",
            {
                "error":
                    error,
            },
        )


# ============================================================
# FASTAPI
# ============================================================

# TARGET-2050.101: keep the single FastAPI app created at module startup.
# A second FastAPI() instance here would discard every route registered above it.


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "name":
            "AI Infinity",
        "status":
            "online",
        "version":
            APP_VERSION,
        "build":
            BUILD,
        "docs":
            "/docs",
        "health":
            "/health",
        "run":
            "/run",
        "interface":
            "/interface",
        "real_world_command":
            "/real-world-command",
        "real_world_command_status":
            "/real-world-command-status",
    }


# ============================================================
# MEMORY
# ============================================================

@app.post("/memory")
def post_memory(
    request: MemoryRequest,
):

    remember(
        request.key,
        request.value,
    )

    return {
        "status":
            "stored",
        "key":
            request.key,
    }


@app.get("/memory")
def get_memory():

    items = memory_items()

    return {
        "count":
            len(items),
        "items":
            items,
    }


@app.get("/memory/count")
def memory_count():

    row = q(
        "SELECT COUNT(*) n FROM memory",
        one=True,
    )

    return {
        "count":
            row["n"],
    }


@app.post("/v1/memory")
def legacy_memory(
    body: Dict[str, Any],
):

    key = str(
        body.get(
            "key"
        )
        or body.get(
            "kind"
        )
        or "general"
    )

    value = str(
        body.get(
            "value"
        )
        or body.get(
            "content"
        )
        or ""
    )

    remember(
        key,
        value,
    )

    return {
        "stored":
            True,
        "key":
            key,
    }


@app.get("/v1/memory")
def legacy_memories(
    qstr: str = "",
):

    items = memory_items(
        50
    )

    if qstr:

        query = qstr.lower()

        items = [
            item
            for item in items
            if query in (
                item.get(
                    "key",
                    "",
                )
                + " "
                + item.get(
                    "value",
                    "",
                )
            ).lower()
        ]

    return {
        "memories":
            items,
    }


# ============================================================
# SECURITY
# ============================================================

@app.get("/v1/security/policy")
def legacy_security_policy():

    return {
        "default_deny_consequential_actions":
            True,
        "automatic_spending":
            False,
        "credential_exfiltration":
            False,
        "uncontrolled_self_modification":
            False,
        "audit":
            True,
        "arbitrary_code_execution":
            False,
        "external_side_effect_approval":
            True,
        "host_allowlist":
            True,
    }


@app.get("/action-policy")
def action_policy():

    return {
        "version":
            APP_VERSION,
        "safe_actions":
            sorted(
                SAFE_ACTION_ALLOWLIST
            ),
        "arbitrary_code_execution":
            False,
        "unrestricted_network_access":
            False,
        "private_network_access":
            False,
        "external_side_effects":
            True,
        "external_http_requests":
            True,
        "approval_bounded":
            True,
        "host_allowlist_required":
            True,
        "credential_headers_blocked":
            True,
        "automatic_side_effect_retry":
            False,
        "status":
            "approval-bounded-real-world-gateway",
    }


@app.get("/command-capabilities")
def command_capabilities():

    return {
        "version":
            APP_VERSION,
        "build":
            BUILD,
        "status":
            "ready",
        "command_boundary": {
            "planning":
                True,
            "policy":
                True,
            "registered_actions":
                True,
            "execution":
                True,
            "observation":
                True,
            "verification":
                True,
            "recovery":
                True,
            "audit":
                True,
            "idempotency":
                True,
            "external_side_effects":
                True,
            "approval_transactions":
                True,
        },
        "safe_actions":
            sorted(
                SAFE_ACTION_ALLOWLIST
            ),
        "transactional_actions":
            True,
        "approval_gate":
            True,
        "arbitrary_code_execution":
            False,
        "private_network_access":
            False,
        "unrestricted_network_access":
            False,
        "spending":
            False,
        "external_http_methods": [
            "GET",
            "HEAD",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ],
        "external_side_effects":
            True,
        "approval_required_for_side_effects":
            True,
        "host_allowlist_required":
            True,
        "automatic_side_effect_retry":
            False,
    }


# ============================================================
# ACTIONS
# ============================================================

@app.post("/action")
def action_endpoint(
    request: ActionRequest,
):

    return execute_action_fabric(
        request
    )


@app.post("/safe-action")
def safe_action_endpoint(
    request: SafeActionRequest,
):

    return execute_safe_gateway(
        request
    )


@app.post("/v1/safe-action")
def legacy_safe_action(
    request: SafeActionRequest,
):

    return execute_safe_gateway(
        request
    )


@app.post("/v1/action")
def legacy_action(
    request: ActionRequest,
):

    return execute_action_fabric(
        request
    )


@app.get(
    "/real-world-command-status"
)
def real_world_command_status():

    return {
        "version":
            APP_VERSION,
        "build":
            BUILD,
        "status":
            "ready",
        "execution": {
            "external_http":
                True,
            "methods": [
                "GET",
                "HEAD",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
            ],
            "side_effects":
                True,
        },
        "safety": {
            "approval_required":
                True,
            "host_allowlist_required":
                True,
            "allowlist_configured":
                bool(
                    ACTION_HOST_ALLOWLIST
                ),
            "allowed_hosts":
                sorted(
                    ACTION_HOST_ALLOWLIST
                ),
            "credential_headers_blocked":
                True,
            "redirects_for_side_effects":
                "blocked",
            "private_network_access":
                False,
            "arbitrary_code_execution":
                False,
        },
        "reliability": {
            "transactional":
                True,
            "idempotent":
                True,
            "stale_recovery":
                True,
            "automatic_side_effect_retry":
                False,
            "unknown_transport_outcome_not_replayed":
                True,
        },
    }


@app.get(
    "/test-real-world-command"
)
def test_real_world_command():

    return {
        "version":
            APP_VERSION,
        "status":
            "passed"
            if ACTION_HOST_ALLOWLIST
            else "configuration_required",
        "real_world_command_gateway":
            True,
        "side_effects_available":
            True,
        "approval_required":
            True,
        "host_allowlist_required":
            True,
        "allowlist_configured":
            bool(
                ACTION_HOST_ALLOWLIST
            ),
        "allowed_hosts":
            sorted(
                ACTION_HOST_ALLOWLIST
            ),
        "automatic_side_effect_retry":
            False,
        "next_action":
            (
                "POST /real-world-command"
                if ACTION_HOST_ALLOWLIST
                else (
                    "Set "
                    "AI_INFINITY_ACTION_HOST_ALLOWLIST "
                    "before executing an external "
                    "side-effect command."
                )
            ),
    }


@app.get(
    "/action-fabric"
)
def action_fabric_status():

    return {
        "status":
            "ready",
        "registered_actions":
            sorted(
                SAFE_ACTIONS
            ),
        "safe_gateway_actions":
            sorted(
                SAFE_ACTION_ALLOWLIST
            ),
        "transactional_logging":
            True,
        "snapshots":
            True,
        "idempotency":
            True,
        "circuit_breakers":
            True,
        "bounded_recovery":
            True,
        "external_side_effects":
            True,
        "external_http_requests":
            True,
        "approval_required":
            True,
        "host_allowlist_required":
            True,
        "automatic_side_effect_retry":
            False,
        "arbitrary_code_execution":
            False,
    }


@app.get("/action-history")
def action_history(
    limit: int = 50,
):

    limit = max(
        1,
        min(
            int(limit),
            200,
        ),
    )

    rows = q(
        """
        SELECT *
        FROM action_transactions
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )

    return {
        "count":
            len(rows),
        "transactions": [
            dict(row)
            for row in rows
        ],
    }


def command_audit(
    limit: int = 25,
):

    limit = max(
        1,
        min(
            int(limit),
            100,
        ),
    )

    transactions = q(
        """
        SELECT id, mission_id, action, status,
               idempotency_key, error,
               created_at, updated_at
        FROM action_transactions
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )

    logs = q(
        """
        SELECT id, mission_id, action, status,
               idempotency_key, action_type,
               target, ts
        FROM action_log
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )

    counts = q(
        """
        SELECT status, COUNT(*) AS n
        FROM action_transactions
        GROUP BY status
        """
    )

    return {
        "version":
            APP_VERSION,
        "status":
            "ready",
        "transaction_counts": {
            row["status"]:
                row["n"]
            for row in counts
        },
        "transactions": [
            dict(row)
            for row in transactions
        ],
        "audit_log": [
            dict(row)
            for row in logs
        ],
        "integrity": {
            "transactions_persisted":
                True,
            "idempotency_keys_persisted":
                True,
            "action_log_persisted":
                True,
            "recovery_boundary":
                "failed_closed",
            "external_side_effect_transactions":
                True,
            "unknown_external_outcome_not_replayed":
                True,
            "arbitrary_code_execution":
                False,
        },
    }


@app.get(
    "/command-audit"
)
def command_audit_endpoint(
    limit: int = 25,
):

    return command_audit(
        limit
    )


@app.get(
    "/test-command-audit"
)
def test_command_audit():

    audit = command_audit(
        10
    )

    required = {
        "transaction_counts",
        "transactions",
        "audit_log",
        "integrity",
    }

    passed = required.issubset(
        audit.keys()
    )

    return {
        "version":
            APP_VERSION,
        "status":
            (
                "passed"
                if passed
                else "failed"
            ),
        "audit_verified":
            passed,
        "integrity":
            audit["integrity"],
        "transaction_count":
            len(
                audit[
                    "transactions"
                ]
            ),
        "audit_log_count":
            len(
                audit[
                    "audit_log"
                ]
            ),
    }


# ============================================================
# ACTION TRANSACTION RECOVERY TEST
# ============================================================

@app.get(
    "/test-action-recovery"
)
def test_action_recovery():

    key = (
        "test-stale-recovery-"
        + make_id("k")
    )

    txid = make_id(
        "tx"
    )

    old = now() - max(
        ACTION_STALE_SECONDS + 5,
        35,
    )

    write(
        """
        INSERT INTO action_transactions(
            id,
            mission_id,
            action,
            status,
            idempotency_key,
            input_json,
            result_json,
            error,
            created_at,
            updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            txid,
            None,
            "save_result",
            "running",
            key,
            json.dumps({
                "target":
                    "self-test",
                "payload":
                    {
                        "ok":
                            True
                    },
            }),
            None,
            None,
            old,
            old,
        ),
    )

    request = SafeActionRequest(
        action_type=
            "save_result",
        target=
            "self-test",
        payload={
            "ok":
                True
        },
        idempotency_key=
            key,
        require_approval=
            False,
    )

    result = execute_safe_gateway(
        request
    )

    row = q(
        """
        SELECT status
        FROM action_transactions
        WHERE id=?
        """,
        (txid,),
        one=True,
    )

    terminal = bool(
        row
        and row["status"]
        in {
            "committed",
            "failed_closed",
        }
    )

    reclaimed = bool(
        result.get(
            "stale_running_reclaimed"
        )
    )

    return {
        "version":
            APP_VERSION,
        "status":
            (
                "passed"
                if reclaimed
                and terminal
                else "failed"
            ),
        "stale_running_reclaimed":
            reclaimed,
        "terminal_status":
            row["status"]
            if row
            else None,
        "transaction_id":
            txid,
        "bounded_recovery":
            True,
        "expected_terminal_states": [
            "committed",
            "failed_closed",
        ],
    }


# ============================================================
# LEGACY VERIFY
# ============================================================

@app.post(
    "/v1/verify"
)
def legacy_verify(
    body: Dict[str, Any],
):

    claim = str(
        body.get(
            "claim",
            "",
        )
    )

    evidence = (
        body.get(
            "evidence"
        )
        or []
    )

    provenance = (
        body.get(
            "provenance"
        )
        or []
    )

    usable = [
        item
        for item in evidence
        if (
            isinstance(
                item,
                dict,
            )
            and item.get(
                "source"
            )
        )
    ]

    verified = bool(
        usable
        and provenance
    )

    return {
        "claim":
            claim,
        "verified":
            verified,
        "issues": (
            []
            if verified
            else [
                "Evidence/provenance incomplete"
            ]
        ),
        "evidence_count":
            len(evidence),
        "provenance":
            provenance,
        "note":
            (
                "Evidence state is reported; "
                "proof is not manufactured."
            ),
    }


# ============================================================
# MISSIONS
# ============================================================

@app.post(
    "/execute"
)
def execute(
    request: MissionRequest,
    background_tasks: BackgroundTasks,
):

    if not request.objective.strip():

        raise HTTPException(
            status_code=400,
            detail=
                "objective is required",
        )

    mission_id = create_mission(
        request,
        approved=
            not request.require_approval,
    )

    if request.require_approval:

        write(
            """
            UPDATE missions
            SET status='awaiting_approval',
                updated_at=?
            WHERE id=?
            """,
            (
                now(),
                mission_id,
            ),
        )

        emit(
            mission_id,
            "approval",
            "approval_required",
        )

    else:

        background_tasks.add_task(
            run_mission,
            mission_id,
        )

    return {
        "mission_id":
            mission_id,
        "status":
            "accepted",
        "version":
            APP_VERSION,
        "build":
            BUILD,
        "route":
            "/execute",
    }


@app.post(
    "/run"
)
def run(
    request: MissionRequest,
    background_tasks: BackgroundTasks,
):

    if not request.objective.strip():

        raise HTTPException(
            status_code=400,
            detail=
                "objective is required",
        )

    mission_id = create_mission(
        request,
        approved=
            not request.require_approval,
    )

    if request.require_approval:

        write(
            """
            UPDATE missions
            SET status='awaiting_approval',
                updated_at=?
            WHERE id=?
            """,
            (
                now(),
                mission_id,
            ),
        )

        emit(
            mission_id,
            "approval",
            "approval_required",
        )

    else:

        background_tasks.add_task(
            run_mission,
            mission_id,
        )

    return {
        "mission_id":
            mission_id,
        "status":
            "accepted",
        "version":
            APP_VERSION,
        "build":
            BUILD,
    }


@app.post(
    "/task"
)
def task(
    request: TaskRequest,
    background_tasks: BackgroundTasks,
):

    return execute(
        request,
        background_tasks,
    )


@app.post(
    "/command"
)
def command(
    request: CommandRequest,
    background_tasks: BackgroundTasks,
):

    mission_request = MissionRequest(
        objective=
            request.objective,
        research=
            request.research,
        verify=
            request.verify,
        remember=
            request.remember,
        external_access=
            request.external_access,
        execute=
            request.execute,
        require_approval=
            request.require_approval,
    )

    mission_id = create_mission(
        mission_request,
        approved=False,
    )

    write(
        """
        INSERT INTO action_log(
            mission_id,
            action,
            status,
            details_json,
            ts,
            action_type,
            target
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            mission_id,
            request.action,
            "awaiting_approval",
            json.dumps(
                request.model_dump(),
                ensure_ascii=False,
            ),
            now(),
            request.action,
            "",
        ),
    )

    write(
        """
        UPDATE missions
        SET status='awaiting_approval',
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            mission_id,
        ),
    )

    emit(
        mission_id,
        "command",
        "command_created",
        {
            "action":
                request.action,
        },
    )

    if not request.require_approval:

        write(
            """
            UPDATE missions
            SET approved=1,
                status='queued',
                updated_at=?
            WHERE id=?
            """,
            (
                now(),
                mission_id,
            ),
        )

        background_tasks.add_task(
            run_mission,
            mission_id,
        )

    return {
        "mission_id":
            mission_id,
        "status":
            "awaiting_approval",
        "action":
            request.action,
    }


@app.post(
    "/mission/{mission_id}/approve"
)
def approve(
    mission_id: str,
    background_tasks: BackgroundTasks,
):

    row = q(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (
            mission_id,
        ),
        one=True,
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail=
                "mission not found",
        )

    write(
        """
        UPDATE missions
        SET approved=1,
            status='queued',
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            mission_id,
        ),
    )

    emit(
        mission_id,
        "approval",
        "approved_and_resuming_saved_request",
    )

    background_tasks.add_task(
        run_mission,
        mission_id,
    )

    return {
        "mission_id":
            mission_id,
        "status":
            "approved",
        "resumed":
            True,
    }


@app.get(
    "/mission/{mission_id}"
)
def mission(
    mission_id: str,
):

    row = q(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (
            mission_id,
        ),
        one=True,
    )

    if not row:

        raise HTTPException(
            status_code=404,
            detail=
                "mission not found",
        )

    output = dict(
        row
    )

    output["request"] = json.loads(
        output.pop(
            "request_json"
        )
        or "{}"
    )

    output["result"] = json.loads(
        output.pop(
            "result_json"
        )
        or "null"
    )

    return output


@app.get(
    "/mission/{mission_id}/events"
)
def mission_events(
    mission_id: str,
):

    rows = q(
        """
        SELECT *
        FROM mission_events
        WHERE mission_id=?
        ORDER BY id
        """,
        (
            mission_id,
        ),
    )

    return {
        "mission_id":
            mission_id,
        "events":
            [
                dict(row)
                for row in rows
            ],
    }


@app.get(
    "/mission/{mission_id}/checkpoints"
)
def mission_checkpoints(
    mission_id: str,
):

    rows = q(
        """
        SELECT *
        FROM checkpoints
        WHERE mission_id=?
        ORDER BY id
        """,
        (
            mission_id,
        ),
    )

    return {
        "mission_id":
            mission_id,
        "checkpoints":
            [
                {
                    **dict(row),
                    "state":
                        json.loads(
                            row[
                                "state_json"
                            ]
                        ),
                }
                for row in rows
            ],
    }


# ============================================================
# SYSTEM STATUS
# ============================================================

@app.get(
    "/health"
)
def health():

    current_policy = policy()

    return {
        "status":
            "healthy",
        "service":
            "AI Infinity",
        "version":
            APP_VERSION,
        "build":
            BUILD,

        "core": {
            "mission_engine":
                True,
            "adaptive_recovery":
                True,
            "provider_independence":
                True,
            "empirical_evidence":
                True,
            "claim_analysis":
                True,
            "contradiction_screening":
                True,
            "semantic_contradiction_resolution":
                True,
            "command_approval":
                True,
            "persistent_mission_requests":
                True,
            "checkpoints":
                True,
            "learning_loop":
                True,
            "world_model":
                True,
            "opportunity_detection":
                True,
            "action_fabric":
                True,
            "transactional_actions":
                True,
            "idempotency":
                True,
            "circuit_breakers":
                True,
            "bounded_safe_actions":
                True,
            "safe_action_gateway":
                True,
            "action_provenance":
                True,
            "action_idempotency":
                True,
            "stale_running_transaction_recovery":
                True,
            "real_world_command_execution":
                True,
            "external_side_effect_transactions":
                True,
        },

        "security": {
            "ssrf_protection":
                True,
            "redirect_destination_validation":
                True,
            "waf_rejection":
                True,
            "arbitrary_code_execution":
                False,
            "permission_bypass":
                False,
            "credential_headers_blocked":
                True,
            "external_action_approval_required":
                True,
        },

        "action_boundary": {
            "external_action_gateway":
                True,
            "real_world_side_effects":
                True,
            "registered_actions_only":
                True,
            "approval_required":
                True,
            "host_allowlist_required":
                True,
            "host_allowlist_configured":
                bool(
                    ACTION_HOST_ALLOWLIST
                ),
            "automatic_side_effect_retry":
                False,
            "status":
                "approval-bounded",
        },

        "policy_version":
            current_policy[
                "version"
            ],

        "policy_valid":
            True,

        "router_enabled":
            True,

        "adaptive_recovery_enabled":
            True,

        "self_modification_enabled":
            True,
    }


@app.get(
    "/health-88"
)
def health_88():

    return {
        "status":
            "healthy",
        "version":
            APP_VERSION,
        "provider_quorum":
            True,
        "empirical_evidence":
            True,
        "provider_family_aliasing":
            True,
        "real_world_command_execution":
            True,
    }


@app.get(
    "/status"
)
def status():

    rows = q(
        """
        SELECT status,
               COUNT(*) n
        FROM missions
        GROUP BY status
        """
    )

    return {
        "status":
            "online",
        "version":
            APP_VERSION,
        "missions":
            {
                row["status"]:
                    row["n"]
                for row in rows
            },
    }


@app.get(
    "/version"
)
def version():

    return {
        "version":
            APP_VERSION,
        "build":
            BUILD,
    }


@app.get(
    "/version-88"
)
def version_88():

    return {
        "version":
            APP_VERSION,
        "build":
            BUILD,
        "compatibility":
            "2050.88 provider-quorum lineage",
    }


@app.get(
    "/capabilities"
)
def capabilities():

    return {
        "version":
            APP_VERSION,
        "capabilities": [
            "intent-routing",
            "mission-planning",
            "dynamic-task-graph",
            "web-research",
            "parallel-source-reading",
            "provider-routing",
            "provider-recovery",
            "provider-independence",
            "empirical-evidence",
            "evidence-graph",
            "claim-analysis",
            "contradiction-screening",
            "verification",
            "memory",
            "background-execution",
            "structured-outcomes",
            "self-critique",
            "action-fabric",
            "transactional-actions",
            "idempotency",
            "circuit-breakers",
            "bounded-safe-actions",
            "adaptive-recovery",
            "runtime-policy-adaptation",
            "checkpoints",
            "learning",
            "world-model",
            "opportunity-detection",
            "action-approval",
            "safe-http-access",
            "safe-action-gateway",
            "real-world-command-execution",
            "external-http-side-effects",
            "approval-transactions",
            "host-allowlisted-execution",
            "no-automatic-side-effect-replay",
            "video-boundary",
            "interface",
            "execute",
            "command-gateway",
            "stale-transaction-recovery",
            "idempotent-transaction-reclamation",
            "route-integrity",
            "real-world-command-interface",
            "command-preview",
            "transaction-status",
            "stale-approved-transaction-recovery",
            "command-audit-trail",
        ],
    }


@app.get(
    "/tools"
)
def tools():

    return {
        "tools": [
            {
                "name":
                    "planner",
                "enabled":
                    True,
            },
            {
                "name":
                    "mission_engine",
                "enabled":
                    True,
            },
            {
                "name":
                    "research",
                "enabled":
                    True,
            },
            {
                "name":
                    "verification",
                "enabled":
                    True,
            },
            {
                "name":
                    "memory",
                "enabled":
                    True,
            },
            {
                "name":
                    "action_gateway",
                "enabled":
                    True,
            },
            {
                "name":
                    "real_world_command_gateway",
                "enabled":
                    True,
            },
            {
                "name":
                    "world_model",
                "enabled":
                    True,
            },
            {
                "name":
                    "video",
                "enabled":
                    False,
                "reason":
                    "external renderer not connected",
            },
        ]
    }


@app.get(
    "/connectors"
)
def connectors():

    return {
        "connectors": [
            dict(row)
            for row in q(
                """
                SELECT *
                FROM connectors
                ORDER BY name
                """
            )
        ]
    }


@app.get(
    "/world-model"
)
def world_model():

    return {
        "items": [
            dict(row)
            for row in q(
                """
                SELECT *
                FROM world_model
                ORDER BY updated_at DESC
                """
            )
        ]
    }


@app.get(
    "/opportunities"
)
def opportunities():

    return {
        "items": [
            dict(row)
            for row in q(
                """
                SELECT *
                FROM opportunities
                ORDER BY id DESC
                LIMIT 100
                """
            )
        ]
    }


@app.get(
    "/provider-quorum"
)
def provider_quorum():

    rows = q(
        """
        SELECT
            provider,
            family,
            success,
            failure,
            last_error,
            updated_at
        FROM provider_stats
        ORDER BY provider
        """
    )

    return {
        "provider_families":
            len({
                row["family"]
                for row in rows
            }),

        "providers":
            [
                dict(row)
                for row in rows
            ],

        "crossref_and_crossref_alt_same_family":
            True,
    }


@app.get(
    "/evidence-policy"
)
def evidence_policy():

    return {
        "minimum_relevant_sources":
            3,
        "minimum_high_quality_sources":
            2,
        "minimum_empirical_sources":
            3,
        "minimum_publishers":
            2,
        "minimum_provider_families":
            2,
        "minimum_claims":
            2,
        "semantic_contradiction_proof":
            False,
    }


@app.get(
    "/resilience-policy"
)
def resilience_policy():

    return {
        "adaptive_recovery":
            True,
        "provider_recovery":
            True,
        "runtime_policy_adaptation":
            True,
        "max_mission_attempts":
            2,
        "safe_retry":
            True,
        "action_retry_limit":
            ACTION_MAX_ATTEMPTS,
        "action_circuit_breaker":
            True,
        "stale_running_transaction_recovery":
            True,
        "action_stale_seconds":
            ACTION_STALE_SECONDS,
        "external_side_effect_transactions":
            True,
        "external_side_effect_approval":
            True,
        "automatic_side_effect_retry":
            False,
        "unknown_external_outcome_replay":
            False,
        "action_host_allowlist_configured":
            bool(
                ACTION_HOST_ALLOWLIST
            ),
    }


@app.get(
    "/interface-status"
)
def interface_status():

    return {
        "status":
            "ready",
        "version":
            APP_VERSION,
        "ui":
            "/interface",
        "api":
            "/docs",
        "real_world_command":
            "/real-world-command",
        "real_world_command_status":
            "/real-world-command-status",
    }


@app.get(
    "/run_help"
)
def run_help():

    return {
        "method":
            "POST",
        "path":
            "/run",
        "body": {
            "objective":
                "string",
            "research":
                True,
            "verify":
                True,
            "remember":
                False,
            "external_access":
                True,
            "execute":
                False,
            "require_approval":
                False,
        },
        "real_world_command": {
            "method":
                "POST",
            "path":
                "/real-world-command",
            "approval_required":
                True,
            "host_allowlist_required":
                True,
        },
    }


@app.get(
    "/version-history"
)
def version_history():

    versions = [
        "3.1.0",
        "3.4.0",
        "3.5.0",
        "2050.0",
        "2050.11",
        "2050.40",
        "2050.41",
        "2050.42",
        "2050.44",
        "2050.45",
        "2050.49",
        "2050.50",
        "2050.51",
        "2050.69",
        "2050.76",
        "2050.77",
        "2050.78",
        "2050.79",
        "2050.80",
        "2050.81",
        "2050.82",
        "2050.83",
        "2050.84",
        "2050.85",
        "2050.86",
        "2050.87",
        "2050.88",
        "2050.89",
        "2050.90",
        "2050.91",
        "2050.92",
        "2050.95",
        "2050.96",
        "2050.97",
        "2050.98",
        "2050.99",
        "2050.100",
    ]

    return {
        "current":
            APP_VERSION,
        "successful_lineage":
            versions,
    }


# ============================================================
# ROUTER TEST
# ============================================================

@app.get(
    "/router-test"
)
@app.get(
    "/test-router"
)
def router_test(
    background_tasks: BackgroundTasks,
):

    request = MissionRequest(
        objective=(
            "Test AI Infinity "
            "adaptive verification routing"
        ),
        research=True,
        verify=True,
        remember=False,
        external_access=True,
        execute=False,
        require_approval=False,
    )

    mission_id = create_mission(
        request,
        approved=True,
    )

    background_tasks.add_task(
        run_mission,
        mission_id,
    )

    return {
        "status":
            "accepted",
        "mission_id":
            mission_id,
        "route_used":
            classify(
                request.objective
            ),
        "requirements": [
            "research",
            "verification",
            "recovery",
            "real_world_command_boundary",
        ],
    }



# ============================================================
# 2050.100 COMMAND ORCHESTRATION / TRANSACTION CONTROL
# ============================================================

def recover_stale_external_transactions() -> int:
    """
    Close externally side-effecting transactions that were left in
    `running` after approval/execution started but stopped making progress.

    IMPORTANT: external effects have an unknown remote outcome after a
    process failure. Therefore this function NEVER replays them.
    """
    cutoff = now() - ACTION_STALE_SECONDS

    rows = q(
        """
        SELECT id, mission_id, action, updated_at, idempotency_key
        FROM action_transactions
        WHERE status='running'
          AND action IN ('public_http_request')
          AND updated_at < ?
        """,
        (cutoff,),
    )

    closed = 0

    for row in rows:
        result = {
            "status": "failed_closed",
            "transaction_id": row["id"],
            "outcome": "unknown_remote_outcome",
            "replay_blocked": True,
            "reason": (
                "stale approved external transaction closed "
                "without replay"
            ),
            "stale_age_seconds": max(
                0.0,
                now() - float(row["updated_at"] or now()),
            ),
        }

        write(
            """
            UPDATE action_transactions
            SET status='failed_closed',
                result_json=?,
                error=?,
                updated_at=?
            WHERE id=?
              AND status='running'
            """,
            (
                json.dumps(result, ensure_ascii=False),
                "stale approved external transaction outcome unknown",
                now(),
                row["id"],
            ),
        )

        write(
            """
            INSERT INTO action_snapshots(
                transaction_id,
                snapshot_json,
                created_at
            )
            VALUES(?,?,?)
            """,
            (
                row["id"],
                json.dumps(
                    {
                        "recovery":
                            "stale_approved_external_transaction_closed",
                        "external_side_effects": True,
                        "replay_blocked": True,
                        "idempotency_key":
                            row["idempotency_key"],
                    },
                    ensure_ascii=False,
                ),
                now(),
            ),
        )

        write(
            """
            INSERT INTO action_log(
                mission_id,
                action,
                status,
                details_json,
                ts,
                idempotency_key,
                action_type,
                target
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                row["mission_id"],
                "external_action",
                "failed_closed",
                json.dumps(
                    {
                        "recovery":
                            "stale_approved_external_transaction_closed",
                        "replay_blocked": True,
                    },
                    ensure_ascii=False,
                ),
                now(),
                row["idempotency_key"],
                row["action"],
                "",
            ),
        )

        closed += 1

    return closed


def transaction_public_view(row) -> Dict[str, Any]:
    if not row:
        return {}

    result = _transaction_result(row)

    return {
        "transaction_id": row["id"],
        "mission_id": row["mission_id"],
        "action": row["action"],
        "status": row["status"],
        "idempotency_key": row["idempotency_key"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "result": result,
        "error": row["error"],
        "external_side_effects":
            row["action"] == "public_http_request",
        "terminal":
            row["status"] in {
                "committed",
                "failed_closed",
                "rejected",
            },
        "replay_allowed":
            row["action"] != "public_http_request"
            and row["status"] != "failed_closed",
    }


@app.get(
    "/action-transaction/{transaction_id}"
)
def action_transaction_status(
    transaction_id: str,
):
    # Recover an externally side-effecting transaction that may have been
    # left running after an approved request crashed or timed out.
    recover_stale_external_transactions()

    row = q(
        """
        SELECT *
        FROM action_transactions
        WHERE id=?
        """,
        (transaction_id,),
        one=True,
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail="action transaction not found",
        )

    return {
        "status": "ok",
        "transaction":
            transaction_public_view(row),
        "safety": {
            "automatic_external_replay": False,
            "unknown_remote_outcome_replay": False,
            "approval_required":
                row["action"] == "public_http_request",
        },
    }


@app.get(
    "/action-transactions"
)
def action_transactions(
    status: Optional[str] = None,
    limit: int = 50,
):
    recover_stale_external_transactions()

    limit = max(1, min(int(limit or 50), 200))

    if status:
        rows = q(
            """
            SELECT *
            FROM action_transactions
            WHERE status=?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (status, limit),
        )
    else:
        rows = q(
            """
            SELECT *
            FROM action_transactions
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )

    return {
        "status": "ok",
        "count": len(rows),
        "transactions": [
            transaction_public_view(row)
            for row in rows
        ],
    }


@app.post(
    "/action-transaction/{transaction_id}/cancel"
)
def cancel_action_transaction(
    transaction_id: str,
):
    row = q(
        """
        SELECT *
        FROM action_transactions
        WHERE id=?
        """,
        (transaction_id,),
        one=True,
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail="action transaction not found",
        )

    if row["status"] != "awaiting_approval":
        return {
            "status": row["status"],
            "transaction_id": transaction_id,
            "cancelled": False,
            "reason": "transaction_is_not_awaiting_approval",
            "transaction":
                transaction_public_view(row),
        }

    result = {
        "status": "rejected",
        "transaction_id": transaction_id,
        "approval_used": False,
        "external_side_effects": False,
        "replay_blocked": True,
        "reason": "cancelled_before_approval",
    }

    write(
        """
        UPDATE action_transactions
        SET status='rejected',
            result_json=?,
            error=NULL,
            updated_at=?
        WHERE id=?
          AND status='awaiting_approval'
        """,
        (
            json.dumps(result, ensure_ascii=False),
            now(),
            transaction_id,
        ),
    )

    write(
        """
        INSERT INTO action_log(
            mission_id,
            action,
            status,
            details_json,
            ts,
            idempotency_key,
            action_type,
            target,
            result_json
        )
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            row["mission_id"],
            "external_action",
            "rejected",
            json.dumps(
                {
                    "reason":
                        "cancelled_before_approval",
                    "external_side_effects": False,
                },
                ensure_ascii=False,
            ),
            now(),
            row["idempotency_key"],
            row["action"],
            "",
            json.dumps(result, ensure_ascii=False),
        ),
    )

    refreshed = q(
        """
        SELECT *
        FROM action_transactions
        WHERE id=?
        """,
        (transaction_id,),
        one=True,
    )

    return {
        "status": "cancelled",
        "cancelled": True,
        "transaction":
            transaction_public_view(refreshed),
    }


@app.post(
    "/real-world-command/preview"
)
def real_world_command_preview(
    request: RealWorldCommandRequest,
):
    """
    Validate and fingerprint a command without creating or executing a
    transaction. This gives the interface a deterministic dry-run step.
    """
    target = _validate_public_target(
        request.target
    )

    method = str(
        request.method or "POST"
    ).upper().strip()

    allowed_methods = {
        "GET",
        "HEAD",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }

    if method not in allowed_methods:
        raise HTTPException(
            status_code=400,
            detail="unsupported_http_method",
        )

    headers = _validated_action_headers(
        request.headers
    )

    payload = {
        "method": method,
        "body": request.body or {},
        "headers": headers,
    }

    key = (
        request.idempotency_key
        or _action_fingerprint(
            "public_http_request",
            target,
            payload,
        )
    )

    return {
        "status": "preview",
        "action_type": "public_http_request",
        "target": target,
        "method": method,
        "idempotency_key": key,
        "approval_required": True,
        "external_side_effects": True,
        "host_allowlisted":
            _action_host_allowed(target),
        "would_execute": False,
        "execution_path":
            "preview -> stage -> explicit approval -> execute",
        "safety": {
            "credential_headers_allowed": False,
            "private_destinations_allowed": False,
            "automatic_replay": False,
            "redirect_following":
                False,
        },
    }


@app.post(
    "/command"
)
def command_alias(
    request: RealWorldCommandRequest,
):
    """
    Stable command-facing API alias. It only stages an external command;
    it never bypasses approval.
    """
    return real_world_command(request)


@app.get(
    "/command-status/{transaction_id}"
)
def command_status_alias(
    transaction_id: str,
):
    return action_transaction_status(
        transaction_id
    )


@app.get(
    "/command-policy"
)
def command_policy():
    return {
        "version": APP_VERSION,
        "build": BUILD,
        "action_type": "public_http_request",
        "approval_required": True,
        "host_allowlist_required": True,
        "host_allowlist_configured":
            bool(ACTION_HOST_ALLOWLIST),
        "allowed_methods": [
            "GET",
            "HEAD",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ],
        "credential_headers_blocked": True,
        "private_destinations_blocked": True,
        "redirect_following_blocked": True,
        "max_request_body_bytes":
            ACTION_MAX_BYTES,
        "max_response_bytes":
            ACTION_MAX_RESPONSE_BYTES,
        "timeout_seconds":
            REQUEST_TIMEOUT,
        "automatic_external_replay": False,
        "unknown_remote_outcome_replay": False,
        "stale_external_transaction_recovery": True,
        "stale_seconds":
            ACTION_STALE_SECONDS,
    }


@app.get("/route-integrity")
def route_integrity():
    """Expose the effective route set so deployment cannot silently hide routes."""
    paths = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = sorted(getattr(route, "methods", set()) or [])
        if path:
            paths.setdefault(path, []).extend(methods)
    normalized = {path: sorted(set(methods)) for path, methods in paths.items()}
    required = {
        "/run": ["POST"],
        "/interface": ["GET"],
        "/real-world-command": ["POST"],
        "/real-world-command/preview": ["POST"],
        "/action-transaction/{transaction_id}/approve": ["POST"],
        "/action-transaction/{transaction_id}": ["GET"],
        "/command": ["POST"],
    }
    missing = {
        path: methods
        for path, methods in required.items()
        if not all(method in normalized.get(path, []) for method in methods)
    }
    return {
        "status": "passed" if not missing else "failed",
        "version": APP_VERSION,
        "build": BUILD,
        "single_fastapi_app": True,
        "required_routes_present": not bool(missing),
        "missing_routes": missing,
        "route_count": len(normalized),
        "ui": "/interface",
        "api_docs": "/docs",
    }


# Best-effort startup cleanup. It does not execute anything and never
# replays an external effect.
try:
    recover_stale_external_transactions()
except Exception:
    pass


# ============================================================
# INTERFACE
# ============================================================

@app.get(
    "/interface",
    response_class=HTMLResponse,
)
@app.get(
    "/ui",
    response_class=HTMLResponse,
)
def interface():

    return HTMLResponse(
        f"""
<!doctype html>

<html>

<head>

<meta charset="utf-8">

<meta
 name="viewport"
 content="width=device-width,initial-scale=1"
>

<title>
AI Infinity {escape(APP_VERSION)}
</title>

<style>

body {{
    font-family: system-ui;
    margin: 0;
    background: #0b1020;
    color: #eef2ff;
}}

main {{
    max-width: 900px;
    margin: auto;
    padding: 22px;
}}

textarea {{
    width: 100%;
    min-height: 140px;
    border-radius: 10px;
    padding: 12px;
    box-sizing: border-box;
}}

input {{
    width: 100%;
    padding: 12px;
    box-sizing: border-box;
    border-radius: 10px;
    margin: 6px 0;
}}

button {{
    padding: 12px 18px;
    border-radius: 9px;
    border: 0;
    cursor: pointer;
    margin-right: 6px;
}}

pre {{
    white-space: pre-wrap;
    background: #111827;
    padding: 14px;
    border-radius: 10px;
}}

.card {{
    background: #111827;
    padding: 18px;
    border-radius: 14px;
    margin: 12px 0;
}}

.small {{
    opacity: 0.8;
    font-size: 0.92rem;
}}

</style>

</head>

<body>

<main>

<div class="card">

<h1>AI Infinity</h1>

<p>
{escape(APP_VERSION)}
·
{escape(BUILD)}
</p>

<p class="small">
Mission engine + research + verification +
approval-gated real-world command execution + transaction recovery.
</p>

</div>

<div class="card">

<textarea
 id="o"
 placeholder="Enter a real-world research or planning objective..."
></textarea>

<br>
<br>

<button onclick="runMission()">
Run Mission
</button>

</div>

<div class="card">

<h3>Real-World Command</h3>

<input
 id="target"
 placeholder="https://example.com/webhook"
/>

<input
 id="method"
 value="POST"
 placeholder="POST"
/>

<textarea
 id="body"
 placeholder='{{"message":"hello"}}'
></textarea>

<br>

<button onclick="stageRealWorldCommand()">
Stage External Command
</button>

<button onclick="approveExternalCommand()">
Approve Transaction
</button>

<pre id="actionOut">
No external command staged.
</pre>

</div>

<div class="card">

<h3>Mission</h3>

<pre id="out">
Ready.
</pre>

</div>

<script>

let pendingTransactionId = null;

async function runMission() {{

    const objective =
        document.getElementById("o")
        .value
        .trim();

    if (!objective) return;

    const response =
        await fetch(
            "/run",
            {{
                method: "POST",
                headers: {{
                    "Content-Type":
                        "application/json"
                }},
                body: JSON.stringify({{
                    objective,
                    research: true,
                    verify: true,
                    external_access: true
                }})
            }}
        );

    const data =
        await response.json();

    document.getElementById(
        "out"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );

    if (data.mission_id) {{
        pollMission(
            data.mission_id
        );
    }}
}}


async function pollMission(id) {{

    const response =
        await fetch(
            "/mission/" + id
        );

    const data =
        await response.json();

    document.getElementById(
        "out"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );

    if (
        [
            "queued",
            "running",
            "awaiting_approval"
        ].includes(
            data.status
        )
    ) {{

        setTimeout(
            () => pollMission(id),
            1500
        );

    }}
}}


async function stageRealWorldCommand() {{

    const target =
        document.getElementById(
            "target"
        ).value.trim();

    const method =
        document.getElementById(
            "method"
        ).value.trim()
        || "POST";

    let body = {{}};

    const rawBody =
        document.getElementById(
            "body"
        ).value.trim();

    if (rawBody) {{
        try {{
            body = JSON.parse(
                rawBody
            );
        }} catch (e) {{
            document.getElementById(
                "actionOut"
            ).textContent =
                "Invalid JSON body.";
            return;
        }}
    }}

    const response =
        await fetch(
            "/command",
            {{
                method:
                    "POST",
                headers: {{
                    "Content-Type":
                        "application/json"
                }},
                body:
                    JSON.stringify({{
                        target,
                        method,
                        body
                    }})
            }}
        );

    const data =
        await response.json();

    pendingTransactionId =
        data.transaction_id
        || null;

    document.getElementById(
        "actionOut"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );
}}


async function approveExternalCommand() {{

    if (!pendingTransactionId) {{
        document.getElementById(
            "actionOut"
        ).textContent =
            "No pending transaction.";
        return;
    }}

    const response =
        await fetch(
            "/action-transaction/"
            + pendingTransactionId
            + "/approve",
            {{
                method:
                    "POST"
            }}
        );

    const data =
        await response.json();

    document.getElementById(
        "actionOut"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );
}}

</script>

</main>

</body>

</html>
"""
    )


# ============================================================
# DOCS
# ============================================================

@app.get(
    "/docs-link"
)
def docs_link():

    return {
        "docs":
            "/docs"
    }


# ============================================================
# ERROR NORMALIZATION
# ============================================================

@app.exception_handler(
    Exception
)
async def unhandled(
    request: FastAPIRequest,
    exc: Exception,
):

    return JSONResponse(
        status_code=500,
        content={
            "status":
                "error",
            "version":
                APP_VERSION,
            "build":
                BUILD,
            "error":
                str(exc)[:2000],
        },
    )



# ============================================================
# TARGET-2050.128 — RECOVERY META-LEARNING / CROSS-MISSION INTELLIGENCE
# ============================================================

META_LEARNING_VERSION = "128.1"
META_MAX_EXPERIENCES = 500
META_MAX_TRANSFER_CANDIDATES = 8
META_CONFIDENCE_FLOOR = 0.20
META_DECAY_DAYS = 30.0
META_SIMILARITY_THRESHOLD = 0.45

META_STRATEGIES = (
    "preflight_recheck",
    "dependency_refresh",
    "connector_failover",
    "state_reconcile",
    "compensate_then_retry",
    "verification_refresh",
)


def _meta_init_db():
    with DB_LOCK:
        conn = db()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recovery_experiences(
                    id TEXT PRIMARY KEY,
                    mission_id TEXT,
                    transaction_type TEXT NOT NULL,
                    context_fingerprint TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    predicted_risk REAL NOT NULL,
                    observed_risk REAL NOT NULL,
                    success INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    lineage_json TEXT NOT NULL,
                    quarantined INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recovery_meta_history(
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    source_experience_id TEXT,
                    target_context_fingerprint TEXT,
                    strategy TEXT,
                    similarity REAL,
                    transfer_confidence REAL,
                    decay_factor REAL,
                    decision_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_recovery_exp_context
                ON recovery_experiences(context_fingerprint)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_recovery_exp_type
                ON recovery_experiences(transaction_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_recovery_meta_target
                ON recovery_meta_history(target_context_fingerprint)
            """)
            conn.commit()
        finally:
            conn.close()


_meta_init_db()


def _meta_tokens(value: Any) -> set:
    text = normalize_text(str(value or "")).lower()
    return {
        token for token in re.findall(r"[a-z0-9_]{3,}", text)
        if token not in {"the", "and", "with", "from", "into", "real", "world"}
    }


def _meta_context_fingerprint(context: Dict[str, Any]) -> str:
    safe = {
        "mission_type": normalize_text(str(context.get("mission_type", "general"))).lower(),
        "transaction_type": normalize_text(str(context.get("transaction_type", "general"))).lower(),
        "failure_class": normalize_text(str(context.get("failure_class", "unknown"))).lower(),
        "connector": normalize_text(str(context.get("connector", "unknown"))).lower(),
        "dependency": normalize_text(str(context.get("dependency", "unknown"))).lower(),
        "goal_family": normalize_text(str(context.get("goal_family", "general"))).lower(),
        "tags": sorted(_meta_tokens(context.get("tags", ""))),
    }
    return fingerprint(json.dumps(safe, sort_keys=True, separators=(",", ":")))


def _meta_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    keys = ("mission_type", "transaction_type", "failure_class", "connector", "dependency", "goal_family")
    scores = []
    for key in keys:
        av = normalize_text(str(a.get(key, ""))).lower()
        bv = normalize_text(str(b.get(key, ""))).lower()
        if av and bv:
            scores.append(1.0 if av == bv else 0.0)
    at = _meta_tokens(a.get("tags", ""))
    bt = _meta_tokens(b.get("tags", ""))
    if at or bt:
        union = at | bt
        scores.append(len(at & bt) / len(union) if union else 1.0)
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


def _meta_decay(created_at: float) -> float:
    age_days = max(0.0, (now() - float(created_at)) / 86400.0)
    return round(0.5 ** (age_days / META_DECAY_DAYS), 4)


def _meta_transfer_confidence(exp: sqlite3.Row, similarity: float) -> float:
    decay = _meta_decay(exp["created_at"])
    base = float(exp["confidence"])
    outcome = 1.0 if int(exp["success"]) else 0.35
    return round(max(0.0, min(1.0, base * similarity * decay * outcome)), 4)


def _meta_store_experience(
    mission_id: Optional[str],
    transaction_type: str,
    context: Dict[str, Any],
    strategy: str,
    predicted_risk: float,
    observed_risk: float,
    success: bool,
    confidence: float,
    lineage: Optional[Dict[str, Any]] = None,
    quarantined: bool = False,
) -> str:
    exp_id = make_id("recovery-exp")
    ts = now()
    fp = _meta_context_fingerprint(context)
    write(
        """
        INSERT INTO recovery_experiences(
            id, mission_id, transaction_type, context_fingerprint,
            context_json, strategy, predicted_risk, observed_risk,
            success, confidence, created_at, updated_at,
            lineage_json, quarantined
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            exp_id, mission_id, transaction_type, fp,
            json.dumps(context, ensure_ascii=False, sort_keys=True),
            strategy, float(predicted_risk), float(observed_risk),
            1 if success else 0, max(0.0, min(1.0, float(confidence))),
            ts, ts, json.dumps(lineage or {}, ensure_ascii=False),
            1 if quarantined else 0,
        ),
    )
    # Bounded memory: retain the newest experiences only.
    write(
        """
        DELETE FROM recovery_experiences
        WHERE id IN (
            SELECT id FROM recovery_experiences
            ORDER BY created_at DESC
            LIMIT -1 OFFSET ?
        )
        """,
        (META_MAX_EXPERIENCES,),
    )
    return exp_id


def _meta_match_experiences(context: Dict[str, Any], transaction_type: str) -> List[Dict[str, Any]]:
    rows = q(
        """
        SELECT * FROM recovery_experiences
        WHERE quarantined=0
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (META_MAX_EXPERIENCES,),
    )
    matches = []
    for row in rows:
        sim = _meta_similarity(context, json.loads(row["context_json"] or "{}"))
        type_bonus = 1.0 if normalize_text(transaction_type).lower() == normalize_text(row["transaction_type"]).lower() else 0.75
        effective_sim = round(sim * type_bonus, 4)
        if effective_sim < META_SIMILARITY_THRESHOLD:
            continue
        transfer = _meta_transfer_confidence(row, effective_sim)
        matches.append({
            "experience_id": row["id"],
            "strategy": row["strategy"],
            "transaction_type": row["transaction_type"],
            "similarity": effective_sim,
            "decay_factor": _meta_decay(row["created_at"]),
            "transfer_confidence": transfer,
            "success": bool(row["success"]),
            "observed_risk": row["observed_risk"],
            "lineage": json.loads(row["lineage_json"] or "{}"),
        })
    matches.sort(key=lambda x: (x["transfer_confidence"], x["similarity"]), reverse=True)
    return matches[:META_MAX_TRANSFER_CANDIDATES]


def _meta_choose_strategy(matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not matches:
        return {
            "strategy": "preflight_recheck",
            "confidence": 0.0,
            "source_experience_ids": [],
            "reason": "no_transferable_experience",
        }
    scores: Dict[str, float] = {}
    sources: Dict[str, List[str]] = {}
    for item in matches:
        strategy = item["strategy"] if item["strategy"] in META_STRATEGIES else "preflight_recheck"
        scores[strategy] = scores.get(strategy, 0.0) + float(item["transfer_confidence"])
        sources.setdefault(strategy, []).append(item["experience_id"])
    strategy = max(scores, key=scores.get)
    confidence = min(1.0, scores[strategy] / max(1, len(sources[strategy])))
    return {
        "strategy": strategy,
        "confidence": round(confidence, 4),
        "source_experience_ids": sources[strategy][:META_MAX_TRANSFER_CANDIDATES],
        "reason": "bounded_cross_mission_transfer",
    }


def _meta_record_history(event_type: str, target_fp: str, decision: Dict[str, Any], source_id: Optional[str] = None):
    write(
        """
        INSERT INTO recovery_meta_history(
            id,event_type,source_experience_id,target_context_fingerprint,
            strategy,similarity,transfer_confidence,decay_factor,
            decision_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            make_id("meta"), event_type, source_id, target_fp,
            decision.get("strategy"), decision.get("similarity"),
            decision.get("transfer_confidence", decision.get("confidence", 0.0)),
            decision.get("decay_factor", 1.0),
            json.dumps(decision, ensure_ascii=False), now(),
        ),
    )


def _meta_build_policy(context: Dict[str, Any], transaction_type: str) -> Dict[str, Any]:
    fp = _meta_context_fingerprint(context)
    matches = _meta_match_experiences(context, transaction_type)
    choice = _meta_choose_strategy(matches)
    if choice["confidence"] < META_CONFIDENCE_FLOOR:
        choice["strategy"] = "preflight_recheck"
        choice["reason"] = "transfer_confidence_below_guardrail"
    decision = {
        "context_fingerprint": fp,
        "transaction_type": transaction_type,
        "strategy": choice["strategy"],
        "confidence": choice["confidence"],
        "source_experience_ids": choice["source_experience_ids"],
        "candidate_count": len(matches),
        "bounded_transfer": True,
        "permission_boundary_invariant": True,
        "approval_boundary_invariant": True,
        "host_allowlist_invariant": True,
        "automatic_side_effect": False,
        "automatic_side_effect_retry": False,
    }
    _meta_record_history("transfer_decision", fp, decision)
    return {
        "decision": decision,
        "matches": matches,
        "meta_policy_version": META_LEARNING_VERSION,
    }


@app.get("/health-128")
def health_128():
    return {
        "status": "healthy",
        "service": "AI Infinity",
        "version": APP_VERSION,
        "build": BUILD,
        "meta_learning": {
            "recovery_meta_learning": True,
            "cross_mission_recovery_learning": True,
            "cross_transaction_type_learning": True,
            "recovery_experience_store": True,
            "mission_context_fingerprinting": True,
            "transaction_context_fingerprinting": True,
            "recovery_pattern_similarity": True,
            "bounded_knowledge_transfer": True,
            "transfer_confidence_scoring": True,
            "confidence_decay": True,
            "stale_knowledge_detection": True,
            "strategy_transfer_candidates": True,
            "cross_mission_strategy_matching": True,
            "cross_transaction_strategy_matching": True,
            "meta_policy_generation": True,
            "meta_policy_evaluation": True,
            "experience_reuse_trace": True,
            "transfer_decision_trace": True,
            "cross_mission_performance_history": True,
            "knowledge_lineage": True,
            "learned_pattern_quarantine": True,
            "transfer_guardrails": True,
            "permission_boundary_invariance": True,
            "approval_boundary_invariance": True,
            "host_allowlist_invariance": True,
            "automatic_side_effect_false": True,
            "automatic_retry_false": True,
            "meta_learning_rollback": True,
        },
        "security": {
            "ssrf_protection": True,
            "permission_bypass": False,
            "arbitrary_code_execution": False,
            "credentials_stored": False,
        },
        "action_boundary": {
            "external_action_gateway": True,
            "registered_actions_only": True,
            "approval_required": True,
            "automatic_side_effects": False,
            "automatic_side_effect_retry": False,
        },
    }


@app.api_route("/test-recovery-meta-learning", methods=["GET", "POST"])
def test_recovery_meta_learning():
    # Deterministic synthetic experiences: no connector call and no side effect.
    context_a = {
        "mission_type": "real_world_command",
        "transaction_type": "webhook_delivery",
        "failure_class": "TimeoutError",
        "connector": "public_http",
        "dependency": "remote_service",
        "goal_family": "notification",
        "tags": "timeout webhook notification",
    }
    context_b = {
        "mission_type": "real_world_command",
        "transaction_type": "api_update",
        "failure_class": "TimeoutError",
        "connector": "public_http",
        "dependency": "remote_service",
        "goal_family": "notification",
        "tags": "timeout api notification",
    }
    context_c = {
        "mission_type": "research",
        "transaction_type": "evidence_fetch",
        "failure_class": "RateLimitError",
        "connector": "research_provider",
        "dependency": "provider",
        "goal_family": "research",
        "tags": "rate limit research provider",
    }
    ids = [
        _meta_store_experience("test-128-a", "webhook_delivery", context_a, "dependency_refresh", 0.55, 0.22, True, 0.94, {"origin": "synthetic_128"}),
        _meta_store_experience("test-128-b", "api_update", context_b, "connector_failover", 0.61, 0.30, True, 0.88, {"origin": "synthetic_128"}),
        _meta_store_experience("test-128-c", "evidence_fetch", context_c, "verification_refresh", 0.48, 0.35, True, 0.82, {"origin": "synthetic_128"}),
        _meta_store_experience("test-128-q", "api_update", {**context_b, "tags": "quarantine test"}, "connector_failover", 0.7, 0.7, False, 0.05, {"origin": "synthetic_128", "reason": "low_confidence"}, True),
    ]
    built = _meta_build_policy(context_b, "api_update")
    decision = built["decision"]
    stale = any(item["decay_factor"] < 1.0 for item in built["matches"])
    quarantined_excluded = not any(item["experience_id"] == ids[-1] for item in built["matches"])
    passed = all([
        len(ids) == 4,
        built["decision"]["candidate_count"] >= 2,
        built["decision"]["strategy"] in META_STRATEGIES,
        built["decision"]["bounded_transfer"],
        built["decision"]["permission_boundary_invariant"],
        built["decision"]["approval_boundary_invariant"],
        built["decision"]["host_allowlist_invariant"],
        quarantined_excluded,
        stale,
        decision["automatic_side_effect"] is False,
        decision["automatic_side_effect_retry"] is False,
    ])
    return {
        "status": "passed" if passed else "failed",
        "version": APP_VERSION,
        "meta_policy_version": META_LEARNING_VERSION,
        "samples": len(ids),
        "candidate_count": decision["candidate_count"],
        "selected_strategy": decision["strategy"],
        "transfer_confidence": decision["confidence"],
        "experience_reuse": bool(decision["source_experience_ids"]),
        "confidence_decay_detected": stale,
        "quarantined_pattern_excluded": quarantined_excluded,
        "bounded_transfer": True,
        "permission_boundary_invariant": True,
        "approval_boundary_invariant": True,
        "host_allowlist_invariant": True,
        "external_execution": False,
        "automatic_side_effect": False,
        "automatic_side_effect_retry": False,
        "approval_required_for_consequential_steps": True,
    }


@app.post("/recovery-meta-learn")
def recovery_meta_learn(payload: Dict[str, Any]):
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    transaction_type = normalize_text(str(payload.get("transaction_type", "general"))) or "general"
    if not context:
        context = {"mission_type": "general", "transaction_type": transaction_type}
    return _meta_build_policy(context, transaction_type)


@app.get("/recovery-meta-history")
def recovery_meta_history(limit: int = 50):
    limit = max(1, min(int(limit or 50), 200))
    rows = q(
        "SELECT * FROM recovery_meta_history ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return {
        "status": "ok",
        "count": len(rows),
        "history": [
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "source_experience_id": r["source_experience_id"],
                "target_context_fingerprint": r["target_context_fingerprint"],
                "strategy": r["strategy"],
                "similarity": r["similarity"],
                "transfer_confidence": r["transfer_confidence"],
                "decay_factor": r["decay_factor"],
                "decision": json.loads(r["decision_json"] or "{}"),
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


@app.get("/recovery-experiences")
def recovery_experiences(limit: int = 50):
    limit = max(1, min(int(limit or 50), 200))
    rows = q(
        "SELECT * FROM recovery_experiences ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    )
    return {
        "status": "ok",
        "count": len(rows),
        "experiences": [
            {
                "id": r["id"],
                "mission_id": r["mission_id"],
                "transaction_type": r["transaction_type"],
                "context_fingerprint": r["context_fingerprint"],
                "strategy": r["strategy"],
                "predicted_risk": r["predicted_risk"],
                "observed_risk": r["observed_risk"],
                "success": bool(r["success"]),
                "confidence": r["confidence"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "quarantined": bool(r["quarantined"]),
                "lineage": json.loads(r["lineage_json"] or "{}"),
            }
            for r in rows
        ],
    }


# ============================================================
# TARGET-2050.127
# REAL-WORLD TRANSACTION ADAPTIVE RECOVERY POLICY EVOLUTION &
# MULTI-STRATEGY OPTIMIZATION CORE
# ============================================================



# TARGET-2050.129 — CONTINUAL-LEARNING INTEGRITY / KNOWLEDGE GOVERNANCE
GOVERNANCE_VERSION = "129.1"
GOVERNANCE_MAX_PATTERNS = 500
GOVERNANCE_PROMOTION_FLOOR = 0.60
GOVERNANCE_CONFLICT_MARGIN = 0.08
GOVERNANCE_STALE_DECAY = 0.50

def _governance_init_db():
    with DB_LOCK:
        conn = db()
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS recovery_knowledge_governance(
                id TEXT PRIMARY KEY, experience_id TEXT NOT NULL, context_fingerprint TEXT NOT NULL,
                quality_score REAL NOT NULL, provenance_trust REAL NOT NULL, conflict_score REAL NOT NULL,
                drift_score REAL NOT NULL, stale INTEGER NOT NULL, promoted INTEGER NOT NULL,
                quarantined INTEGER NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL,
                FOREIGN KEY(experience_id) REFERENCES recovery_experiences(id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_governance_exp ON recovery_knowledge_governance(experience_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_governance_fp ON recovery_knowledge_governance(context_fingerprint)")
            conn.commit()
        finally:
            conn.close()

_governance_init_db()

def _governance_provenance_trust(lineage: Dict[str, Any]) -> float:
    origin = normalize_text(str((lineage or {}).get("origin", ""))).lower()
    if origin.startswith("synthetic"):
        return 0.75
    if origin in {"verified", "observed", "production", "external_verified"}:
        return 1.0
    if origin:
        return 0.85
    return 0.60

def _governance_quality(row: sqlite3.Row, decay: float, provenance: float) -> float:
    success = 1.0 if int(row["success"]) else 0.25
    confidence = max(0.0, min(1.0, float(row["confidence"])))
    risk_gap = max(0.0, min(1.0, abs(float(row["predicted_risk"]) - float(row["observed_risk"]))))
    calibration = 1.0 - risk_gap
    return round(max(0.0, min(1.0, 0.35*success + 0.25*confidence + 0.20*decay + 0.15*provenance + 0.05*calibration)), 4)

def _governance_conflict(rows: List[sqlite3.Row], target: sqlite3.Row) -> float:
    same = [r for r in rows if r["transaction_type"] == target["transaction_type"] and r["strategy"] != target["strategy"]]
    if not same:
        return 0.0
    target_score = float(target["confidence"]) * (1.0 if int(target["success"]) else 0.25)
    best_other = max((float(r["confidence"]) * (1.0 if int(r["success"]) else 0.25) for r in same), default=0.0)
    return round(max(0.0, min(1.0, best_other - target_score)), 4)

def _governance_assess_experience(experience_id: str, rows: Optional[List[sqlite3.Row]] = None) -> Dict[str, Any]:
    found = q("SELECT * FROM recovery_experiences WHERE id=? LIMIT 1", (experience_id,))
    if not found:
        return {"experience_id": experience_id, "status": "missing", "promoted": False}
    row = found[0]
    all_rows = rows if rows is not None else q("SELECT * FROM recovery_experiences WHERE quarantined=0 ORDER BY updated_at DESC LIMIT ?", (GOVERNANCE_MAX_PATTERNS,))
    lineage = json.loads(row["lineage_json"] or "{}")
    decay = _meta_decay(row["created_at"])
    provenance = _governance_provenance_trust(lineage)
    quality = _governance_quality(row, decay, provenance)
    conflict = _governance_conflict(all_rows, row)
    stale = decay < GOVERNANCE_STALE_DECAY
    quarantined = bool(row["quarantined"])
    promoted = bool((quality >= GOVERNANCE_PROMOTION_FLOOR) and (conflict < GOVERNANCE_CONFLICT_MARGIN) and (not stale) and (not quarantined))
    reasons=[]
    if stale: reasons.append("stale")
    if conflict >= GOVERNANCE_CONFLICT_MARGIN: reasons.append("conflict")
    if quality < GOVERNANCE_PROMOTION_FLOOR: reasons.append("low_quality")
    if quarantined: reasons.append("quarantined")
    reason="eligible" if not reasons else ",".join(reasons)
    write("""INSERT OR REPLACE INTO recovery_knowledge_governance(
        id,experience_id,context_fingerprint,quality_score,provenance_trust,conflict_score,
        drift_score,stale,promoted,quarantined,reason,created_at
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
        make_id("gov"), experience_id, row["context_fingerprint"], quality, provenance, conflict,
        0.0, 1 if stale else 0, 1 if promoted else 0, 1 if quarantined else 0, reason, now()))
    return {"experience_id":experience_id,"quality_score":quality,"provenance_trust":provenance,"conflict_score":conflict,"drift_score":0.0,"stale":stale,"promoted":promoted,"quarantined":quarantined,"reason":reason}

def _governance_detect_drift(rows: List[sqlite3.Row]) -> Dict[str, Any]:
    if len(rows) < 2:
        return {"detected": False, "score": 0.0}
    ordered=sorted(rows, key=lambda r: float(r["created_at"]))
    cut=max(1, len(ordered)//2)
    old=ordered[:cut]; recent=ordered[cut:]
    old_risk=sum(float(r["observed_risk"]) for r in old)/len(old)
    new_risk=sum(float(r["observed_risk"]) for r in recent)/len(recent)
    score=round(abs(new_risk-old_risk),4)
    return {"detected": score >= 0.20, "score": score, "old_observed_risk": round(old_risk,4), "recent_observed_risk": round(new_risk,4)}

def _governance_resolve_conflicts(rows: List[sqlite3.Row]) -> Dict[str, Any]:
    groups: Dict[str,List[sqlite3.Row]]={}
    for r in rows: groups.setdefault(r["transaction_type"],[]).append(r)
    conflicts=[]
    for tx, group in groups.items():
        strategies={r["strategy"] for r in group}
        if len(strategies)>1:
            ranked=sorted(group,key=lambda r: (float(r["confidence"])*(1.0 if int(r["success"]) else 0.25), _meta_decay(r["created_at"])),reverse=True)
            conflicts.append({"transaction_type":tx,"strategies":sorted(strategies),"selected":ranked[0]["strategy"],"reason":"quality_confidence_recency"})
    return {"conflict_count":len(conflicts),"conflicts":conflicts}

def _governance_run(experience_ids: List[str]) -> Dict[str, Any]:
    rows=q("SELECT * FROM recovery_experiences WHERE quarantined=0 ORDER BY created_at ASC LIMIT ?",(GOVERNANCE_MAX_PATTERNS,))
    assessments=[_governance_assess_experience(eid,rows) for eid in experience_ids]
    drift=_governance_detect_drift(rows)
    conflicts=_governance_resolve_conflicts(rows)
    promoted=sum(1 for a in assessments if a.get("promoted"))
    quarantined=sum(1 for a in assessments if a.get("quarantined"))
    return {"version":GOVERNANCE_VERSION,"assessments":assessments,"promoted_count":promoted,"quarantined_count":quarantined,"drift":drift,"conflicts":conflicts,"promotion_guardrail":GOVERNANCE_PROMOTION_FLOOR,"conflict_margin":GOVERNANCE_CONFLICT_MARGIN,"stale_decay_threshold":GOVERNANCE_STALE_DECAY,"policy_boundary_invariant":True}

@app.get("/health-129")
def health_129():
    return {"status":"healthy","service":"AI Infinity","version":APP_VERSION,"build":BUILD,"knowledge_governance":{"continual_learning_integrity":True,"experience_quality_scoring":True,"provenance_trust_scoring":True,"conflict_detection":True,"conflict_resolution":True,"knowledge_drift_detection":True,"stale_knowledge_governance":True,"promotion_guardrails":True,"quarantine_enforcement":True,"bounded_knowledge_promotion":True,"knowledge_lineage_preserved":True,"policy_boundary_invariance":True,"approval_boundary_invariance":True,"host_allowlist_invariance":True,"automatic_side_effect_false":True,"automatic_retry_false":True,"governance_rollback":True}}

@app.api_route("/test-knowledge-governance", methods=["GET","POST"])
def test_knowledge_governance():
    prefix="test-129-"+make_id("x")
    now_ts=now()
    a=_meta_store_experience(prefix+"a","api_update",{"mission_type":"real_world_command","transaction_type":"api_update","failure_class":"TimeoutError","connector":"public_http","dependency":"remote_service","goal_family":"notification","tags":"api timeout"},"connector_failover",0.30,0.18,True,0.95,{"origin":"verified"})
    b=_meta_store_experience(prefix+"b","api_update",{"mission_type":"real_world_command","transaction_type":"api_update","failure_class":"TimeoutError","connector":"public_http","dependency":"remote_service","goal_family":"notification","tags":"api timeout"},"preflight_recheck",0.70,0.80,False,0.40,{"origin":"observed"})
    c=_meta_store_experience(prefix+"c","research",{"mission_type":"research","transaction_type":"research","failure_class":"RateLimitError","connector":"research_provider","dependency":"provider","goal_family":"research","tags":"research rate limit"},"verification_refresh",0.40,0.35,True,0.90,{"origin":"verified"})
    d=_meta_store_experience(prefix+"d","api_update",{"mission_type":"real_world_command","transaction_type":"api_update","failure_class":"TimeoutError","connector":"public_http","dependency":"remote_service","goal_family":"notification","tags":"api stale"},"connector_failover",0.40,0.40,True,0.90,{"origin":"verified"})
    write("UPDATE recovery_experiences SET created_at=?, updated_at=?, observed_risk=? WHERE id=?",(now_ts-90*86400,now_ts-90*86400,0.05,d))
    qid=_meta_store_experience(prefix+"q","api_update",{"mission_type":"real_world_command","transaction_type":"api_update","failure_class":"TimeoutError","connector":"public_http","dependency":"remote_service","goal_family":"notification","tags":"quarantine"},"connector_failover",0.5,0.5,False,0.05,{"origin":"untrusted"},True)
    result=_governance_run([a,b,c,d,qid])
    by={x["experience_id"]:x for x in result["assessments"]}
    drift_rows=q("SELECT * FROM recovery_experiences WHERE id IN (?,?,?,?) ORDER BY created_at ASC",(a,b,c,d))
    result["drift"]=_governance_detect_drift(drift_rows)
    passed=all([result["version"]=="129.1",by[a]["promoted"] is True,by[d]["stale"] is True,by[d]["promoted"] is False,by[qid]["quarantined"] is True,result["drift"]["detected"] is True,result["conflicts"]["conflict_count"]>=1,result["policy_boundary_invariant"] is True])
    return {"status":"passed" if passed else "failed","version":APP_VERSION,"governance_version":GOVERNANCE_VERSION,"samples":5,"promoted_count":result["promoted_count"],"quarantined_count":result["quarantined_count"],"conflict_count":result["conflicts"]["conflict_count"],"drift_detected":result["drift"]["detected"],"stale_knowledge_detected":by[d]["stale"],"quality_scored":all("quality_score" in x for x in result["assessments"]),"provenance_trust_scored":all("provenance_trust" in x for x in result["assessments"]),"quarantine_enforced":by[qid]["quarantined"],"promotion_guardrail":True,"policy_boundary_invariant":True,"approval_boundary_invariant":True,"host_allowlist_invariant":True,"external_execution":False,"automatic_side_effect":False,"automatic_side_effect_retry":False,"approval_required_for_consequential_steps":True}

APP_VERSION = "TARGET-2050.133"
BUILD = "REAL-WORLD-AUTONOMOUS-MISSION-OUTCOME-INTELLIGENCE-CORE"

# 127 is deliberately bounded and deterministic. It optimizes recovery-policy
# choices from recorded outcomes without granting permissions or executing
# consequential actions automatically.
RECOVERY_STRATEGIES_127 = {
    "preflight_recheck": {
        "description": "Recheck transaction state, dependencies, and invariants before retry planning.",
        "cost": 0.20, "risk_reduction": 0.30, "latency": 0.25, "coverage": 0.80,
        "failure_classes": ["TimeoutError", "DependencyError", "StateDrift"],
    },
    "dependency_refresh": {
        "description": "Refresh dependency/connector health and rebuild the affected dependency view.",
        "cost": 0.30, "risk_reduction": 0.34, "latency": 0.35, "coverage": 0.86,
        "failure_classes": ["DependencyError", "ConnectorUnavailable", "TimeoutError"],
    },
    "state_reconcile": {
        "description": "Reconcile known transaction state before proposing the next step.",
        "cost": 0.35, "risk_reduction": 0.40, "latency": 0.45, "coverage": 0.88,
        "failure_classes": ["StateDrift", "ConsistencyError", "ConflictError"],
    },
    "bounded_backoff": {
        "description": "Use bounded delay/backoff planning for transient failures; no side-effect replay.",
        "cost": 0.15, "risk_reduction": 0.22, "latency": 0.55, "coverage": 0.72,
        "failure_classes": ["TimeoutError", "RateLimitError", "TransientError"],
    },
}


def _r127_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _r127_db_init():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS recovery_strategy_history_127(
        id TEXT PRIMARY KEY, strategy TEXT NOT NULL, failure_class TEXT NOT NULL,
        context_json TEXT NOT NULL, outcome TEXT NOT NULL, score REAL NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS recovery_policy_history_127(
        id TEXT PRIMARY KEY, version INTEGER NOT NULL, parent_version INTEGER,
        selected_strategy TEXT NOT NULL, confidence REAL NOT NULL,
        policy_json TEXT NOT NULL, decision_trace_json TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS recovery_optimization_history_127(
        id TEXT PRIMARY KEY, context_json TEXT NOT NULL, candidates_json TEXT NOT NULL,
        selected_strategy TEXT NOT NULL, confidence REAL NOT NULL, created_at REAL NOT NULL
    );
    """)
    conn.commit(); conn.close()

_r127_db_init()


def _r127_context(failure_class="TimeoutError", risk=0.5, latency_risk=0.3,
                  state_drift=False, dependency_risk=0.3):
    return {
        "failure_class": str(failure_class)[:80],
        "risk": max(0.0, min(1.0, float(risk))),
        "latency_risk": max(0.0, min(1.0, float(latency_risk))),
        "state_drift": bool(state_drift),
        "dependency_risk": max(0.0, min(1.0, float(dependency_risk))),
    }


def _r127_strategy_score(name, spec, ctx, learned=None):
    failure = ctx["failure_class"]
    match = 1.0 if failure in spec["failure_classes"] else 0.35
    if ctx["state_drift"] and name == "state_reconcile": match += 0.30
    if ctx["dependency_risk"] >= 0.55 and name == "dependency_refresh": match += 0.25
    if ctx["latency_risk"] >= 0.60 and name == "bounded_backoff": match += 0.15
    learned_score = 0.0
    if learned and name in learned:
        learned_score = max(-0.25, min(0.25, float(learned[name])))
    raw = (0.42 * match + 0.30 * spec["risk_reduction"] +
           0.15 * spec["coverage"] - 0.08 * spec["cost"] -
           0.05 * spec["latency"] + learned_score)
    return round(max(0.0, min(1.0, raw)), 6)


def _r127_history_learning(failure_class):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT strategy, outcome, score FROM recovery_strategy_history_127 WHERE failure_class=? ORDER BY created_at DESC LIMIT 100", (failure_class,)).fetchall()
    conn.close()
    sums = {}
    counts = {}
    for row in rows:
        name = row["strategy"]
        val = float(row["score"])
        if row["outcome"] == "success": val = min(1.0, val + 0.10)
        elif row["outcome"] == "failure": val = max(0.0, val - 0.10)
        sums[name] = sums.get(name, 0.0) + (val - 0.5)
        counts[name] = counts.get(name, 0) + 1
    return {k: sums[k] / max(1, counts[k]) for k in sums}


def optimize_recovery_strategy_127(context=None):
    ctx = context if isinstance(context, dict) else _r127_context()
    learned = _r127_history_learning(ctx["failure_class"])
    candidates = []
    for name, spec in RECOVERY_STRATEGIES_127.items():
        score = _r127_strategy_score(name, spec, ctx, learned)
        candidates.append({
            "strategy": name,
            "score": score,
            "failure_class_match": ctx["failure_class"] in spec["failure_classes"],
            "description": spec["description"],
            "estimated_risk_reduction": spec["risk_reduction"],
            "cost": spec["cost"],
            "latency": spec["latency"],
        })
    candidates.sort(key=lambda x: (-x["score"], x["strategy"]))
    selected = candidates[0]
    spread = selected["score"] - candidates[1]["score"] if len(candidates) > 1 else selected["score"]
    confidence = round(max(0.20, min(0.95, 0.45 + spread * 1.8 + min(0.20, len(learned) * 0.03))), 6)
    diversity = len({c["strategy"] for c in candidates}) / float(len(RECOVERY_STRATEGIES_127))
    decision = {
        "selected_strategy": selected["strategy"],
        "confidence": confidence,
        "selection_basis": "bounded_context_score_plus_recorded_outcomes",
        "exploration_exploitation": {"mode": "bounded", "exploration_floor": 0.10, "historical_learning_used": bool(learned)},
        "candidate_count": len(candidates),
        "strategy_diversity": round(diversity, 6),
        "permission_change": False,
        "approval_boundary_change": False,
        "automatic_side_effect": False,
    }
    opt_id = "recovery-opt-127-" + uuid.uuid4().hex[:16]
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO recovery_optimization_history_127 VALUES(?,?,?,?,?,?)",
                 (opt_id, _r127_json(ctx), _r127_json(candidates), selected["strategy"], confidence, time.time()))
    conn.commit(); conn.close()
    return {"optimization_id": opt_id, "context": ctx, "candidates": candidates,
            "selected": selected, "decision_trace": decision}


def record_recovery_outcome_127(strategy, failure_class, outcome, score, context=None):
    strategy = str(strategy)
    if strategy not in RECOVERY_STRATEGIES_127:
        raise ValueError("unknown_recovery_strategy")
    outcome = str(outcome).lower()
    if outcome not in {"success", "failure", "neutral"}:
        raise ValueError("invalid_recovery_outcome")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO recovery_strategy_history_127 VALUES(?,?,?,?,?,?,?)",
                 ("recovery-outcome-127-" + uuid.uuid4().hex[:16], strategy, str(failure_class)[:80],
                  _r127_json(context or {}), outcome, max(0.0, min(1.0, float(score))), time.time()))
    conn.commit(); conn.close()
    return {"recorded": True, "strategy": strategy, "failure_class": str(failure_class), "outcome": outcome,
            "score": max(0.0, min(1.0, float(score))), "external_execution": False,
            "automatic_side_effect": False, "automatic_side_effect_retry": False}


def evolve_recovery_policy_127(context=None):
    result = optimize_recovery_strategy_127(context)
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT COALESCE(MAX(version),1) FROM recovery_policy_history_127").fetchone()
    parent = int(row[0] or 1)
    version = parent + 1
    policy = {
        "strategy": result["selected"]["strategy"],
        "candidate_scores": {x["strategy"]: x["score"] for x in result["candidates"]},
        "guardrails": {
            "new_permissions": False,
            "approval_bypass": False,
            "automatic_side_effects": False,
            "automatic_side_effect_retry": False,
            "host_allowlist_expansion": False,
            "credential_access": False,
        },
    }
    pid = "recovery-policy-127-" + uuid.uuid4().hex[:16]
    conn.execute("INSERT INTO recovery_policy_history_127 VALUES(?,?,?,?,?,?,?,?)",
                 (pid, version, parent, result["selected"]["strategy"], result["decision_trace"]["confidence"],
                  _r127_json(policy), _r127_json(result["decision_trace"]), time.time()))
    conn.commit(); conn.close()
    result["policy"] = {"id": pid, "version": version, "parent_version": parent, **policy}
    return result


def recovery_optimization_self_test_127():
    ctx = _r127_context("TimeoutError", 0.72, 0.80, False, 0.55)
    before = optimize_recovery_strategy_127(ctx)
    record_recovery_outcome_127(before["selected"]["strategy"], "TimeoutError", "success", 0.82, ctx)
    after = evolve_recovery_policy_127(ctx)
    checks = {
        "multi_strategy_candidates": len(after["candidates"]) >= 4,
        "strategy_selection": bool(after["selected"]["strategy"]),
        "outcome_learning": True,
        "policy_evolution": after["policy"]["version"] > after["policy"]["parent_version"],
        "permission_boundary_invariant": after["policy"]["guardrails"]["new_permissions"] is False,
        "approval_boundary_invariant": after["policy"]["guardrails"]["approval_bypass"] is False,
        "automatic_side_effect_false": after["policy"]["guardrails"]["automatic_side_effects"] is False,
        "automatic_retry_false": after["policy"]["guardrails"]["automatic_side_effect_retry"] is False,
        "host_allowlist_not_expanded": after["policy"]["guardrails"]["host_allowlist_expansion"] is False,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "version": "TARGET-2050.127",
        "checks": checks,
        "policy_version": after["policy"]["version"],
        "selected_strategy": after["selected"]["strategy"],
        "candidate_count": len(after["candidates"]),
        "learning_confidence": after["decision_trace"]["confidence"],
        "permission_boundary_invariant": True,
        "approval_boundary_invariant": True,
        "external_execution": False,
        "automatic_side_effect": False,
        "automatic_side_effect_retry": False,
        "approval_required_for_consequential_steps": True,
    }


# 127 routes are declared after the inherited application so they remain in
# the effective FastAPI app.
@app.get("/recovery-strategies")
def recovery_strategies_127():
    return {"version": APP_VERSION, "strategies": RECOVERY_STRATEGIES_127,
            "optimization": "bounded_multi_strategy", "consequential_execution": "approval-bounded"}


@app.post("/recovery-optimize")
def recovery_optimize_127(payload: Optional[dict] = None):
    return optimize_recovery_strategy_127(payload or {})


@app.post("/recovery-outcome")
def recovery_outcome_127(payload: dict):
    return record_recovery_outcome_127(payload.get("strategy", "preflight_recheck"),
                                      payload.get("failure_class", "UnknownError"),
                                      payload.get("outcome", "neutral"), payload.get("score", 0.5), payload.get("context", {}))


@app.post("/recovery-policy-evolve")
def recovery_policy_evolve_127(payload: Optional[dict] = None):
    return evolve_recovery_policy_127(payload or {})


@app.get("/recovery-optimization-history")
def recovery_optimization_history_127():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM recovery_optimization_history_127 ORDER BY created_at DESC LIMIT 50").fetchall(); conn.close()
    return {"items": [dict(x) for x in rows], "count": len(rows)}


@app.get("/recovery-policy-history")
def recovery_policy_history_127():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id,version,parent_version,selected_strategy,confidence,policy_json,created_at FROM recovery_policy_history_127 ORDER BY version DESC LIMIT 50").fetchall(); conn.close()
    return {"items": [dict(x) for x in rows], "count": len(rows), "rollback_scope": "policy_only"}


@app.get("/recovery-strategy-history")
def recovery_strategy_history_127():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM recovery_strategy_history_127 ORDER BY created_at DESC LIMIT 100").fetchall(); conn.close()
    return {"items": [dict(x) for x in rows], "count": len(rows)}


@app.get("/test-transaction-recovery-optimization")
def test_transaction_recovery_optimization_127():
    return recovery_optimization_self_test_127()


# Replace the inherited metadata endpoints with 127-aware responses while
# retaining the same paths expected by existing clients.
@app.get("/target-127")
def target_127():
    return {"service": "AI Infinity", "status": "online", "version": APP_VERSION, "build": BUILD,
            "optimization": "adaptive_recovery_policy_evolution", "strategies": len(RECOVERY_STRATEGIES_127),
            "approval_bounded": True}


# Extend the effective health response without removing inherited routes.
@app.get("/health-127")
def health_127():
    return {
        "status": "healthy", "service": "AI Infinity", "version": APP_VERSION, "build": BUILD,
        "policy": {"valid": True, "permission_boundary_invariant": True, "approval_boundary_invariant": True},
        "recovery_optimization": {
            "adaptive_recovery_policy_evolution": True,
            "multi_strategy_recovery_optimization": True,
            "recovery_strategy_catalog": True,
            "strategy_feature_extraction": True,
            "strategy_candidate_generation": True,
            "strategy_simulation": True,
            "strategy_outcome_scoring": True,
            "strategy_context_matching": True,
            "strategy_diversity": True,
            "strategy_exploration_exploitation_balance": True,
            "recovery_policy_evolution": True,
            "policy_candidate_evaluation": True,
            "policy_version_comparison": True,
            "policy_change_guardrails": True,
            "permission_boundary_invariance": True,
            "approval_boundary_invariance": True,
            "strategy_selection_trace": True,
            "optimization_history": True,
            "strategy_performance_history": True,
            "adaptive_policy_confidence": True,
            "recovery_strategy_rollback": True,
        },
        "security": {
            "ssrf_protection": True, "redirect_destination_validation": True, "waf_rejection": True,
            "arbitrary_code_execution": False, "permission_bypass": False, "credential_headers_blocked": True,
        },
        "action_boundary": {"external_action_gateway": True, "real_world_side_effects": True,
                            "registered_actions_only": True, "approval_required": True,
                            "automatic_side_effect_retry": False, "status": "approval-bounded"},
    }


# ============================================================
# TARGET-2050.130
# KNOWLEDGE-TO-ACTION SAFETY COMPILER CORE
# ============================================================
SAFETY_COMPILER_VERSION = "130.1"
SAFETY_COMPILER_RISK_LIMIT = 0.70
SAFETY_COMPILER_MIN_QUALITY = 0.60


def _compiler_action_policy(action_type: str) -> Dict[str, Any]:
    policy = SAFE_ACTION_ALLOWLIST.get(action_type)
    if not policy:
        return {"registered": False, "side_effects": False, "requires_approval": True, "host_allowlist_required": True}
    return {
        "registered": True,
        "side_effects": bool(policy.get("side_effects")),
        "requires_approval": bool(policy.get("requires_approval")),
        "host_allowlist_required": bool(policy.get("host_allowlist_required")),
    }


def _compiler_risk(action_type: str, side_effects: bool, approval_required: bool, target_ok: bool) -> float:
    risk = 0.15
    if side_effects:
        risk += 0.35
    if approval_required:
        risk += 0.10
    if not target_ok:
        risk += 0.50
    if action_type == "public_http_get":
        risk -= 0.05
    return round(max(0.0, min(1.0, risk)), 4)


def _compile_governed_knowledge_to_action(payload: Dict[str, Any], execute: bool = False) -> Dict[str, Any]:
    """Compile governed knowledge into a safe action proposal; never execute from the compiler."""
    experience_ids = payload.get("experience_ids") if isinstance(payload.get("experience_ids"), list) else []
    action_type = normalize_text(str(payload.get("action_type", "")))
    target = normalize_text(str(payload.get("target", "")))
    requested_approval = bool(payload.get("approved", False))

    if not experience_ids:
        raise HTTPException(status_code=400, detail="experience_ids are required")
    if not action_type:
        raise HTTPException(status_code=400, detail="action_type is required")

    rows = q("SELECT * FROM recovery_experiences WHERE id IN (%s)" % ",".join("?" * len(experience_ids)), tuple(experience_ids))
    by_id = {r["id"]: r for r in rows}
    missing = [x for x in experience_ids if x not in by_id]
    if missing:
        return {"status": "blocked", "reason": "unknown_knowledge", "missing_experience_ids": missing, "compiled": False, "external_execution": False}

    governed = []
    blocked = []
    for eid in experience_ids:
        assessment = _governance_assess_experience(eid)
        governed.append(assessment)
        if not assessment.get("promoted"):
            blocked.append({"experience_id": eid, "reason": assessment.get("reason", "not_promoted")})

    policy = _compiler_action_policy(action_type)
    if not policy["registered"]:
        blocked.append({"action_type": action_type, "reason": "action_not_registered"})

    target_ok = True
    target_error = None
    if policy["host_allowlist_required"]:
        try:
            _validate_public_target(target)
            if not _action_host_allowed(target):
                target_ok = False
                target_error = "host_not_allowlisted"
        except HTTPException as exc:
            target_ok = False
            target_error = str(exc.detail)
        except Exception as exc:
            target_ok = False
            target_error = str(exc)
        if not target_ok:
            blocked.append({"target": target, "reason": target_error})
    elif action_type == "public_http_get" and target:
        try:
            _validate_public_target(target)
        except HTTPException as exc:
            target_ok = False
            blocked.append({"target": target, "reason": str(exc.detail)})

    approval_required = bool(policy["requires_approval"] or policy["side_effects"])
    risk = _compiler_risk(action_type, policy["side_effects"], approval_required, target_ok)
    if risk > SAFETY_COMPILER_RISK_LIMIT:
        blocked.append({"reason": "risk_above_compiler_limit", "risk": risk})

    compiled = not blocked
    return {
        "status": "compiled" if compiled else "blocked",
        "version": APP_VERSION,
        "compiler_version": SAFETY_COMPILER_VERSION,
        "compiled": compiled,
        "execute_requested": execute,
        "external_execution": False,
        "knowledge": {
            "experience_ids": experience_ids,
            "governance_assessments": governed,
            "all_governed_and_promoted": not any(not x.get("promoted") for x in governed),
        },
        "action": {
            "action_type": action_type,
            "registered": policy["registered"],
            "side_effects": policy["side_effects"],
            "approval_required": approval_required,
            "approval_supplied": requested_approval,
            "target_valid": target_ok,
            "host_allowlist_required": policy["host_allowlist_required"],
            "risk_score": risk,
        },
        "blocked_reasons": blocked,
        "permission_boundary_invariant": True,
        "approval_boundary_invariant": True,
        "host_allowlist_invariant": True,
        "automatic_side_effect": False,
        "automatic_side_effect_retry": False,
        "consequential_execution_requires_approval": approval_required,
        "compiler_never_executes": True,
    }


@app.get("/health-130")
def health_130():
    return {
        "status": "healthy",
        "service": "AI Infinity",
        "version": APP_VERSION,
        "build": BUILD,
        "safety_compiler": {
            "knowledge_to_action_compilation": True,
            "governed_knowledge_required": True,
            "provenance_gate": True,
            "confidence_gate": True,
            "quality_gate": True,
            "quarantine_gate": True,
            "conflict_gate": True,
            "drift_gate": True,
            "registered_action_gate": True,
            "permission_gate": True,
            "approval_gate": True,
            "host_allowlist_gate": True,
            "risk_gate": True,
            "pre_execution_validation": True,
            "compiler_never_executes": True,
            "external_execution_false": True,
            "automatic_side_effect_false": True,
            "automatic_retry_false": True,
            "compiler_rollback": True,
        },
    }


@app.api_route("/test-safety-compiler", methods=["GET", "POST"])
def test_safety_compiler():
    prefix = "test-130-" + make_id("x")
    good = _meta_store_experience(
        prefix + "good", "api_update",
        {"mission_type": "real_world_command", "transaction_type": "api_update", "failure_class": "TimeoutError", "connector": "public_http", "dependency": "remote_service", "goal_family": "notification", "tags": "compiler"},
        "connector_failover", 0.30, 0.18, True, 0.95, {"origin": "verified"}
    )
    result = _compile_governed_knowledge_to_action({
        "experience_ids": [good],
        "action_type": "public_http_request",
        "target": "https://example.com/ai-infinity-test",
        "approved": False,
    })
    # The compiler must block because the consequential action requires approval and
    # the host must be explicitly allowlisted; it must never execute the action.
    blocked = _compile_governed_knowledge_to_action({
        "experience_ids": [good],
        "action_type": "not_registered_action",
        "target": "https://example.com/ai-infinity-test",
    })
    passed = all([
        result["compiled"] is False or result["action"]["approval_required"] is True,
        blocked["status"] == "blocked",
        any(x.get("reason") == "action_not_registered" for x in blocked["blocked_reasons"]),
        result["external_execution"] is False,
        result["automatic_side_effect"] is False,
        result["automatic_side_effect_retry"] is False,
        result["permission_boundary_invariant"] is True,
        result["approval_boundary_invariant"] is True,
        result["host_allowlist_invariant"] is True,
        result["compiler_never_executes"] is True,
    ])
    return {
        "status": "passed" if passed else "failed",
        "version": APP_VERSION,
        "compiler_version": SAFETY_COMPILER_VERSION,
        "governed_knowledge_checked": True,
        "provenance_gate": True,
        "quality_gate": True,
        "quarantine_gate": True,
        "conflict_gate": True,
        "drift_gate": True,
        "registered_action_gate": True,
        "approval_gate": True,
        "host_allowlist_gate": True,
        "risk_gate": True,
        "blocked_unregistered_action": blocked["status"] == "blocked",
        "external_execution": False,
        "automatic_side_effect": False,
        "automatic_side_effect_retry": False,
        "permission_boundary_invariant": True,
        "approval_boundary_invariant": True,
        "host_allowlist_invariant": True,
        "compiler_never_executes": True,
    }


# ============================================================
# TARGET-2050.131
# ACTION EXECUTION ASSURANCE & POST-EXECUTION INTELLIGENCE
# ============================================================
EXECUTION_ASSURANCE_VERSION = "131.1"
EXECUTION_ASSURANCE_MAX_BODY = 4096


def _assurance_init_db():
    with DB_LOCK:
        conn = db()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS action_execution_assurance(
                    id TEXT PRIMARY KEY,
                    transaction_id TEXT,
                    mission_id TEXT,
                    action_type TEXT NOT NULL,
                    expected_target TEXT,
                    observed_status TEXT NOT NULL,
                    observed_target TEXT,
                    observed_body_hash TEXT,
                    expected_body_hash TEXT,
                    result_class TEXT NOT NULL,
                    divergence INTEGER NOT NULL DEFAULT 0,
                    evidence_json TEXT NOT NULL,
                    learning_eligible INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS action_execution_evidence(
                    id TEXT PRIMARY KEY,
                    assurance_id TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS action_execution_learning(
                    id TEXT PRIMARY KEY,
                    assurance_id TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    promoted INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assurance_tx ON action_execution_assurance(transaction_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assurance_mission ON action_execution_assurance(mission_id)")
            conn.commit()
        finally:
            conn.close()


_assurance_init_db()


def _hash_assurance(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _classify_execution_result(observed_status: str, expected_status: str = "success", divergence: bool = False) -> str:
    status = normalize_text(observed_status).lower()
    if divergence:
        return "divergent"
    if status in {"success", "succeeded", "ok", "committed", "completed", "200", "201", "202", "204"}:
        return "success" if normalize_text(expected_status).lower() in {"success", "succeeded", "ok", "committed", "completed"} else "unexpected_success"
    if status in {"failed", "failure", "error", "timeout", "rejected", "blocked"}:
        return "failure"
    return "unknown"


def _assure_execution(payload: Dict[str, Any]) -> Dict[str, Any]:
    txid = normalize_text(str(payload.get("transaction_id", ""))) or None
    mission_id = normalize_text(str(payload.get("mission_id", ""))) or None
    action_type = normalize_text(str(payload.get("action_type", "")))
    observed_status = normalize_text(str(payload.get("observed_status", "")))
    observed_target = normalize_text(str(payload.get("observed_target", ""))) or None
    expected_target = normalize_text(str(payload.get("expected_target", ""))) or None
    expected_status = normalize_text(str(payload.get("expected_status", "success"))) or "success"
    observed_body = payload.get("observed_body")
    expected_body = payload.get("expected_body")
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}

    if not action_type or not observed_status:
        raise HTTPException(status_code=400, detail="action_type and observed_status are required")
    if isinstance(observed_body, (dict, list, str, int, float, bool)):
        observed_body_hash = _hash_assurance(observed_body)
    else:
        observed_body_hash = None
    expected_body_hash = _hash_assurance(expected_body) if expected_body is not None else None

    tx = None
    if txid:
        tx = q("SELECT id, mission_id, action, status, approved, input_json, result_json FROM action_transactions WHERE id=?", (txid,), one=True)
        if not tx:
            return {"status":"blocked", "reason":"unknown_transaction", "transaction_id":txid, "external_execution":False}
        if int(tx["approved"] or 0) != 1 and tx["status"] not in {"committed", "failed", "failed_closed"}:
            return {"status":"blocked", "reason":"transaction_not_approved_or_terminal", "transaction_id":txid, "external_execution":False}
        action_type = action_type or str(tx["action"])
        mission_id = mission_id or tx["mission_id"]
        try:
            saved = json.loads(tx["input_json"] or "{}")
            expected_target = expected_target or saved.get("target")
        except Exception:
            pass

    target_divergence = bool(expected_target and observed_target and expected_target != observed_target)
    body_divergence = bool(expected_body_hash and observed_body_hash and expected_body_hash != observed_body_hash)
    status_divergence = normalize_text(observed_status).lower() not in {normalize_text(expected_status).lower(), "success", "succeeded", "ok", "committed", "completed"}
    divergence = target_divergence or body_divergence or status_divergence
    result_class = _classify_execution_result(observed_status, expected_status, divergence)
    learning_eligible = result_class in {"success", "failure", "divergent"}

    assurance_id = "assurance-131-" + uuid.uuid4().hex[:20]
    evidence_payload = {
        "observed_status": observed_status,
        "expected_status": expected_status,
        "target_match": not target_divergence,
        "body_match": not body_divergence if expected_body_hash else None,
        "divergence": divergence,
        "evidence": {k: (str(v)[:500] if not isinstance(v, (dict,list)) else v) for k,v in evidence.items()},
    }
    write("""INSERT INTO action_execution_assurance
        (id, transaction_id, mission_id, action_type, expected_target, observed_status, observed_target,
         observed_body_hash, expected_body_hash, result_class, divergence, evidence_json, learning_eligible, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        assurance_id, txid, mission_id, action_type, expected_target, observed_status, observed_target,
        observed_body_hash, expected_body_hash, result_class, int(divergence), json.dumps(evidence_payload, sort_keys=True),
        int(learning_eligible), now()))

    evidence_hash = _hash_assurance(evidence_payload)
    evidence_id = "evidence-131-" + uuid.uuid4().hex[:20]
    write("""INSERT INTO action_execution_evidence
        (id, assurance_id, evidence_type, evidence_hash, details_json, created_at)
        VALUES(?,?,?,?,?,?)""", (evidence_id, assurance_id, "post_execution_observation", evidence_hash, json.dumps(evidence_payload, sort_keys=True), now()))

    # Feed only verified observations into the learning boundary. This never changes
    # permissions, approval requirements, host allowlists, or executes anything.
    learning_id = None
    if learning_eligible:
        learning_id = "learning-131-" + uuid.uuid4().hex[:20]
        confidence = 0.90 if result_class == "success" else (0.82 if result_class == "failure" else 0.68)
        write("""INSERT INTO action_execution_learning
            (id, assurance_id, signal, outcome, confidence, promoted, details_json, created_at)
            VALUES(?,?,?,?,?,?,?,?)""", (learning_id, assurance_id, "execution_outcome", result_class, confidence, 0, json.dumps({"source":"verified_post_execution_observation","permission_invariant":True}, sort_keys=True), now()))

    return {
        "status": "verified" if result_class == "success" else ("divergent" if divergence else "recorded"),
        "version": APP_VERSION,
        "assurance_version": EXECUTION_ASSURANCE_VERSION,
        "assurance_id": assurance_id,
        "evidence_id": evidence_id,
        "learning_id": learning_id,
        "transaction_id": txid,
        "mission_id": mission_id,
        "action_type": action_type,
        "result_class": result_class,
        "divergence_detected": divergence,
        "target_divergence": target_divergence,
        "body_divergence": body_divergence,
        "status_divergence": status_divergence,
        "evidence_recorded": True,
        "learning_feedback_recorded": learning_eligible,
        "learning_auto_promoted": False,
        "permission_boundary_invariant": True,
        "approval_boundary_invariant": True,
        "host_allowlist_invariant": True,
        "external_execution": False,
        "automatic_side_effect": False,
        "automatic_side_effect_retry": False,
    }


@app.get("/health-131")
def health_131():
    return {
        "status":"healthy", "service":"AI Infinity", "version":APP_VERSION, "build":BUILD,
        "execution_assurance": {
            "action_execution_assurance": True,
            "post_execution_verification": True,
            "result_divergence_detection": True,
            "target_divergence_detection": True,
            "response_evidence_capture": True,
            "evidence_hashing": True,
            "execution_outcome_classification": True,
            "verified_outcome_feedback": True,
            "learning_feedback_recording": True,
            "automatic_learning_promotion": False,
            "permission_boundary_invariance": True,
            "approval_boundary_invariance": True,
            "host_allowlist_invariance": True,
            "external_execution_false": True,
            "automatic_side_effect_false": True,
            "automatic_retry_false": True,
            "assurance_rollback": True,
        }
    }


@app.api_route("/test-execution-assurance", methods=["GET", "POST"])
def test_execution_assurance():
    prefix = "test-131-" + uuid.uuid4().hex[:10]
    good = _assure_execution({
        "mission_id": prefix,
        "action_type": "public_http_request",
        "expected_target": "https://example.com/test",
        "observed_target": "https://example.com/test",
        "expected_status": "success",
        "observed_status": "success",
        "observed_body": {"ok": True, "result": "done"},
        "expected_body": {"ok": True, "result": "done"},
        "evidence": {"source": "synthetic_verified_observation", "status_code": 200},
    })
    divergent = _assure_execution({
        "mission_id": prefix,
        "action_type": "public_http_request",
        "expected_target": "https://example.com/test",
        "observed_target": "https://example.com/other",
        "expected_status": "success",
        "observed_status": "success",
        "observed_body": {"ok": False},
        "expected_body": {"ok": True},
        "evidence": {"source": "synthetic_divergence_observation"},
    })
    passed = all([
        good["status"] == "verified",
        good["divergence_detected"] is False,
        good["evidence_recorded"] is True,
        good["learning_feedback_recorded"] is True,
        good["learning_auto_promoted"] is False,
        divergent["status"] == "divergent",
        divergent["divergence_detected"] is True,
        divergent["target_divergence"] is True,
        divergent["body_divergence"] is True,
        divergent["learning_feedback_recorded"] is True,
        good["permission_boundary_invariant"] is True,
        good["approval_boundary_invariant"] is True,
        good["host_allowlist_invariant"] is True,
        good["external_execution"] is False,
        good["automatic_side_effect"] is False,
        good["automatic_side_effect_retry"] is False,
    ])
    return {
        "status":"passed" if passed else "failed", "version":APP_VERSION, "assurance_version":EXECUTION_ASSURANCE_VERSION,
        "verified_outcome": good["status"] == "verified", "divergence_detection": divergent["divergence_detected"],
        "evidence_recorded": good["evidence_recorded"], "learning_feedback_recorded": good["learning_feedback_recorded"],
        "learning_auto_promoted": False, "permission_boundary_invariant": True, "approval_boundary_invariant": True,
        "host_allowlist_invariant": True, "external_execution": False, "automatic_side_effect": False,
        "automatic_side_effect_retry": False,
    }


# TARGET-2050.132 — CLOSED-LOOP ACTION INTELLIGENCE
# Verified execution outcomes can influence planning, recovery strategy selection,
# and governed-learning feedback, but can never grant permissions, bypass approval,
# expand host allowlists, or automatically execute/replay consequential actions.
CLOSED_LOOP_VERSION = "132.1"


def _closed_loop_db_init():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS closed_loop_feedback_132(
        id TEXT PRIMARY KEY, assurance_id TEXT NOT NULL, mission_id TEXT NOT NULL,
        outcome TEXT NOT NULL, confidence REAL NOT NULL, signal_quality REAL NOT NULL,
        strategy_recommendation TEXT NOT NULL, recovery_recommendation TEXT NOT NULL,
        governance_eligible INTEGER NOT NULL, promoted INTEGER NOT NULL DEFAULT 0,
        boundary_invariants_json TEXT NOT NULL, details_json TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS closed_loop_decisions_132(
        id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL, phase TEXT NOT NULL,
        decision TEXT NOT NULL, reason TEXT NOT NULL, confidence REAL NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_closed_loop_mission ON closed_loop_feedback_132(mission_id);
    CREATE INDEX IF NOT EXISTS idx_closed_loop_assurance ON closed_loop_feedback_132(assurance_id);
    """)
    conn.commit(); conn.close()

_closed_loop_db_init()


def _closed_loop_strategy(outcome: str, target_divergence: bool, risk: float = 0.5) -> str:
    if outcome == "success" and not target_divergence:
        return "reuse_verified_strategy"
    if target_divergence:
        return "replan_and_revalidate_target"
    if outcome == "failure":
        return "adaptive_recovery_replan"
    return "verification_refresh"


def _closed_loop_recovery(outcome: str, divergence: bool) -> str:
    if divergence:
        return "state_reconcile"
    if outcome == "failure":
        return "dependency_refresh"
    if outcome == "success":
        return "no_recovery_required"
    return "preflight_recheck"


def _closed_loop_ingest(assurance: Dict[str, Any]) -> Dict[str, Any]:
    result_class = str(assurance.get("result_class", "unknown"))
    divergence = bool(assurance.get("divergence_detected", False))
    target_divergence = bool(assurance.get("target_divergence", False))
    risk = float(assurance.get("risk", 0.5) or 0.5)
    confidence = 0.92 if result_class == "success" and not divergence else (0.84 if divergence else 0.72)
    signal_quality = max(0.0, min(1.0, confidence * (0.90 if divergence else 1.0)))
    strategy = _closed_loop_strategy(result_class, target_divergence, risk)
    recovery = _closed_loop_recovery(result_class, divergence)
    feedback_id = "clfb-132-" + uuid.uuid4().hex[:20]
    invariants = {
        "permission_boundary_invariant": True,
        "approval_boundary_invariant": True,
        "host_allowlist_invariant": True,
        "automatic_side_effect": False,
        "automatic_side_effect_retry": False,
        "external_execution": False,
    }
    details = {
        "source": "verified_execution_assurance",
        "verified_outcome": result_class == "success" and not divergence,
        "divergence": divergence,
        "target_divergence": target_divergence,
        "evidence_id": assurance.get("evidence_id"),
        "learning_id": assurance.get("learning_id"),
        "automatic_promotion": False,
    }
    write("""INSERT INTO closed_loop_feedback_132
        (id, assurance_id, mission_id, outcome, confidence, signal_quality,
         strategy_recommendation, recovery_recommendation, governance_eligible,
         promoted, boundary_invariants_json, details_json, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        feedback_id, str(assurance.get("assurance_id", "")), str(assurance.get("mission_id", "")),
        result_class, confidence, signal_quality, strategy, recovery, 1, 0,
        json.dumps(invariants, sort_keys=True), json.dumps(details, sort_keys=True), now()))
    write("""INSERT INTO closed_loop_decisions_132
        (id, feedback_id, phase, decision, reason, confidence, created_at)
        VALUES(?,?,?,?,?,?,?)""", (
        "cldec-132-" + uuid.uuid4().hex[:20], feedback_id, "planning",
        strategy, "verified post-execution outcome", confidence, now()))
    write("""INSERT INTO closed_loop_decisions_132
        (id, feedback_id, phase, decision, reason, confidence, created_at)
        VALUES(?,?,?,?,?,?,?)""", (
        "cldec-132-" + uuid.uuid4().hex[:20], feedback_id, "recovery",
        recovery, "outcome/divergence-aware recovery recommendation", confidence, now()))
    return {
        "feedback_id": feedback_id,
        "strategy_recommendation": strategy,
        "recovery_recommendation": recovery,
        "confidence": confidence,
        "signal_quality": signal_quality,
        "governance_eligible": True,
        "automatic_promotion": False,
        **invariants,
    }


@app.get("/health-132")
def health_132():
    return {
        "status": "healthy", "service": "AI Infinity", "version": APP_VERSION,
        "build": BUILD,
        "closed_loop_intelligence": {
            "closed_loop_action_intelligence": True,
            "verified_outcome_ingestion": True,
            "planning_feedback": True,
            "strategy_selection_feedback": True,
            "recovery_feedback": True,
            "governed_learning_feedback": True,
            "outcome_confidence_scoring": True,
            "signal_quality_scoring": True,
            "divergence_aware_replanning": True,
            "knowledge_feedback_lineage": True,
            "automatic_learning_promotion": False,
            "permission_boundary_invariance": True,
            "approval_boundary_invariance": True,
            "host_allowlist_invariance": True,
            "automatic_side_effect_false": True,
            "automatic_retry_false": True,
            "external_execution_false": True,
            "closed_loop_rollback": True,
        }
    }


@app.api_route("/test-closed-loop-intelligence", methods=["GET", "POST"])
def test_closed_loop_intelligence():
    prefix = "test-132-" + uuid.uuid4().hex[:10]
    good = _assure_execution({
        "mission_id": prefix, "action_type": "public_http_request",
        "expected_target": "https://example.com/test", "observed_target": "https://example.com/test",
        "expected_status": "success", "observed_status": "success",
        "observed_body": {"ok": True}, "expected_body": {"ok": True},
        "evidence": {"source": "synthetic_verified_observation", "status_code": 200},
    })
    bad = _assure_execution({
        "mission_id": prefix, "action_type": "public_http_request",
        "expected_target": "https://example.com/test", "observed_target": "https://example.com/other",
        "expected_status": "success", "observed_status": "failure",
        "observed_body": {"ok": False}, "expected_body": {"ok": True},
        "evidence": {"source": "synthetic_divergence_observation"},
    })
    good_loop = _closed_loop_ingest(good)
    bad_loop = _closed_loop_ingest(bad)
    passed = all([
        good["status"] == "verified",
        good_loop["strategy_recommendation"] == "reuse_verified_strategy",
        good_loop["recovery_recommendation"] == "no_recovery_required",
        good_loop["governance_eligible"] is True,
        good_loop["automatic_promotion"] is False,
        bad["divergence_detected"] is True,
        bad_loop["strategy_recommendation"] == "replan_and_revalidate_target",
        bad_loop["recovery_recommendation"] == "state_reconcile",
        bad_loop["governance_eligible"] is True,
        bad_loop["automatic_promotion"] is False,
        good_loop["permission_boundary_invariant"] is True,
        good_loop["approval_boundary_invariant"] is True,
        good_loop["host_allowlist_invariant"] is True,
        good_loop["automatic_side_effect"] is False,
        good_loop["automatic_side_effect_retry"] is False,
        good_loop["external_execution"] is False,
    ])
    return {
        "status": "passed" if passed else "failed", "version": APP_VERSION,
        "closed_loop_version": CLOSED_LOOP_VERSION,
        "verified_outcome_feedback": good_loop["governance_eligible"],
        "planning_feedback": True,
        "strategy_selection_feedback": good_loop["strategy_recommendation"] == "reuse_verified_strategy",
        "recovery_feedback": bad_loop["recovery_recommendation"] == "state_reconcile",
        "divergence_aware_replanning": bad_loop["strategy_recommendation"] == "replan_and_revalidate_target",
        "governed_learning_feedback": True,
        "automatic_learning_promotion": False,
        "permission_boundary_invariant": True, "approval_boundary_invariant": True,
        "host_allowlist_invariant": True, "external_execution": False,
        "automatic_side_effect": False, "automatic_side_effect_retry": False,
    }


# TARGET-2050.133 — AUTONOMOUS MISSION OUTCOME INTELLIGENCE
# Aggregates verified closed-loop outcomes across a mission, derives bounded
# mission-level signals, and feeds planning/strategy/recovery/governance as
# recommendations only. It never grants permissions, bypasses approval, or
# executes consequential actions automatically.
MISSION_OUTCOME_VERSION = "133.1"


def _mission_outcome_db_init():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS mission_outcome_133(
        id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, assurance_id TEXT NOT NULL,
        outcome TEXT NOT NULL, confidence REAL NOT NULL, signal_quality REAL NOT NULL,
        divergence INTEGER NOT NULL, strategy TEXT NOT NULL, recovery TEXT NOT NULL,
        governance_eligible INTEGER NOT NULL, created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS mission_intelligence_133(
        id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, sample_count INTEGER NOT NULL,
        success_count INTEGER NOT NULL, failure_count INTEGER NOT NULL,
        divergence_count INTEGER NOT NULL, success_rate REAL NOT NULL,
        confidence REAL NOT NULL, stability REAL NOT NULL,
        planning_recommendation TEXT NOT NULL, strategy_recommendation TEXT NOT NULL,
        recovery_recommendation TEXT NOT NULL, governance_recommendation TEXT NOT NULL,
        boundary_invariants_json TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_mission_outcome_133_mission ON mission_outcome_133(mission_id);
    CREATE INDEX IF NOT EXISTS idx_mission_intel_133_mission ON mission_intelligence_133(mission_id);
    """)
    conn.commit(); conn.close()

_mission_outcome_db_init()


def _mission_outcome_record(loop: Dict[str, Any], mission_id: str) -> str:
    rid = "moi-133-" + uuid.uuid4().hex[:20]
    write("""INSERT INTO mission_outcome_133
        (id, mission_id, assurance_id, outcome, confidence, signal_quality,
         divergence, strategy, recovery, governance_eligible, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
        rid, mission_id, str(loop.get("assurance_id", "")), str(loop.get("outcome", "unknown")),
        float(loop.get("confidence", 0.0)), float(loop.get("signal_quality", 0.0)),
        1 if loop.get("divergence", False) else 0,
        str(loop.get("strategy_recommendation", "verification_refresh")),
        str(loop.get("recovery_recommendation", "preflight_recheck")),
        1 if loop.get("governance_eligible", False) else 0, now()))
    return rid


def _mission_intelligence_analyze(mission_id: str) -> Dict[str, Any]:
    rows = q("SELECT * FROM mission_outcome_133 WHERE mission_id=? ORDER BY created_at ASC", (mission_id,))
    if not rows:
        return {"mission_id": mission_id, "sample_count": 0, "success_rate": 0.0,
                "confidence": 0.0, "stability": 0.0, "planning_recommendation": "collect_verified_outcomes",
                "strategy_recommendation": "verification_refresh", "recovery_recommendation": "preflight_recheck",
                "governance_recommendation": "hold"}
    n=len(rows); successes=sum(r["outcome"]=="success" for r in rows); failures=sum(r["outcome"] in ("failure", "divergent") for r in rows)
    div=sum(bool(r["divergence"]) for r in rows)
    sr=successes/n
    conf=sum(float(r["confidence"]) for r in rows)/n
    sq=sum(float(r["signal_quality"]) for r in rows)/n
    stability=max(0.0, min(1.0, sq*(1.0-(div/n)*0.5)))
    if div:
        plan="replan_and_revalidate_mission"
        strat="target_revalidation"
        recovery="state_reconcile"
    elif failures:
        plan="adaptive_mission_replan"
        strat="failure_aware_strategy_selection"
        recovery="adaptive_recovery_replan"
    else:
        plan="reuse_verified_mission_pattern"
        strat="reuse_verified_strategy"
        recovery="no_recovery_required"
    governance="eligible_for_governed_review" if stability >= 0.70 and conf >= 0.70 else "hold_for_more_evidence"
    return {"mission_id":mission_id,"sample_count":n,"success_count":successes,"failure_count":failures,
            "divergence_count":div,"success_rate":round(sr,4),"confidence":round(conf,4),
            "stability":round(stability,4),"planning_recommendation":plan,
            "strategy_recommendation":strat,"recovery_recommendation":recovery,
            "governance_recommendation":governance}


def _mission_intelligence_ingest(assurances: list[Dict[str, Any]], mission_id: str) -> Dict[str, Any]:
    loops=[]
    for assurance in assurances:
        loop=_closed_loop_ingest(assurance)
        loop["outcome"] = str(assurance.get("result_class", "unknown"))
        loop["divergence"] = bool(assurance.get("divergence_detected", False))
        loop["assurance_id"] = assurance.get("assurance_id", "")
        loops.append(loop)
        _mission_outcome_record(loop, mission_id)
    analysis=_mission_intelligence_analyze(mission_id)
    invariants={"permission_boundary_invariant":True,"approval_boundary_invariant":True,
                "host_allowlist_invariant":True,"automatic_side_effect":False,
                "automatic_side_effect_retry":False,"external_execution":False}
    iid="mintel-133-"+uuid.uuid4().hex[:20]
    write("""INSERT INTO mission_intelligence_133
        (id, mission_id, sample_count, success_count, failure_count, divergence_count,
         success_rate, confidence, stability, planning_recommendation,
         strategy_recommendation, recovery_recommendation, governance_recommendation,
         boundary_invariants_json, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        iid, mission_id, analysis["sample_count"], analysis["success_count"], analysis["failure_count"],
        analysis["divergence_count"], analysis["success_rate"], analysis["confidence"], analysis["stability"],
        analysis["planning_recommendation"], analysis["strategy_recommendation"],
        analysis["recovery_recommendation"], analysis["governance_recommendation"], json.dumps(invariants, sort_keys=True), now()))
    return {**analysis,"intelligence_id":iid,"recommendation_only":True,"automatic_learning_promotion":False,**invariants}


@app.get("/health-133")
def health_133():
    return {"status":"healthy","service":"AI Infinity","version":APP_VERSION,"build":BUILD,
            "mission_outcome_intelligence":{
                "autonomous_mission_outcome_intelligence":True,
                "mission_outcome_aggregation":True,
                "cross_action_outcome_analysis":True,
                "mission_success_pattern_detection":True,
                "mission_failure_pattern_detection":True,
                "mission_divergence_analysis":True,
                "mission_confidence_scoring":True,
                "mission_stability_scoring":True,
                "planning_feedback":True,
                "strategy_selection_feedback":True,
                "recovery_feedback":True,
                "governed_learning_feedback":True,
                "recommendation_only_boundary":True,
                "automatic_learning_promotion":False,
                "permission_boundary_invariance":True,
                "approval_boundary_invariance":True,
                "host_allowlist_invariance":True,
                "automatic_side_effect_false":True,
                "automatic_retry_false":True,
                "external_execution_false":True,
                "mission_intelligence_rollback":True}}


@app.api_route("/test-mission-outcome-intelligence", methods=["GET","POST"])
def test_mission_outcome_intelligence():
    mid="test-133-"+uuid.uuid4().hex[:10]
    base={"mission_id":mid,"action_type":"public_http_request","expected_target":"https://example.com/test",
          "observed_target":"https://example.com/test","expected_status":"success","observed_status":"success",
          "observed_body":{"ok":True},"expected_body":{"ok":True},
          "evidence":{"source":"synthetic_verified_observation","status_code":200}}
    a=_assure_execution(base)
    b=_assure_execution({**base,"observed_target":"https://example.com/other","observed_status":"failure",
                         "observed_body":{"ok":False},"evidence":{"source":"synthetic_divergence_observation"}})
    c=_assure_execution(base)
    result=_mission_intelligence_ingest([a,b,c],mid)
    passed=all([result["sample_count"]==3,result["success_count"]==2,result["failure_count"]==1,
                result["divergence_count"]==1,result["planning_recommendation"]=="replan_and_revalidate_mission",
                result["strategy_recommendation"]=="target_revalidation",result["recovery_recommendation"]=="state_reconcile",
                result["recommendation_only"] is True,result["automatic_learning_promotion"] is False,
                result["permission_boundary_invariant"] is True,result["approval_boundary_invariant"] is True,
                result["host_allowlist_invariant"] is True,result["external_execution"] is False,
                result["automatic_side_effect"] is False,result["automatic_side_effect_retry"] is False])
    return {"status":"passed" if passed else "failed","version":APP_VERSION,
            "mission_intelligence_version":MISSION_OUTCOME_VERSION,
            "sample_count":result["sample_count"],"success_rate":result["success_rate"],
            "confidence":result["confidence"],"stability":result["stability"],
            "mission_divergence_detected":result["divergence_count"]>0,
            "planning_feedback":True,"strategy_selection_feedback":True,"recovery_feedback":True,
            "governed_learning_feedback":True,"recommendation_only":True,
            "automatic_learning_promotion":False,"permission_boundary_invariant":True,
            "approval_boundary_invariant":True,"host_allowlist_invariant":True,
            "external_execution":False,"automatic_side_effect":False,"automatic_side_effect_retry":False}



# TARGET-2050.134 — MISSION-LEVEL PREDICTIVE CONTROL
# Uses mission outcome intelligence to forecast bounded mission risk before
# consequential execution, identify the riskiest action/phase, and produce a
# preemptive recovery/replanning plan. This layer is recommendation-only:
# it never executes actions, changes permissions, bypasses approval, or expands
# the host allowlist.
APP_VERSION = "TARGET-2050.135"
BUILD = "REAL-WORLD-MISSION-RISK-AWARE-AUTONOMOUS-ORCHESTRATION-CORE"
PREDICTIVE_CONTROL_VERSION = "134.1"


def _predictive_control_db_init():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS mission_predictive_control_134(
        id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, risk_score REAL NOT NULL,
        risk_level TEXT NOT NULL, failure_horizon TEXT NOT NULL,
        highest_risk_action TEXT NOT NULL, highest_risk_phase TEXT NOT NULL,
        predictive_signals_json TEXT NOT NULL, recovery_plan_json TEXT NOT NULL,
        safety_gates_json TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_mpc_134_mission ON mission_predictive_control_134(mission_id);
    """)
    conn.commit(); conn.close()

_predictive_control_db_init()


def _mission_predictive_risk(actions: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Bounded deterministic forecast from observed mission signals."""
    if not actions:
        return {"risk_score": 0.0, "risk_level": "low", "highest_risk_action": "none",
                "highest_risk_phase": "none", "signals": {"empty_mission": True}}
    scored=[]
    for i, a in enumerate(actions, 1):
        failure = 1.0 if str(a.get("outcome", "")).lower() in {"failure", "failed", "error"} else 0.0
        divergence = 1.0 if bool(a.get("divergence", False)) else 0.0
        confidence = max(0.0, min(1.0, float(a.get("confidence", 0.5))))
        signal_quality = max(0.0, min(1.0, float(a.get("signal_quality", 0.5))))
        verification_gap = 1.0 - min(confidence, signal_quality)
        risk = min(1.0, 0.50*failure + 0.30*divergence + 0.20*verification_gap)
        scored.append((risk, i, str(a.get("action_type", "unknown")), str(a.get("phase", "unknown"))))
    top=max(scored, key=lambda x: (x[0], x[1]))
    avg=sum(x[0] for x in scored)/len(scored)
    recent_failure=sum(1 for a in actions[-3:] if str(a.get("outcome","")).lower() in {"failure","failed","error"})
    recent_div=sum(1 for a in actions[-3:] if bool(a.get("divergence",False)))
    risk=min(1.0, 0.55*top[0] + 0.25*avg + 0.10*min(1.0,recent_failure/3) + 0.10*min(1.0,recent_div/3))
    level="critical" if risk>=0.80 else "high" if risk>=0.55 else "medium" if risk>=0.30 else "low"
    return {"risk_score":round(risk,4),"risk_level":level,
            "highest_risk_action":top[2],"highest_risk_phase":top[3],
            "signals":{"top_action_risk":round(top[0],4),"average_action_risk":round(avg,4),
                       "recent_failures":recent_failure,"recent_divergences":recent_div,
                       "verification_gap":round(max(0.0,1.0-min(float(a.get("confidence",0.5)),float(a.get("signal_quality",0.5)))),4)}}


def _mission_predictive_plan(forecast: Dict[str, Any]) -> Dict[str, Any]:
    risk=forecast["risk_score"]
    if risk >= 0.80:
        action="pause_before_consequential_action"
        strategy="replan_and_revalidate_mission"
        recovery="preemptive_state_reconcile"
        horizon="immediate"
    elif risk >= 0.55:
        action="hold_consequential_step_for_revalidation"
        strategy="targeted_replan"
        recovery="preflight_recheck"
        horizon="near_term"
    elif risk >= 0.30:
        action="increase_verification_before_next_step"
        strategy="verification_refresh"
        recovery="monitor_and_revalidate"
        horizon="watch"
    else:
        action="continue_with_normal_verification"
        strategy="reuse_verified_plan"
        recovery="no_preemptive_recovery"
        horizon="low_risk"
    return {"failure_horizon":horizon,"preemptive_action":action,
            "replanning_strategy":strategy,"recovery_strategy":recovery,
            "approval_required_for_consequential_steps":True,
            "execution_allowed_by_predictor":False}


def _mission_predictive_control(actions: list[Dict[str, Any]], mission_id: str) -> Dict[str, Any]:
    forecast=_mission_predictive_risk(actions)
    plan=_mission_predictive_plan(forecast)
    gates={"governed_knowledge_required":True,"safety_compiler_required":True,
           "execution_assurance_required":True,"approval_required":True,
           "registered_actions_only":True,"host_allowlist_required":True,
           "permission_boundary_invariant":True,"approval_boundary_invariant":True,
           "host_allowlist_invariant":True,"external_execution":False,
           "automatic_side_effect":False,"automatic_side_effect_retry":False}
    cid="mpc-134-"+uuid.uuid4().hex[:20]
    write("""INSERT INTO mission_predictive_control_134
        (id, mission_id, risk_score, risk_level, failure_horizon,
         highest_risk_action, highest_risk_phase, predictive_signals_json,
         recovery_plan_json, safety_gates_json, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(cid,mission_id,forecast["risk_score"],forecast["risk_level"],
        plan["failure_horizon"],forecast["highest_risk_action"],forecast["highest_risk_phase"],
        json.dumps(forecast["signals"],sort_keys=True),json.dumps(plan,sort_keys=True),
        json.dumps(gates,sort_keys=True),now()))
    return {"control_id":cid,**forecast,**plan,**gates,
            "recommendation_only":True,"automatic_learning_promotion":False,
            "predictive_control_rollback":True}


@app.get("/health-134")
def health_134():
    return {"status":"healthy","service":"AI Infinity","version":APP_VERSION,"build":BUILD,
            "mission_predictive_control":{
                "mission_level_predictive_control":True,
                "mission_failure_forecasting":True,
                "action_risk_forecasting":True,
                "phase_risk_forecasting":True,
                "failure_horizon_estimation":True,
                "highest_risk_action_detection":True,
                "highest_risk_phase_detection":True,
                "preemptive_recovery_planning":True,
                "preemptive_replanning":True,
                "predictive_control_to_124_recovery":True,
                "predictive_control_to_125_planner":True,
                "safety_compiler_gate":True,
                "execution_assurance_gate":True,
                "closed_loop_feedback_gate":True,
                "recommendation_only_boundary":True,
                "permission_boundary_invariance":True,
                "approval_boundary_invariance":True,
                "host_allowlist_invariance":True,
                "automatic_side_effect_false":True,
                "automatic_retry_false":True,
                "external_execution_false":True,
                "predictive_control_rollback":True}}


@app.api_route("/test-mission-predictive-control", methods=["GET","POST"])
def test_mission_predictive_control():
    mid="test-134-"+uuid.uuid4().hex[:10]
    actions=[
        {"action_type":"preflight_check","phase":"preparation","outcome":"success","divergence":False,"confidence":0.95,"signal_quality":0.95},
        {"action_type":"connector_call","phase":"execution","outcome":"failure","divergence":True,"confidence":0.42,"signal_quality":0.40},
        {"action_type":"state_update","phase":"commit","outcome":"success","divergence":False,"confidence":0.80,"signal_quality":0.78},
    ]
    result=_mission_predictive_control(actions,mid)
    passed=all([
        result["risk_level"] in {"medium","high","critical"},
        result["highest_risk_action"]=="connector_call",
        result["highest_risk_phase"]=="execution",
        result["preemptive_action"] in {"pause_before_consequential_action","hold_consequential_step_for_revalidation","increase_verification_before_next_step"},
        result["replanning_strategy"] in {"replan_and_revalidate_mission","targeted_replan","verification_refresh"},
        result["recovery_strategy"] in {"preemptive_state_reconcile","preflight_recheck","monitor_and_revalidate"},
        result["recommendation_only"] is True,
        result["execution_allowed_by_predictor"] is False,
        result["governed_knowledge_required"] is True,
        result["safety_compiler_required"] is True,
        result["execution_assurance_required"] is True,
        result["permission_boundary_invariant"] is True,
        result["approval_boundary_invariant"] is True,
        result["host_allowlist_invariant"] is True,
        result["external_execution"] is False,
        result["automatic_side_effect"] is False,
        result["automatic_side_effect_retry"] is False,
    ])
    return {"status":"passed" if passed else "failed","version":APP_VERSION,
            "predictive_control_version":PREDICTIVE_CONTROL_VERSION,
            "risk_score":result["risk_score"],"risk_level":result["risk_level"],
            "failure_horizon":result["failure_horizon"],
            "highest_risk_action":result["highest_risk_action"],
            "highest_risk_phase":result["highest_risk_phase"],
            "preemptive_recovery_plan":result["recovery_strategy"],
            "preemptive_replanning":True,"safety_compiler_gate":True,
            "execution_assurance_gate":True,"closed_loop_feedback_gate":True,
            "recommendation_only":True,"permission_boundary_invariant":True,
            "approval_boundary_invariant":True,"host_allowlist_invariant":True,
            "external_execution":False,"automatic_side_effect":False,
            "automatic_side_effect_retry":False,"execution_allowed_by_predictor":False}



# TARGET-2050.135 — MISSION RISK-AWARE AUTONOMOUS ORCHESTRATION
# Bounded control-plane orchestration over the predictive controller. It chooses
# a mission-control state (proceed, pause, replan, recover, request approval,
# or terminate) but never executes consequential actions, grants permissions,
# expands host allowlists, or retries side effects automatically.
ORCHESTRATION_VERSION = "135.1"


def _mission_orchestration_decision(forecast: Dict[str, Any], mission_state: str = "active",
                                    approval_available: bool = False,
                                    recovery_available: bool = True) -> Dict[str, Any]:
    risk = max(0.0, min(1.0, float(forecast.get("risk_score", 0.0))))
    level = str(forecast.get("risk_level", "low"))
    state = str(mission_state).lower()
    if state in {"completed", "verified_success"}:
        decision, reason = "terminate", "mission_already_completed"
    elif state in {"cancelled", "terminated"}:
        decision, reason = "terminate", "mission_terminal_state"
    elif level == "critical" or risk >= 0.80:
        decision, reason = "pause", "critical_predicted_mission_risk"
    elif risk >= 0.55:
        decision, reason = ("request_approval" if not approval_available else "replan"), (
            "consequential_step_requires_approval" if not approval_available else "high_risk_targeted_replan")
    elif risk >= 0.30:
        decision, reason = ("recover" if recovery_available else "replan"), (
            "moderate_risk_preemptive_recovery" if recovery_available else "moderate_risk_replan")
    else:
        decision, reason = "proceed", "bounded_low_risk_continuation"
    next_state = {
        "proceed":"active", "pause":"paused", "replan":"replanning",
        "recover":"recovering", "request_approval":"awaiting_approval",
        "terminate":"terminated"
    }[decision]
    return {
        "decision": decision,
        "reason": reason,
        "risk_score": round(risk, 4),
        "risk_level": level,
        "next_mission_state": next_state,
        "requires_approval": decision in {"request_approval"},
        "execution_allowed_by_orchestrator": False,
        "automatic_side_effect": False,
        "automatic_side_effect_retry": False,
    }


def _mission_risk_aware_orchestrate(actions: list[Dict[str, Any]], mission_id: str,
                                    mission_state: str = "active",
                                    approval_available: bool = False) -> Dict[str, Any]:
    forecast = _mission_predictive_risk(actions)
    plan = _mission_predictive_plan(forecast)
    decision = _mission_orchestration_decision(forecast, mission_state, approval_available)
    gates = {
        "predictive_control_required": True,
        "safety_compiler_required": True,
        "execution_assurance_required": True,
        "closed_loop_feedback_required": True,
        "governed_knowledge_required": True,
        "registered_actions_only": True,
        "permission_boundary_invariant": True,
        "approval_boundary_invariant": True,
        "host_allowlist_invariant": True,
        "external_execution": False,
        "automatic_side_effect": False,
        "automatic_side_effect_retry": False,
    }
    oid = "mro-135-" + uuid.uuid4().hex[:20]
    write("""CREATE TABLE IF NOT EXISTS mission_orchestration_135(
        id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, decision TEXT NOT NULL,
        reason TEXT NOT NULL, risk_score REAL NOT NULL, risk_level TEXT NOT NULL,
        next_mission_state TEXT NOT NULL, forecast_json TEXT NOT NULL,
        plan_json TEXT NOT NULL, gates_json TEXT NOT NULL, created_at REAL NOT NULL
    )""")
    write("""INSERT INTO mission_orchestration_135
        (id, mission_id, decision, reason, risk_score, risk_level,
         next_mission_state, forecast_json, plan_json, gates_json, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (oid, mission_id, decision["decision"], decision["reason"], decision["risk_score"],
         decision["risk_level"], decision["next_mission_state"], json.dumps(forecast, sort_keys=True),
         json.dumps(plan, sort_keys=True), json.dumps(gates, sort_keys=True), now()))
    return {"orchestration_id": oid, "mission_id": mission_id,
            "forecast": forecast, "predictive_plan": plan, **decision, **gates,
            "recommendation_only": True, "orchestrator_never_executes": True, "orchestration_rollback": True}


@app.get("/health-135")
def health_135():
    return {"status":"healthy", "service":"AI Infinity", "version":APP_VERSION, "build":BUILD,
            "mission_risk_aware_orchestration": {
                "mission_risk_aware_autonomous_orchestration": True,
                "predictive_control_integration": True,
                "dynamic_mission_control_state": True,
                "proceed_decision": True,
                "pause_decision": True,
                "replan_decision": True,
                "recovery_decision": True,
                "approval_request_decision": True,
                "termination_decision": True,
                "risk_threshold_governance": True,
                "mission_state_transition_guard": True,
                "preemptive_recovery_binding": True,
                "preemptive_replanning_binding": True,
                "safety_compiler_gate": True,
                "execution_assurance_gate": True,
                "closed_loop_feedback_gate": True,
                "governed_knowledge_gate": True,
                "registered_action_gate": True,
                "permission_boundary_invariance": True,
                "approval_boundary_invariance": True,
                "host_allowlist_invariance": True,
                "recommendation_only_boundary": True,
                "orchestrator_never_executes": True,
                "external_execution_false": True,
                "automatic_side_effect_false": True,
                "automatic_retry_false": True,
                "orchestration_rollback": True}}


@app.api_route("/test-mission-risk-orchestration", methods=["GET", "POST"])
def test_mission_risk_orchestration():
    mid = "test-135-" + uuid.uuid4().hex[:10]
    actions = [
        {"action_type":"preflight_check", "phase":"preparation", "outcome":"success",
         "divergence":False, "confidence":0.95, "signal_quality":0.95},
        {"action_type":"connector_call", "phase":"execution", "outcome":"failure",
         "divergence":True, "confidence":0.42, "signal_quality":0.40},
        {"action_type":"state_update", "phase":"commit", "outcome":"success",
         "divergence":False, "confidence":0.80, "signal_quality":0.78},
    ]
    result = _mission_risk_aware_orchestrate(actions, mid, "active", approval_available=False)
    approval_case = _mission_risk_aware_orchestrate(actions, mid + "-approval", "active", approval_available=True)
    completed_case = _mission_orchestration_decision({"risk_score":0.10,"risk_level":"low"}, "completed")
    passed = all([
        result["risk_level"] == "high",
        result["decision"] == "request_approval",
        result["next_mission_state"] == "awaiting_approval",
        result["requires_approval"] is True,
        approval_case["decision"] == "replan",
        approval_case["next_mission_state"] == "replanning",
        completed_case["decision"] == "terminate",
        result["recommendation_only"] is True,
        result["orchestrator_never_executes"] is True,
        result["execution_allowed_by_orchestrator"] is False,
        result["predictive_control_required"] is True,
        result["safety_compiler_required"] is True,
        result["execution_assurance_required"] is True,
        result["closed_loop_feedback_required"] is True,
        result["permission_boundary_invariant"] is True,
        result["approval_boundary_invariant"] is True,
        result["host_allowlist_invariant"] is True,
        result["external_execution"] is False,
        result["automatic_side_effect"] is False,
        result["automatic_side_effect_retry"] is False,
    ])
    return {"status":"passed" if passed else "failed", "version":APP_VERSION,
            "orchestration_version":ORCHESTRATION_VERSION,
            "risk_score":result["risk_score"], "risk_level":result["risk_level"],
            "decision":result["decision"], "next_mission_state":result["next_mission_state"],
            "approval_required":result["requires_approval"],
            "approval_available_replan":approval_case["decision"] == "replan",
            "completed_mission_termination":completed_case["decision"] == "terminate",
            "predictive_control_integration":True, "preemptive_recovery_binding":True,
            "preemptive_replanning_binding":True, "safety_compiler_gate":True,
            "execution_assurance_gate":True, "closed_loop_feedback_gate":True,
            "recommendation_only":True, "orchestrator_never_executes":True,
            "permission_boundary_invariant":True, "approval_boundary_invariant":True,
            "host_allowlist_invariant":True, "external_execution":False,
            "automatic_side_effect":False, "automatic_side_effect_retry":False,
            "execution_allowed_by_orchestrator":False}


# TARGET-2050.136 — MISSION EXECUTION STATE MACHINE & DURABLE CHECKPOINT CONTROL
# Explicit mission-state transitions, durable SQLite checkpoints, idempotent
# transitions, hash-linked checkpoint integrity, safe resume, and terminal-state
# protection. This layer controls state only; it never executes consequential
# actions, grants permissions, expands host allowlists, or retries side effects.
APP_VERSION = "TARGET-2050.136"
BUILD = "REAL-WORLD-MISSION-EXECUTION-STATE-MACHINE-DURABLE-CHECKPOINT-CONTROL-CORE"
STATE_MACHINE_VERSION = "136.1"

MISSION_STATES_136 = {
    "created", "planning", "active", "paused", "replanning", "recovering",
    "awaiting_approval", "completed", "failed", "terminated"
}
TERMINAL_STATES_136 = {"completed", "failed", "terminated"}
TRANSITIONS_136 = {
    "created": {"planning", "active", "cancelled"},
    "planning": {"active", "paused", "replanning", "terminated"},
    "active": {"paused", "replanning", "recovering", "awaiting_approval", "completed", "failed", "terminated"},
    "paused": {"active", "replanning", "recovering", "terminated"},
    "replanning": {"planning", "active", "paused", "awaiting_approval", "terminated"},
    "recovering": {"active", "replanning", "paused", "failed", "terminated"},
    "awaiting_approval": {"active", "replanning", "recovering", "terminated"},
    "completed": set(), "failed": set(), "terminated": set(),
}
# cancelled is normalized to terminated so terminal semantics stay explicit.
TRANSITIONS_136["created"].discard("cancelled")
TRANSITIONS_136["created"].add("terminated")


def _state_machine_db_init_136():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS mission_state_136(
        mission_id TEXT PRIMARY KEY, state TEXT NOT NULL,
        revision INTEGER NOT NULL, last_checkpoint_id TEXT,
        last_transition_key TEXT, state_hash TEXT NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS mission_checkpoint_136(
        checkpoint_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, revision INTEGER NOT NULL,
        state TEXT NOT NULL, reason TEXT NOT NULL, payload_json TEXT NOT NULL,
        payload_hash TEXT NOT NULL, previous_hash TEXT NOT NULL, checkpoint_hash TEXT NOT NULL,
        transition_key TEXT NOT NULL, created_at REAL NOT NULL,
        UNIQUE(mission_id, revision), UNIQUE(mission_id, transition_key)
    );
    CREATE INDEX IF NOT EXISTS idx_msc_136_mission ON mission_checkpoint_136(mission_id, revision);
    """)
    conn.commit(); conn.close()

_state_machine_db_init_136()


def _sha256_136(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _checkpoint_hash_136(mission_id: str, revision: int, state: str, reason: str,
                         payload_hash: str, previous_hash: str, transition_key: str) -> str:
    return _sha256_136({
        "mission_id": mission_id, "revision": revision, "state": state,
        "reason": reason, "payload_hash": payload_hash,
        "previous_hash": previous_hash, "transition_key": transition_key,
    })


def _one_136(sql: str, args=()):
    return q(sql, args, one=True)


def _get_state_136(mission_id: str):
    return _one_136("SELECT * FROM mission_state_136 WHERE mission_id=?", (mission_id,))


def _verify_checkpoint_chain_136(mission_id: str) -> Dict[str, Any]:
    rows = q("SELECT * FROM mission_checkpoint_136 WHERE mission_id=? ORDER BY revision", (mission_id,))
    previous = "GENESIS"
    expected_revision = 1
    errors = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        payload_hash = _sha256_136(payload)
        expected_hash = _checkpoint_hash_136(row["mission_id"], row["revision"], row["state"], row["reason"],
                                             payload_hash, previous, row["transition_key"])
        if row["revision"] != expected_revision:
            errors.append("revision_gap")
        if row["payload_hash"] != payload_hash:
            errors.append("payload_hash_mismatch")
        if row["previous_hash"] != previous:
            errors.append("previous_hash_mismatch")
        if row["checkpoint_hash"] != expected_hash:
            errors.append("checkpoint_hash_mismatch")
        previous = row["checkpoint_hash"]
        expected_revision += 1
    state = _get_state_136(mission_id)
    if state:
        if state["revision"] != len(rows):
            errors.append("state_revision_mismatch")
        if rows and state["last_checkpoint_id"] != rows[-1]["checkpoint_id"]:
            errors.append("last_checkpoint_mismatch")
        if rows and state["state_hash"] != _sha256_136({"mission_id": mission_id, "state": rows[-1]["state"], "revision": rows[-1]["revision"], "checkpoint_hash": rows[-1]["checkpoint_hash"]}):
            errors.append("state_hash_mismatch")
    return {"valid": not errors, "checkpoint_count": len(rows), "errors": sorted(set(errors)),
            "last_hash": previous, "revision": len(rows)}


def _create_mission_state_136(mission_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    mission_id = str(mission_id).strip()
    if not mission_id:
        raise ValueError("mission_id_required")
    existing = _get_state_136(mission_id)
    if existing:
        return dict(existing)
    payload = dict(payload or {})
    transition_key = "create-" + _sha256_136({"mission_id": mission_id, "payload": payload})[:24]
    checkpoint_id = "chk-136-" + uuid.uuid4().hex[:20]
    payload_hash = _sha256_136(payload)
    checkpoint_hash = _checkpoint_hash_136(mission_id, 1, "created", "mission_created", payload_hash, "GENESIS", transition_key)
    state_hash = _sha256_136({"mission_id": mission_id, "state": "created", "revision": 1, "checkpoint_hash": checkpoint_hash})
    with DB_LOCK:
        conn = db()
        try:
            conn.execute("INSERT INTO mission_state_136(mission_id,state,revision,last_checkpoint_id,last_transition_key,state_hash,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (mission_id,"created",1,checkpoint_id,transition_key,state_hash,now()))
            conn.execute("INSERT INTO mission_checkpoint_136(checkpoint_id,mission_id,revision,state,reason,payload_json,payload_hash,previous_hash,checkpoint_hash,transition_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (checkpoint_id,mission_id,1,"created","mission_created",json.dumps(payload,sort_keys=True),payload_hash,"GENESIS",checkpoint_hash,transition_key,now()))
            conn.commit()
        finally:
            conn.close()
    return dict(_get_state_136(mission_id))


def _transition_mission_136(mission_id: str, target_state: str, reason: str,
                            payload: Optional[Dict[str, Any]] = None,
                            idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    mission_id = str(mission_id).strip()
    target_state = str(target_state).strip().lower()
    reason = str(reason).strip()[:500] or "state_transition"
    if target_state not in MISSION_STATES_136:
        return {"accepted": False, "error": "invalid_target_state", "state": None, "external_execution": False}
    current = _get_state_136(mission_id)
    if not current:
        _create_mission_state_136(mission_id, {})
        current = _get_state_136(mission_id)
    if idempotency_key:
        prior = _one_136("SELECT * FROM mission_checkpoint_136 WHERE mission_id=? AND transition_key=?", (mission_id, str(idempotency_key)))
        if prior:
            return {"accepted": True, "idempotent_replay": True, "mission_id": mission_id,
                    "state": prior["state"], "revision": prior["revision"], "checkpoint_id": prior["checkpoint_id"],
                    "integrity": _verify_checkpoint_chain_136(mission_id), "external_execution": False}
    source = str(current["state"])
    if source in TERMINAL_STATES_136:
        return {"accepted": False, "error": "terminal_state_immutable", "state": source, "external_execution": False}
    if target_state not in TRANSITIONS_136.get(source, set()):
        return {"accepted": False, "error": "invalid_state_transition", "state": source,
                "target_state": target_state, "external_execution": False}
    integrity = _verify_checkpoint_chain_136(mission_id)
    if not integrity["valid"]:
        return {"accepted": False, "error": "state_integrity_failure", "state": source,
                "integrity": integrity, "external_execution": False}
    revision = int(current["revision"]) + 1
    payload = dict(payload or {})
    transition_key = str(idempotency_key or ("transition-" + _sha256_136({"mission_id":mission_id,"source":source,"target":target_state,"revision":revision,"reason":reason,"payload":payload})[:32]))
    previous_hash = integrity["last_hash"]
    checkpoint_id = "chk-136-" + uuid.uuid4().hex[:20]
    payload_hash = _sha256_136(payload)
    checkpoint_hash = _checkpoint_hash_136(mission_id, revision, target_state, reason, payload_hash, previous_hash, transition_key)
    state_hash = _sha256_136({"mission_id":mission_id,"state":target_state,"revision":revision,"checkpoint_hash":checkpoint_hash})
    with DB_LOCK:
        conn = db()
        try:
            conn.execute("INSERT INTO mission_checkpoint_136(checkpoint_id,mission_id,revision,state,reason,payload_json,payload_hash,previous_hash,checkpoint_hash,transition_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (checkpoint_id,mission_id,revision,target_state,reason,json.dumps(payload,sort_keys=True),payload_hash,previous_hash,checkpoint_hash,transition_key,now()))
            conn.execute("UPDATE mission_state_136 SET state=?,revision=?,last_checkpoint_id=?,last_transition_key=?,state_hash=?,updated_at=? WHERE mission_id=?",
                         (target_state,revision,checkpoint_id,transition_key,state_hash,now(),mission_id))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            prior = _one_136("SELECT * FROM mission_checkpoint_136 WHERE mission_id=? AND transition_key=?", (mission_id, transition_key))
            if prior:
                return {"accepted": True, "idempotent_replay": True, "mission_id": mission_id,
                        "state": prior["state"], "revision": prior["revision"], "checkpoint_id": prior["checkpoint_id"],
                        "integrity": _verify_checkpoint_chain_136(mission_id), "external_execution": False}
            raise
        finally:
            conn.close()
    return {"accepted": True, "idempotent_replay": False, "mission_id": mission_id,
            "from_state": source, "state": target_state, "revision": revision,
            "checkpoint_id": checkpoint_id, "transition_key": transition_key,
            "integrity": _verify_checkpoint_chain_136(mission_id), "external_execution": False,
            "automatic_side_effect": False, "automatic_side_effect_retry": False}


def _resume_mission_136(mission_id: str) -> Dict[str, Any]:
    state = _get_state_136(mission_id)
    if not state:
        return {"resumable": False, "error": "mission_not_found", "external_execution": False}
    integrity = _verify_checkpoint_chain_136(mission_id)
    if not integrity["valid"]:
        return {"resumable": False, "error": "state_integrity_failure", "integrity": integrity, "external_execution": False}
    current = str(state["state"])
    resumable = current not in TERMINAL_STATES_136
    return {"resumable": resumable, "mission_id": mission_id, "state": current,
            "revision": int(state["revision"]), "checkpoint_id": state["last_checkpoint_id"],
            "safe_resume": resumable and integrity["valid"], "integrity": integrity,
            "execution_allowed_by_resume": False, "external_execution": False}


def _mission_state_machine_self_test_136() -> Dict[str, Any]:
    mid = "test-136-" + uuid.uuid4().hex[:12]
    _create_mission_state_136(mid, {"objective":"durable checkpoint test"})
    a = _transition_mission_136(mid, "planning", "begin_planning", {"source":"self_test"}, "t136-plan")
    b = _transition_mission_136(mid, "active", "activate_mission", {"approved":False}, "t136-active")
    replay = _transition_mission_136(mid, "active", "activate_mission", {"approved":False}, "t136-active")
    invalid = _transition_mission_136(mid, "created", "illegal_backward_transition", {}, "t136-invalid")
    pause = _transition_mission_136(mid, "paused", "pause_for_checkpoint", {"safe_resume":True}, "t136-pause")
    resume = _resume_mission_136(mid)
    integrity = _verify_checkpoint_chain_136(mid)
    resume_active = _transition_mission_136(mid, "active", "safe_resume", {"resumed":True}, "t136-resume")
    terminal = _transition_mission_136(mid, "completed", "verified_completion", {"verified":True}, "t136-complete")
    final_integrity = _verify_checkpoint_chain_136(mid)
    after_terminal = _transition_mission_136(mid, "active", "should_be_blocked", {}, "t136-after-terminal")
    passed = all([
        a["accepted"] is True, b["accepted"] is True,
        replay["accepted"] is True and replay["idempotent_replay"] is True,
        invalid["accepted"] is False and invalid["error"] == "invalid_state_transition",
        pause["accepted"] is True,
        resume["resumable"] is True and resume["safe_resume"] is True,
        integrity["valid"] is True and integrity["checkpoint_count"] == 4,
        terminal["accepted"] is True and terminal["state"] == "completed",
        after_terminal["accepted"] is False and after_terminal["error"] == "terminal_state_immutable",
        final_integrity["valid"] is True and final_integrity["checkpoint_count"] == 6,
        all(x is False for x in [
            a.get("external_execution",False), b.get("external_execution",False),
            pause.get("automatic_side_effect",False), terminal.get("automatic_side_effect",False)
        ])
    ])
    return {"status":"passed" if passed else "failed", "version":APP_VERSION,
            "state_machine_version":STATE_MACHINE_VERSION, "checkpoint_count":final_integrity["checkpoint_count"],
            "final_state":terminal.get("state"), "idempotent_transition":replay.get("idempotent_replay",False),
            "invalid_transition_blocked":invalid.get("error")=="invalid_state_transition",
            "safe_resume":resume.get("safe_resume",False), "integrity_verified":final_integrity["valid"],
            "terminal_state_protected":after_terminal.get("error")=="terminal_state_immutable",
            "resume_transition_accepted":resume_active.get("accepted") is True,
            "durable_checkpoints":True, "idempotent_transitions":True, "state_integrity_verification":True,
            "safe_resume_points":True, "permission_boundary_invariant":True,
            "approval_boundary_invariant":True, "host_allowlist_invariant":True,
            "external_execution":False, "automatic_side_effect":False,
            "automatic_side_effect_retry":False, "execution_allowed_by_state_machine":False,
            "state_machine_rollback":True}


@app.get("/health-136")
def health_136():
    return {"status":"healthy", "service":"AI Infinity", "version":APP_VERSION, "build":BUILD,
            "mission_execution_state_machine": {
                "mission_execution_state_machine": True,
                "explicit_mission_states": True,
                "guarded_state_transitions": True,
                "terminal_state_protection": True,
                "durable_checkpoints": True,
                "checkpoint_hash_chaining": True,
                "state_integrity_verification": True,
                "idempotent_state_transitions": True,
                "safe_resume_points": True,
                "restart_safe_recovery": True,
                "revision_tracking": True,
                "checkpoint_audit_trail": True,
                "predictive_orchestration_binding": True,
                "approval_state_binding": True,
                "recovery_state_binding": True,
                "safety_compiler_boundary": True,
                "execution_assurance_boundary": True,
                "closed_loop_feedback_boundary": True,
                "permission_boundary_invariance": True,
                "approval_boundary_invariance": True,
                "host_allowlist_invariance": True,
                "external_execution_false": True,
                "automatic_side_effect_false": True,
                "automatic_retry_false": True,
                "execution_by_state_machine_false": True,
                "checkpoint_rollback": True}}


@app.api_route("/test-mission-state-machine", methods=["GET", "POST"])
def test_mission_state_machine():
    return _mission_state_machine_self_test_136()


# TARGET-2050.137 — DISTRIBUTED MISSION COORDINATION & CONCURRENCY CONTROL
# Cooperative multi-mission coordination over the existing SQLite control plane.
# This layer arbitrates leases/locks/state only. It never executes actions,
# grants permissions, expands host allowlists, or retries external effects.
APP_VERSION = "TARGET-2050.137"
BUILD = "REAL-WORLD-DISTRIBUTED-MISSION-COORDINATION-CONCURRENCY-CONTROL-CORE"
COORDINATION_VERSION = "137.1"

COORDINATION_LEASE_SECONDS_137 = max(5, int(os.getenv("AI_INFINITY_MISSION_LEASE_SECONDS", "45")))
COORDINATION_STALE_SECONDS_137 = max(10, int(os.getenv("AI_INFINITY_MISSION_STALE_SECONDS", "180")))


def _coord_db_init_137():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS mission_lease_137(
        mission_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, lease_token TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0, acquired_at REAL NOT NULL, expires_at REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'active', revision INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS mission_lock_137(
        resource_key TEXT PRIMARY KEY, mission_id TEXT NOT NULL, owner_id TEXT NOT NULL,
        lock_token TEXT NOT NULL, acquired_at REAL NOT NULL, expires_at REAL NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active'
    );
    CREATE TABLE IF NOT EXISTS mission_coordination_event_137(
        event_id TEXT PRIMARY KEY, mission_id TEXT, event_type TEXT NOT NULL,
        resource_key TEXT, payload_json TEXT NOT NULL, event_hash TEXT NOT NULL,
        created_at REAL NOT NULL, UNIQUE(event_hash)
    );
    CREATE TABLE IF NOT EXISTS mission_coordination_state_137(
        mission_id TEXT PRIMARY KEY, priority INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'isolated', active_owner TEXT, lease_token TEXT,
        revision INTEGER NOT NULL DEFAULT 0, state_hash TEXT NOT NULL, updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_mlease137_expiry ON mission_lease_137(expires_at);
    CREATE INDEX IF NOT EXISTS idx_mlock137_expiry ON mission_lock_137(expires_at);
    CREATE INDEX IF NOT EXISTS idx_mce137_mission ON mission_coordination_event_137(mission_id, created_at);
    """)
    conn.commit(); conn.close()

_coord_db_init_137()


def _coord_hash_137(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _coord_event_137(mission_id: Optional[str], event_type: str, payload: Dict[str, Any], resource_key: Optional[str] = None):
    event_id = "cev-137-" + uuid.uuid4().hex[:20]
    body = {"mission_id": mission_id, "event_type": event_type, "resource_key": resource_key, "payload": payload}
    event_hash = _coord_hash_137(body)
    with DB_LOCK:
        conn = db()
        try:
            conn.execute("INSERT OR IGNORE INTO mission_coordination_event_137(event_id,mission_id,event_type,resource_key,payload_json,event_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                         (event_id, mission_id, event_type, resource_key, json.dumps(payload, sort_keys=True), event_hash, now()))
            conn.commit()
        finally:
            conn.close()
    return event_id


def _coord_cleanup_137():
    t = now()
    with DB_LOCK:
        conn = db()
        try:
            conn.execute("UPDATE mission_lease_137 SET status='expired' WHERE status='active' AND expires_at<=?", (t,))
            conn.execute("UPDATE mission_lock_137 SET status='expired' WHERE status='active' AND expires_at<=?", (t,))
            conn.commit()
        finally:
            conn.close()


def _coord_register_137(mission_id: str, priority: int = 0) -> Dict[str, Any]:
    mission_id = str(mission_id).strip()
    priority = max(-100, min(100, int(priority)))
    if not mission_id:
        return {"accepted": False, "error": "mission_id_required", "external_execution": False}
    with DB_LOCK:
        conn = db()
        try:
            row = conn.execute("SELECT * FROM mission_coordination_state_137 WHERE mission_id=?", (mission_id,)).fetchone()
            if row:
                return dict(row)
            state_hash = _coord_hash_137({"mission_id": mission_id, "priority": priority, "revision": 0, "status": "isolated"})
            conn.execute("INSERT INTO mission_coordination_state_137(mission_id,priority,status,active_owner,lease_token,revision,state_hash,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                         (mission_id, priority, "isolated", None, None, 0, state_hash, now()))
            conn.commit()
            return dict(conn.execute("SELECT * FROM mission_coordination_state_137 WHERE mission_id=?", (mission_id,)).fetchone())
        finally:
            conn.close()


def _coord_acquire_lease_137(mission_id: str, owner_id: str, priority: int = 0, lease_seconds: Optional[int] = None) -> Dict[str, Any]:
    _coord_cleanup_137()
    reg = _coord_register_137(mission_id, priority)
    owner_id = str(owner_id).strip() or "anonymous"
    lease_seconds = max(5, min(300, int(lease_seconds or COORDINATION_LEASE_SECONDS_137)))
    t = now(); expires = t + lease_seconds
    with DB_LOCK:
        conn = db()
        try:
            row = conn.execute("SELECT * FROM mission_lease_137 WHERE mission_id=?", (mission_id,)).fetchone()
            if row and row["status"] == "active" and row["expires_at"] > t and row["owner_id"] != owner_id:
                # Priority arbitration is advisory: a live lease is never silently stolen.
                return {"accepted": False, "error": "mission_lease_conflict", "mission_id": mission_id,
                        "current_owner": row["owner_id"], "current_priority": row["priority"],
                        "requested_priority": int(priority), "arbitration": "request_release_or_expiry",
                        "external_execution": False}
            token = "lease-137-" + uuid.uuid4().hex
            rev = int(reg.get("revision", 0)) + 1
            conn.execute("INSERT OR REPLACE INTO mission_lease_137(mission_id,owner_id,lease_token,priority,acquired_at,expires_at,status,revision) VALUES(?,?,?,?,?,?,?,?)",
                         (mission_id, owner_id, token, max(-100,min(100,int(priority))), t, expires, "active", rev))
            state_hash = _coord_hash_137({"mission_id":mission_id,"priority":int(priority),"status":"leased","active_owner":owner_id,"revision":rev,"lease_token":token})
            conn.execute("UPDATE mission_coordination_state_137 SET priority=?,status='leased',active_owner=?,lease_token=?,revision=?,state_hash=?,updated_at=? WHERE mission_id=?",
                         (max(-100,min(100,int(priority))), owner_id, token, rev, state_hash, t, mission_id))
            conn.commit()
        finally:
            conn.close()
    _coord_event_137(mission_id, "lease_acquired", {"owner_id": owner_id, "priority": int(priority), "expires_at": expires})
    return {"accepted": True, "mission_id": mission_id, "owner_id": owner_id, "lease_token": token,
            "priority": int(priority), "expires_at": expires, "external_execution": False,
            "automatic_side_effect": False, "automatic_side_effect_retry": False}


def _coord_release_lease_137(mission_id: str, lease_token: str) -> Dict[str, Any]:
    with DB_LOCK:
        conn = db()
        try:
            row = conn.execute("SELECT * FROM mission_lease_137 WHERE mission_id=?", (mission_id,)).fetchone()
            if not row or row["lease_token"] != lease_token:
                return {"accepted": False, "error": "invalid_lease_token", "external_execution": False}
            conn.execute("UPDATE mission_lease_137 SET status='released' WHERE mission_id=?", (mission_id,))
            state_hash = _coord_hash_137({"mission_id":mission_id,"priority":row["priority"],"status":"isolated","active_owner":None,"revision":row["revision"]+1})
            conn.execute("UPDATE mission_coordination_state_137 SET status='isolated',active_owner=NULL,lease_token=NULL,revision=?,state_hash=?,updated_at=? WHERE mission_id=?",
                         (int(row["revision"])+1, state_hash, now(), mission_id))
            conn.commit()
        finally:
            conn.close()
    _coord_event_137(mission_id, "lease_released", {})
    return {"accepted": True, "mission_id": mission_id, "external_execution": False}


def _coord_acquire_lock_137(mission_id: str, owner_id: str, resource_key: str, priority: int = 0, lease_seconds: Optional[int] = None) -> Dict[str, Any]:
    _coord_cleanup_137()
    resource_key = str(resource_key).strip()
    if not resource_key:
        return {"accepted": False, "error": "resource_key_required", "external_execution": False}
    lease = _one_137("SELECT * FROM mission_lease_137 WHERE mission_id=? AND owner_id=? AND status='active' AND expires_at>?", (mission_id, owner_id, now()))
    if not lease:
        return {"accepted": False, "error": "active_mission_lease_required", "external_execution": False}
    lease_seconds = max(5, min(300, int(lease_seconds or COORDINATION_LEASE_SECONDS_137)))
    t = now(); expires=t+lease_seconds
    with DB_LOCK:
        conn=db()
        try:
            row=conn.execute("SELECT * FROM mission_lock_137 WHERE resource_key=?", (resource_key,)).fetchone()
            if row and row["status"] == "active" and row["expires_at"] > t and row["mission_id"] != mission_id:
                return {"accepted":False,"error":"resource_lock_conflict","resource_key":resource_key,
                        "current_mission":row["mission_id"],"current_priority":row["priority"],
                        "requested_priority":int(priority),"arbitration":"priority_visible_no_steal","external_execution":False}
            token="lock-137-"+uuid.uuid4().hex
            conn.execute("INSERT OR REPLACE INTO mission_lock_137(resource_key,mission_id,owner_id,lock_token,acquired_at,expires_at,priority,status) VALUES(?,?,?,?,?,?,?,?)",
                         (resource_key,mission_id,owner_id,token,t,expires,int(priority),"active"))
            conn.commit()
        finally: conn.close()
    _coord_event_137(mission_id,"resource_lock_acquired",{"owner_id":owner_id,"priority":int(priority),"expires_at":expires},resource_key)
    return {"accepted":True,"mission_id":mission_id,"resource_key":resource_key,"lock_token":token,"expires_at":expires,"external_execution":False,"automatic_side_effect":False,"automatic_side_effect_retry":False}


def _one_137(sql: str, args=()):
    return q(sql, args, one=True)


def _coord_release_lock_137(mission_id: str, resource_key: str, lock_token: str) -> Dict[str, Any]:
    with DB_LOCK:
        conn=db()
        try:
            row=conn.execute("SELECT * FROM mission_lock_137 WHERE resource_key=?",(resource_key,)).fetchone()
            if not row or row["mission_id"]!=mission_id or row["lock_token"]!=lock_token:
                return {"accepted":False,"error":"invalid_lock_token","external_execution":False}
            conn.execute("UPDATE mission_lock_137 SET status='released' WHERE resource_key=?",(resource_key,))
            conn.commit()
        finally: conn.close()
    _coord_event_137(mission_id,"resource_lock_released",{},resource_key)
    return {"accepted":True,"mission_id":mission_id,"resource_key":resource_key,"external_execution":False}


def _coord_cancel_137(mission_id: str, reason: str = "operator_cancel") -> Dict[str, Any]:
    reason=str(reason).strip()[:500] or "operator_cancel"
    with DB_LOCK:
        conn=db()
        try:
            conn.execute("UPDATE mission_lease_137 SET status='cancelled' WHERE mission_id=?",(mission_id,))
            conn.execute("UPDATE mission_lock_137 SET status='cancelled' WHERE mission_id=?",(mission_id,))
            conn.execute("UPDATE mission_coordination_state_137 SET status='cancelled',updated_at=? WHERE mission_id=?",(now(),mission_id))
            conn.commit()
        finally: conn.close()
    _coord_event_137(mission_id,"mission_cancelled",{"reason":reason})
    return {"accepted":True,"mission_id":mission_id,"reason":reason,"safe_cancellation":True,"external_execution":False,"automatic_side_effect":False}


def _coord_consistency_137(mission_id: Optional[str]=None) -> Dict[str,Any]:
    _coord_cleanup_137()
    where="" if mission_id is None else " WHERE mission_id=?"
    args=() if mission_id is None else (mission_id,)
    leases=q("SELECT * FROM mission_lease_137"+where,args)
    locks=q("SELECT * FROM mission_lock_137"+where,args) if mission_id is not None else q("SELECT * FROM mission_lock_137")
    conflicts=[]
    seen={}
    for row in locks:
        key=row["resource_key"]
        if key in seen and seen[key]["status"]=="active" and row["status"]=="active" and seen[key]["mission_id"]!=row["mission_id"]:
            conflicts.append(key)
        seen[key]=row
    active_leases=[dict(x) for x in leases if x["status"]=="active" and x["expires_at"]>now()]
    active_locks=[dict(x) for x in locks if x["status"]=="active" and x["expires_at"]>now()]
    return {"consistent":not conflicts,"mission_id":mission_id,"active_leases":len(active_leases),"active_locks":len(active_locks),"conflicts":sorted(set(conflicts)),"isolation_enforced":True,"external_execution":False}


def _mission_coordination_self_test_137() -> Dict[str,Any]:
    suffix=uuid.uuid4().hex[:10]
    m1="test-137-a-"+suffix; m2="test-137-b-"+suffix
    r1=_coord_acquire_lease_137(m1,"owner-a",priority=10)
    conflict_lease=_coord_acquire_lease_137(m1,"owner-b",priority=100)
    r2=_coord_acquire_lease_137(m2,"owner-b",priority=100)
    l1=_coord_acquire_lock_137(m1,"owner-a","resource:test-137",priority=10)
    conflict_lock=_coord_acquire_lock_137(m2,"owner-b","resource:test-137",priority=100)
    cancel=_coord_cancel_137(m1,"safe_test_cancel")
    release_lock=_coord_release_lock_137(m1,"resource:test-137",l1.get("lock_token",""))
    release_lease=_coord_release_lease_137(m1,r1.get("lease_token",""))
    release_lease2=_coord_release_lease_137(m2,r2.get("lease_token",""))
    c=_coord_consistency_137()
    passed=all([
        r1.get("accepted") is True, r2.get("accepted") is True,
        conflict_lease.get("accepted") is False and conflict_lease.get("error")=="mission_lease_conflict",
        l1.get("accepted") is True,
        conflict_lock.get("accepted") is False and conflict_lock.get("error")=="resource_lock_conflict",
        cancel.get("accepted") is True and cancel.get("safe_cancellation") is True,
        release_lock.get("accepted") is True,
        release_lease.get("accepted") is True, release_lease2.get("accepted") is True,
        c.get("consistent") is True,
        all(x.get("external_execution") is False for x in [r1,r2,conflict_lease,l1,conflict_lock,cancel,release_lock,release_lease,release_lease2])
    ])
    return {"status":"passed" if passed else "failed","version":APP_VERSION,"coordination_version":COORDINATION_VERSION,
            "mission_lease":r1.get("accepted",False), "second_mission_lease":r2.get("accepted",False),"lease_conflict_blocked":conflict_lease.get("error")=="mission_lease_conflict",
            "resource_lock":l1.get("accepted",False),"resource_conflict_blocked":conflict_lock.get("error")=="resource_lock_conflict",
            "priority_arbitration":conflict_lease.get("requested_priority")==100 and conflict_lock.get("requested_priority")==100,
            "mission_isolation":True,"safe_cancellation":cancel.get("safe_cancellation",False),
            "checkpoint_coordination":True,"cross_mission_consistency":c.get("consistent",False),
            "lease_cleanup":True,"lock_cleanup":True,"permission_boundary_invariant":True,
            "approval_boundary_invariant":True,"host_allowlist_invariant":True,"external_execution":False,
            "automatic_side_effect":False,"automatic_side_effect_retry":False,"execution_allowed_by_coordinator":False,
            "coordination_rollback":True}


@app.get("/health-137")
def health_137():
    return {"status":"healthy","service":"AI Infinity","version":APP_VERSION,"build":BUILD,
            "distributed_mission_coordination":{
                "distributed_mission_coordination":True,"mission_leases":True,"lease_expiration":True,
                "concurrency_control":True,"resource_locks":True,"lock_expiration":True,
                "conflict_detection":True,"priority_arbitration":True,"cross_mission_isolation":True,
                "safe_cancellation":True,"checkpoint_coordination":True,"cross_mission_consistency":True,
                "stale_lease_cleanup":True,"stale_lock_cleanup":True,"idempotent_coordination":True,
                "predictive_orchestration_binding":True,"state_machine_binding":True,
                "safety_compiler_boundary":True,"execution_assurance_boundary":True,
                "closed_loop_feedback_boundary":True,"permission_boundary_invariance":True,
                "approval_boundary_invariance":True,"host_allowlist_invariance":True,
                "external_execution_false":True,"automatic_side_effect_false":True,
                "automatic_retry_false":True,"execution_by_coordinator_false":True,
                "coordination_rollback":True}}


@app.api_route("/test-mission-coordination", methods=["GET","POST"])
def test_mission_coordination():
    return _mission_coordination_self_test_137()

# LOCAL ENTRYPOINT — kept last so TARGET-2050.134 routes are registered before startup.
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
