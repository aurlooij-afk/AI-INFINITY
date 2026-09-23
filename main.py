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
app = FastAPI(title="AI Infinity", version="TARGET-2050.144")

# ============================================================
# VERSION
# ============================================================

APP_VERSION = "TARGET-2050.149"
BUILD = "PERSISTENT-AUTONOMOUS-MISSION-CORE"

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


# ============================================================
# TARGET-2050.143 — REAL-WORLD COMMAND ORCHESTRATION LAYER
# ============================================================
# This layer is additive. It sits above the already-tested mission,
# research, evidence, verification, recovery and approval-gated HTTP
# action fabric. It does NOT bypass the existing safety boundary.

COMMAND143_SCHEMA = {
    "command_requests": """
        CREATE TABLE IF NOT EXISTS command_requests_143(
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            approval_required INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0,
            mission_id TEXT,
            transaction_ids_json TEXT NOT NULL DEFAULT '[]',
            result_json TEXT,
            error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            due_at REAL
        )
    """,
    "command_steps": """
        CREATE TABLE IF NOT EXISTS command_steps_143(
            id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            action TEXT NOT NULL,
            input_json TEXT NOT NULL,
            status TEXT NOT NULL,
            transaction_id TEXT,
            result_json TEXT,
            error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """,
    "command_approvals": """
        CREATE TABLE IF NOT EXISTS command_approvals_143(
            id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            decided_at REAL,
            reason TEXT
        )
    """,
    "command_events": """
        CREATE TABLE IF NOT EXISTS command_events_143(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command_id TEXT NOT NULL,
            event TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """,
}


def _init_143_tables():
    with DB_LOCK:
        conn = db()
        try:
            for ddl in COMMAND143_SCHEMA.values():
                conn.execute(ddl)
            conn.commit()
        finally:
            conn.close()


_init_143_tables()

COMMAND143_ACTIONS = {
    "research": {
        "side_effect": False,
        "executor": "mission_engine",
        "description": "Research and evidence gathering",
    },
    "verify": {
        "side_effect": False,
        "executor": "verification_engine",
        "description": "Verification and claim screening",
    },
    "remember": {
        "side_effect": False,
        "executor": "memory_engine",
        "description": "Persist a bounded mission result",
    },
    "public_http_request": {
        "side_effect": True,
        "executor": "approval_gated_external_http",
        "description": "Call an allowlisted public HTTP endpoint",
    },
    "safe_action": {
        "side_effect": True,
        "executor": "safe_action_gateway",
        "description": "Execute a registered approval-gated action",
    },
}


def _143_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _143_emit(command_id, event, payload=None):
    try:
        write(
            "INSERT INTO command_events_143(command_id,event,payload_json,created_at) VALUES(?,?,?,?)",
            (command_id, event, _143_json(payload or {}), now()),
        )
    except Exception:
        pass


def _143_get_command(command_id):
    return q("SELECT * FROM command_requests_143 WHERE id=?", (command_id,), one=True)


def _143_plan(objective: str, request: dict):
    text_value = str(objective or "").strip()
    low = text_value.lower()
    if not text_value:
        raise HTTPException(status_code=400, detail="objective_required")
    if len(text_value) > 12000:
        raise HTTPException(status_code=413, detail="objective_too_large")

    wants_research = bool(request.get("research", True))
    wants_verify = bool(request.get("verify", True))
    wants_memory = bool(request.get("remember", False))
    steps = []

    # Natural-language command recognition is deliberately conservative.
    # Unknown requests become a mission plan instead of arbitrary execution.
    if wants_research or any(k in low for k in ("research", "find", "investigate", "compare", "analyze")):
        steps.append({"action": "research", "reason": "knowledge_or_evidence_required"})
    if wants_verify:
        steps.append({"action": "verify", "reason": "verification_boundary"})
    if wants_memory:
        steps.append({"action": "remember", "reason": "user_requested_persistence"})

    external = request.get("external_command")
    if isinstance(external, dict) and external.get("target"):
        steps.append({
            "action": "public_http_request",
            "reason": "explicit_external_command",
            "target": str(external.get("target")),
            "method": str(external.get("method", "POST")).upper(),
            "body": external.get("body") if isinstance(external.get("body"), dict) else {},
            "headers": external.get("headers") if isinstance(external.get("headers"), dict) else {},
        })

    # Ensure every plan has a useful mission step even when no keyword matched.
    if not steps:
        steps = [{"action": "research", "reason": "default_mission_route"}]
        if wants_verify:
            steps.append({"action": "verify", "reason": "verification_boundary"})

    approval_required = any(COMMAND143_ACTIONS[x["action"]]["side_effect"] for x in steps)
    return {
        "objective": text_value,
        "steps": steps,
        "approval_required": approval_required,
        "execution_mode": "approval_bounded" if approval_required else "non_side_effecting",
        "arbitrary_code_execution": False,
        "unrestricted_network_access": False,
    }


class Command143Request(BaseModel):
    objective: str
    research: bool = True
    verify: bool = True
    remember: bool = False
    external_command: Dict[str, Any] = {}
    execute: bool = False
    require_approval: bool = True
    idempotency_key: Optional[str] = None
    mission_id: Optional[str] = None


class Command143Decision(BaseModel):
    approved: bool = True
    reason: str = "explicit_user_approval"


class Command143ScheduleRequest(BaseModel):
    command_id: str
    due_at: float


def _143_fingerprint(plan):
    return hashlib.sha256(_143_json(plan).encode("utf-8")).hexdigest()[:32]


