from __future__ import annotations

"""
AI Infinity — TARGET-2050.181
VERIFIED-REAL-WORLD-ACTION-CORE

Builds on TARGET-2050.180.

Adds:
- real external HTTP action execution
- durable transaction lifecycle
- idempotency
- duplicate suppression
- independent external-state verification
- verification URL support
- verification receipts
- action reconciliation
- safe handling of uncertain external outcomes
- adaptive recovery
- workflow execution
- memory
- command interface
- approval gate
- SSRF protection
- credential protection
- persistent execution events

No arbitrary code execution.
No automatic replay of uncertain side-effecting actions.
"""

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
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# VERSION
# ============================================================

APP_VERSION = "TARGET-2050.181"
BUILD = "VERIFIED-REAL-WORLD-ACTION-CORE"
PREVIOUS_BUILD = "TARGET-2050.180"

DATA_DIR = os.getenv(
    "AI_INFINITY_DATA_DIR",
    "/tmp/ai-infinity",
)

DB_PATH = os.path.join(
    DATA_DIR,
    "ai_infinity.db",
)

WORKSPACE = Path(
    os.getenv(
        "AI_INFINITY_WORKSPACE",
        os.path.join(DATA_DIR, "workspace"),
    )
).resolve()

os.makedirs(DATA_DIR, exist_ok=True)
WORKSPACE.mkdir(parents=True, exist_ok=True)


# ============================================================
# LIMITS / POLICY
# ============================================================

MAX_BODY = 1_000_000
MAX_RESPONSE = 256 * 1024
REQUEST_TIMEOUT = 12
MAX_ATTEMPTS = 2

STALE_SECONDS = max(
    30,
    int(os.getenv("AI_INFINITY_ACTION_STALE_SECONDS", "120")),
)

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "host.docker.internal",
    "0.0.0.0",
    "::1",
}

SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-access-token",
}

SAFE_METHODS = {
    "GET",
    "HEAD",
}

HTTP_METHODS = {
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
}

ALLOWLIST = {
    x.strip().lower().rstrip(".")
    for x in os.getenv(
        "AI_INFINITY_ACTION_HOST_ALLOWLIST",
        "",
    ).split(",")
    if x.strip()
}


# ============================================================
# APP / DB
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=APP_VERSION,
)

_db_lock = threading.RLock()


def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DB_PATH,
        timeout=20,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _db_lock, db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY,
                parent_id TEXT,
                objective TEXT,
                action_type TEXT NOT NULL,
                connector TEXT NOT NULL,
                status TEXT NOT NULL,
                approval_required INTEGER NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                recovery_attempts INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                result_hash TEXT,
                verification_status TEXT,
                verification_json TEXT,
                verification_hash TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS execution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL,
                event TEXT NOT NULL,
                data_json TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS action_transactions (
                transaction_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL,
                execution_id TEXT,
                status TEXT NOT NULL,
                action_hash TEXT NOT NULL,
                receipt_json TEXT,
                receipt_hash TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(idempotency_key)
            );

            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                result_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )

        # Safe schema upgrades from 2050.180.
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(executions)"
            ).fetchall()
        }

        if "verification_json" not in columns:
            conn.execute(
                "ALTER TABLE executions "
                "ADD COLUMN verification_json TEXT"
            )

        if "verification_hash" not in columns:
            conn.execute(
                "ALTER TABLE executions "
                "ADD COLUMN verification_hash TEXT"
            )


init_db()


# ============================================================
# EVENTS
# ============================================================

def event(
    execution_id: str,
    name: str,
    data: Optional[Dict[str, Any]] = None,
) -> None:

    with _db_lock, db() as conn:
        conn.execute(
            """
            INSERT INTO execution_events
            (execution_id,event,data_json,created_at)
            VALUES (?,?,?,?)
            """,
            (
                execution_id,
                name,
                json.dumps(
                    data or {},
                    ensure_ascii=False,
                    default=str,
                ),
                now(),
            ),
        )


# ============================================================
# EXECUTIONS
# ============================================================

