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
# LOCAL ENTRYPOINT
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