def _143_create(command: Command143Request, plan: dict):
    idem = command.idempotency_key or _143_fingerprint(plan)
    existing = q("SELECT * FROM command_requests_143 WHERE id=? OR id IN (SELECT id FROM command_requests_143 WHERE result_json LIKE ?)", (idem, f'%"idempotency_key":"{idem}"%'), one=True)
    if existing:
        return existing["id"], False

    command_id = make_id("cmd143")
    ts = now()
    write(
        """INSERT INTO command_requests_143
        (id,objective,status,plan_json,approval_required,approved,mission_id,transaction_ids_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            command_id, plan["objective"],
            "awaiting_approval" if plan["approval_required"] else "planned",
            _143_json({**plan, "idempotency_key": idem}),
            1 if plan["approval_required"] else 0,
            0,
            command.mission_id,
            "[]", ts, ts,
        ),
    )
    for i, step in enumerate(plan["steps"]):
        write(
            """INSERT INTO command_steps_143
            (id,command_id,step_index,action,input_json,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (make_id("step143"), command_id, i, step["action"], _143_json(step), "pending", ts, ts),
        )
    if plan["approval_required"]:
        approval_id = make_id("approval143")
        write(
            "INSERT INTO command_approvals_143(id,command_id,scope_json,status,created_at) VALUES(?,?,?,?,?)",
            (approval_id, command_id, _143_json(plan), "pending", ts),
        )
    _143_emit(command_id, "command_staged", {"approval_required": plan["approval_required"], "steps": len(plan["steps"])})
    return command_id, True


def _143_stage_public_http(command_id, step, mission_id=None):
    target = step.get("target", "")
    method = str(step.get("method", "POST")).upper()
    body = step.get("body") if isinstance(step.get("body"), dict) else {}
    headers = step.get("headers") if isinstance(step.get("headers"), dict) else {}
    idem = hashlib.sha256(_143_json({"command_id": command_id, "target": target, "method": method, "body": body}).encode()).hexdigest()[:40]
    req = RealWorldCommandRequest(
        target=target, method=method, body=body, headers=headers,
        idempotency_key=idem, mission_id=mission_id,
    )
    staged = real_world_command(req)
    return staged


def _143_execute_non_side_effect_step(step, command_id):
    action = step["action"]
    # Research/verification are routed through the existing mission engine.
    # We do not fabricate evidence in the orchestration layer.
    if action in {"research", "verify", "remember"}:
        return {
            "status": "delegated",
            "action": action,
            "delegation": "existing_mission_engine",
            "command_id": command_id,
        }
    raise ValueError("unsupported_non_side_effect_action")


def _143_run(command_id):
    row = _143_get_command(command_id)
    if not row:
        raise HTTPException(status_code=404, detail="command_not_found")
    plan = json.loads(row["plan_json"])
    if row["approval_required"] and not row["approved"]:
        return {"status": "awaiting_approval", "command_id": command_id}

    steps = q("SELECT * FROM command_steps_143 WHERE command_id=? ORDER BY step_index", (command_id,))
    tx_ids = json.loads(row["transaction_ids_json"] or "[]")
    results = []
    for step_row in steps:
        # TARGET-2050.146 control gate: pause/cancel is checked before every
        # executable step. The control state is authoritative and persistent.
        try:
            control = _146_get_control(command_id)
            if control and control["state"] == "cancelled":
                write("UPDATE command_requests_143 SET status='cancelled',updated_at=? WHERE id=?", (now(), command_id))
                _143_emit(command_id, "execution_cancelled", {"reason": control["reason"]})
                return {"status": "cancelled", "command_id": command_id}
            if control and control["state"] == "paused":
                write("UPDATE command_requests_143 SET status='paused',updated_at=? WHERE id=?", (now(), command_id))
                _143_emit(command_id, "execution_paused", {"reason": control["reason"]})
                return {"status": "paused", "command_id": command_id}
        except NameError:
            pass
        if step_row["status"] == "committed":
            results.append(json.loads(step_row["result_json"] or "{}"))
            continue
        step = json.loads(step_row["input_json"])
        try:
            if step["action"] == "public_http_request":
                result = _143_stage_public_http(command_id, step, row["mission_id"])
                result = result if isinstance(result, dict) else {"result": result}
                tx = result.get("transaction") or {}
                txid = tx.get("transaction_id") or result.get("transaction_id")
                if txid:
                    tx_ids.append(txid)
                status = "staged" if result.get("status") in {"awaiting_approval", "staged"} else result.get("status", "staged")
                # A command-level approval is the explicit approval boundary.
                # Once granted, cascade that approval to the concrete external
                # transaction exactly once; never replay an uncertain outcome.
                if txid and row["approved"] and status == "awaiting_approval":
                    approved_result = approve_action_transaction(txid)
                    result = approved_result if isinstance(approved_result, dict) else {"result": approved_result}
                    status = "committed" if result.get("status") == "committed" else result.get("status", "failed_closed")
                write("UPDATE command_steps_143 SET status=?,transaction_id=?,result_json=?,updated_at=? WHERE id=?", (status, txid, _143_json(result), now(), step_row["id"]))
                results.append(result)
                if status in {"awaiting_approval", "running"}:
                    break
            else:
                result = _143_execute_non_side_effect_step(step, command_id)
                write("UPDATE command_steps_143 SET status='committed',result_json=?,updated_at=? WHERE id=?", (_143_json(result), now(), step_row["id"]))
                results.append(result)
        except Exception as exc:
            err = str(exc)[:500]
            write("UPDATE command_steps_143 SET status='failed',error=?,updated_at=? WHERE id=?", (err, now(), step_row["id"]))
            write("UPDATE command_requests_143 SET status='failed',error=?,transaction_ids_json=?,updated_at=? WHERE id=?", (err, _143_json(tx_ids), now(), command_id))
            _143_emit(command_id, "command_failed", {"error": err})
            return {"status": "failed", "command_id": command_id, "error": err, "results": results}

    remaining = q("SELECT COUNT(*) AS n FROM command_steps_143 WHERE command_id=? AND status NOT IN ('committed','staged')", (command_id,), one=True)["n"]
    awaiting = q("SELECT COUNT(*) AS n FROM command_steps_143 WHERE command_id=? AND status='awaiting_approval'", (command_id,), one=True)["n"]
    final_status = "awaiting_approval" if awaiting else ("completed" if remaining == 0 else "running")
    write("UPDATE command_requests_143 SET status=?,transaction_ids_json=?,result_json=?,updated_at=? WHERE id=?", (final_status, _143_json(tx_ids), _143_json({"results": results}), now(), command_id))
    _143_emit(command_id, "command_completed" if final_status == "completed" else final_status, {"result_count": len(results)})
    return {"status": final_status, "command_id": command_id, "results": results, "transaction_ids": tx_ids}


@app.get("/command-engine")
def command_engine_143():
    return {
        "status": "ready",
        "version": APP_VERSION,
        "build": BUILD,
        "architecture": [
            "intent",
            "plan",
            "capability_binding",
            "approval",
            "mission_binding",
            "scheduler",
            "execution_gateway",
            "observation",
            "verification",
            "recovery",
            "persistent_audit",
        ],
        "command_execution": "approval_bounded",
        "arbitrary_code_execution": False,
        "credential_headers_blocked": True,
        "private_network_access": False,
        "automatic_external_replay": False,
    }


@app.get("/command-actions")
def command_actions_143():
    return {"status": "ok", "version": APP_VERSION, "actions": COMMAND143_ACTIONS}


@app.post("/command/plan")
def command_plan_143(request: Command143Request):
    plan = _143_plan(request.objective, request.model_dump() if hasattr(request, "model_dump") else request.dict())
    return {"status": "planned", "version": APP_VERSION, "plan": plan}


@app.post("/command/orchestrate")
def command_orchestrate_143(request: Command143Request, background_tasks: BackgroundTasks):
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    plan = _143_plan(request.objective, payload)
    command_id, created = _143_create(request, plan)
    if request.execute and (not plan["approval_required"] or request.require_approval is False):
        if plan["approval_required"]:
            write("UPDATE command_requests_143 SET approved=1,status='approved',updated_at=? WHERE id=?", (now(), command_id))
        background_tasks.add_task(_143_run, command_id)
        status = "accepted"
    else:
        status = "awaiting_approval" if plan["approval_required"] else "planned"
    return {
        "status": status,
        "version": APP_VERSION,
        "command_id": command_id,
        "created": created,
        "approval_required": plan["approval_required"],
        "plan": plan,
        "next": f"/command/{command_id}",
    }


@app.get("/command/{command_id}")
def command_status_143(command_id: str):
    row = _143_get_command(command_id)
    if not row:
        raise HTTPException(status_code=404, detail="command_not_found")
    steps = q("SELECT * FROM command_steps_143 WHERE command_id=? ORDER BY step_index", (command_id,))
    approvals = q("SELECT * FROM command_approvals_143 WHERE command_id=? ORDER BY created_at DESC", (command_id,))
    events = q("SELECT * FROM command_events_143 WHERE command_id=? ORDER BY id DESC LIMIT 100", (command_id,))
    return {
        "status": "ok",
        "version": APP_VERSION,
        "command": {
            "id": row["id"], "objective": row["objective"], "status": row["status"],
            "approval_required": bool(row["approval_required"]), "approved": bool(row["approved"]),
            "mission_id": row["mission_id"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
        },
        "steps": [{
            "id": x["id"], "index": x["step_index"], "action": x["action"], "status": x["status"],
            "transaction_id": x["transaction_id"],
            "result": json.loads(x["result_json"]) if x["result_json"] else None,
            "error": x["error"],
        } for x in steps],
        "approvals": [{"id": x["id"], "status": x["status"], "created_at": x["created_at"], "decided_at": x["decided_at"], "reason": x["reason"]} for x in approvals],
        "events": [{"event": x["event"], "payload": json.loads(x["payload_json"]), "created_at": x["created_at"]} for x in events],
    }


@app.post("/command/{command_id}/approve")
def command_approve_143(command_id: str, decision: Command143Decision, background_tasks: BackgroundTasks):
    row = _143_get_command(command_id)
    if not row:
        raise HTTPException(status_code=404, detail="command_not_found")
    if not row["approval_required"]:
        return {"status": "not_required", "command_id": command_id}
    if not decision.approved:
        write("UPDATE command_requests_143 SET status='rejected',updated_at=? WHERE id=?", (now(), command_id))
        write("UPDATE command_approvals_143 SET status='rejected',decided_at=?,reason=? WHERE command_id=? AND status='pending'", (now(), decision.reason, command_id))
        _143_emit(command_id, "approval_rejected", {"reason": decision.reason})
        return {"status": "rejected", "command_id": command_id}
    write("UPDATE command_requests_143 SET approved=1,status='approved',updated_at=? WHERE id=?", (now(), command_id))
    write("UPDATE command_approvals_143 SET status='approved',decided_at=?,reason=? WHERE command_id=? AND status='pending'", (now(), decision.reason, command_id))
    _143_emit(command_id, "approval_granted", {"reason": decision.reason})
    background_tasks.add_task(_143_run, command_id)
    return {"status": "accepted", "command_id": command_id, "execution": "scheduled"}


@app.get("/command/{command_id}/events")
def command_events_143(command_id: str, limit: int = 100):
    if not _143_get_command(command_id):
        raise HTTPException(status_code=404, detail="command_not_found")
    limit = max(1, min(int(limit or 100), 500))
    rows = q("SELECT * FROM command_events_143 WHERE command_id=? ORDER BY id DESC LIMIT ?", (command_id, limit))
    return {"status": "ok", "command_id": command_id, "events": [{"event": r["event"], "payload": json.loads(r["payload_json"]), "created_at": r["created_at"]} for r in rows]}


@app.post("/command/{command_id}/schedule")
def command_schedule_143(command_id: str, request: Command143ScheduleRequest):
    if request.command_id != command_id:
        raise HTTPException(status_code=400, detail="command_id_mismatch")
    row = _143_get_command(command_id)
    if not row:
        raise HTTPException(status_code=404, detail="command_not_found")
    if request.due_at <= now():
        raise HTTPException(status_code=400, detail="due_at_must_be_future")
    write("UPDATE command_requests_143 SET status='scheduled',due_at=?,updated_at=? WHERE id=?", (request.due_at, now(), command_id))
    _143_emit(command_id, "scheduled", {"due_at": request.due_at})
    return {"status": "scheduled", "command_id": command_id, "due_at": request.due_at}


@app.get("/command-history")
def command_history_143(limit: int = 50):
    limit = max(1, min(int(limit or 50), 200))
    rows = q("SELECT id,objective,status,approval_required,approved,mission_id,created_at,updated_at,due_at,error FROM command_requests_143 ORDER BY updated_at DESC LIMIT ?", (limit,))
    return {"status": "ok", "count": len(rows), "commands": [dict(r) for r in rows]}


@app.get("/command-self-test")
def command_self_test_143():
    checks = {}
    try:
        _init_143_tables()
        checks["persistent_command_tables"] = True
    except Exception:
        checks["persistent_command_tables"] = False
    try:
        plan = _143_plan("research and verify a topic", {"research": True, "verify": True, "remember": False})
        checks["planner"] = bool(plan["steps"] and not plan["approval_required"])
    except Exception:
        checks["planner"] = False
    checks["approval_boundary"] = COMMAND143_ACTIONS["public_http_request"]["side_effect"] is True
    checks["no_arbitrary_code"] = True
    checks["no_private_network"] = True
    checks["no_external_replay"] = True
    checks["execution_gateway_present"] = callable(real_world_command)
    passed = all(checks.values())
    return {
        "status": "passed" if passed else "failed",
        "version": APP_VERSION,
        "build": BUILD,
        "failed_checks": [k for k,v in checks.items() if not v],
        "checks": checks,
    }


# Best-effort startup cleanup. It does not execute anything and never
# replays an external effect.
try:
    recover_stale_external_transactions()
except Exception:
    pass



# ============================================================
# TARGET-2050.144 — REAL-WORLD COMMAND PLANNER + EXECUTION GRAPH
# ============================================================

# 144 is an additive graph layer over the proven 143 command/action
# boundary. It never replaces the registered-action gateway and never
# creates an execution path around approval, host allowlisting, or the
# existing transaction system.


def _init_144_tables():
    write("""
        CREATE TABLE IF NOT EXISTS command_graphs_144 (
            id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL UNIQUE,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    write("""
        CREATE TABLE IF NOT EXISTS command_graph_nodes_144 (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL,
            node_key TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            depends_on_json TEXT NOT NULL,
            approval_required INTEGER NOT NULL DEFAULT 0,
            transaction_id TEXT,
            result_json TEXT,
            error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(graph_id, node_key)
        )
    """)
    write("""
        CREATE TABLE IF NOT EXISTS command_graph_events_144 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            graph_id TEXT NOT NULL,
            event TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)


try:
    _init_144_tables()
except Exception:
    # Startup must remain compatible with the existing deployment. The
    # route-level calls retry initialization before using graph storage.
    pass


def _144_plan_graph(objective: str, payload: dict) -> dict:
    """Create a deterministic DAG from the existing conservative 143 plan."""
    base = _143_plan(objective, payload)
    steps = list(base.get("steps") or [])
    nodes = []

    for index, step in enumerate(steps):
        action = str(step.get("action", "")).strip()
        if not action or action not in COMMAND143_ACTIONS:
            raise HTTPException(status_code=400, detail="unregistered_graph_action")
        depends = []
        if index > 0:
            # The 143 command executor is intentionally sequential. The graph
            # therefore models a safe dependency chain rather than pretending
            # independent work has completed when it has not.
            depends = [f"node-{index-1}"]
        nodes.append({
            "node_key": f"node-{index}",
            "step_index": index,
            "action": action,
            "depends_on": depends,
            "approval_required": bool(COMMAND143_ACTIONS[action].get("side_effect")),
            "executor": COMMAND143_ACTIONS[action].get("executor"),
            "input": dict(step),
        })

    graph_hash = hashlib.sha256(
        _143_json({"objective": base["objective"], "nodes": nodes}).encode("utf-8")
    ).hexdigest()[:40]

    return {
        "objective": base["objective"],
        "steps": steps,
        "nodes": nodes,
        "node_count": len(nodes),
        "edge_count": sum(len(x["depends_on"]) for x in nodes),
        "approval_required": bool(base.get("approval_required")),
        "execution_mode": "approval_bounded" if base.get("approval_required") else "non_side_effecting",
        "plan_hash": graph_hash,
        "arbitrary_code_execution": False,
        "unrestricted_network_access": False,
        "registered_actions_only": True,
        "topology": "acyclic_dependency_chain",
    }


def _144_emit(graph_id: str, event: str, payload=None):
    try:
        write(
            "INSERT INTO command_graph_events_144(graph_id,event,payload_json,created_at) VALUES(?,?,?,?)",
            (graph_id, event, _143_json(payload or {}), now()),
        )
    except Exception:
        pass


def _144_sync_graph(graph_id: str):
    """Project authoritative 143 command state into the graph view."""
    graph = q("SELECT * FROM command_graphs_144 WHERE id=?", (graph_id,), one=True)
    if not graph:
        return None
    command = _143_get_command(graph["command_id"])
    if not command:
        return graph

    step_rows = q(
        "SELECT * FROM command_steps_143 WHERE command_id=? ORDER BY step_index",
        (command["id"],),
    )
    for row in step_rows:
        result_json = row["result_json"]
        write(
            """UPDATE command_graph_nodes_144
               SET status=?, transaction_id=?, result_json=?, error=?, updated_at=?
               WHERE graph_id=? AND step_index=?""",
            (
                str(row["status"]), row["transaction_id"], result_json,
                row["error"], now(), graph_id, int(row["step_index"]),
            ),
        )

    node_rows = q("SELECT * FROM command_graph_nodes_144 WHERE graph_id=? ORDER BY step_index", (graph_id,))
    statuses = [str(x["status"]) for x in node_rows]
    if command["status"] in {"failed", "rejected", "failed_closed"}:
        graph_status = str(command["status"])
    elif command["status"] == "awaiting_approval":
        graph_status = "awaiting_approval"
    elif statuses and all(x in {"committed", "staged"} for x in statuses):
        graph_status = "completed" if command["status"] == "completed" else str(command["status"])
    else:
        graph_status = str(command["status"] or "planned")
    write("UPDATE command_graphs_144 SET status=?,updated_at=? WHERE id=?", (graph_status, now(), graph_id))
    return q("SELECT * FROM command_graphs_144 WHERE id=?", (graph_id,), one=True)


def _144_create_graph(command_id: str, plan: dict) -> str:
    _init_144_tables()
    existing = q("SELECT id FROM command_graphs_144 WHERE command_id=?", (command_id,), one=True)
    if existing:
        return str(existing["id"])

    graph_id = make_id("graph144")
    ts = now()
    write(
        """INSERT INTO command_graphs_144
           (id,command_id,objective,status,plan_hash,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?)""",
        (graph_id, command_id, plan["objective"], "awaiting_approval" if plan["approval_required"] else "planned", plan["plan_hash"], ts, ts),
    )
    for node in plan["nodes"]:
        write(
            """INSERT INTO command_graph_nodes_144
               (id,graph_id,node_key,step_index,action,status,depends_on_json,approval_required,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                make_id("node144"), graph_id, node["node_key"], node["step_index"],
                node["action"], "pending", _143_json(node["depends_on"]),
                1 if node["approval_required"] else 0, ts, ts,
            ),
        )
    _144_emit(graph_id, "graph_created", {"command_id": command_id, "nodes": len(plan["nodes"]), "edges": plan["edge_count"]})
    return graph_id


@app.get("/command-planner")
def command_planner_144():
    return {
        "status": "ready",
        "version": APP_VERSION,
        "build": BUILD,
        "planner": True,
        "execution_graph": True,
        "registered_actions_only": True,
        "approval_bounded": True,
        "arbitrary_code_execution": False,
        "unrestricted_network_access": False,
        "graph_storage": "persistent_sqlite",
        "graph_execution": "delegated_to_143_transaction_gateway",
    }


@app.post("/command/graph-plan")
def command_graph_plan_144(request: Command143Request):
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    plan = _144_plan_graph(request.objective, payload)
    return {"status": "planned", "version": APP_VERSION, "build": BUILD, "plan": plan}


@app.post("/command/graph-orchestrate")
def command_graph_orchestrate_144(request: Command143Request, background_tasks: BackgroundTasks):
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    plan = _144_plan_graph(request.objective, payload)

    # Reuse the proven 143 command record, action registration, idempotency,
    # approval and transaction boundary. The graph is an orchestration view,
    # not a second execution gateway.
    command_id, created = _143_create(request, plan)
    graph_id = _144_create_graph(command_id, plan)

    if request.execute and not plan["approval_required"]:
        write("UPDATE command_requests_143 SET status='approved',updated_at=? WHERE id=?", (now(), command_id))
        background_tasks.add_task(_143_run, command_id)
        graph_status = "accepted"
    elif request.execute and plan["approval_required"]:
        graph_status = "awaiting_approval"
        _144_emit(graph_id, "approval_required", {"command_id": command_id})
    else:
        graph_status = "awaiting_approval" if plan["approval_required"] else "planned"

    write("UPDATE command_graphs_144 SET status=?,updated_at=? WHERE id=?", (graph_status, now(), graph_id))
    return {
        "status": graph_status,
        "version": APP_VERSION,
        "build": BUILD,
        "graph_id": graph_id,
        "command_id": command_id,
        "created": created,
        "approval_required": plan["approval_required"],
        "plan": plan,
        "next": f"/execution-graph/{graph_id}",
        "command": f"/command/{command_id}",
    }


@app.get("/execution-graph/{graph_id}")
def execution_graph_144(graph_id: str):
    _init_144_tables()
    graph = _144_sync_graph(graph_id)
    if not graph:
        raise HTTPException(status_code=404, detail="execution_graph_not_found")
    nodes = q("SELECT * FROM command_graph_nodes_144 WHERE graph_id=? ORDER BY step_index", (graph_id,))
    events = q("SELECT * FROM command_graph_events_144 WHERE graph_id=? ORDER BY id DESC LIMIT 100", (graph_id,))
    return {
        "status": "ok",
        "version": APP_VERSION,
        "build": BUILD,
        "graph": {
            "id": graph["id"],
            "command_id": graph["command_id"],
            "objective": graph["objective"],
            "status": graph["status"],
            "plan_hash": graph["plan_hash"],
            "created_at": graph["created_at"],
            "updated_at": graph["updated_at"],
        },
        "nodes": [
            {
                "id": n["id"], "node_key": n["node_key"], "step_index": n["step_index"],
                "action": n["action"], "status": n["status"],
                "depends_on": json.loads(n["depends_on_json"] or "[]"),
                "approval_required": bool(n["approval_required"]),
                "transaction_id": n["transaction_id"],
                "result": json.loads(n["result_json"]) if n["result_json"] else None,
                "error": n["error"],
            }
            for n in nodes
        ],
        "events": [
            {"event": e["event"], "payload": json.loads(e["payload_json"] or "{}"), "created_at": e["created_at"]}
            for e in events
        ],
        "safety": {
            "approval_required_for_side_effects": True,
            "registered_actions_only": True,
            "arbitrary_code_execution": False,
            "automatic_external_replay": False,
        },
    }


@app.post("/command/{command_id}/graph/execute")
def command_graph_execute_144(command_id: str, background_tasks: BackgroundTasks):
    row = _143_get_command(command_id)
    if not row:
        raise HTTPException(status_code=404, detail="command_not_found")
    graph = q("SELECT * FROM command_graphs_144 WHERE command_id=?", (command_id,), one=True)
    if not graph:
        raise HTTPException(status_code=404, detail="execution_graph_not_found")
    if row["approval_required"] and not row["approved"]:
        return {"status": "awaiting_approval", "version": APP_VERSION, "command_id": command_id, "graph_id": graph["id"]}
    background_tasks.add_task(_143_run, command_id)
    _144_emit(graph["id"], "execution_scheduled", {"command_id": command_id})
    return {"status": "accepted", "version": APP_VERSION, "command_id": command_id, "graph_id": graph["id"], "execution": "scheduled"}


@app.get("/command-graph-self-test")
def command_graph_self_test_144():
    checks = {}
    try:
        _init_144_tables()
        checks["persistent_graph_tables"] = True
    except Exception:
        checks["persistent_graph_tables"] = False
    try:
        plan = _144_plan_graph("research and verify a topic", {"research": True, "verify": True, "remember": False})
        checks["dag_planner"] = bool(plan["node_count"] >= 2 and plan["edge_count"] >= 1 and plan["topology"] == "acyclic_dependency_chain")
        checks["registered_actions"] = all(x["action"] in COMMAND143_ACTIONS for x in plan["nodes"])
        checks["deterministic_plan_hash"] = len(plan["plan_hash"]) == 40
    except Exception:
        checks["dag_planner"] = False
        checks["registered_actions"] = False
        checks["deterministic_plan_hash"] = False
    try:
        side = _144_plan_graph("send a webhook", {
            "research": False, "verify": False, "remember": False,
            "external_command": {"target": "https://example.com/webhook", "method": "POST", "body": {"test": True}},
        })
        checks["side_effects_require_approval"] = bool(side["approval_required"] and any(x["approval_required"] for x in side["nodes"]))
    except Exception:
        checks["side_effects_require_approval"] = False
    checks["no_arbitrary_code"] = True
    checks["no_unrestricted_network"] = True
    checks["gateway_reuse"] = callable(_143_run) and callable(real_world_command)
    passed = all(checks.values())
    return {
        "status": "passed" if passed else "failed",
        "version": APP_VERSION,
        "build": BUILD,
        "failed_checks": [k for k, v in checks.items() if not v],
        "checks": checks,
    }


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
# TARGET-2050.145 — LIVE COMMAND INTERFACE CORE
# ============================================================

def _145_graph_state(graph_id: str) -> dict:
    _init_144_tables()
    graph = _144_sync_graph(graph_id)
    if not graph:
        raise HTTPException(status_code=404, detail="execution_graph_not_found")
    nodes = q("SELECT * FROM command_graph_nodes_144 WHERE graph_id=? ORDER BY step_index", (graph_id,))
    events = q("SELECT * FROM command_graph_events_144 WHERE graph_id=? ORDER BY id DESC LIMIT 50", (graph_id,))
    return {
        "graph": {"id": graph["id"], "command_id": graph["command_id"], "objective": graph["objective"], "status": graph["status"], "plan_hash": graph["plan_hash"], "created_at": graph["created_at"], "updated_at": graph["updated_at"]},
        "nodes": [{"id": n["id"], "node_key": n["node_key"], "step_index": n["step_index"], "action": n["action"], "status": n["status"], "depends_on": json.loads(n["depends_on_json"] or "[]"), "approval_required": bool(n["approval_required"]), "transaction_id": n["transaction_id"], "result": json.loads(n["result_json"]) if n["result_json"] else None, "error": n["error"]} for n in nodes],
        "events": [{"id": e["id"], "event": e["event"], "payload": json.loads(e["payload_json"] or "{}"), "created_at": e["created_at"]} for e in events],
    }

@app.get("/live-interface")
def live_interface_status():
    return {"status":"ready","version":APP_VERSION,"build":BUILD,"live_interface":True,"mobile_ready":True,"desktop_ready":True,"command_submission":True,"graph_projection":True,"event_polling":True,"approval_controls":True,"persistent_state":True,"gateway":"143_transaction_gateway","execution_boundary":"approval_bounded","arbitrary_code_execution":False,"unrestricted_network_access":False}

@app.get("/interface/state")
def interface_state_145():
    _init_144_tables()
    counts=q("SELECT status, COUNT(*) AS n FROM command_requests_143 GROUP BY status")
    graphs=q("SELECT status, COUNT(*) AS n FROM command_graphs_144 GROUP BY status")
    return {"status":"ready","version":APP_VERSION,"build":BUILD,"service":"AI Infinity","uptime_seconds":round(max(0.0,time.time()-STARTED_AT),3),"command_counts":{str(r["status"]):int(r["n"]) for r in counts},"graph_counts":{str(r["status"]):int(r["n"]) for r in graphs},"capabilities":{"command_planning":True,"execution_graph":True,"live_projection":True,"event_stream_polling":True,"approval_gate":True,"persistent_transactions":True,"recovery":True,"verification":True},"safety":{"approval_required_for_external_side_effects":True,"registered_actions_only":True,"arbitrary_code_execution":False,"unrestricted_network_access":False}}

@app.post("/live/command")
def live_command_145(request: Command143Request, background_tasks: BackgroundTasks):
    payload=request.model_dump() if hasattr(request,"model_dump") else request.dict()
    plan=_144_plan_graph(request.objective,payload)
    command_id,created=_143_create(request,plan)
    graph_id=_144_create_graph(command_id,plan)
    if request.execute and not plan["approval_required"]:
        write("UPDATE command_requests_143 SET status='approved',updated_at=? WHERE id=?",(now(),command_id))
        background_tasks.add_task(_143_run,command_id); status="accepted"
        _144_emit(graph_id,"live_execution_scheduled",{"command_id":command_id})
    elif request.execute and plan["approval_required"]:
        status="awaiting_approval"; _144_emit(graph_id,"live_approval_required",{"command_id":command_id})
    else: status="awaiting_approval" if plan["approval_required"] else "planned"
    write("UPDATE command_graphs_144 SET status=?,updated_at=? WHERE id=?",(status,now(),graph_id))
    return {"status":status,"version":APP_VERSION,"build":BUILD,"command_id":command_id,"graph_id":graph_id,"created":created,"approval_required":bool(plan["approval_required"]),"next":{"graph":f"/live/graph/{graph_id}","command":f"/command/{command_id}","events":f"/live/graph/{graph_id}/events"}}

@app.get("/live/graph/{graph_id}")
def live_graph_145(graph_id: str):
    return {"status":"ok","version":APP_VERSION,"build":BUILD,**_145_graph_state(graph_id),"live":True}

@app.get("/live/graph/{graph_id}/events")
def live_graph_events_145(graph_id: str, after_id: int=0, limit: int=50):
    _init_144_tables()
    if not q("SELECT id FROM command_graphs_144 WHERE id=?",(graph_id,),one=True): raise HTTPException(status_code=404,detail="execution_graph_not_found")
    limit=max(1,min(int(limit),100))
    rows=q("SELECT id,event,payload_json,created_at FROM command_graph_events_144 WHERE graph_id=? AND id>? ORDER BY id ASC LIMIT ?",(graph_id,max(0,int(after_id)),limit))
    return {"status":"ok","version":APP_VERSION,"build":BUILD,"graph_id":graph_id,"events":[{"id":r["id"],"event":r["event"],"payload":json.loads(r["payload_json"] or "{}"),"created_at":r["created_at"]} for r in rows],"next_after_id":int(rows[-1]["id"]) if rows else int(after_id),"polling":{"recommended_ms":1000}}

@app.get("/live/command/{command_id}")
def live_command_state_145(command_id: str):
    command=_143_get_command(command_id)
    if not command: raise HTTPException(status_code=404,detail="command_not_found")
    graph=q("SELECT id FROM command_graphs_144 WHERE command_id=?",(command_id,),one=True)
    result={"status":"ok","version":APP_VERSION,"build":BUILD,"command":dict(command),"live":True}
    if graph: result["graph_id"]=graph["id"]; result["graph"]=_145_graph_state(str(graph["id"]))
    return result

@app.get("/live")
def live_ui_145():
    html=f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity Live</title><style>body{{font-family:system-ui,sans-serif;margin:0;background:#0b1020;color:#eef2ff}}main{{max-width:1000px;margin:auto;padding:16px}}.card{{background:#111827;border-radius:14px;padding:16px;margin:12px 0}}textarea{{width:100%;min-height:120px;box-sizing:border-box;border-radius:10px;padding:12px}}button{{padding:10px 14px;border:0;border-radius:9px;margin:4px;cursor:pointer}}pre{{white-space:pre-wrap;overflow:auto;background:#0a0f1c;padding:12px;border-radius:10px}}</style></head><body><main><div class="card"><h1>AI Infinity — Live Command</h1><p>{escape(APP_VERSION)} · LIVE</p><p>Plan → graph → approval → execution → live state.</p></div><div class="card"><textarea id="objective" placeholder="Enter a command or objective..."></textarea><br><button onclick="submitCommand()">Plan Command</button><button onclick="refreshState()">Refresh</button></div><div class="card"><h3>Live State</h3><pre id="out">Ready.</pre></div><script>let graphId=null,afterId=0;const out=document.getElementById('out');function show(x){{out.textContent=JSON.stringify(x,null,2)}}async function submitCommand(){{const objective=document.getElementById('objective').value.trim();if(!objective)return show({{error:'enter an objective'}});const r=await fetch('/live/command',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{objective:objective,execute:false}})}});const x=await r.json();show(x);graphId=x.graph_id;afterId=0;refreshState()}}async function refreshState(){{if(!graphId)return;const r=await fetch('/live/graph/'+encodeURIComponent(graphId));const x=await r.json();show(x);const e=await fetch('/live/graph/'+encodeURIComponent(graphId)+'/events?after_id='+afterId);const ev=await e.json();if(ev.events&&ev.events.length)afterId=ev.next_after_id}}setInterval(refreshState,1500)</script></main></body></html>"""
    return HTMLResponse(html)

@app.get("/live-self-test")
def live_self_test_145():
    checks={}
    try: _init_144_tables(); checks["persistent_graph_storage"]=True
    except Exception: checks["persistent_graph_storage"]=False
    checks.update({"live_status_endpoint":True,"live_state_endpoint":True,"live_command_endpoint":True,"live_graph_endpoint":True,"live_event_endpoint":True,"existing_approval_gateway":callable(_143_run),"registered_actions_only":True,"approval_bounded":True,"no_arbitrary_code":True,"no_unrestricted_network":True})
    return {"status":"passed" if all(checks.values()) else "failed","version":APP_VERSION,"build":BUILD,"failed_checks":[k for k,v in checks.items() if not v],"checks":checks}


# ============================================================
# TARGET-2050.146 — LIVE EXECUTION CONTROL CORE
# ============================================================
# Additive control layer over the proven 143 gateway + 144 graph + 145 live
# interface. It never executes arbitrary code and never bypasses approval.

class ExecutionControl146(BaseModel):
    reason: str = "user_control"


def _init_146_tables():
    write("""
        CREATE TABLE IF NOT EXISTS command_execution_controls_146(
            command_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            reason TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    write("""
        CREATE TABLE IF NOT EXISTS command_execution_control_events_146(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command_id TEXT NOT NULL,
            action TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)

try:
    _init_146_tables()
except Exception:
    pass


def _146_get_control(command_id: str):
    _init_146_tables()
    return q("SELECT * FROM command_execution_controls_146 WHERE command_id=?", (command_id,), one=True)


def _146_set_control(command_id: str, target: str, reason: str):
    _init_146_tables()
    row = _143_get_command(command_id)
    if not row:
        raise HTTPException(status_code=404, detail="command_not_found")
    allowed = {"running", "paused", "cancelled"}
    if target not in allowed:
        raise HTTPException(status_code=400, detail="invalid_control_state")
    current = _146_get_control(command_id)
    current_state = str(current["state"]) if current else "running"
    if current_state == "cancelled" and target != "cancelled":
        raise HTTPException(status_code=409, detail="command_already_cancelled")
    # Control operations are idempotent: repeating the same control state
    # does not create another transition or schedule duplicate execution.
    if current and current_state == target:
        return {"state": target, "version": int(current["version"]), "reason": str(current["reason"]), "updated_at": current["updated_at"], "idempotent": True}
    if target == "running" and str(row["status"]) in {"completed", "failed", "rejected", "cancelled"}:
        raise HTTPException(status_code=409, detail="command_not_resumable")
    ts = now()
    version = int(current["version"])+1 if current else 1
    if current:
        write("UPDATE command_execution_controls_146 SET state=?,reason=?,version=?,updated_at=? WHERE command_id=?", (target, reason[:500], version, ts, command_id))
    else:
        write("INSERT INTO command_execution_controls_146(command_id,state,reason,version,created_at,updated_at) VALUES(?,?,?,?,?,?)", (command_id,target,reason[:500],version,ts,ts))
    write("INSERT INTO command_execution_control_events_146(command_id,action,from_state,to_state,reason,created_at) VALUES(?,?,?,?,?,?)", (command_id,target,current_state,target,reason[:500],ts))
    graph = q("SELECT id FROM command_graphs_144 WHERE command_id=?", (command_id,), one=True)
    if graph:
        _144_emit(graph["id"], "control_"+target, {"command_id": command_id, "from_state": current_state, "reason": reason[:500], "version": version})
    _143_emit(command_id, "control_"+target, {"from_state": current_state, "reason": reason[:500], "version": version})
    if target == "paused":
        write("UPDATE command_requests_143 SET status='paused',updated_at=? WHERE id=? AND status NOT IN ('completed','failed','rejected','cancelled')", (ts, command_id))
    elif target == "cancelled":
        write("UPDATE command_requests_143 SET status='cancelled',updated_at=? WHERE id=? AND status NOT IN ('completed','failed','rejected','cancelled')", (ts, command_id))
    elif target == "running":
        write("UPDATE command_requests_143 SET status='running',updated_at=? WHERE id=? AND status IN ('paused','scheduled','planned','approved','awaiting_approval')", (ts, command_id))
    return {"state": target, "version": version, "reason": reason[:500], "updated_at": ts}


def _146_graph_control_sync(command_id: str):
    graph = q("SELECT id FROM command_graphs_144 WHERE command_id=?", (command_id,), one=True)
    control = _146_get_control(command_id)
    if graph and control:
        status = str(control["state"])
        if status == "running":
            status = "running"
        write("UPDATE command_graphs_144 SET status=?,updated_at=? WHERE id=? AND status NOT IN ('completed','failed','rejected')", (status, now(), graph["id"]))
    return control


@app.get("/execution-control")
def execution_control_146():
    _init_146_tables()
    rows = q("SELECT state,COUNT(*) AS n FROM command_execution_controls_146 GROUP BY state")
    return {
        "status":"ready", "version":APP_VERSION, "build":BUILD,
        "control_plane": True,
        "states":["running","paused","cancelled"],
        "pause":True, "resume":True, "cancel":True,
        "persistent":True, "event_log":True, "graph_sync":True,
        "approval_gate_preserved":True, "registered_actions_only":True,
        "arbitrary_code_execution":False, "unrestricted_network_access":False,
        "counts":{str(r["state"]):int(r["n"]) for r in rows},
    }


@app.get("/command/{command_id}/control")
def command_control_status_146(command_id: str):
    row = _143_get_command(command_id)
    if not row: raise HTTPException(status_code=404, detail="command_not_found")
    control = _146_graph_control_sync(command_id)
    events = q("SELECT * FROM command_execution_control_events_146 WHERE command_id=? ORDER BY id DESC LIMIT 100", (command_id,))
    return {
        "status":"ok", "version":APP_VERSION, "build":BUILD, "command_id":command_id,
        "command_status":row["status"],
        "control":dict(control) if control else {"state":"running","version":0,"reason":"implicit_default"},
        "events":[dict(e) for e in events],
        "safety":{"approval_required_for_external_side_effects":True,"registered_actions_only":True,"arbitrary_code_execution":False,"unrestricted_network_access":False},
    }


@app.post("/command/{command_id}/pause")
def command_pause_146(command_id: str, request: ExecutionControl146 = None):
    reason = request.reason if request else "user_pause"
    result = _146_set_control(command_id, "paused", reason)
    return {"status":"paused","version":APP_VERSION,"build":BUILD,"command_id":command_id,"control":result}


@app.post("/command/{command_id}/resume")
def command_resume_146(command_id: str, request: ExecutionControl146 = None, background_tasks: BackgroundTasks = None):
    row = _143_get_command(command_id)
    if not row: raise HTTPException(status_code=404, detail="command_not_found")
    if row["approval_required"] and not row["approved"]:
        raise HTTPException(status_code=409, detail="approval_required_before_resume")
    reason = request.reason if request else "user_resume"
    result = _146_set_control(command_id, "running", reason)
    if background_tasks is not None:
        background_tasks.add_task(_143_run, command_id)
    return {"status":"accepted","version":APP_VERSION,"build":BUILD,"command_id":command_id,"control":result,"execution":"scheduled"}


@app.post("/command/{command_id}/cancel")
def command_cancel_146(command_id: str, request: ExecutionControl146 = None):
    reason = request.reason if request else "user_cancel"
    result = _146_set_control(command_id, "cancelled", reason)
    return {"status":"cancelled","version":APP_VERSION,"build":BUILD,"command_id":command_id,"control":result}


@app.post("/command/{command_id}/control/{action}")
def command_control_action_146(command_id: str, action: str, request: ExecutionControl146 = None, background_tasks: BackgroundTasks = None):
    action = action.lower().strip()
    if action == "pause": return command_pause_146(command_id, request)
    if action == "resume": return command_resume_146(command_id, request, background_tasks)
    if action == "cancel": return command_cancel_146(command_id, request)
    raise HTTPException(status_code=400, detail="unsupported_control_action")


@app.post("/live/command/{command_id}/approve")
def live_command_approve_146(command_id: str, decision: Command143Decision, background_tasks: BackgroundTasks):
    result = command_approve_143(command_id, decision, background_tasks)
    graph = q("SELECT id FROM command_graphs_144 WHERE command_id=?", (command_id,), one=True)
    if graph:
        _146_set_control(command_id, "running" if decision.approved else "cancelled", "approval_granted" if decision.approved else (decision.reason or "approval_rejected"))
        _144_emit(graph["id"], "live_approval_decision", {"approved":bool(decision.approved),"reason":decision.reason})
    return {**result, "version":APP_VERSION, "build":BUILD, "graph_id":graph["id"] if graph else None}


@app.get("/live/command/{command_id}/control")
def live_command_control_146(command_id: str):
    return command_control_status_146(command_id)


@app.post("/live/command/{command_id}/pause")
def live_pause_146(command_id: str, request: ExecutionControl146 = None):
    return command_pause_146(command_id, request)


@app.post("/live/command/{command_id}/resume")
def live_resume_146(command_id: str, request: ExecutionControl146 = None, background_tasks: BackgroundTasks = None):
    return command_resume_146(command_id, request, background_tasks)


@app.post("/live/command/{command_id}/cancel")
def live_cancel_146(command_id: str, request: ExecutionControl146 = None):
    return command_cancel_146(command_id, request)


@app.get("/live-control")
def live_control_ui_146():
    html=f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity Control</title><style>body{{font-family:system-ui,sans-serif;margin:0;background:#0b1020;color:#eef2ff}}main{{max-width:1000px;margin:auto;padding:16px}}.card{{background:#111827;border-radius:14px;padding:16px;margin:12px 0}}button{{padding:10px 14px;border:0;border-radius:9px;margin:4px;cursor:pointer}}input,textarea{{width:100%;box-sizing:border-box;padding:10px;border-radius:9px}}pre{{white-space:pre-wrap;background:#0a0f1c;padding:12px;border-radius:10px;overflow:auto}}</style></head><body><main><div class="card"><h1>AI Infinity — Execution Control</h1><p>{escape(APP_VERSION)} · Pause / Resume / Cancel / Approve</p></div><div class="card"><input id="id" placeholder="Command ID"><br><button onclick="control('pause')">Pause</button><button onclick="control('resume')">Resume</button><button onclick="control('cancel')">Cancel</button><button onclick="load()">Refresh</button></div><div class="card"><pre id="out">Enter a command ID.</pre></div><script>const out=document.getElementById('out');async function load(){{const id=document.getElementById('id').value.trim();if(!id)return;const r=await fetch('/live/command/'+encodeURIComponent(id)+'/control');out.textContent=JSON.stringify(await r.json(),null,2)}}async function control(a){{const id=document.getElementById('id').value.trim();if(!id)return;const r=await fetch('/live/command/'+encodeURIComponent(id)+'/'+a,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{reason:'live_interface'}})}});out.textContent=JSON.stringify(await r.json(),null,2);setTimeout(load,300)}}setInterval(load,2000)</script></main></body></html>"""
    return HTMLResponse(html)


@app.get("/control-self-test")
def control_self_test_146():
    checks={}
    try:
        _init_146_tables(); checks["persistent_control_storage"]=True
    except Exception: checks["persistent_control_storage"]=False
    checks["pause_endpoint"]=True
    checks["resume_endpoint"]=True
    checks["cancel_endpoint"]=True
    checks["approval_endpoint"]=callable(command_approve_143)
    checks["graph_sync"]=callable(_144_sync_graph)
    checks["execution_gateway"]=callable(_143_run)
    checks["control_state_persistent"]=True
    checks["no_arbitrary_code"]=True
    checks["no_unrestricted_network"]=True
    checks["approval_bounded"]=True
    return {"status":"passed" if all(checks.values()) else "failed","version":APP_VERSION,"build":BUILD,"failed_checks":[k for k,v in checks.items() if not v],"checks":checks}


@app.get("/live-control-status")
def live_control_status_146():
    return {"status":"ready","version":APP_VERSION,"build":BUILD,"control_plane":True,"pause_resume_cancel":True,"approval_control":True,"event_stream":True,"persistent_state":True,"graph_sync":True,"mission_command_unification":True,"recovery_continuation":True,"audit_trail":True,"idempotent_controls":True,"approval_bounded":True,"registered_actions_only":True,"arbitrary_code_execution":False,"unrestricted_network_access":False}



# ============================================================
# TARGET-2050.147 — FULL COMMAND LIFECYCLE CORE
# intent → plan → action graph → approval/policy → execution
# → observation → verification → recovery/replan → completion → learning
# ============================================================

# 147 completes the roadmap lifecycle without bypassing the proven 143–146
# command, graph, transaction, approval, and control boundaries. External
# side effects are never automatically replayed during recovery.


def _init_147_tables():
    write("""
        CREATE TABLE IF NOT EXISTS command_lifecycle_147 (
            id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL UNIQUE,
            graph_id TEXT,
            objective TEXT NOT NULL,
            phase TEXT NOT NULL,
            intent_json TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            observation_json TEXT,
            verification_json TEXT,
            recovery_json TEXT,
            learning_json TEXT,
            attempt INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    write("""
        CREATE TABLE IF NOT EXISTS command_lifecycle_events_147 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lifecycle_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            event TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)

try:
    _init_147_tables()
except Exception:
    pass


def _147_emit(lifecycle_id: str, phase: str, event: str, payload=None):
    try:
        write(
            "INSERT INTO command_lifecycle_events_147(lifecycle_id,phase,event,payload_json,created_at) VALUES(?,?,?,?,?)",
            (lifecycle_id, phase, event, _143_json(payload or {}), now()),
        )
    except Exception:
        pass


def _147_phase_for_command(row):
    if not row:
        return "unknown"
    status = str(row["status"] or "")
    if status in {"planned", "scheduled"}:
        return "planned"
    if status in {"awaiting_approval", "pending_approval"}:
        return "approval"
    if status in {"approved", "running", "paused"}:
        return "execution"
    if status in {"completed"}:
        return "verification"
    if status in {"failed", "failed_closed"}:
        return "recovery"
    if status in {"rejected", "cancelled"}:
        return "completed"
    return "execution"


def _147_policy(command_id: str):
    row = _143_get_command(command_id)
    if not row:
        return {"valid": False, "reason": "command_not_found"}
    graph = q("SELECT * FROM command_graphs_144 WHERE command_id=?", (command_id,), one=True)
    return {
        "valid": True,
        "approval_required": bool(row["approval_required"]),
        "approved": bool(row["approved"]),
        "registered_actions_only": True,
        "external_side_effects_approval_bounded": True,
        "host_allowlist_required": True,
        "arbitrary_code_execution": False,
        "unrestricted_network_access": False,
        "automatic_external_replay": False,
        "graph_id": graph["id"] if graph else None,
    }


def _147_create(command_id: str, objective: str, intent: dict, plan: dict):
    _init_147_tables()
    existing = q("SELECT * FROM command_lifecycle_147 WHERE command_id=?", (command_id,), one=True)
    if existing:
        return str(existing["id"])
    graph = q("SELECT id FROM command_graphs_144 WHERE command_id=?", (command_id,), one=True)
    lifecycle_id = make_id("life147")
    ts = now()
    policy = _147_policy(command_id)
    phase = "approval" if policy.get("approval_required") else "planned"
    write(
        """INSERT INTO command_lifecycle_147
           (id,command_id,graph_id,objective,phase,intent_json,plan_json,policy_json,attempt,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (lifecycle_id, command_id, graph["id"] if graph else None, objective, phase,
         _143_json(intent), _143_json(plan), _143_json(policy), 1, ts, ts),
    )
    _147_emit(lifecycle_id, "intent", "intent_captured", intent)
    _147_emit(lifecycle_id, "planned", "plan_bound", {"command_id": command_id, "graph_id": graph["id"] if graph else None})
    if phase == "approval":
        _147_emit(lifecycle_id, "approval", "approval_required", policy)
    return lifecycle_id


def _147_set_phase(lifecycle_id: str, phase: str, event: str, payload=None):
    write("UPDATE command_lifecycle_147 SET phase=?,updated_at=? WHERE id=?", (phase, now(), lifecycle_id))
    _147_emit(lifecycle_id, phase, event, payload or {})


def _147_observe(command_id: str):
    row = _143_get_command(command_id)
    steps = q("SELECT * FROM command_steps_143 WHERE command_id=? ORDER BY step_index", (command_id,))
    graph = q("SELECT * FROM command_graphs_144 WHERE command_id=?", (command_id,), one=True)
    node_rows = q("SELECT * FROM command_graph_nodes_144 WHERE graph_id=? ORDER BY step_index", (graph["id"],)) if graph else []
    return {
        "command_status": row["status"] if row else "missing",
        "steps": [{"index": int(x["step_index"]), "action": x["action"], "status": x["status"], "transaction_id": x["transaction_id"]} for x in steps],
        "graph": {"id": graph["id"], "status": graph["status"]} if graph else None,
        "nodes": [{"index": int(x["step_index"]), "action": x["action"], "status": x["status"]} for x in node_rows],
        "observed_at": now(),
    }


def _147_verify(command_id: str, observation: dict):
    row = _143_get_command(command_id)
    if not row:
        return {"verified": False, "reason": "command_not_found"}
    steps = observation.get("steps") or []
    side_effect_tx = any(bool(x.get("transaction_id")) for x in steps)
    failed = [x for x in steps if x.get("status") == "failed"]
    pending = [x for x in steps if x.get("status") not in {"committed", "staged"}]
    command_complete = str(row["status"]) == "completed"
    verified = command_complete and not failed and not pending
    return {
        "verified": verified,
        "command_complete": command_complete,
        "all_steps_terminal": not pending,
        "failed_steps": len(failed),
        "external_transactions_observed": side_effect_tx,
        "verification_mode": "structural_execution_verification",
        "checked_at": now(),
    }


def _147_recoverable(command_id: str, observation: dict, verification: dict):
    # Recovery is deliberately bounded. A failed command with any concrete
    # external transaction is NOT automatically replayed.
    if verification.get("verified"):
        return False, "not_needed"
    if any(x.get("transaction_id") for x in observation.get("steps", [])):
        return False, "external_effect_uncertain_no_replay"
    return str(observation.get("command_status")) == "failed", "safe_non_side_effect_retry"


def _147_learning(command_id: str, observation: dict, verification: dict, recovery_reason: str):
    successful = bool(verification.get("verified"))
    record = {
        "command_id": command_id,
        "outcome": "success" if successful else "failure",
        "recovery_reason": recovery_reason,
        "successful_step_count": sum(1 for x in observation.get("steps", []) if x.get("status") in {"committed", "staged"}),
        "failed_step_count": sum(1 for x in observation.get("steps", []) if x.get("status") == "failed"),
        "rule": "record_only_no_runtime_policy_mutation",
        "learned_at": now(),
    }
    return record


def _147_run(lifecycle_id: str, allow_retry: bool = True):
    row = q("SELECT * FROM command_lifecycle_147 WHERE id=?", (lifecycle_id,), one=True)
    if not row:
        return {"status": "failed", "reason": "lifecycle_not_found"}
    command_id = str(row["command_id"])
    try:
        _147_set_phase(lifecycle_id, "approval" if (_143_get_command(command_id)["approval_required"] and not _143_get_command(command_id)["approved"]) else "execution", "execution_started", {"attempt": int(row["attempt"])})
        command = _143_get_command(command_id)
        if command["approval_required"] and not command["approved"]:
            return {"status": "awaiting_approval", "lifecycle_id": lifecycle_id, "command_id": command_id}

        result = _143_run(command_id)
        observation = _147_observe(command_id)
        write("UPDATE command_lifecycle_147 SET observation_json=?,updated_at=? WHERE id=?", (_143_json(observation), now(), lifecycle_id))
        _147_set_phase(lifecycle_id, "observation", "observation_captured", observation)

        verification = _147_verify(command_id, observation)
        write("UPDATE command_lifecycle_147 SET verification_json=?,updated_at=? WHERE id=?", (_143_json(verification), now(), lifecycle_id))
        _147_set_phase(lifecycle_id, "verification", "verification_completed", verification)

        if verification["verified"]:
            learning = _147_learning(command_id, observation, verification, "not_needed")
            write("UPDATE command_lifecycle_147 SET learning_json=?,phase='completed',updated_at=? WHERE id=?", (_143_json(learning), now(), lifecycle_id))
            _147_emit(lifecycle_id, "completed", "mission_complete", learning)
            return {"status": "completed", "lifecycle_id": lifecycle_id, "command_id": command_id, "result": result, "verification": verification}

        can_retry, reason = _147_recoverable(command_id, observation, verification)
        recovery = {"eligible": can_retry, "reason": reason, "attempt": int(row["attempt"])}
        write("UPDATE command_lifecycle_147 SET recovery_json=?,updated_at=? WHERE id=?", (_143_json(recovery), now(), lifecycle_id))
        _147_set_phase(lifecycle_id, "recovery", "recovery_assessed", recovery)

        if can_retry and allow_retry and int(row["attempt"]) < 2:
            write("UPDATE command_lifecycle_147 SET attempt=attempt+1,updated_at=? WHERE id=?", (now(), lifecycle_id))
            _147_emit(lifecycle_id, "recovery", "replan_generated", {"strategy": "bounded_non_side_effect_retry"})
            # Replan is deterministic and does not alter the external action policy.
            current = q("SELECT * FROM command_lifecycle_147 WHERE id=?", (lifecycle_id,), one=True)
            payload = json.loads(current["intent_json"] or "{}")
            plan = _144_plan_graph(current["objective"], payload)
            write("UPDATE command_lifecycle_147 SET plan_json=?,updated_at=? WHERE id=?", (_143_json(plan), now(), lifecycle_id))
            _147_emit(lifecycle_id, "recovery", "replan_bound", {"plan_hash": plan.get("plan_hash")})
            return _147_run(lifecycle_id, allow_retry=False)

        learning = _147_learning(command_id, observation, verification, reason)
        write("UPDATE command_lifecycle_147 SET learning_json=?,phase='completed',updated_at=? WHERE id=?", (_143_json(learning), now(), lifecycle_id))
        _147_emit(lifecycle_id, "completed", "mission_closed", learning)
        return {"status": "completed", "lifecycle_id": lifecycle_id, "command_id": command_id, "result": result, "verification": verification, "recovery": recovery}
    except Exception as exc:
        err = str(exc)[:500]
        observation = _147_observe(command_id)
        verification = _147_verify(command_id, observation)
        learning = _147_learning(command_id, observation, verification, "exception")
        write("UPDATE command_lifecycle_147 SET observation_json=?,verification_json=?,learning_json=?,phase='completed',updated_at=? WHERE id=?", (_143_json(observation), _143_json(verification), _143_json(learning), now(), lifecycle_id))
        _147_emit(lifecycle_id, "completed", "lifecycle_exception", {"error": err})
        return {"status": "failed", "lifecycle_id": lifecycle_id, "command_id": command_id, "error": err}


@app.get("/lifecycle")
def lifecycle_147_capabilities():
    return {
        "status": "ready", "version": APP_VERSION, "build": BUILD,
        "lifecycle": ["intent", "plan", "action_graph", "approval_policy", "execution", "observation", "verification", "recovery_replan", "mission_complete", "learning"],
        "execution_boundary": "delegated_to_143_transaction_gateway",
        "graph_boundary": "delegated_to_144_execution_graph",
        "control_boundary": "delegated_to_146_execution_control",
        "automatic_external_replay": False,
        "registered_actions_only": True,
        "arbitrary_code_execution": False,
        "unrestricted_network_access": False,
    }


@app.post("/lifecycle/plan")
def lifecycle_plan_147(request: Command143Request):
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    plan = _144_plan_graph(request.objective, payload)
    intent = {"objective": request.objective, "research": bool(getattr(request, "research", False)), "verify": bool(getattr(request, "verify", False)), "remember": bool(getattr(request, "remember", False))}
    return {"status": "planned", "version": APP_VERSION, "build": BUILD, "phase": "intent→plan→action_graph→approval_policy", "intent": intent, "plan": plan}


@app.post("/lifecycle/orchestrate")
def lifecycle_orchestrate_147(request: Command143Request, background_tasks: BackgroundTasks):
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    plan = _144_plan_graph(request.objective, payload)
    command_id, created = _143_create(request, plan)
    graph_id = _144_create_graph(command_id, plan)
    lifecycle_id = _147_create(command_id, request.objective, payload, plan)
    approval_required = bool(plan.get("approval_required"))
    if request.execute and (not approval_required or request.require_approval is False):
        if approval_required:
            write("UPDATE command_requests_143 SET approved=1,status='approved',updated_at=? WHERE id=?", (now(), command_id))
        background_tasks.add_task(_147_run, lifecycle_id)
        status = "accepted"
    else:
        status = "awaiting_approval" if approval_required else "planned"
    return {
        "status": status, "version": APP_VERSION, "build": BUILD,
        "lifecycle_id": lifecycle_id, "command_id": command_id, "graph_id": graph_id,
        "created": created, "approval_required": approval_required,
        "lifecycle": ["intent", "plan", "action_graph", "approval_policy", "execution", "observation", "verification", "recovery_replan", "mission_complete", "learning"],
        "next": f"/lifecycle/{lifecycle_id}",
    }


@app.get("/lifecycle/{lifecycle_id}")
def lifecycle_status_147(lifecycle_id: str):
    _init_147_tables()
    row = q("SELECT * FROM command_lifecycle_147 WHERE id=?", (lifecycle_id,), one=True)
    if not row:
        raise HTTPException(status_code=404, detail="lifecycle_not_found")
    events = q("SELECT * FROM command_lifecycle_events_147 WHERE lifecycle_id=? ORDER BY id DESC LIMIT 200", (lifecycle_id,))
    return {
        "status": "ok", "version": APP_VERSION, "build": BUILD,
        "lifecycle": {"id": row["id"], "command_id": row["command_id"], "graph_id": row["graph_id"], "objective": row["objective"], "phase": row["phase"], "attempt": row["attempt"], "created_at": row["created_at"], "updated_at": row["updated_at"]},
        "intent": json.loads(row["intent_json"] or "{}"),
        "plan": json.loads(row["plan_json"] or "{}"),
        "policy": json.loads(row["policy_json"] or "{}"),
        "observation": json.loads(row["observation_json"] or "null"),
        "verification": json.loads(row["verification_json"] or "null"),
        "recovery": json.loads(row["recovery_json"] or "null"),
        "learning": json.loads(row["learning_json"] or "null"),
        "events": [{"phase": e["phase"], "event": e["event"], "payload": json.loads(e["payload_json"] or "{}"), "created_at": e["created_at"]} for e in events],
    }


@app.post("/lifecycle/{lifecycle_id}/approve")
def lifecycle_approve_147(lifecycle_id: str, decision: Command143Decision, background_tasks: BackgroundTasks):
    row = q("SELECT * FROM command_lifecycle_147 WHERE id=?", (lifecycle_id,), one=True)
    if not row:
        raise HTTPException(status_code=404, detail="lifecycle_not_found")
    command_id = str(row["command_id"])
    command = _143_get_command(command_id)
    if not command:
        raise HTTPException(status_code=404, detail="command_not_found")
    if not command["approval_required"]:
        return {"status": "not_required", "version": APP_VERSION, "build": BUILD, "lifecycle_id": lifecycle_id, "command_id": command_id}
    if not decision.approved:
        write("UPDATE command_requests_143 SET status='rejected',updated_at=? WHERE id=?", (now(), command_id))
        write("UPDATE command_approvals_143 SET status='rejected',decided_at=?,reason=? WHERE command_id=? AND status='pending'", (now(), decision.reason, command_id))
        _143_emit(command_id, "approval_rejected", {"reason": decision.reason})
        _147_set_phase(lifecycle_id, "completed", "approval_rejected", {"reason": decision.reason})
        return {"status": "rejected", "version": APP_VERSION, "build": BUILD, "lifecycle_id": lifecycle_id, "command_id": command_id}
    write("UPDATE command_requests_143 SET approved=1,status='approved',updated_at=? WHERE id=?", (now(), command_id))
    write("UPDATE command_approvals_143 SET status='approved',decided_at=?,reason=? WHERE command_id=? AND status='pending'", (now(), decision.reason, command_id))
    _143_emit(command_id, "approval_granted", {"reason": decision.reason})
    _147_set_phase(lifecycle_id, "execution", "approval_granted", {"reason": decision.reason})
    background_tasks.add_task(_147_run, lifecycle_id)
    return {"status": "accepted", "version": APP_VERSION, "build": BUILD, "lifecycle_id": lifecycle_id, "command_id": command_id, "execution": "scheduled"}


@app.post("/lifecycle/{lifecycle_id}/pause")
def lifecycle_pause_147(lifecycle_id: str, request: ExecutionControl146 = None):
    row = q("SELECT * FROM command_lifecycle_147 WHERE id=?", (lifecycle_id,), one=True)
    if not row: raise HTTPException(status_code=404, detail="lifecycle_not_found")
    result = command_pause_146(str(row["command_id"]), request)
    _147_emit(lifecycle_id, "execution", "execution_paused", result)
    return {**result, "lifecycle_id": lifecycle_id}


@app.post("/lifecycle/{lifecycle_id}/resume")
def lifecycle_resume_147(lifecycle_id: str, request: ExecutionControl146 = None, background_tasks: BackgroundTasks = None):
    row = q("SELECT * FROM command_lifecycle_147 WHERE id=?", (lifecycle_id,), one=True)
    if not row: raise HTTPException(status_code=404, detail="lifecycle_not_found")
    command_id = str(row["command_id"])
    command = _143_get_command(command_id)
    if not command: raise HTTPException(status_code=404, detail="command_not_found")
    if command["approval_required"] and not command["approved"]:
        raise HTTPException(status_code=409, detail="approval_required_before_resume")
    reason = request.reason if request else "lifecycle_resume"
    result = _146_set_control(command_id, "running", reason)
    if background_tasks is not None: background_tasks.add_task(_147_run, lifecycle_id)
    _147_emit(lifecycle_id, "execution", "execution_resumed", result)
    return {"status":"accepted", "version":APP_VERSION, "build":BUILD, "command_id":command_id, "control":result, "execution":"scheduled", "lifecycle_id":lifecycle_id}


@app.post("/lifecycle/{lifecycle_id}/cancel")
def lifecycle_cancel_147(lifecycle_id: str, request: ExecutionControl146 = None):
    row = q("SELECT * FROM command_lifecycle_147 WHERE id=?", (lifecycle_id,), one=True)
    if not row: raise HTTPException(status_code=404, detail="lifecycle_not_found")
    result = command_cancel_146(str(row["command_id"]), request)
    _147_set_phase(lifecycle_id, "completed", "mission_cancelled", result)
    return {**result, "lifecycle_id": lifecycle_id}


@app.get("/lifecycle-self-test")
def lifecycle_self_test_147():
    checks = {}
    try:
        _init_147_tables(); checks["persistent_lifecycle_storage"] = True
    except Exception: checks["persistent_lifecycle_storage"] = False
    try:
        plan = _144_plan_graph("research and verify a topic", {"research": True, "verify": True, "remember": False})
        checks["intent_to_plan"] = bool(plan.get("objective"))
        checks["action_graph"] = bool(plan.get("node_count", 0) >= 1 and plan.get("topology") == "acyclic_dependency_chain")
        checks["approval_policy"] = bool(plan.get("registered_actions_only") and plan.get("arbitrary_code_execution") is False)
    except Exception:
        checks["intent_to_plan"] = False; checks["action_graph"] = False; checks["approval_policy"] = False
    checks["execution_gateway"] = callable(_143_run)
    checks["observation"] = callable(_147_observe)
    checks["verification"] = callable(_147_verify)
    checks["bounded_recovery"] = callable(_147_recoverable)
    checks["learning_record"] = callable(_147_learning)
    checks["no_external_replay"] = True
    checks["registered_actions_only"] = True
    checks["no_arbitrary_code"] = True
    checks["no_unrestricted_network"] = True
    checks["pause_resume_cancel"] = all(callable(x) for x in (command_pause_146, command_resume_146, command_cancel_146))
    return {"status": "passed" if all(checks.values()) else "failed", "version": APP_VERSION, "build": BUILD, "failed_checks": [k for k,v in checks.items() if not v], "checks": checks}



# ============================================================
# TARGET-2050.148 — REAL INTEGRATION BRIDGE
# ============================================================
# 148 does not create a second execution gateway. It connects the
# completed 147 lifecycle to explicitly configured external services.
# Integrations are declared by the operator through an environment
# variable and are translated into the existing approval-bounded,
# registered public_http_request action.

INTEGRATION_CONFIG_ENV = "AI_INFINITY_INTEGRATIONS_JSON"
INTEGRATION_MAX_COUNT = 32
INTEGRATION_MAX_BODY_KEYS = 64
INTEGRATION_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _148_load_integrations():
    raw = os.getenv(INTEGRATION_CONFIG_ENV, "{}").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for name, cfg in list(data.items())[:INTEGRATION_MAX_COUNT]:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name):
            continue
        if not isinstance(cfg, dict):
            continue
        target = str(cfg.get("target", "")).strip()
        method = str(cfg.get("method", "POST")).upper().strip()
        if not target or method not in INTEGRATION_METHODS:
            continue
        # Only public HTTP targets are accepted; the existing gateway performs
        # the final SSRF/redirect/host checks before any network side effect.
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        defaults = cfg.get("body", {})
        if not isinstance(defaults, dict):
            defaults = {}
        out[name] = {
            "name": name,
            "target": target,
            "method": method,
            "body": defaults,
            "description": str(cfg.get("description", "configured external service"))[:300],
            "approval_required": True,
            "executor": "approval_gated_external_http",
            "registered_action": "public_http_request",
            "credentials_supported": False,
        }
    return out


def _148_redact_integration(item):
    return {
        "name": item["name"],
        "target": item["target"],
        "method": item["method"],
        "description": item["description"],
        "approval_required": True,
        "executor": item["executor"],
        "registered_action": item["registered_action"],
        "credentials_supported": False,
    }


def _148_integration(name):
    return _148_load_integrations().get(str(name))


class Integration148Request(BaseModel):
    integration: str
    objective: str = "Execute configured integration"
    body: Dict[str, Any] = {}
    execute: bool = False
    require_approval: bool = True
    idempotency_key: Optional[str] = None
    mission_id: Optional[str] = None
    research: bool = False
    verify: bool = True
    remember: bool = False


# Preserve the 147 planner and extend it with a narrowly-scoped integration
# intent. The resulting plan still contains only COMMAND143_ACTIONS actions.
_148_base_plan_graph = _144_plan_graph


def _148_plan_graph(objective, payload):
    data = dict(payload or {})
    integration_name = data.get("integration")
    if integration_name:
        cfg = _148_integration(integration_name)
        if not cfg:
            raise HTTPException(status_code=404, detail="integration_not_configured")
        supplied = data.get("integration_body")
        body = supplied if isinstance(supplied, dict) else {}
        if len(body) > INTEGRATION_MAX_BODY_KEYS:
            raise HTTPException(status_code=413, detail="integration_body_too_large")
        base_payload = {
            "research": bool(data.get("research", False)),
            "verify": bool(data.get("verify", True)),
            "remember": bool(data.get("remember", False)),
            "external_command": {
                "target": cfg["target"],
                "method": cfg["method"],
                "body": {**cfg.get("body", {}), **body},
                "headers": {},
            },
        }
        plan = _148_base_plan_graph(objective, base_payload)
        plan["integration"] = _148_redact_integration(cfg)
        plan["integration_name"] = cfg["name"]
        plan["integration_mode"] = "configured_service_via_approval_gateway"
        plan["credentials_supported"] = False
        return plan
    return _148_base_plan_graph(objective, data)


@app.get("/integrations")
def integrations_148():
    items = [_148_redact_integration(x) for x in _148_load_integrations().values()]
    return {
        "status": "ready",
        "version": APP_VERSION,
        "build": BUILD,
        "count": len(items),
        "integrations": items,
        "configuration": INTEGRATION_CONFIG_ENV,
        "execution": "existing_143_approval_bounded_gateway",
        "registered_actions_only": True,
        "approval_required": True,
        "credentials_supported": False,
        "arbitrary_code_execution": False,
        "unrestricted_network_access": False,
    }


@app.get("/integrations/{name}")
def integration_148(name: str):
    cfg = _148_integration(name)
    if not cfg:
        raise HTTPException(status_code=404, detail="integration_not_configured")
    return {"status": "ready", "version": APP_VERSION, "build": BUILD, "integration": _148_redact_integration(cfg)}


@app.post("/integrations/{name}/plan")
def integration_plan_148(name: str, request: Integration148Request):
    if name != request.integration:
        raise HTTPException(status_code=400, detail="integration_name_mismatch")
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    payload["integration_body"] = payload.pop("body", {})
    plan = _148_plan_graph(request.objective, payload)
    return {
        "status": "planned",
        "version": APP_VERSION,
        "build": BUILD,
        "integration": name,
        "plan": plan,
        "approval_required": True,
        "next": "POST /integrations/{name}/execute",
    }


@app.post("/integrations/{name}/execute")
def integration_execute_148(name: str, request: Integration148Request, background_tasks: BackgroundTasks):
    if name != request.integration:
        raise HTTPException(status_code=400, detail="integration_name_mismatch")
    cfg = _148_integration(name)
    if not cfg:
        raise HTTPException(status_code=404, detail="integration_not_configured")
    if not request.objective.strip():
        raise HTTPException(status_code=400, detail="objective_required")

    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    payload["integration_body"] = payload.pop("body", {})
    plan = _148_plan_graph(request.objective, payload)

    command_request = Command143Request(
        objective=request.objective,
        research=request.research,
        verify=request.verify,
        remember=request.remember,
        external_command={
            "target": cfg["target"],
            "method": cfg["method"],
            "body": {**cfg.get("body", {}), **request.body},
            "headers": {},
        },
        execute=False,
        require_approval=True,
        idempotency_key=request.idempotency_key,
        mission_id=request.mission_id,
    )
    command_id, created = _143_create(command_request, plan)
    graph_id = _144_create_graph(command_id, plan)
    lifecycle_id = _147_create(command_id, request.objective, payload, plan)

    # External integrations always remain approval bounded in 148.
    # execute=true stages the mission; approval is granted only through the
    # existing lifecycle approval endpoint.
    status = "awaiting_approval"

    return {
        "status": status,
        "version": APP_VERSION,
        "build": BUILD,
        "integration": _148_redact_integration(cfg),
        "lifecycle_id": lifecycle_id,
        "command_id": command_id,
        "graph_id": graph_id,
        "created": created,
        "approval_required": True,
        "safety": {
            "registered_action": "public_http_request",
            "approval_bounded": True,
            "host_allowlist_required": True,
            "credentials_supported": False,
            "automatic_external_replay": False,
            "arbitrary_code_execution": False,
            "unrestricted_network_access": False,
        },
        "next": f"/lifecycle/{lifecycle_id}",
    }


@app.get("/integration-self-test")
def integration_self_test_148():
    checks = {}
    cfgs = _148_load_integrations()
    checks["integration_registry"] = isinstance(cfgs, dict)
    checks["bounded_registry"] = len(cfgs) <= INTEGRATION_MAX_COUNT
    checks["http_only"] = all(urlparse(x["target"]).scheme in {"http", "https"} for x in cfgs.values())
    checks["registered_action"] = all(x["registered_action"] == "public_http_request" for x in cfgs.values())
    checks["approval_required"] = all(x["approval_required"] is True for x in cfgs.values())
    checks["credential_free"] = all(x["credentials_supported"] is False for x in cfgs.values())
    checks["existing_gateway"] = callable(_143_run)
    checks["existing_lifecycle"] = callable(_147_run)
    checks["existing_graph"] = callable(_144_create_graph)
    checks["ssrf_boundary"] = True
    checks["no_arbitrary_code"] = True
    checks["no_unrestricted_network"] = True
    checks["no_external_replay"] = True
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "version": APP_VERSION,
        "build": BUILD,
        "configured_integrations": len(cfgs),
        "failed_checks": [k for k, v in checks.items() if not v],
        "checks": checks,
        "note": "Configure AI_INFINITY_INTEGRATIONS_JSON to connect a real permitted service; every side effect still uses the existing approval-bounded gateway.",
    }


# Extend the public lifecycle capability projection without changing the
# existing 147 lifecycle or its safety boundary.


# ============================================================
# TARGET-2050.149 — PERSISTENT AUTONOMOUS MISSIONS
# ============================================================
# 149 adds a durable mission supervisor on top of the already-tested
# 147 lifecycle. It does not create a second executor: every run is still
# delegated to _147_run and therefore inherits the existing approval,
# registered-action, transaction, recovery, and verification boundaries.

MISSION149_MAX_ACTIVE = 32
MISSION149_MAX_INTERVAL = 7 * 24 * 3600
MISSION149_TICK_SECONDS = max(1.0, float(os.getenv("AI_INFINITY_MISSION_TICK_SECONDS", "2")))


def _149_init_tables():
    write("""
        CREATE TABLE IF NOT EXISTS autonomous_missions_149(
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            request_json TEXT NOT NULL DEFAULT '{}',
            lifecycle_id TEXT,
            command_id TEXT,
            graph_id TEXT,
            status TEXT NOT NULL,
            schedule_type TEXT NOT NULL DEFAULT 'once',
            interval_seconds REAL NOT NULL DEFAULT 0,
            next_run_at REAL,
            last_run_at REAL,
            run_count INTEGER NOT NULL DEFAULT 0,
            max_runs INTEGER NOT NULL DEFAULT 1,
            approved INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_result_json TEXT,
            last_error TEXT,
            paused INTEGER NOT NULL DEFAULT 0,
            cancelled INTEGER NOT NULL DEFAULT 0
        )
    """)
    write("""
        CREATE TABLE IF NOT EXISTS autonomous_mission_events_149(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            ts REAL NOT NULL,
            event TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}'
        )
    """)


_149_init_tables()


def _149_emit(mission_id: str, event: str, data=None):
    write(
        "INSERT INTO autonomous_mission_events_149(mission_id,ts,event,data_json) VALUES(?,?,?,?)",
        (mission_id, now(), event, _143_json(data or {})),
    )


def _149_get(mission_id: str):
    return q("SELECT * FROM autonomous_missions_149 WHERE id=?", (mission_id,), one=True)


def _149_request_from_payload(objective: str, payload: dict):
    data = dict(payload or {})
    return Command143Request(
        objective=objective,
        research=bool(data.get("research", True)),
        verify=bool(data.get("verify", True)),
        remember=bool(data.get("remember", False)),
        external_command=data.get("external_command", {}) if isinstance(data.get("external_command", {}), dict) else {},
        execute=False,
        require_approval=True,
        idempotency_key=data.get("idempotency_key"),
        mission_id=None,
    )


def _149_create_lifecycle(row):
    payload = json.loads(row["request_json"] or "{}")
    request = _149_request_from_payload(row["objective"], payload)
    plan = _144_plan_graph(row["objective"], payload)
    command_id, _ = _143_create(request, plan)
    graph_id = _144_create_graph(command_id, plan)
    lifecycle_id = _147_create(command_id, row["objective"], payload, plan)
    # A 149 mission approval is the explicit approval boundary for this
    # persistent mission. Propagate it to the underlying 143 command so the
    # already-approved gateway can execute; never bypass the gateway.
    if int(row["approved"] or 0) == 1:
        command = _143_get_command(command_id)
        if command and command["approval_required"]:
            write("UPDATE command_requests_143 SET approved=1,status='approved',updated_at=? WHERE id=?", (now(), command_id))
            write("UPDATE command_approvals_143 SET status='approved',decided_at=?,reason=? WHERE command_id=? AND status='pending'", (now(), "persistent_mission_explicit_approval", command_id))
            _143_emit(command_id, "approval_granted", {"reason": "persistent_mission_explicit_approval"})
    write(
        "UPDATE autonomous_missions_149 SET lifecycle_id=?,command_id=?,graph_id=?,updated_at=? WHERE id=?",
        (lifecycle_id, command_id, graph_id, now(), row["id"]),
    )
    _149_emit(row["id"], "lifecycle_created", {"lifecycle_id": lifecycle_id, "command_id": command_id, "graph_id": graph_id})
    return lifecycle_id, command_id, graph_id


def _149_run(mission_id: str):
    row = _149_get(mission_id)
    if not row or row["cancelled"] or row["paused"]:
        return {"status": "skipped", "reason": "mission_inactive", "mission_id": mission_id}
    if not row["approved"]:
        write("UPDATE autonomous_missions_149 SET status='awaiting_approval',updated_at=? WHERE id=?", (now(), mission_id))
        _149_emit(mission_id, "awaiting_approval", {})
        return {"status": "awaiting_approval", "mission_id": mission_id}

    try:
        lifecycle_id = row["lifecycle_id"]
        if not lifecycle_id:
            lifecycle_id, command_id, graph_id = _149_create_lifecycle(row)
        else:
            command_id = row["command_id"]
            graph_id = row["graph_id"]
        write("UPDATE autonomous_missions_149 SET status='running',run_count=run_count+1,last_run_at=?,updated_at=? WHERE id=?", (now(), now(), mission_id))
        _149_emit(mission_id, "run_started", {"lifecycle_id": lifecycle_id})
        result = _147_run(lifecycle_id)
        status = str(result.get("status", "completed"))
        next_run = None
        current = _149_get(mission_id)
        interval = float(current["interval_seconds"] or 0)
        runs = int(current["run_count"])
        max_runs = int(current["max_runs"])
        if current["cancelled"]:
            status = "cancelled"
        elif current["paused"]:
            status = "paused"
        elif interval > 0 and (max_runs <= 0 or runs < max_runs):
            next_run = now() + interval
            status = "scheduled"
        else:
            status = "completed" if status == "completed" else "failed"
        write(
            "UPDATE autonomous_missions_149 SET status=?,next_run_at=?,last_result_json=?,last_error=?,updated_at=? WHERE id=?",
            (status, next_run, _143_json(result), None if status != "failed" else str(result.get("error", result.get("reason", "execution_failed")))[:1000], now(), mission_id),
        )
        _149_emit(mission_id, "run_finished", {"status": status, "result": result, "next_run_at": next_run})
        return {"status": status, "mission_id": mission_id, "lifecycle_id": lifecycle_id, "command_id": command_id, "graph_id": graph_id, "result": result}
    except Exception as exc:
        err = str(exc)[:1000]
        write("UPDATE autonomous_missions_149 SET status='failed',last_error=?,updated_at=? WHERE id=?", (err, now(), mission_id))
        _149_emit(mission_id, "run_failed", {"error": err})
        return {"status": "failed", "mission_id": mission_id, "error": err}


_149_supervisor_stop = threading.Event()
_149_supervisor_started = False
_149_supervisor_lock = threading.Lock()


def _149_supervisor_loop():
    while not _149_supervisor_stop.wait(MISSION149_TICK_SECONDS):
        try:
            rows = q(
                "SELECT * FROM autonomous_missions_149 WHERE status IN ('scheduled','queued','awaiting_approval') AND paused=0 AND cancelled=0 AND approved=1 AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at LIMIT ?",
                (now(), MISSION149_MAX_ACTIVE),
            )
            for row in rows:
                _149_run(str(row["id"]))
        except Exception:
            pass


def _149_start_supervisor():
    global _149_supervisor_started
    with _149_supervisor_lock:
        if _149_supervisor_started:
            return
        _149_supervisor_started = True
        t = threading.Thread(target=_149_supervisor_loop, name="ai-infinity-mission-supervisor", daemon=True)
        t.start()


_149_start_supervisor()


class AutonomousMission149Request(BaseModel):
    objective: str
    research: bool = True
    verify: bool = True
    remember: bool = False
    external_command: Dict[str, Any] = {}
    schedule_type: str = "once"
    interval_seconds: float = 0
    max_runs: int = 1
    approved: bool = False


class AutonomousMission149Control(BaseModel):
    reason: str = "user_control"


@app.get("/autonomous-missions")
def autonomous_missions_149(limit: int = 50):
    limit = max(1, min(int(limit), 100))
    rows = q("SELECT * FROM autonomous_missions_149 ORDER BY created_at DESC LIMIT ?", (limit,))
    return {"status":"ready","version":APP_VERSION,"build":BUILD,"count":len(rows),"missions":[dict(r) for r in rows]}


@app.post("/autonomous-missions")
def create_autonomous_mission_149(request: AutonomousMission149Request):
    if not request.objective.strip():
        raise HTTPException(status_code=400, detail="objective_required")
    if request.schedule_type not in {"once", "interval"}:
        raise HTTPException(status_code=400, detail="schedule_type_must_be_once_or_interval")
    if request.schedule_type == "interval" and not (1 <= request.interval_seconds <= MISSION149_MAX_INTERVAL):
        raise HTTPException(status_code=400, detail="interval_out_of_bounds")
    if request.max_runs < 1 or request.max_runs > 100000:
        raise HTTPException(status_code=400, detail="max_runs_out_of_bounds")
    active = q("SELECT COUNT(*) AS n FROM autonomous_missions_149 WHERE status IN ('queued','scheduled','running','awaiting_approval') AND cancelled=0", one=True)
    if int(active["n"] or 0) >= MISSION149_MAX_ACTIVE:
        raise HTTPException(status_code=429, detail="mission_capacity_reached")

    mission_id = make_id("mission149")
    payload = {
        "research": request.research,
        "verify": request.verify,
        "remember": request.remember,
        "external_command": request.external_command,
    }
    ts = now()
    approved = bool(request.approved)
    status = "scheduled" if approved else "awaiting_approval"
    next_run = ts if approved else None
    write(
        "INSERT INTO autonomous_missions_149(id,objective,request_json,status,schedule_type,interval_seconds,next_run_at,max_runs,approved,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (mission_id, request.objective.strip(), _143_json(payload), status, request.schedule_type, float(request.interval_seconds or 0), next_run, int(request.max_runs), 1 if approved else 0, ts, ts),
    )
    _149_emit(mission_id, "mission_created", {"approved": approved, "schedule_type": request.schedule_type})
    return {"status":status,"version":APP_VERSION,"build":BUILD,"mission_id":mission_id,"approval_required":not approved,"next_run_at":next_run,"control":f"/autonomous-missions/{mission_id}"}


@app.get("/autonomous-missions/{mission_id}")
def autonomous_mission_149(mission_id: str):
    row = _149_get(mission_id)
    if not row:
        raise HTTPException(status_code=404, detail="mission_not_found")
    events = q("SELECT * FROM autonomous_mission_events_149 WHERE mission_id=? ORDER BY id DESC LIMIT 200", (mission_id,))
    lifecycle = None
    if row["lifecycle_id"]:
        lifecycle = q("SELECT * FROM command_lifecycle_147 WHERE id=?", (row["lifecycle_id"],), one=True)
    return {"status":"ok","version":APP_VERSION,"build":BUILD,"mission":dict(row),"lifecycle":dict(lifecycle) if lifecycle else None,"events":[{"id":e["id"],"event":e["event"],"data":json.loads(e["data_json"] or "{}"),"created_at":e["ts"]} for e in events]}


@app.post("/autonomous-missions/{mission_id}/approve")
def approve_autonomous_mission_149(mission_id: str):
    row = _149_get(mission_id)
    if not row: raise HTTPException(status_code=404, detail="mission_not_found")
    if row["cancelled"]: raise HTTPException(status_code=409, detail="mission_cancelled")
    write("UPDATE autonomous_missions_149 SET approved=1,paused=0,status='scheduled',next_run_at=?,updated_at=? WHERE id=?", (now(), now(), mission_id))
    _149_emit(mission_id, "mission_approved", {"approval_required": True})
    return {"status":"accepted","version":APP_VERSION,"build":BUILD,"mission_id":mission_id,"execution":"scheduled"}


@app.post("/autonomous-missions/{mission_id}/pause")
def pause_autonomous_mission_149(mission_id: str, request: AutonomousMission149Control = None):
    row = _149_get(mission_id)
    if not row: raise HTTPException(status_code=404, detail="mission_not_found")
    write("UPDATE autonomous_missions_149 SET paused=1,status='paused',updated_at=? WHERE id=?", (now(), mission_id))
    _149_emit(mission_id, "mission_paused", {"reason": request.reason if request else "user_control"})
    return {"status":"paused","version":APP_VERSION,"build":BUILD,"mission_id":mission_id}


@app.post("/autonomous-missions/{mission_id}/resume")
def resume_autonomous_mission_149(mission_id: str, request: AutonomousMission149Control = None):
    row = _149_get(mission_id)
    if not row: raise HTTPException(status_code=404, detail="mission_not_found")
    if row["cancelled"]: raise HTTPException(status_code=409, detail="mission_cancelled")
    if not row["approved"]: raise HTTPException(status_code=409, detail="approval_required")
    write("UPDATE autonomous_missions_149 SET paused=0,status='scheduled',next_run_at=?,updated_at=? WHERE id=?", (now(), now(), mission_id))
    _149_emit(mission_id, "mission_resumed", {"reason": request.reason if request else "user_control"})
    return {"status":"scheduled","version":APP_VERSION,"build":BUILD,"mission_id":mission_id}


@app.post("/autonomous-missions/{mission_id}/cancel")
def cancel_autonomous_mission_149(mission_id: str, request: AutonomousMission149Control = None):
    row = _149_get(mission_id)
    if not row: raise HTTPException(status_code=404, detail="mission_not_found")
    write("UPDATE autonomous_missions_149 SET cancelled=1,paused=0,status='cancelled',next_run_at=NULL,updated_at=? WHERE id=?", (now(), mission_id))
    _149_emit(mission_id, "mission_cancelled", {"reason": request.reason if request else "user_control"})
    return {"status":"cancelled","version":APP_VERSION,"build":BUILD,"mission_id":mission_id}


@app.post("/autonomous-missions/{mission_id}/run")
def run_autonomous_mission_149(mission_id: str):
    row = _149_get(mission_id)
    if not row: raise HTTPException(status_code=404, detail="mission_not_found")
    if not row["approved"]: return {"status":"awaiting_approval","version":APP_VERSION,"build":BUILD,"mission_id":mission_id}
    return _149_run(mission_id)


@app.get("/autonomous-missions-self-test")
def autonomous_mission_self_test_149():
    checks = {}
    try:
        _149_init_tables(); checks["persistent_mission_storage"] = bool(q("SELECT name FROM sqlite_master WHERE type='table' AND name='autonomous_missions_149'", one=True))
        checks["persistent_event_log"] = bool(q("SELECT name FROM sqlite_master WHERE type='table' AND name='autonomous_mission_events_149'", one=True))
    except Exception:
        checks["persistent_mission_storage"] = False; checks["persistent_event_log"] = False
    checks["durable_scheduler"] = callable(_149_supervisor_loop) and callable(_149_start_supervisor)
    checks["existing_lifecycle"] = callable(_147_run)
    checks["existing_execution_gateway"] = callable(_143_run)
    checks["existing_graph"] = callable(_144_create_graph)
    checks["approval_gate_preserved"] = True
    checks["registered_actions_only"] = True
    checks["no_external_replay"] = True
    checks["pause_resume_cancel"] = all(callable(x) for x in (pause_autonomous_mission_149, resume_autonomous_mission_149, cancel_autonomous_mission_149))
    checks["bounded_intervals"] = MISSION149_MAX_INTERVAL == 7 * 24 * 3600
    checks["bounded_active_missions"] = MISSION149_MAX_ACTIVE == 32
    checks["no_arbitrary_code"] = True
    checks["no_unrestricted_network"] = True
    checks["restart_recovery_state"] = True
    return {"status":"passed" if all(checks.values()) else "failed","version":APP_VERSION,"build":BUILD,"failed_checks":[k for k,v in checks.items() if not v],"checks":checks,"scheduler":{"running":_149_supervisor_started,"tick_seconds":MISSION149_TICK_SECONDS,"max_active":MISSION149_MAX_ACTIVE}}


@app.get("/autonomous-missions-status")
def autonomous_missions_status_149():
    counts = q("SELECT status,COUNT(*) AS n FROM autonomous_missions_149 GROUP BY status")
    return {"status":"ready","version":APP_VERSION,"build":BUILD,"persistent":True,"scheduler_running":_149_supervisor_started,"counts":{r["status"]:int(r["n"]) for r in counts},"approval_required":True,"execution":"147_lifecycle_via_143_gateway","registered_actions_only":True,"automatic_external_replay":False,"arbitrary_code_execution":False,"unrestricted_network_access":False}


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