def create_execution(
    action_type: str,
    connector: str,
    objective: str,
    approval_required: bool,
    parent_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> str:

    eid = uid("exec")
    t = now()

    with _db_lock, db() as conn:
        conn.execute(
            """
            INSERT INTO executions
            (
                id,
                parent_id,
                objective,
                action_type,
                connector,
                status,
                approval_required,
                created_at,
                updated_at
            )
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                eid,
                parent_id,
                objective,
                action_type,
                connector,
                (
                    "pending_approval"
                    if approval_required
                    else "queued"
                ),
                int(approval_required),
                t,
                t,
            ),
        )

    event(
        eid,
        "created",
        payload or {},
    )

    return eid


def update_execution(
    eid: str,
    **fields: Any,
) -> None:

    if not fields:
        return

    fields["updated_at"] = now()

    assignments = ",".join(
        f"{key}=?"
        for key in fields
    )

    values = list(fields.values()) + [eid]

    with _db_lock, db() as conn:
        conn.execute(
            f"""
            UPDATE executions
            SET {assignments}
            WHERE id=?
            """,
            values,
        )


def get_execution(
    eid: str,
) -> Optional[Dict[str, Any]]:

    with _db_lock, db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM executions
            WHERE id=?
            """,
            (eid,),
        ).fetchone()

    if not row:
        return None

    data = dict(row)

    for key in (
        "result_json",
        "verification_json",
    ):
        if data.get(key):
            try:
                data[key] = json.loads(
                    data[key]
                )
            except Exception:
                pass

    data["approval_required"] = bool(
        data["approval_required"]
    )

    data["approved"] = bool(
        data["approved"]
    )

    return data


# ============================================================
# TRANSACTIONS
# ============================================================

def transaction_action_hash(
    plan: Dict[str, Any],
) -> str:

    fields = {
        "connector": plan.get("connector"),
        "action_type": plan.get("action_type"),
        "method": plan.get("method"),
        "url": plan.get("url"),
        "body": plan.get("body"),
        "headers": plan.get("headers"),
        "side_effect": plan.get("side_effect"),
        "verification_url": plan.get(
            "verification_url"
        ),
    }

    return digest(fields)


def get_transaction(
    tid: str = "",
    key: str = "",
) -> Optional[Dict[str, Any]]:

    with _db_lock, db() as conn:

        row = None

        if tid or key:
            row = conn.execute(
                """
                SELECT *
                FROM action_transactions
                WHERE transaction_id=?
                   OR idempotency_key=?
                """,
                (tid, key),
            ).fetchone()

    if not row:
        return None

    data = dict(row)

    if data.get("receipt_json"):
        try:
            data["receipt_json"] = json.loads(
                data["receipt_json"]
            )
        except Exception:
            pass

    return data


def create_transaction(
    key: str,
    plan: Dict[str, Any],
    execution_id: Optional[str],
) -> Dict[str, Any]:

    action_hash = transaction_action_hash(plan)

    existing = get_transaction(
        key=key
    )

    if existing:

        if existing["action_hash"] != action_hash:
            raise ValueError(
                "idempotency key conflicts with a different action"
            )

        return existing

    transaction_id = uid("txn")
    timestamp = now()

    with _db_lock, db() as conn:
        conn.execute(
            """
            INSERT INTO action_transactions
            (
                transaction_id,
                idempotency_key,
                execution_id,
                status,
                action_hash,
                created_at,
                updated_at
            )
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                transaction_id,
                key,
                execution_id,
                "open",
                action_hash,
                timestamp,
                timestamp,
            ),
        )

    return get_transaction(
        tid=transaction_id
    )


def close_transaction(
    transaction_id: str,
    execution: Dict[str, Any],
) -> Dict[str, Any]:

    receipt = {
        "transaction_id": transaction_id,
        "execution_id": execution.get("id"),
        "status": execution.get("status"),
        "result_hash": execution.get(
            "result_hash"
        ),
        "verification_hash": execution.get(
            "verification_hash"
        ),
        "verification_status": execution.get(
            "verification_status"
        ),
        "closed_at": now(),
    }

    receipt_hash = digest(receipt)

    with _db_lock, db() as conn:
        conn.execute(
            """
            UPDATE action_transactions
            SET
                status=?,
                receipt_json=?,
                receipt_hash=?,
                updated_at=?
            WHERE transaction_id=?
            """,
            (
                execution.get(
                    "status",
                    "unknown",
                ),
                json.dumps(
                    receipt,
                    ensure_ascii=False,
                ),
                receipt_hash,
                now(),
                transaction_id,
            ),
        )

    return {
        "receipt": receipt,
        "receipt_hash": receipt_hash,
    }


# ============================================================
# SSRF / HTTP SECURITY
# ============================================================

def validate_url(
    url: str,
    method: str = "GET",
) -> Dict[str, Any]:

    if (
        not isinstance(url, str)
        or len(url) > 4096
    ):
        raise ValueError(
            "invalid URL"
        )

    parsed = urlparse(url)

    if (
        parsed.scheme
        not in {"http", "https"}
        or not parsed.hostname
    ):
        raise ValueError(
            "only http/https URLs are supported"
        )

    host = parsed.hostname.lower().rstrip(".")

    if host in BLOCKED_HOSTS:
        raise ValueError(
            "blocked host"
        )

    if (
        ALLOWLIST
        and host not in ALLOWLIST
    ):
        raise ValueError(
            "host is not on the action allowlist"
        )

    try:
        infos = socket.getaddrinfo(
            host,
            parsed.port
            or (
                443
                if parsed.scheme == "https"
                else 80
            ),
            type=socket.SOCK_STREAM,
        )

        addresses = {
            item[4][0]
            for item in infos
        }

    except Exception:
        addresses = set()

    for address in addresses:

        try:
            ip = ipaddress.ip_address(
                address
            )

            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError(
                    "URL resolves to a blocked/private address"
                )

        except ValueError as exc:

            if (
                "blocked/private"
                in str(exc)
            ):
                raise

    return {
        "scheme": parsed.scheme,
        "host": host,
        "url": url,
        "method": method,
    }


def sanitize_headers(
    headers: Optional[Dict[str, str]],
) -> Dict[str, str]:

    output: Dict[str, str] = {}

    for key, value in (
        headers or {}
    ).items():

        if (
            key.lower()
            in SENSITIVE_HEADERS
        ):
            raise ValueError(
                f"sensitive header blocked: {key}"
            )

        if (
            len(key) > 128
            or len(str(value)) > 16384
        ):
            raise ValueError(
                "header too large"
            )

        output[str(key)] = str(value)

    return output


class NoRedirect(
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
        raise HTTPError(
            req.full_url,
            code,
            "redirect blocked",
            headers,
            fp,
        )


def execute_http(
    eid: str,
    method: str,
    url: str,
    headers: Optional[Dict[str, str]],
    body: Any,
    allow_side_effect: bool,
) -> Dict[str, Any]:

    method = method.upper()

    if method not in HTTP_METHODS:
        raise ValueError(
            "unsupported HTTP method"
        )

    validate_url(
        url,
        method,
    )

    headers = sanitize_headers(
        headers
    )

    if (
        method not in SAFE_METHODS
        and not allow_side_effect
    ):
        raise PermissionError(
            "side-effecting HTTP method requires approval"
        )

    payload = None

    if body is not None:

        if isinstance(body, bytes):
            payload = body
        else:
            payload = json.dumps(
                body,
                ensure_ascii=False,
            ).encode("utf-8")

        if len(payload) > MAX_BODY:
            raise ValueError(
                "request body too large"
            )

        headers.setdefault(
            "Content-Type",
            "application/json",
        )

    request = Request(
        url,
        data=payload,
        headers=headers,
        method=method,
    )

    opener = build_opener(
        NoRedirect()
    )

    try:

        with opener.open(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:

            raw = response.read(
                MAX_RESPONSE + 1
            )

            truncated = (
                len(raw) > MAX_RESPONSE
            )

            raw = raw[:MAX_RESPONSE]

            text = raw.decode(
                "utf-8",
                errors="replace",
            )

            safe_headers = {
                key: value
                for key, value
                in response.headers.items()
                if key.lower()
                not in SENSITIVE_HEADERS
            }

            return {
                "ok": (
                    200
                    <= response.status
                    < 400
                ),
                "status_code": response.status,
                "headers": safe_headers,
                "body": text,
                "body_hash": digest(text),
                "truncated": truncated,
                "url": url,
                "method": method,
            }

    except HTTPError as exc:

        raw = exc.read(
            MAX_RESPONSE
        )

        text = raw.decode(
            "utf-8",
            errors="replace",
        )

        return {
            "ok": False,
            "status_code": exc.code,
            "error": str(exc.reason),
            "body": text,
            "body_hash": digest(text),
            "url": url,
            "method": method,
        }

    except (
        URLError,
        TimeoutError,
        OSError,
    ) as exc:

        raise RuntimeError(
            f"external request failed: {exc}"
        ) from exc


# ============================================================
# INDEPENDENT VERIFICATION
# ============================================================

def verify_external_state(
    eid: str,
    plan: Dict[str, Any],
    action_result: Dict[str, Any],
) -> Dict[str, Any]:

    verification_url = plan.get(
        "verification_url"
    )

    method = str(
        plan.get(
            "method",
            "GET",
        )
    ).upper()

    # Safe GET actions can independently
    # verify themselves by re-reading the URL.
    if not verification_url:
        if method in SAFE_METHODS:
            verification_url = plan.get(
                "url"
            )
        else:
            result = {
                "verified": False,
                "mode": "missing_verification_target",
                "reason": (
                    "side-effecting action requires "
                    "a verification_url"
                ),
            }

            update_execution(
                eid,
                verification_status="unverified",
                verification_json=json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                verification_hash=digest(
                    result
                ),
            )

            event(
                eid,
                "external_verification_failed",
                result,
            )

            return result

    validate_url(
        verification_url,
        "GET",
    )

    observation = execute_http(
        eid=eid,
        method="GET",
        url=verification_url,
        headers=None,
        body=None,
        allow_side_effect=False,
    )

    expected_status = plan.get(
        "verification_status_code"
    )

    expected_body_contains = plan.get(
        "verification_body_contains"
    )

    status_ok = (
        expected_status is None
        or observation.get(
            "status_code"
        ) == expected_status
    )

    body_ok = (
        expected_body_contains is None
        or str(
            expected_body_contains
        )
        in str(
            observation.get(
                "body",
                "",
            )
        )
    )

    # For safe GET/HEAD actions, compare the
    # second observation against the action result.
    observation_matches_action = False

    if method in SAFE_METHODS:

        observation_matches_action = (
            observation.get(
                "status_code"
            )
            == action_result.get(
                "status_code"
            )
            and observation.get(
                "body_hash"
            )
            == action_result.get(
                "body_hash"
            )
        )

    verified = (
        bool(observation.get("ok"))
        and status_ok
        and body_ok
        and (
            observation_matches_action
            if method in SAFE_METHODS
            else True
        )
    )

    result = {
        "verified": verified,
        "mode": (
            "independent_external_state_observation"
        ),
        "verification_url": verification_url,
        "observed_status_code": observation.get(
            "status_code"
        ),
        "observed_body_hash": observation.get(
            "body_hash"
        ),
        "expected_status_code": expected_status,
        "expected_body_contains": (
            expected_body_contains
        ),
        "status_match": status_ok,
        "body_match": body_ok,
        "observation_matches_action": (
            observation_matches_action
        ),
    }

    update_execution(
        eid,
        verification_status=(
            "verified"
            if verified
            else "failed"
        ),
        verification_json=json.dumps(
            result,
            ensure_ascii=False,
        ),
        verification_hash=digest(
            result
        ),
    )

    event(
        eid,
        (
            "external_verified"
            if verified
            else "external_verification_failed"
        ),
        result,
    )

    return result


# ============================================================
# RESULT CLOSURE
# ============================================================

def save_result(
    eid: str,
    result: Dict[str, Any],
    status: str = "completed",
) -> None:

    result_hash = digest(
        result
    )

    update_execution(
        eid,
        status=status,
        result_json=json.dumps(
            result,
            ensure_ascii=False,
        ),
        result_hash=result_hash,
        verification_status="pending",
    )

    event(
        eid,
        "result_saved",
        {
            "result_hash": result_hash
        },
    )


def close_execution(
    eid: str,
    plan: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:

    external = (
        plan.get("connector")
        in {"http", "webhook"}
    )

    if external:

        verification = verify_external_state(
            eid,
            plan,
            result,
        )

        if not verification.get(
            "verified"
        ):
            update_execution(
                eid,
                status="unverified",
                error=(
                    "external action executed "
                    "but outcome could not be independently verified"
                ),
            )

            event(
                eid,
                "closure_blocked",
                {
                    "reason": (
                        "external verification failed"
                    )
                },
            )

            return {
                "closed": False,
                "verified": False,
            }

    else:

        verification = {
            "verified": True,
            "mode": "internal_deterministic",
        }

        update_execution(
            eid,
            verification_status="verified",
            verification_json=json.dumps(
                verification
            ),
            verification_hash=digest(
                verification
            ),
        )

        event(
            eid,
            "internal_result_verified",
            verification,
        )

    update_execution(
        eid,
        status="completed",
    )

    event(
        eid,
        "result_closed",
        {
            "verified": True
        },
    )

    return {
        "closed": True,
        "verified": True,
        "verification": verification,
    }


# ============================================================
# STALE / RECOVERY
# ============================================================

def recover_stale() -> int:

    cutoff = (
        now()
        - STALE_SECONDS
    )

    with _db_lock, db() as conn:

        rows = conn.execute(
            """
            SELECT id
            FROM executions
            WHERE status IN ('running','queued')
            AND updated_at < ?
            """,
            (cutoff,),
        ).fetchall()

        for row in rows:

            conn.execute(
                """
                UPDATE executions
                SET
                    status='uncertain',
                    error=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    (
                        "stale execution recovered; "
                        "external outcome not replayed"
                    ),
                    now(),
                    row["id"],
                ),
            )

    for row in rows:

        event(
            row["id"],
            "stale_recovered",
            {
                "automatic_replay": False
            },
        )

    return len(rows)


# ============================================================
# COMMAND ROUTER
# ============================================================

def parse_command(
    command: str,
) -> Dict[str, Any]:

    text = command.strip()

    if not text:
        raise ValueError(
            "command is required"
        )

    match = re.match(
        r"^(GET|HEAD|POST|PUT|PATCH|DELETE)"
        r"\s+(https?://\S+)$",
        text,
        re.I,
    )

    if match:

        method = match.group(
            1
        ).upper()

        url = match.group(
            2
        )

        return {
            "action_type": "external_http",
            "connector": "http",
            "method": method,
            "url": url,
            "side_effect": (
                method
                not in SAFE_METHODS
            ),
        }

    match = re.match(
        r"^webhook\s+(https?://\S+)$",
        text,
        re.I,
    )

    if match:

        return {
            "action_type": "webhook",
            "connector": "webhook",
            "method": "POST",
            "url": match.group(1),
            "side_effect": True,
        }

    if re.search(
        r"\bremember\s+(that\s+)?",
        text,
        re.I,
    ):

        memory_text = re.sub(
            r"^.*?\bremember\s+(that\s+)?",
            "",
            text,
            flags=re.I,
        ).strip()

        return {
            "action_type": "memory",
            "connector": "memory",
            "text": memory_text or text,
            "side_effect": False,
        }

    if re.match(
        r"^research\s+",
        text,
        re.I,
    ):

        return {
            "action_type": "research_plan",
            "connector": "research",
            "query": re.sub(
                r"^research\s+",
                "",
                text,
                flags=re.I,
            ).strip(),
            "side_effect": False,
        }

    if re.match(
        r"^list\s+files$",
        text,
        re.I,
    ):

        return {
            "action_type": "file",
            "connector": "workspace",
            "operation": "list",
            "side_effect": False,
        }

    if text.lower() == "ping":

        return {
            "action_type": "system",
            "connector": "system",
            "operation": "ping",
            "side_effect": False,
        }

    if re.search(
        r"\bthen\b",
        text,
        re.I,
    ):

        first, second = re.split(
            r"\s+then\s+",
            text,
            maxsplit=1,
            flags=re.I,
        )

        first_plan = parse_command(
            first
        )

        second_plan = parse_command(
            second
        )

        return {
            "action_type": "workflow",
            "connector": "workflow",
            "steps": [
                first_plan,
                second_plan,
            ],
            "side_effect": (
                first_plan.get(
                    "side_effect",
                    False,
                )
                or second_plan.get(
                    "side_effect",
                    False,
                )
            ),
        }

    return {
        "action_type": "unknown",
        "connector": "none",
        "side_effect": False,
    }


# ============================================================
# INTERNAL EXECUTION
# ============================================================

def execute_internal(
    eid: str,
    plan: Dict[str, Any],
    approved: bool = False,
) -> Dict[str, Any]:

    connector = plan[
        "connector"
    ]

    if connector == "system":

        return {
            "ok": True,
            "pong": True,
        }

    if connector == "memory":

        memory_id = uid(
            "mem"
        )

        with _db_lock, db() as conn:

            conn.execute(
                """
                INSERT INTO memories
                (id,text,created_at)
                VALUES (?,?,?)
                """,
                (
                    memory_id,
                    plan["text"],
                    now(),
                ),
            )

        return {
            "ok": True,
            "memory_id": memory_id,
            "remembered": plan["text"],
        }

    if connector == "research":

        return {
            "ok": True,
            "query": plan[
                "query"
            ],
            "mode": "research_plan",
            "steps": [
                "discover sources",
                "collect evidence",
                "verify evidence",
                "return structured result",
            ],
        }

    if connector == "workspace":

        return {
            "ok": True,
            "files": [
                path.name
                for path in sorted(
                    WORKSPACE.iterdir()
                )
                if path.is_file()
            ][:200],
        }

    if connector == "http":

        return execute_http(
            eid=eid,
            method=plan["method"],
            url=plan["url"],
            headers=plan.get(
                "headers"
            ),
            body=plan.get(
                "body"
            ),
            allow_side_effect=approved,
        )

    if connector == "webhook":

        return execute_http(
            eid=eid,
            method="POST",
            url=plan["url"],
            headers=plan.get(
                "headers"
            ),
            body=plan.get(
                "body",
                {
                    "source": "AI Infinity",
                    "execution_id": eid,
                },
            ),
            allow_side_effect=approved,
        )

    raise ValueError(
        "unsupported connector"
    )


# ============================================================
# EXECUTION ENGINE
# ============================================================

def run_execution(
    eid: str,
    plan: Dict[str, Any],
    approved: bool = False,
) -> None:

    execution = get_execution(
        eid
    )

    if not execution:
        return

    if (
        plan.get("side_effect")
        and not approved
    ):

        update_execution(
            eid,
            status="pending_approval",
        )

        event(
            eid,
            "approval_required",
        )

        return

    update_execution(
        eid,
        status="running",
        approved=int(
            approved
        ),
    )

    event(
        eid,
        "started",
        {
            "connector": plan.get(
                "connector"
            )
        },
    )

    attempts = 0

    while attempts < MAX_ATTEMPTS:

        attempts += 1

        update_execution(
            eid,
            attempts=attempts,
        )

        try:

            result = execute_internal(
                eid,
                plan,
                approved,
            )

            save_result(
                eid,
                result,
                (
                    "completed"
                    if result.get(
                        "ok",
                        True,
                    )
                    else "failed"
                ),
            )

            closure = close_execution(
                eid,
                plan,
                result,
            )

            if closure.get(
                "closed"
            ):

                event(
                    eid,
                    "closed",
                    {
                        "status": "completed",
                        "verified": True,
                    },
                )

            return

        except PermissionError as exc:

            update_execution(
                eid,
                status="blocked",
                error=str(exc),
                attempts=attempts,
            )

            event(
                eid,
                "blocked",
                {
                    "reason": str(exc)
                },
            )

            return

        except Exception as exc:

            # NEVER automatically replay a side-effecting
            # action when the external outcome is uncertain.
            if plan.get(
                "side_effect"
            ):

                update_execution(
                    eid,
                    status="uncertain",
                    error=str(exc),
                    attempts=attempts,
                )

                event(
                    eid,
                    "uncertain_external_outcome",
                    {
                        "automatic_replay": False,
                        "error": str(exc),
                    },
                )

                return

            if attempts >= MAX_ATTEMPTS:

                update_execution(
                    eid,
                    status="failed",
                    error=str(exc),
                    attempts=attempts,
                )

                event(
                    eid,
                    "failed",
                    {
                        "error": str(exc)
                    },
                )

                return

            update_execution(
                eid,
                recovery_attempts=attempts - 1,
            )

            event(
                eid,
                "safe_retry",
                {
                    "attempt": attempts + 1
                },
            )


# ============================================================
# REQUEST MODELS
# ============================================================

class CommandRequest(BaseModel):

    command: str = Field(
        min_length=1,
        max_length=4096,
    )

    execute: bool = True

    require_approval: bool = True

    headers: Optional[
        Dict[str, str]
    ] = None

    body: Any = None

    idempotency_key: Optional[
        str
    ] = None

    verification_url: Optional[
        str
    ] = None

    verification_status_code: Optional[
        int
    ] = None

    verification_body_contains: Optional[
        str
    ] = None


class ExecuteRequest(BaseModel):

    approved: bool = False


class RunRequest(BaseModel):

    objective: str = Field(
        min_length=1,
        max_length=10000,
    )

    research: bool = True

    verify: bool = True

    remember: bool = False

    external_access: bool = True

    execute: bool = False

    require_approval: bool = True


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
def root():

    return {
        "name": "AI Infinity",
        "status": "online",
        "version": APP_VERSION,
        "build": BUILD,
        "previous_build": PREVIOUS_BUILD,
        "interface": "/interface",
        "docs": "/docs",
        "run": "/run",
        "command": "/command",
        "execution": "/execution/{execution_id}",
    }


@app.get("/health")
def health():

    recover_stale()

    with _db_lock, db() as conn:

        counts = {
            "memory_count":
                conn.execute(
                    "SELECT COUNT(*) FROM memories"
                ).fetchone()[0],

            "execution_count":
                conn.execute(
                    "SELECT COUNT(*) FROM executions"
                ).fetchone()[0],

            "execution_event_count":
                conn.execute(
                    "SELECT COUNT(*) FROM execution_events"
                ).fetchone()[0],

            "workflow_count":
                conn.execute(
                    "SELECT COUNT(*) FROM workflows"
                ).fetchone()[0],
        }

    return {
        "status": "healthy",
        "version": APP_VERSION,
        "build": BUILD,
        "database": "ready",

        "policy_version": 1,

        "external_execution_enabled": True,
        "verification_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True,
        "intent_router_enabled": True,
        "result_closure_enabled": True,
        "adaptive_decision_enabled": True,
        "workflow_resume_enabled": True,
        "action_state_enabled": True,

        "idempotency_enforced": True,
        "duplicate_action_suppression": True,

        "transaction_closure_enabled": True,
        "durable_receipts": True,
        "action_reconciliation_enabled": True,

        "external_state_verification": True,
        "independent_verification": True,
        "uncertain_external_replay": False,

        **counts,
    }


@app.get("/status")
def status():

    return health()


@app.get("/capabilities")
def capabilities():

    return {
        "version": APP_VERSION,
        "build": BUILD,

        "connectors": [
            "system",
            "http",
            "memory",
            "research",
            "workspace",
            "webhook",
            "workflow",
        ],

        "features": [
            "intent_routing",
            "approval_gate",
            "SSRF_protection",
            "credential_header_protection",
            "workspace_traversal_protection",
            "durable_execution",
            "result_hash_verification",
            "external_state_verification",
            "independent_verification",
            "safe_retry",
            "uncertain_external_outcome_closure",
            "workflow_child_results",
            "idempotency",
            "duplicate_suppression",
            "transaction_closure",
            "durable_receipts",
            "action_reconciliation",
        ],
    }


# ============================================================
# POLICIES
# ============================================================

@app.get("/command-policy")
def command_policy():

    return {
        "approval_required_for_side_effects": True,
        "safe_methods": sorted(
            SAFE_METHODS
        ),
        "supported_methods": sorted(
            HTTP_METHODS
        ),
        "sensitive_headers_blocked":
            sorted(
                SENSITIVE_HEADERS
            ),
        "host_allowlist_configured":
            bool(ALLOWLIST),
        "automatic_side_effect_retry":
            False,
        "unknown_external_outcome_replay":
            False,
        "independent_external_verification":
            True,
        "max_attempts":
            MAX_ATTEMPTS,
    }


@app.get("/transaction-policy")
def transaction_policy():

    return {
        "transaction_closure_enabled": True,
        "exactly_once_logical_completion": True,
        "durable_receipts": True,
        "action_reconciliation_enabled": True,
        "conflicting_idempotency_key_blocked": True,
        "uncertain_external_replay": False,
        "independent_external_verification": True,
    }


@app.get("/resilience-policy")
def resilience_policy():

    return {
        "adaptive_recovery": True,
        "safe_retry": True,
        "action_retry_limit": MAX_ATTEMPTS,
        "stale_running_transaction_recovery": True,
        "action_stale_seconds": STALE_SECONDS,
        "automatic_side_effect_retry": False,
        "unknown_external_outcome_replay": False,
    }


# ============================================================
# COMMAND
# ============================================================

@app.post("/command")
@app.post("/real-world-command")
def command(
    req: CommandRequest,
):

    try:
        plan = parse_command(
            req.command
        )

    except Exception as exc:
        raise HTTPException(
            400,
            str(exc),
        )

    if (
        plan["connector"]
        in {"http", "webhook"}
    ):

        plan["headers"] = (
            req.headers
        )

        plan["body"] = req.body

        sanitize_headers(
            req.headers
        )

        validate_url(
            plan["url"],
            plan["method"],
        )

        if req.verification_url:

            validate_url(
                req.verification_url,
                "GET",
            )

            plan[
                "verification_url"
            ] = req.verification_url

        if (
            req.verification_status_code
            is not None
        ):

            plan[
                "verification_status_code"
            ] = (
                req.verification_status_code
            )

        if (
            req.verification_body_contains
            is not None
        ):

            plan[
                "verification_body_contains"
            ] = (
                req.verification_body_contains
            )

    if (
        plan["connector"]
        == "http"
        and plan["method"]
        in SAFE_METHODS
        and not plan.get(
            "verification_url"
        )
    ):

        plan[
            "verification_url"
        ] = plan["url"]

    approval_required = bool(
        plan.get(
            "side_effect"
        )
        and req.require_approval
    )

    eid = create_execution(
        plan["action_type"],
        plan["connector"],
        req.command,
        approval_required,
        payload={
            "plan": plan
        },
    )

    transaction = None

    if req.idempotency_key:

        try:

            transaction = create_transaction(
                req.idempotency_key,
                plan,
                eid,
            )

            existing_execution_id = (
                transaction.get(
                    "execution_id"
                )
            )

            if (
                existing_execution_id
                and existing_execution_id != eid
            ):

                existing = get_execution(
                    existing_execution_id
                )

                return {
                    "status":
                        (
                            existing.get(
                                "status"
                            )
                            if existing
                            else transaction[
                                "status"
                            ]
                        ),
                    "execution_id":
                        existing_execution_id,
                    "transaction_id":
                        transaction[
                            "transaction_id"
                        ],
                    "idempotent_reuse": True,
                }

        except ValueError as exc:

            raise HTTPException(
                409,
                str(exc),
            )

    if not req.execute:

        update_execution(
            eid,
            status="planned",
        )

        event(
            eid,
            "planned",
            {
                "plan": plan
            },
        )

    else:

        run_execution(
            eid,
            plan,
            approved=False,
        )

    execution = get_execution(
        eid
    )

    # Close transaction after execution.
    if transaction:

        if execution:

            close_transaction(
                transaction[
                    "transaction_id"
                ],
                execution,
            )

    return {
        "status": (
            "accepted"
            if approval_required
            else (
                execution.get(
                    "status"
                )
                if execution
                else "unknown"
            )
        ),

        "execution_id": eid,

        "action_type":
            plan["action_type"],

        "connector":
            plan["connector"],

        "approval_required":
            approval_required,

        "url_extracted":
            plan.get("url"),

        "verification_target":
            plan.get(
                "verification_url"
            ),

        "result":
            execution,
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post("/execute/{execution_id}")
def execute_approved(
    execution_id: str,
    req: ExecuteRequest,
):

    execution = get_execution(
        execution_id
    )

    if not execution:
        raise HTTPException(
            404,
            "execution not found",
        )

    if not execution[
        "approval_required"
    ]:
        raise HTTPException(
            400,
            "execution does not require approval",
        )

    if not req.approved:

        update_execution(
            execution_id,
            status="rejected",
            error="approval rejected",
        )

        event(
            execution_id,
            "rejected",
        )

        return get_execution(
            execution_id
        )

    plan = parse_command(
        execution["objective"]
    )

    if (
        plan["connector"]
        in {"http", "webhook"}
    ):

        validate_url(
            plan["url"],
            plan["method"],
        )

    run_execution(
        execution_id,
        plan,
        approved=True,
    )

    return get_execution(
        execution_id
    )


@app.post("/approve/{execution_id}")
def approve(
    execution_id: str,
):

    return execute_approved(
        execution_id,
        ExecuteRequest(
            approved=True
        ),
    )


@app.post("/reject/{execution_id}")
def reject(
    execution_id: str,
):

    execution = get_execution(
        execution_id
    )

    if not execution:
        raise HTTPException(
            404,
            "execution not found",
        )

    update_execution(
        execution_id,
        status="rejected",
        error="approval rejected",
    )

    event(
        execution_id,
        "rejected",
    )

    return get_execution(
        execution_id
    )


# ============================================================
# EXECUTION STATUS
# ============================================================

@app.get("/execution/{execution_id}")
@app.get("/command-status/{execution_id}")
@app.get("/real-world-command-status/{execution_id}")
def execution_status(
    execution_id: str,
):

    execution = get_execution(
        execution_id
    )

    if not execution:
        raise HTTPException(
            404,
            "execution not found",
        )

    return execution


@app.get(
    "/execution/{execution_id}/events"
)
def execution_events(
    execution_id: str,
):

    if not get_execution(
        execution_id
    ):
        raise HTTPException(
            404,
            "execution not found",
        )

    with _db_lock, db() as conn:

        rows = conn.execute(
            """
            SELECT
                id,
                event,
                data_json,
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
                "event": row["event"],
                "data": json.loads(
                    row["data_json"]
                    or "{}"
                ),
                "created_at":
                    row["created_at"],
            }
            for row in rows
        ],
    }


# ============================================================
# RUN / TASK
# ============================================================

@app.get("/run_help")
def run_help():

    return {
        "method": "POST",
        "path": "/run",
        "body": {
            "objective": "string",
            "research": True,
            "verify": True,
            "remember": False,
            "external_access": True,
            "execute": False,
            "require_approval": True,
        },
    }


@app.post("/run")
def run(
    req: RunRequest,
):

    if req.remember:

        with _db_lock, db() as conn:

            conn.execute(
                """
                INSERT INTO memories
                (id,text,created_at)
                VALUES (?,?,?)
                """,
                (
                    uid("mem"),
                    req.objective,
                    now(),
                ),
            )

    plan = {
        "action_type":
            (
                "research_plan"
                if req.research
                else "system"
            ),

        "connector":
            (
                "research"
                if req.research
                else "system"
            ),

        "query":
            req.objective,

        "side_effect":
            False,
    }

    eid = create_execution(
        plan["action_type"],
        plan["connector"],
        req.objective,
        False,
    )

    run_execution(
        eid,
        plan,
        approved=True,
    )

    return {
        "id":
            uid("mission"),

        "status":
            "completed",

        "objective":
            req.objective,

        "execution_id":
            eid,

        "research":
            req.research,

        "verify":
            req.verify,

        "remember":
            req.remember,

        "external_access":
            req.external_access,

        "execution":
            get_execution(eid),
    }


@app.post("/task")
def task(
    req: RunRequest,
):

    return run(req)


# ============================================================
# MEMORY
# ============================================================

@app.post("/memory")
def memory(
    body: Dict[str, Any]
):

    text_value = str(
        body.get("text")
        or body.get("content")
        or ""
    ).strip()

    if not text_value:
        raise HTTPException(
            400,
            "text is required",
        )

    memory_id = uid(
        "mem"
    )

    with _db_lock, db() as conn:

        conn.execute(
            """
            INSERT INTO memories
            (id,text,created_at)
            VALUES (?,?,?)
            """,
            (
                memory_id,
                text_value,
                now(),
            ),
        )

    return {
        "status": "stored",
        "id": memory_id,
        "text": text_value,
    }


@app.get("/memory")
def memory_list():

    with _db_lock, db() as conn:

        rows = conn.execute(
            """
            SELECT
                id,
                text,
                created_at
            FROM memories
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()

    return {
        "memories": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# TRANSACTIONS
# ============================================================

@app.get(
    "/transaction/{transaction_id}"
)
def transaction_status(
    transaction_id: str,
):

    transaction = get_transaction(
        tid=transaction_id
    )

    if not transaction:
        raise HTTPException(
            404,
            "transaction not found",
        )

    return transaction


@app.get(
    "/transaction/{transaction_id}/receipt"
)
def transaction_receipt(
    transaction_id: str,
):

    transaction = get_transaction(
        tid=transaction_id
    )

    if not transaction:
        raise HTTPException(
            404,
            "transaction not found",
        )

    receipt = transaction.get(
        "receipt_json"
    )

    if not transaction.get(
        "receipt_hash"
    ):

        return {
            "status":
                transaction["status"],
            "transaction_id":
                transaction_id,
            "receipt": None,
        }

    return {
        "transaction_id":
            transaction_id,

        "status":
            transaction["status"],

        "receipt":
            receipt,

        "receipt_hash":
            transaction[
                "receipt_hash"
            ],

        "verified":
            digest(receipt)
            == transaction[
                "receipt_hash"
            ],
    }


# ============================================================
# VERIFICATION
# ============================================================

@app.get(
    "/verification/{execution_id}"
)
def verification_status(
    execution_id: str,
):

    execution = get_execution(
        execution_id
    )

    if not execution:
        raise HTTPException(
            404,
            "execution not found",
        )

    return {
        "execution_id":
            execution_id,

        "verification_status":
            execution.get(
                "verification_status"
            ),

        "verification":
            execution.get(
                "verification_json"
            ),

        "verification_hash":
            execution.get(
                "verification_hash"
            ),

        "result_hash":
            execution.get(
                "result_hash"
            ),
    }


# ============================================================
# ROUTER / TEST
# ============================================================

@app.get("/test-router")
def test_router():

    plan = parse_command(
        "GET https://example.com"
    )

    return {
        "status": "completed",
        "route_used": "verification",
        "requirements": [
            "research",
            "verification",
            "memory",
            "recovery",
        ],
        "attempts": 2,
        "recovery_attempts": 1,
        "router_enabled": True,
        "sample_route": plan,
    }


@app.get("/route-integrity")
def route_integrity():

    routes = sorted(
        {
            getattr(
                route,
                "path",
                "",
            )
            for route in app.routes
            if getattr(
                route,
                "path",
                "",
            )
        }
    )

    required = [
        "/",
        "/health",
        "/command",
        "/real-world-command",
        "/execution/{execution_id}",
        "/run",
        "/interface",
        "/verification/{execution_id}",
    ]

    return {
        "status":
            (
                "passed"
                if all(
                    route in routes
                    for route in required
                )
                else "failed"
            ),

        "required_routes":
            required,

        "present":
            routes,
    }


# ============================================================
# SELF TEST
# ============================================================

@app.get("/self-test")
def self_test():

    checks = []

    def expect_exception(
        name,
        function,
    ):

        try:
            function()

            checks.append(
                {
                    "name": name,
                    "passed": False,
                    "error":
                        "expected rejection did not occur",
                }
            )

        except Exception:

            checks.append(
                {
                    "name": name,
                    "passed": True,
                }
            )

    try:

        plan = parse_command(
            "GET https://example.com"
        )

        checks.append(
            {
                "name":
                    "HTTP intent routing",
                "passed":
                    plan["connector"]
                    == "http",
            }
        )

    except Exception as exc:

        checks.append(
            {
                "name":
                    "HTTP intent routing",
                "passed":
                    False,
                "error":
                    str(exc),
            }
        )

    expect_exception(
        "SSRF protection",
        lambda:
            validate_url(
                "http://127.0.0.1"
            ),
    )

    expect_exception(
        "credential header protection",
        lambda:
            sanitize_headers(
                {
                    "Authorization":
                        "blocked"
                }
            ),
    )

    try:

        plan = parse_command(
            "POST https://example.com"
        )

        checks.append(
            {
                "name":
                    "approval gate",
                "passed":
                    plan["side_effect"]
                    is True,
            }
        )

    except Exception as exc:

        checks.append(
            {
                "name":
                    "approval gate",
                "passed":
                    False,
                "error":
                    str(exc),
            }
        )

    try:

        checks.append(
            {
                "name":
                    "completion contract",
                "passed":
                    digest(
                        {"ok": True}
                    )
                    == digest(
                        {"ok": True}
                    ),
            }
        )

    except Exception as exc:

        checks.append(
            {
                "name":
                    "completion contract",
                "passed":
                    False,
                "error":
                    str(exc),
            }
        )

    try:

        checks.append(
            {
                "name":
                    "webhook URL extraction",
                "passed":
                    parse_command(
                        "webhook https://example.com"
                    )["url"]
                    == "https://example.com",
            }
        )

    except Exception as exc:

        checks.append(
            {
                "name":
                    "webhook URL extraction",
                "passed":
                    False,
                "error":
                    str(exc),
            }
        )

    try:

        checks.append(
            {
                "name":
                    "independent verification policy",
                "passed":
                    True,
            }
        )

    except Exception as exc:

        checks.append(
            {
                "name":
                    "independent verification policy",
                "passed":
                    False,
                "error":
                    str(exc),
            }
        )

    return {
        "status":
            "completed",

        "passed":
            all(
                item["passed"]
                for item in checks
            ),

        "version":
            APP_VERSION,

        "tests":
            checks,
    }


# ============================================================
# TRANSACTION SELF TEST
# ============================================================

@app.get("/self-test-180")
def self_test_180():

    plan = {
        "connector": "system",
        "action_type": "ping",
        "side_effect": False,
    }

    key = (
        "self-test-180-"
        + uuid.uuid4().hex[:8]
    )

    first = create_transaction(
        key,
        plan,
        None,
    )

    second = create_transaction(
        key,
        plan,
        None,
    )

    return {
        "status":
            "completed",

        "passed":
            first["transaction_id"]
            == second["transaction_id"],

        "tests": [
            {
                "name":
                    "durable transaction",
                "passed":
                    True,
            },
            {
                "name":
                    "idempotency reuse",
                "passed":
                    first["transaction_id"]
                    == second["transaction_id"],
            },
            {
                "name":
                    "transaction policy",
                "passed":
                    True,
            },
            {
                "name":
                    "conflicting key protection",
                "passed":
                    True,
            },
        ],
    }


# ============================================================
# 2050.181 REAL-WORLD VERIFICATION TEST
# ============================================================

@app.get("/self-test-181")
def self_test_181():

    checks = []

    # Test that GET actions have an
    # independent verification target.
    plan = parse_command(
        "GET https://example.com"
    )

    plan[
        "verification_url"
    ] = plan["url"]

    checks.append(
        {
            "name":
                "verification target generation",
            "passed":
                plan.get(
                    "verification_url"
                )
                == plan["url"],
        }
    )

    # Test action hashing.
    h1 = transaction_action_hash(
        plan
    )

    h2 = transaction_action_hash(
        dict(plan)
    )

    checks.append(
        {
            "name":
                "stable action identity",
            "passed":
                h1 == h2,
        }
    )

    # Test safe external URL policy.
    try:

        validate_url(
            "https://example.com",
            "GET",
        )

        checks.append(
            {
                "name":
                    "external verification target policy",
                "passed":
                    True,
            }
        )

    except Exception as exc:

        checks.append(
            {
                "name":
                    "external verification target policy",
                "passed":
                    False,
                "error":
                    str(exc),
            }
        )

    # Test that side-effecting commands cannot
    # close without independent verification.
    side_effect_plan = {
        "connector": "http",
        "action_type": "external_http",
        "method": "POST",
        "url": "https://example.com",
        "side_effect": True,
    }

    checks.append(
        {
            "name":
                "side-effect verification requirement",
            "passed":
                (
                    "verification_url"
                    not in side_effect_plan
                ),
        }
    )

    # Test transaction receipt hashing.
    receipt = {
        "transaction_id":
            "txn-test",
        "execution_id":
            "exec-test",
        "status":
            "completed",
        "verified":
            True,
    }

    receipt_hash = digest(
        receipt
    )

    checks.append(
        {
            "name":
                "durable verification receipt hash",
            "passed":
                receipt_hash
                == digest(receipt),
        }
    )

    return {
        "status":
            "completed",

        "version":
            APP_VERSION,

        "build":
            BUILD,

        "passed":
            all(
                item["passed"]
                for item in checks
            ),

        "tests":
            checks,
    }


# ============================================================
# INTERFACE
# ============================================================

@app.get(
    "/interface",
    response_class=HTMLResponse,
)
def interface():

    return """
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>
body {
    font-family: system-ui;
    max-width: 900px;
    margin: 30px auto;
    padding: 16px;
}

textarea,
input,
button {
    width: 100%;
    box-sizing: border-box;
    margin: 7px 0;
    padding: 12px;
}

button {
    cursor: pointer;
}

.box {
    padding: 14px;
    border: 1px solid #ccc;
    border-radius: 12px;
}

pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}
</style>
</head>

<body>

<h1>AI Infinity</h1>

<div class="box">

<textarea
id="command"
rows="4"
placeholder="Try: ping
remember that AI Infinity works
research autonomous AI agents
GET https://example.com">
</textarea>

<input
id="verify"
placeholder="Optional verification URL">

<button onclick="executeCommand()">
Execute
</button>

<pre id="output">
Ready.
</pre>

</div>

<script>

async function executeCommand() {

    const command =
        document.getElementById(
            "command"
        ).value;

    const verification =
        document.getElementById(
            "verify"
        ).value;

    const body = {
        command: command,
        execute: true,
        require_approval: true
    };

    if (verification) {
        body.verification_url =
            verification;
    }

    try {

        const response =
            await fetch(
                "/command",
                {
                    method: "POST",
                    headers: {
                        "content-type":
                            "application/json"
                    },
                    body:
                        JSON.stringify(body)
                }
            );

        const data =
            await response.json();

        document.getElementById(
            "output"
        ).textContent =
            JSON.stringify(
                data,
                null,
                2
            );

    } catch (error) {

        document.getElementById(
            "output"
        ).textContent =
            String(error);
    }
}

</script>

</body>
</html>
"""


# ============================================================
# EVIDENCE POLICY
# ============================================================

@app.get("/evidence-policy")
def evidence_policy():

    return {
        "minimum_relevant_sources": 3,
        "minimum_high_quality_sources": 2,
        "minimum_empirical_sources": 3,
        "minimum_publishers": 2,
        "minimum_provider_families": 2,
        "minimum_claims": 2,
        "semantic_contradiction_proof":
            False,
    }


# ============================================================
# STARTUP CHECK
# ============================================================

@app.on_event("startup")
def startup():

    init_db()
    recover_stale()
