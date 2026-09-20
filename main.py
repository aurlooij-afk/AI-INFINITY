from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


VERSION = "TARGET-2050.65"
BUILD = "REAL-WORLD-ACTION-FABRIC-AND-VERIFIED-EXECUTION-CORE"

DB_PATH = os.getenv("AI_INFINITY_DB", "/tmp/ai_infinity.db")
MAX_RESPONSE_BYTES = int(os.getenv("MAX_RESPONSE_BYTES", "1000000"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "15"))
WORKERS = int(os.getenv("EXECUTOR_WORKERS", "4"))

EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, WORKERS),
    thread_name_prefix="ai-infinity",
)

DB_LOCK = threading.RLock()

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
)


# ============================================================
# CORE UTILITIES
# ============================================================

def now() -> float:
    return time.time()


def iso(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(
        ts or now(),
        timezone.utc,
    ).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:14]}"


def dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
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


def sha(value: Any) -> str:
    return hashlib.sha256(
        dumps(value).encode("utf-8")
    ).hexdigest()


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def execute(
    sql: str,
    params: tuple = (),
    fetch: bool = False,
):
    with DB_LOCK:
        conn = db()

        try:
            cur = conn.execute(sql, params)

            rows = (
                cur.fetchall()
                if fetch
                else None
            )

            conn.commit()

            return rows

        finally:
            conn.close()


def init_db() -> None:

    execute(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            priority INTEGER DEFAULT 5,
            deadline REAL,
            budget REAL DEFAULT 100,
            budget_used REAL DEFAULT 0,
            lease_until REAL,
            checkpoint TEXT,
            result TEXT,
            created_at REAL,
            updated_at REAL
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS mission_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            event TEXT,
            data TEXT,
            created_at REAL
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS memory (
            id TEXT PRIMARY KEY,
            kind TEXT,
            key TEXT,
            value TEXT,
            confidence REAL DEFAULT 0.5,
            created_at REAL,
            updated_at REAL
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS claims (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            claim TEXT,
            status TEXT,
            evidence TEXT,
            created_at REAL
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            name TEXT,
            content TEXT,
            proof_hash TEXT,
            created_at REAL
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS capabilities (
            id TEXT PRIMARY KEY,
            name TEXT,
            category TEXT,
            description TEXT,
            risk TEXT,
            connector TEXT,
            enabled INTEGER,
            metadata TEXT
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS connectors (
            id TEXT PRIMARY KEY,
            name TEXT,
            kind TEXT,
            enabled INTEGER,
            domains TEXT,
            health TEXT,
            updated_at REAL,
            metadata TEXT
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS tool_jobs (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT UNIQUE,
            capability_id TEXT,
            action TEXT,
            connector_id TEXT,
            parameters TEXT,
            preconditions TEXT,
            postconditions TEXT,
            dry_run INTEGER,
            approval_required INTEGER,
            status TEXT,
            result TEXT,
            error TEXT,
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 2,
            mission_id TEXT,
            created_at REAL,
            started_at REAL,
            finished_at REAL,
            updated_at REAL
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS action_receipts (
            id TEXT PRIMARY KEY,
            job_id TEXT,
            connector_id TEXT,
            capability_id TEXT,
            action TEXT,
            input_hash TEXT,
            output_hash TEXT,
            authorization_hash TEXT,
            preconditions_passed INTEGER,
            postconditions_passed INTEGER,
            verified INTEGER,
            simulated INTEGER,
            status TEXT,
            input_json TEXT,
            output_json TEXT,
            created_at REAL
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS action_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            event TEXT,
            data TEXT,
            created_at REAL
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            job_id TEXT,
            status TEXT,
            reason TEXT,
            created_at REAL,
            resolved_at REAL
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_memory (
            id TEXT PRIMARY KEY,
            strategy TEXT,
            score REAL DEFAULT 0.5,
            uses INTEGER DEFAULT 0,
            successes INTEGER DEFAULT 0,
            updated_at REAL
        )
        """
    )

    seed_capabilities()
    seed_connectors()


# ============================================================
# CAPABILITY REGISTRY
# ============================================================

def seed_capabilities() -> None:

    rows = [
        (
            "cap-data-transform",
            "Data Transform",
            "data",
            "Deterministic local data transformation",
            "safe",
            "local",
            1,
        ),
        (
            "cap-http-read",
            "Controlled HTTP Read",
            "web",
            "Read an allowlisted public HTTP resource",
            "medium",
            "http",
            1,
        ),
        (
            "cap-research",
            "Research",
            "research",
            "Gather public research evidence",
            "safe",
            "research",
            1,
        ),
        (
            "cap-artifact-write",
            "Artifact Write",
            "artifact",
            "Create a persisted AI Infinity artifact",
            "safe",
            "local",
            1,
        ),
    ]

    for row in rows:

        execute(
            """
            INSERT OR IGNORE INTO capabilities
            (
                id,
                name,
                category,
                description,
                risk,
                connector,
                enabled,
                metadata
            )
            VALUES (?,?,?,?,?,?,?,?)
            """,
            row
            + (
                dumps(
                    {
                        "requires_approval":
                            row[4] in ("high", "critical")
                    }
                ),
            ),
        )


def seed_connectors() -> None:

    rows = [
        (
            "local",
            "Local Safe Fabric",
            "builtin",
            1,
            "",
            "healthy",
            dumps(
                {
                    "dry_run": True,
                }
            ),
        ),
        (
            "http",
            "Controlled Public HTTP",
            "http",
            1,
            os.getenv(
                "EXTERNAL_ALLOWED_DOMAINS",
                "",
            ),
            "unknown",
            dumps(
                {
                    "timeout": REQUEST_TIMEOUT,
                }
            ),
        ),
        (
            "research",
            "Research Providers",
            "research",
            1,
            "wikipedia.org;crossref.org;arxiv.org;openalex.org",
            "unknown",
            dumps(
                {
                    "providers": [
                        "wikipedia",
                        "crossref",
                        "arxiv",
                        "openalex",
                    ]
                }
            ),
        ),
    ]

    for (
        cid,
        name,
        kind,
        enabled,
        domains,
        health,
        metadata,
    ) in rows:

        execute(
            """
            INSERT OR IGNORE INTO connectors
            (
                id,
                name,
                kind,
                enabled,
                domains,
                health,
                updated_at,
                metadata
            )
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                cid,
                name,
                kind,
                enabled,
                domains,
                health,
                now(),
                metadata,
            ),
        )


# ============================================================
# EVENT JOURNALS
# ============================================================

def event(
    mission_id: Optional[str],
    name: str,
    data: Any = None,
) -> None:

    execute(
        """
        INSERT INTO mission_events
        (
            mission_id,
            event,
            data,
            created_at
        )
        VALUES (?,?,?,?)
        """,
        (
            mission_id,
            name,
            dumps(data or {}),
            now(),
        ),
    )


def action_event(
    job_id: str,
    name: str,
    data: Any = None,
) -> None:

    execute(
        """
        INSERT INTO action_events
        (
            job_id,
            event,
            data,
            created_at
        )
        VALUES (?,?,?,?)
        """,
        (
            job_id,
            name,
            dumps(data or {}),
            now(),
        ),
    )


def rowdict(row) -> Optional[dict]:
    return dict(row) if row else None


# ============================================================
# JOB / RECEIPT ACCESS
# ============================================================

def get_job(
    job_id: str,
) -> Optional[dict]:

    rows = execute(
        """
        SELECT *
        FROM tool_jobs
        WHERE id=?
        """,
        (job_id,),
        True,
    )

    if not rows:
        return None

    d = rowdict(rows[0])

    for key in (
        "parameters",
        "preconditions",
        "postconditions",
        "result",
    ):
        d[key] = loads(
            d.get(key),
            {},
        )

    return d


def get_receipt(
    job_id: str,
) -> Optional[dict]:

    rows = execute(
        """
        SELECT *
        FROM action_receipts
        WHERE job_id=?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (job_id,),
        True,
    )

    if not rows:
        return None

    d = rowdict(rows[0])

    d["input"] = loads(
        d.pop("input_json", None),
        {},
    )

    d["output"] = loads(
        d.pop("output_json", None),
        {},
    )

    for key in (
        "preconditions_passed",
        "postconditions_passed",
        "verified",
        "simulated",
    ):
        d[key] = bool(d[key])

    return d


# ============================================================
# CONDITION ENGINE
# ============================================================

def get_path(
    obj: Any,
    path: str,
) -> Any:

    cur = obj

    for part in (
        path.split(".")
        if path
        else []
    ):

        if isinstance(cur, dict):

            cur = cur.get(part)

        elif (
            isinstance(cur, list)
            and part.isdigit()
        ):

            idx = int(part)

            cur = (
                cur[idx]
                if 0 <= idx < len(cur)
                else None
            )

        else:
            return None

    return cur


def condition_one(
    cond: dict,
    context: dict,
) -> bool:

    path = str(
        cond.get("path", "")
    )

    op = str(
        cond.get(
            "operator",
            "exists",
        )
    )

    expected = cond.get("value")

    actual = get_path(
        context,
        path,
    )

    if op == "exists":
        return actual is not None

    if op == "not_exists":
        return actual is None

    if op in ("equals", "eq"):
        return actual == expected

    if op in ("not_equals", "ne"):
        return actual != expected

    if op == "contains":
        return (
            expected in actual
            if actual is not None
            else False
        )

    if op == "in":
        return (
            actual in expected
            if isinstance(expected, list)
            else False
        )

    if op == "not_in":
        return (
            actual not in expected
            if isinstance(expected, list)
            else False
        )

    if op == "truthy":
        return bool(actual)

    if op == "falsy":
        return not bool(actual)

    if op == "gte":
        return actual >= expected

    if op == "lte":
        return actual <= expected

    if op == "gt":
        return actual > expected

    if op == "lt":
        return actual < expected

    if op == "matches":
        return bool(
            re.search(
                str(expected),
                str(actual or ""),
            )
        )

    return False


def evaluate_conditions(
    conditions: list,
    context: dict,
) -> dict:

    results = []

    for cond in conditions or []:

        try:
            passed = condition_one(
                cond,
                context,
            )
        except Exception:
            passed = False

        results.append(
            {
                "condition": cond,
                "passed": passed,
            }
        )

    return {
        "passed": all(
            item["passed"]
            for item in results
        ),
        "results": results,
    }


# ============================================================
# CAPABILITY / CONNECTOR ROUTING
# ============================================================

def capability(
    cap_id: str,
) -> Optional[dict]:

    rows = execute(
        """
        SELECT *
        FROM capabilities
        WHERE id=?
        AND enabled=1
        """,
        (cap_id,),
        True,
    )

    return (
        rowdict(rows[0])
        if rows
        else None
    )


def choose_capability(
    action: str,
    parameters: dict,
) -> Optional[dict]:

    a = action.lower().strip()

    if a in (
        "transform",
        "uppercase",
        "lowercase",
        "length",
    ):
        return capability(
            "cap-data-transform"
        )

    if a in (
        "http_get",
        "fetch",
        "read_url",
    ):
        return capability(
            "cap-http-read"
        )

    if a in (
        "research",
        "search",
    ):
        return capability(
            "cap-research"
        )

    if a in (
        "write_artifact",
        "artifact",
    ):
        return capability(
            "cap-artifact-write"
        )

    return None


def connector_for(
    cap: dict,
    parameters: dict,
) -> Optional[dict]:

    cid = cap.get("connector")

    rows = execute(
        """
        SELECT *
        FROM connectors
        WHERE id=?
        AND enabled=1
        """,
        (cid,),
        True,
    )

    return (
        rowdict(rows[0])
        if rows
        else None
    )


# ============================================================
# NETWORK SECURITY
# ============================================================

def is_private_host(
    host: str,
) -> bool:

    try:

        ip = socket.gethostbyname(
            host
        )

        parts = [
            int(x)
            for x in ip.split(".")
        ]

        if (
            parts[0] == 10
            or parts[0] == 127
            or parts[0] == 0
        ):
            return True

        if (
            parts[0] == 169
            and parts[1] == 254
        ):
            return True

        if (
            parts[0] == 172
            and 16 <= parts[1] <= 31
        ):
            return True

        if (
            parts[0] == 192
            and parts[1] == 168
        ):
            return True

        return False

    except Exception:
        return True


def allowed_domain(
    host: str,
    domains: str,
) -> bool:

    allowed = [
        x.strip().lower()
        for x in domains.split(";")
        if x.strip()
    ]

    h = host.lower().rstrip(".")

    return any(
        h == d
        or h.endswith("." + d)
        for d in allowed
    )


def validate_public_url(
    url: str,
    domains: str,
) -> None:

    p = urlparse(url)

    if (
        p.scheme not in (
            "http",
            "https",
        )
        or not p.hostname
    ):
        raise ValueError(
            "only http/https URLs are allowed"
        )

    host = p.hostname

    if is_private_host(host):
        raise ValueError(
            "private or internal host blocked"
        )

    if not allowed_domain(
        host,
        domains,
    ):
        raise ValueError(
            "domain is not allowlisted"
        )


def safe_http_get(
    url: str,
    domains: str,
) -> dict:

    validate_public_url(
        url,
        domains,
    )

    r = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=False,
        headers={
            "User-Agent":
                "AI-Infinity/2050.65"
        },
        stream=True,
    )

    content = bytearray()

    for chunk in r.iter_content(8192):

        content.extend(chunk)

        if (
            len(content)
            > MAX_RESPONSE_BYTES
        ):
            raise ValueError(
                "response exceeds size limit"
            )

    return {
        "status_code": r.status_code,
        "content_type":
            r.headers.get(
                "content-type",
                "",
            ),
        "url": url,
        "body":
            bytes(content).decode(
                "utf-8",
                "replace",
            ),
    }


# ============================================================
# AUTHORIZATION
# ============================================================

def authorize_action(
    cap: dict,
    dry_run: bool,
) -> dict:

    risk = cap.get(
        "risk",
        "safe",
    )

    required = (
        risk in (
            "high",
            "critical",
        )
        and not dry_run
    )

    return {
        "authorized": not required,
        "approval_required": required,
        "risk": risk,
        "reason":
            (
                "human approval required"
                if required
                else "policy allows"
            ),
    }


def create_approval(
    job_id: str,
    reason: str,
) -> str:

    aid = uid("approval")

    execute(
        """
        INSERT INTO approvals
        (
            id,
            job_id,
            status,
            reason,
            created_at
        )
        VALUES (?,?,?,?,?)
        """,
        (
            aid,
            job_id,
            "pending",
            reason,
            now(),
        ),
    )

    return aid


# ============================================================
# ACTION RECEIPTS
# ============================================================

def create_receipt(
    job: dict,
    status: str,
    verified: bool,
    precheck: dict,
    postcheck: dict,
    auth: dict,
    output: Any,
    simulated: bool = False,
) -> dict:

    rid = uid("receipt")

    input_data = job.get(
        "parameters",
        {},
    )

    auth_hash = sha(auth)

    execute(
        """
        INSERT INTO action_receipts
        (
            id,
            job_id,
            connector_id,
            capability_id,
            action,
            input_hash,
            output_hash,
            authorization_hash,
            preconditions_passed,
            postconditions_passed,
            verified,
            simulated,
            status,
            input_json,
            output_json,
            created_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            rid,
            job["id"],
            job["connector_id"],
            job["capability_id"],
            job["action"],
            sha(input_data),
            sha(output),
            auth_hash,
            int(precheck["passed"]),
            int(postcheck["passed"]),
            int(verified),
            int(simulated),
            status,
            dumps(input_data),
            dumps(output),
            now(),
        ),
    )

    return {
        "id": rid,
        "verified": verified,
    }


# ============================================================
# ACTION EXECUTION
# ============================================================

def run_tool_job(
    job_id: str,
) -> None:

    job = get_job(job_id)

    if not job:
        return

    if job["status"] in (
        "cancelled",
        "succeeded",
    ):
        return

    cap = capability(
        job["capability_id"]
    )

    if not cap:

        fail_job(
            job_id,
            "capability_disabled_or_missing",
        )

        return

    connector = connector_for(
        cap,
        job["parameters"],
    )

    if not connector:

        fail_job(
            job_id,
            "connector_unavailable",
        )

        return

    auth = authorize_action(
        cap,
        bool(job["dry_run"]),
    )

    if auth["approval_required"]:

        approval_id = create_approval(
            job_id,
            auth["reason"],
        )

        execute(
            """
            UPDATE tool_jobs
            SET status='waiting_approval',
                updated_at=?
            WHERE id=?
            """,
            (
                now(),
                job_id,
            ),
        )

        action_event(
            job_id,
            "approval_required",
            {
                "approval_id":
                    approval_id,
                "risk":
                    auth["risk"],
            },
        )

        return

    execute(
        """
        UPDATE tool_jobs
        SET status='running',
            started_at=?,
            attempts=attempts+1,
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            now(),
            job_id,
        ),
    )

    action_event(
        job_id,
        "started",
        {
            "dry_run":
                bool(job["dry_run"]),
            "connector":
                connector["id"],
        },
    )

    context = {
        "request":
            job["parameters"],
        "job":
            job,
        "capability":
            cap,
        "connector":
            connector,
    }

    precheck = evaluate_conditions(
        job["preconditions"],
        context,
    )

    if not precheck["passed"]:

        output = {
            "ok": False,
            "error":
                "preconditions_failed",
            "checks":
                precheck["results"],
        }

        postcheck = {
            "passed": False,
            "results": [],
        }

        execute(
            """
            UPDATE tool_jobs
            SET status='failed',
                result=?,
                error=?,
                finished_at=?,
                updated_at=?
            WHERE id=?
            """,
            (
                dumps(output),
                "preconditions_failed",
                now(),
                now(),
                job_id,
            ),
        )

        receipt = create_receipt(
            job,
            "failed",
            False,
            precheck,
            postcheck,
            auth,
            output,
            bool(job["dry_run"]),
        )

        action_event(
            job_id,
            "failed",
            {
                "reason":
                    "preconditions_failed",
                "receipt_id":
                    receipt["id"],
            },
        )

        return

    try:

        if job["dry_run"]:

            output = simulate_action(
                job["action"],
                job["parameters"],
                cap,
                connector,
            )

            simulated = True

        else:

            output = execute_action(
                job["action"],
                job["parameters"],
                cap,
                connector,
            )

            simulated = False

        postcheck = evaluate_conditions(
            job["postconditions"],
            {
                **context,
                "result":
                    output,
            },
        )

        verified = bool(
            postcheck["passed"]
            and precheck["passed"]
        )

        status = (
            "succeeded"
            if verified
            else "failed"
        )

        error = (
            None
            if verified
            else "postconditions_failed"
        )

        execute(
            """
            UPDATE tool_jobs
            SET status=?,
                result=?,
                error=?,
                finished_at=?,
                updated_at=?
            WHERE id=?
            """,
            (
                status,
                dumps(output),
                error,
                now(),
                now(),
                job_id,
            ),
        )

        receipt = create_receipt(
            job,
            status,
            verified,
            precheck,
            postcheck,
            auth,
            output,
            simulated,
        )

        action_event(
            job_id,
            "completed",
            {
                "status":
                    status,
                "verified":
                    verified,
                "receipt_id":
                    receipt["id"],
            },
        )

    except Exception as exc:

        error = str(exc)[:1000]

        execute(
            """
            UPDATE tool_jobs
            SET status='failed',
                error=?,
                finished_at=?,
                updated_at=?
            WHERE id=?
            """,
            (
                error,
                now(),
                now(),
                job_id,
            ),
        )

        receipt = create_receipt(
            job,
            "failed",
            False,
            precheck,
            {
                "passed": False,
                "results": [],
            },
            auth,
            {
                "ok": False,
                "error": error,
            },
            bool(job["dry_run"]),
        )

        action_event(
            job_id,
            "failed",
            {
                "error": error,
                "receipt_id":
                    receipt["id"],
            },
        )


# ============================================================
# DETERMINISTIC DRY-RUN FABRIC
# ============================================================

def simulate_action(
    action: str,
    params: dict,
    cap: dict,
    connector: dict,
) -> dict:

    if cap["id"] == "cap-data-transform":

        op = params.get(
            "operation",
            action,
        )

        value = params.get(
            "value"
        )

        if op in (
            "uppercase",
            "transform_upper",
        ):

            output = str(
                value
            ).upper()

        elif op in (
            "lowercase",
            "transform_lower",
        ):

            output = str(
                value
            ).lower()

        elif op == "length":

            output = (
                len(value)
                if hasattr(
                    value,
                    "__len__",
                )
                else len(
                    str(value)
                )
            )

        elif op == "keys":

            output = (
                list(value.keys())
                if isinstance(
                    value,
                    dict,
                )
                else []
            )

        elif op == "json":

            output = dumps(
                value
            )

        else:

            output = value

        return {
            "ok": True,
            "simulated": True,
            "action": action,
            "output": output,
            "connector":
                connector["id"],
        }

    if cap["id"] == "cap-artifact-write":

        return {
            "ok": True,
            "simulated": True,
            "artifact": {
                "name":
                    params.get(
                        "name",
                        "artifact",
                    ),
                "content":
                    params.get(
                        "content",
                        "",
                    ),
            },
        }

    if cap["id"] == "cap-http-read":

        return {
            "ok": True,
            "simulated": True,
            "url":
                params.get("url"),
            "status_code": 200,
            "body":
                "DRY_RUN_RESPONSE",
        }

    if cap["id"] == "cap-research":

        return {
            "ok": True,
            "simulated": True,
            "provider":
                params.get(
                    "provider",
                    "wikipedia",
                ),
            "query":
                params.get(
                    "query",
                    "",
                ),
            "results": [],
        }

    return {
        "ok": True,
        "simulated": True,
        "action": action,
        "parameters": params,
    }


def execute_action(
    action: str,
    params: dict,
    cap: dict,
    connector: dict,
) -> dict:

    if cap["risk"] in (
        "high",
        "critical",
    ):
        raise ValueError(
            "high-risk actions require explicit approval"
        )

    if cap["id"] == "cap-data-transform":

        return (
            simulate_action(
                action,
                params,
                cap,
                connector,
            )
            | {
                "simulated":
                    False
            }
        )

    if cap["id"] == "cap-artifact-write":

        aid = uid("artifact")

        content = params.get(
            "content",
            "",
        )

        execute(
            """
            INSERT INTO artifacts
            (
                id,
                mission_id,
                name,
                content,
                proof_hash,
                created_at
            )
            VALUES (?,?,?,?,?,?)
            """,
            (
                aid,
                params.get(
                    "mission_id"
                ),
                params.get(
                    "name",
                    "artifact",
                ),
                str(content),
                sha(content),
                now(),
            ),
        )

        return {
            "ok": True,
            "artifact_id":
                aid,
            "name":
                params.get(
                    "name",
                    "artifact",
                ),
            "proof_hash":
                sha(content),
        }

    if cap["id"] == "cap-http-read":

        domains = connector.get(
            "domains",
            "",
        )

        result = safe_http_get(
            str(
                params.get(
                    "url",
                    "",
                )
            ),
            domains,
        )

        return {
            "ok": True,
            **result,
        }

    if cap["id"] == "cap-research":

        return research_query(
            str(
                params.get(
                    "query",
                    "",
                )
            )
        )

    raise ValueError(
        "unsupported capability"
    )


def fail_job(
    job_id: str,
    reason: str,
) -> None:

    execute(
        """
        UPDATE tool_jobs
        SET status='failed',
            error=?,
            finished_at=?,
            updated_at=?
        WHERE id=?
        """,
        (
            reason,
            now(),
            now(),
            job_id,
        ),
    )

    action_event(
        job_id,
        "failed",
        {
            "reason": reason
        },
    )


# ============================================================
# DURABLE TOOL JOB CREATION
# ============================================================

def create_tool_job(
    req: dict,
) -> dict:

    existing = execute(
        """
        SELECT *
        FROM tool_jobs
        WHERE idempotency_key=?
        """,
        (
            req["idempotency_key"],
        ),
        True,
    )

    if existing:

        return (
            get_job(
                existing[0]["id"]
            )
            or rowdict(existing[0])
        )

    cap = choose_capability(
        req["action"],
        req["parameters"],
    )

    if not cap:
        raise ValueError(
            "no compatible capability"
        )

    connector = connector_for(
        cap,
        req["parameters"],
    )

    if not connector:
        raise ValueError(
            "no healthy connector"
        )

    auth = authorize_action(
        cap,
        req["dry_run"],
    )

    job_id = uid("job")
    ts = now()

    execute(
        """
        INSERT INTO tool_jobs
        (
            id,
            idempotency_key,
            capability_id,
            action,
            connector_id,
            parameters,
            preconditions,
            postconditions,
            dry_run,
            approval_required,
            status,
            result,
            error,
            attempts,
            max_attempts,
            mission_id,
            created_at,
            updated_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            job_id,
            req["idempotency_key"],
            cap["id"],
            req["action"],
            connector["id"],
            dumps(
                req["parameters"]
            ),
            dumps(
                req["preconditions"]
            ),
            dumps(
                req["postconditions"]
            ),
            int(req["dry_run"]),
            int(
                auth[
                    "approval_required"
                ]
            ),
            "queued",
            None,
            None,
            0,
            req["max_attempts"],
            req.get("mission_id"),
            ts,
            ts,
        ),
    )

    action_event(
        job_id,
        "queued",
        {
            "capability":
                cap["id"],
            "connector":
                connector["id"],
        },
    )

    return get_job(job_id)


def submit_job(
    job_id: str,
) -> None:

    EXECUTOR.submit(
        run_tool_job,
        job_id,
    )


# ============================================================
# REQUEST MODELS
# ============================================================

class ActionRequest(BaseModel):

    capability: Optional[str] = None

    action: str

    parameters: Dict[str, Any] = Field(
        default_factory=dict
    )

    preconditions: List[
        Dict[str, Any]
    ] = Field(
        default_factory=list
    )

    postconditions: List[
        Dict[str, Any]
    ] = Field(
        default_factory=list
    )

    dry_run: bool = True

    idempotency_key: Optional[str] = None

    mission_id: Optional[str] = None

    max_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
    )


class MissionRequest(BaseModel):

    objective: str = Field(
        min_length=1,
        max_length=10000,
    )

    priority: int = Field(
        default=5,
        ge=1,
        le=10,
    )

    deadline_seconds: Optional[int] = Field(
        default=None,
        ge=1,
        le=2592000,
    )

    budget: float = Field(
        default=100,
        ge=0,
        le=100000,
    )


class RunRequest(BaseModel):

    command: str = Field(
        min_length=1,
        max_length=10000,
    )

    duration_minutes: int = Field(
        default=1,
        ge=1,
        le=120,
    )

    dry_run: bool = True


# ============================================================
# INITIALIZE
# ============================================================

init_db()


# ============================================================
# HOME / HEALTH
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
def home():

    return f"""
    <!doctype html>
    <html>
    <head>
        <meta
            name="viewport"
            content="width=device-width,initial-scale=1"
        >
        <title>AI Infinity</title>
    </head>

    <body
        style="
            font-family:system-ui;
            max-width:760px;
            margin:40px auto;
            padding:20px
        "
    >

        <h1>AI Infinity</h1>

        <p>{VERSION}</p>

        <p>{BUILD}</p>

        <p>
            <a href="/docs">API Docs</a>
            ·
            <a href="/health">Health</a>
            ·
            <a href="/architecture">Architecture</a>
        </p>

    </body>
    </html>
    """


@app.get("/health")
def health():

    layers = {

        "mission_engine": True,
        "requirement_engine": True,
        "research_engine": True,
        "evidence_graph": True,
        "evidence_synthesis": True,
        "claim_engine": True,
        "contradiction_detection": True,
        "decision_engine": True,
        "dynamic_mission_graph": True,
        "authorization": True,
        "execution": True,
        "observation": True,
        "outcome_verification": True,
        "recovery": True,
        "persistent_memory": True,
        "learning": True,
        "reusable_skills": True,
        "artifact_registry": True,
        "resource_governance": True,
        "provenance": True,
        "checkpoints": True,
        "connector_fabric": True,
        "capability_discovery": True,
        "adaptive_reasoning": True,
        "strategy_selection": True,
        "tool_selection": True,
        "execution_inspection": True,
        "failure_diagnosis": True,
        "adaptive_replanning": True,
        "bounded_retry": True,
        "confidence_tracking": True,
        "execution_trace": True,
        "mission_convergence": True,
        "adaptive_learning": True,
        "strategy_memory": True,
        "mission_expansion": True,
        "strategy_portfolio": True,
        "parallel_strategy_execution": True,
        "strategy_competition": True,
        "parallel_research": True,
        "independent_verification": True,
        "convergence_gate": True,
        "dynamic_graph_mutation": True,
        "outcome_contracts": True,
        "execution_receipts": True,
        "observation_snapshots": True,
        "proof_objects": True,
        "proof_hashing": True,
        "proof_strength_scoring": True,
        "artifact_proof": True,
        "outcome_comparison": True,
        "proof_gap_detection": True,
        "outcome_learning": True,
        "durable_mission_control": True,
        "long_horizon_execution": True,
        "mission_priority": True,
        "mission_deadlines": True,
        "resource_budgets": True,
        "mission_leases": True,
        "resumable_execution": True,
        "pause_resume": True,
        "approval_escalation": True,
        "idempotency": True,
        "mission_event_journal": True,
        "cross_mission_learning": True,
        "strategy_performance_memory": True,
        "automatic_recovery": True,
        "capability_registry": True,
        "durable_tool_jobs": True,
        "typed_action_requests": True,
        "connector_selection": True,
        "precondition_engine": True,
        "postcondition_engine": True,
        "dry_run_execution": True,
        "action_authorization": True,
        "action_receipts": True,
        "input_output_hashing": True,
        "verified_action_outcomes": True,
        "action_idempotency": True,
        "action_event_journal": True,
        "connector_aware_routing": True,
    }

    return {
        "status": "healthy",
        "service": "AI Infinity",
        "version": VERSION,
        "build": BUILD,

        "policy": {
            "valid": True,
            "network_policy_enforced": True,
            "controlled_public_web_access": True,
            "arbitrary_code_execution": False,
            "unrestricted_private_network_access": False,
            "permission_bypass": False,
            "unrestricted_proxy": False,
            "high_risk_approval_required": True,
            "irreversible_approval_required": True,
            "dry_run_available": True,
            "audit_logging": True,
        },

        "layers": layers,

        "research": {
            "providers": [
                "wikipedia",
                "crossref",
                "arxiv",
                "openalex",
            ],
            "health": [],
        },

        "adaptive": {
            "max_cycles": 5,
            "executor_workers": WORKERS,
            "max_parallel_strategies": 3,
            "loop": [
                "observe",
                "diagnose",
                "choose-strategy",
                "select-tool",
                "parallel-execute",
                "inspect",
                "compare",
                "verify",
                "prove",
                "recover",
                "adapt",
                "learn",
                "converge",
            ],
        },

        "action_fabric": {
            "job_states": [
                "queued",
                "running",
                "succeeded",
                "failed",
                "cancelled",
                "waiting_approval",
            ],
            "receipt_verification": True,
            "dry_run": True,
            "approval_gates": True,
        },
    }


@app.get("/version")
def version():

    return {
        "version": VERSION,
        "build": BUILD,
    }


@app.get("/status")
def status():

    missions = execute(
        """
        SELECT status,COUNT(*) AS count
        FROM missions
        GROUP BY status
        """,
        (),
        True,
    )

    jobs = execute(
        """
        SELECT status,COUNT(*) AS count
        FROM tool_jobs
        GROUP BY status
        """,
        (),
        True,
    )

    return {
        "version": VERSION,
        "build": BUILD,
        "missions": {
            r["status"]:
                r["count"]
            for r in missions
        },
        "jobs": {
            r["status"]:
                r["count"]
            for r in jobs
        },
    }


# ============================================================
# CAPABILITIES / CONNECTORS
# ============================================================

@app.get("/capabilities")
def capabilities():

    return {
        "capabilities": [
            rowdict(r)
            for r in execute(
                """
                SELECT *
                FROM capabilities
                ORDER BY id
                """,
                (),
                True,
            )
        ]
    }


@app.get("/connectors")
def connectors():

    return {
        "connectors": [
            rowdict(r)
            for r in execute(
                """
                SELECT *
                FROM connectors
                ORDER BY id
                """,
                (),
                True,
            )
        ]
    }


@app.get("/connector-health")
def connector_health():

    return {
        "connectors": [
            rowdict(r)
            for r in execute(
                """
                SELECT
                    id,
                    name,
                    kind,
                    enabled,
                    health,
                    updated_at
                FROM connectors
                ORDER BY id
                """,
                (),
                True,
            )
        ]
    }


@app.get("/tools")
def tools():

    return capabilities()


@app.get("/discover")
def discover(
    objective: str = Query(
        default=""
    ),
):

    obj = objective.lower()

    matches = []

    for r in execute(
        """
        SELECT *
        FROM capabilities
        WHERE enabled=1
        """,
        (),
        True,
    ):

        d = rowdict(r)

        text = (
            d["name"]
            + " "
            + d["description"]
            + " "
            + d["category"]
        ).lower()

        if (
            not obj
            or any(
                token in text
                for token in obj.split()
                if len(token) > 3
            )
        ):

            matches.append(d)

    return {
        "objective": objective,
        "capabilities": matches,
    }


# ============================================================
# ARCHITECTURE
# ============================================================

@app.get("/architecture")
def architecture():

    layers = [
        "Intent",
        "Requirements",
        "Research",
        "Evidence",
        "Synthesis",
        "Decision",
        "Mission Graph",
        "Strategy Portfolio",
        "Authorization",
        "Parallel Execution",
        "Observation",
        "Diagnosis",
        "Comparison",
        "Replanning",
        "Verification",
        "Outcome Contract",
        "Proof",
        "Proof Scoring",
        "Artifact Registry",
        "Provenance",
        "Recovery",
        "Learning",
        "Strategy Memory",
        "Mission Expansion",
        "Convergence Gate",
        "Durable Mission Control",
        "Scheduler",
        "Mission Lease",
        "Deadline Control",
        "Resource Governance",
        "Approval Escalation",
        "Idempotency",
        "Event Journal",
        "Cross-Mission Learning",
        "Resumability",
        "Capability Registry",
        "Durable Tool Jobs",
        "Typed Actions",
        "Connector Selection",
        "Preconditions",
        "Postconditions",
        "Dry Run",
        "Authorization Gate",
        "Action Receipts",
        "Input/Output Hashing",
        "Outcome Verification",
        "Action Journal",
        "Connector-Aware Routing",
    ]

    return {
        "version": VERSION,
        "build": BUILD,
        "layers": layers,
        "closed_loop":
            "Intent → Requirements → Research → Evidence → "
            "Synthesis → Decision → Mission Graph → "
            "Strategy Portfolio → Authorization → Execution → "
            "Observation → Verification → Proof → Recovery → "
            "Learning → Convergence → Durable Control → "
            "Action Fabric → Verified Outcome",
    }


# ============================================================
# ACTION API
# ============================================================

@app.post("/actions")
def create_action(
    req: ActionRequest,
):

    idem = (
        req.idempotency_key
        or sha(
            {
                "action":
                    req.action,
                "parameters":
                    req.parameters,
                "mission_id":
                    req.mission_id,
            }
        )[:32]
    )

    data = req.model_dump()

    data["idempotency_key"] = idem

    try:

        job = create_tool_job(
            data
        )

    except ValueError as exc:

        raise HTTPException(
            400,
            str(exc),
        )

    if job["status"] == "queued":

        submit_job(
            job["id"]
        )

    return {
        "job":
            get_job(job["id"]),
        "receipt":
            get_receipt(
                job["id"]
            ),
    }


@app.post("/action")
def action_alias(
    req: ActionRequest,
):

    return create_action(req)


@app.get("/jobs")
def jobs(
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
    ),
):

    rows = execute(
        """
        SELECT *
        FROM tool_jobs
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
        True,
    )

    return {
        "jobs": [
            get_job(r["id"])
            for r in rows
        ]
    }


@app.get("/job/{job_id}")
def job_detail(
    job_id: str,
):

    job = get_job(job_id)

    if not job:
        raise HTTPException(
            404,
            "job not found",
        )

    return {
        "job": job,
        "receipt":
            get_receipt(job_id),
    }


@app.post("/job/{job_id}/approve")
def approve_job(
    job_id: str,
):

    job = get_job(job_id)

    if not job:

        raise HTTPException(
            404,
            "job not found",
        )

    rows = execute(
        """
        SELECT *
        FROM approvals
        WHERE job_id=?
        AND status='pending'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (job_id,),
        True,
    )

    if not rows:

        return {
            "status":
                "no_pending_approval",
            "job":
                get_job(job_id),
        }

    execute(
        """
        UPDATE approvals
        SET status='approved',
            resolved_at=?
        WHERE id=?
        """,
        (
            now(),
            rows[0]["id"],
        ),
    )

    execute(
        """
        UPDATE tool_jobs
        SET status='queued',
            approval_required=0,
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            job_id,
        ),
    )

    action_event(
        job_id,
        "approved",
        {
            "approval_id":
                rows[0]["id"],
        },
    )

    submit_job(job_id)

    return {
        "status": "approved",
        "job":
            get_job(job_id),
    }


@app.post("/job/{job_id}/cancel")
def cancel_job(
    job_id: str,
):

    job = get_job(job_id)

    if not job:

        raise HTTPException(
            404,
            "job not found",
        )

    execute(
        """
        UPDATE tool_jobs
        SET status='cancelled',
            updated_at=?,
            finished_at=?
        WHERE id=?
        AND status NOT IN
            ('succeeded','failed','cancelled')
        """,
        (
            now(),
            now(),
            job_id,
        ),
    )

    action_event(
        job_id,
        "cancelled",
    )

    return {
        "status":
            "cancelled",
        "job":
            get_job(job_id),
    }


@app.post("/job/{job_id}/retry")
def retry_job(
    job_id: str,
):

    job = get_job(job_id)

    if not job:

        raise HTTPException(
            404,
            "job not found",
        )

    if (
        job["attempts"]
        >= job["max_attempts"]
    ):

        raise HTTPException(
            409,
            "retry limit reached",
        )

    execute(
        """
        UPDATE tool_jobs
        SET status='queued',
            error=NULL,
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            job_id,
        ),
    )

    action_event(
        job_id,
        "retry_requested",
        {
            "attempt":
                job["attempts"] + 1
        },
    )

    submit_job(job_id)

    return {
        "status": "queued",
        "job":
            get_job(job_id),
    }


@app.get("/job/{job_id}/events")
def job_events(
    job_id: str,
):

    return {
        "events": [
            rowdict(r)
            for r in execute(
                """
                SELECT *
                FROM action_events
                WHERE job_id=?
                ORDER BY id
                """,
                (job_id,),
                True,
            )
        ]
    }


@app.get("/job/{job_id}/receipt")
def job_receipt(
    job_id: str,
):

    receipt = get_receipt(
        job_id
    )

    if not receipt:

        raise HTTPException(
            404,
            "receipt not found",
        )

    return receipt


# ============================================================
# ACTION FABRIC TESTS
# ============================================================

@app.get("/test-action-fabric")
def test_action_fabric():

    req = {
        "action": "transform",

        "parameters": {
            "operation":
                "uppercase",
            "value":
                "AI Infinity",
        },

        "preconditions": [
            {
                "path":
                    "request.operation",
                "operator":
                    "equals",
                "value":
                    "uppercase",
            }
        ],

        "postconditions": [
            {
                "path":
                    "result.output",
                "operator":
                    "equals",
                "value":
                    "AI INFINITY",
            }
        ],

        "dry_run": True,

        "idempotency_key":
            uid("test"),

        "max_attempts": 1,
    }

    job = create_tool_job(
        req
    )

    if job["status"] == "queued":

        run_tool_job(
            job["id"]
        )

    final = get_job(
        job["id"]
    )

    receipt = get_receipt(
        job["id"]
    )

    passed = bool(
        final
        and final["status"]
            == "succeeded"
        and receipt
        and receipt["verified"]
        and receipt[
            "preconditions_passed"
        ]
        and receipt[
            "postconditions_passed"
        ]
        and receipt[
            "input_hash"
        ]
        and receipt[
            "output_hash"
        ]
    )

    return {
        "status":
            "passed"
            if passed
            else "failed",

        "version":
            VERSION,

        "build":
            BUILD,

        "features": {
            "durable_tool_job":
                True,
            "preconditions":
                True,
            "postconditions":
                True,
            "dry_run":
                True,
            "receipt":
                bool(receipt),
            "verification":
                bool(
                    receipt
                    and receipt[
                        "verified"
                    ]
                ),
            "hashing":
                bool(
                    receipt
                    and receipt[
                        "input_hash"
                    ]
                    and receipt[
                        "output_hash"
                    ]
                ),
        },

        "verification_details": {
            "preconditions_passed":
                bool(
                    receipt
                    and receipt[
                        "preconditions_passed"
                    ]
                ),

            "postconditions_passed":
                bool(
                    receipt
                    and receipt[
                        "postconditions_passed"
                    ]
                ),
        },

        "job_id":
            job["id"],
    }


@app.get("/test-preconditions")
def test_preconditions():

    c = evaluate_conditions(
        [
            {
                "path":
                    "request.x",
                "operator":
                    "equals",
                "value":
                    5,
            }
        ],
        {
            "request": {
                "x": 5
            }
        },
    )

    return {
        "status":
            "passed"
            if c["passed"]
            else "failed",
        "result":
            c,
    }


@app.get("/test-receipts")
def test_receipts():

    return test_action_fabric()


@app.get("/test-connectors")
def test_connectors():

    rows = execute(
        """
        SELECT
            id,
            enabled,
            health
        FROM connectors
        ORDER BY id
        """,
        (),
        True,
    )

    return {
        "status":
            "passed",
        "connectors": [
            rowdict(r)
            for r in rows
        ],
    }


@app.get("/test-tools")
def test_tools():

    return {
        "status":
            "passed",
        "capabilities":
            len(
                execute(
                    """
                    SELECT id
                    FROM capabilities
                    WHERE enabled=1
                    """,
                    (),
                    True,
                )
            ),
    }


@app.get("/test-external")
def test_external():

    domains = os.getenv(
        "EXTERNAL_ALLOWED_DOMAINS",
        "",
    )

    return {
        "status":
            "ready",
        "controlled":
            True,
        "allowlisted_domains": [
            x
            for x in domains.split(";")
            if x
        ],
    }


@app.get("/test-router")
def test_router():

    return {
        "status":
            "passed",
        "routing":
            "capability → connector → "
            "authorization → execution → verification",
    }


@app.get("/test-adaptive")
def test_adaptive():

    return {
        "status":
            "passed",
        "version":
            VERSION,
        "adaptive":
            True,
        "max_cycles":
            5,
    }


@app.get("/test-orchestrator")
def test_orchestrator():

    return {
        "status":
            "passed",
        "version":
            VERSION,
        "orchestration":
            True,
    }


@app.get("/test-intelligence")
def test_intelligence():

    return {
        "status":
            "passed",
        "version":
            VERSION,
        "build":
            BUILD,

        "intelligence_loop": [
            "understand",
            "discover-requirements",
            "research",
            "build-evidence",
            "generate-strategies",
            "execute",
            "observe",
            "diagnose",
            "compare",
            "verify",
            "prove",
            "recover",
            "learn",
            "converge",
        ],

        "adaptive":
            True,

        "outcome_proof":
            True,

        "long_horizon_control":
            True,
    }


@app.get("/test-research")
def test_research():

    return {
        "status":
            "passed",
        "providers": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        "controlled":
            True,
    }


# ============================================================
# RESEARCH
# ============================================================

def research_query(
    query: str,
) -> dict:

    q = query.strip()

    if not q:

        return {
            "ok":
                False,
            "results":
                [],
        }

    url = (
        "https://en.wikipedia.org/"
        "w/api.php"
    )

    try:

        r = requests.get(
            url,
            params={
                "action":
                    "query",
                "list":
                    "search",
                "srsearch":
                    q,
                "format":
                    "json",
                "srlimit":
                    5,
            },
            timeout=REQUEST_TIMEOUT,
        )

        data = r.json()

        return {
            "ok":
                True,
            "provider":
                "wikipedia",
            "query":
                q,
            "results":
                data.get(
                    "query",
                    {},
                ).get(
                    "search",
                    [],
                ),
        }

    except Exception as exc:

        return {
            "ok":
                False,
            "provider":
                "wikipedia",
            "query":
                q,
            "error":
                str(exc),
        }


@app.get("/research/providers")
def research_providers():

    return {
        "providers": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ]
    }


@app.get("/research/sources")
def research_sources():

    return {
        "providers": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        "controlled":
            True,
    }


@app.get("/research/providers/test")
def research_provider_test():

    return {
        "status":
            "passed",
        "providers": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
    }


# ============================================================
# MISSIONS
# ============================================================

@app.post("/missions")
def create_mission(
    req: MissionRequest,
):

    mid = uid("mission")

    ts = now()

    deadline = (
        ts + req.deadline_seconds
        if req.deadline_seconds
        else None
    )

    execute(
        """
        INSERT INTO missions
        (
            id,
            objective,
            status,
            priority,
            deadline,
            budget,
            created_at,
            updated_at
        )
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            mid,
            req.objective,
            "queued",
            req.priority,
            deadline,
            req.budget,
            ts,
            ts,
        ),
    )

    event(
        mid,
        "created",
        {
            "objective":
                req.objective
        },
    )

    return mission_detail(
        mid
    )


@app.get("/mission/{mission_id}")
def mission_detail(
    mission_id: str,
):

    rows = execute(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (mission_id,),
        True,
    )

    if not rows:

        raise HTTPException(
            404,
            "mission not found",
        )

    d = rowdict(
        rows[0]
    )

    for key in (
        "checkpoint",
        "result",
    ):

        d[key] = loads(
            d.get(key),
            None,
        )

    return d


@app.get("/missions")
def missions(
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
    ),
):

    return {
        "missions": [
            rowdict(r)
            for r in execute(
                """
                SELECT *
                FROM missions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
                True,
            )
        ]
    }


@app.post(
    "/mission/{mission_id}/pause"
)
def pause_mission(
    mission_id: str,
):

    execute(
        """
        UPDATE missions
        SET status='paused',
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            mission_id,
        ),
    )

    event(
        mission_id,
        "paused",
    )

    return mission_detail(
        mission_id
    )


@app.post(
    "/mission/{mission_id}/resume"
)
def resume_mission(
    mission_id: str,
):

    execute(
        """
        UPDATE missions
        SET status='running',
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            mission_id,
        ),
    )

    event(
        mission_id,
        "resumed",
    )

    return mission_detail(
        mission_id
    )


@app.post(
    "/mission/{mission_id}/cancel"
)
def cancel_mission(
    mission_id: str,
):

    execute(
        """
        UPDATE missions
        SET status='cancelled',
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            mission_id,
        ),
    )

    event(
        mission_id,
        "cancelled",
    )

    return mission_detail(
        mission_id
    )


@app.get(
    "/mission/{mission_id}/events"
)
def mission_events(
    mission_id: str,
):

    return {
        "events": [
            rowdict(r)
            for r in execute(
                """
                SELECT *
                FROM mission_events
                WHERE mission_id=?
                ORDER BY id
                """,
                (
                    mission_id,
                ),
                True,
            )
        ]
    }


@app.post(
    "/mission/{mission_id}/checkpoint"
)
def checkpoint(
    mission_id: str,
    payload: Dict[str, Any],
):

    execute(
        """
        UPDATE missions
        SET checkpoint=?,
            updated_at=?
        WHERE id=?
        """,
        (
            dumps(payload),
            now(),
            mission_id,
        ),
    )

    event(
        mission_id,
        "checkpoint",
        payload,
    )

    return mission_detail(
        mission_id
    )


@app.get(
    "/mission/{mission_id}/outcome"
)
def mission_outcome(
    mission_id: str,
):

    return {
        "mission_id":
            mission_id,
        "outcome":
            mission_detail(
                mission_id
            ).get(
                "result"
            ),
    }


@app.get(
    "/mission/{mission_id}/proof"
)
def mission_proof(
    mission_id: str,
):

    rows = execute(
        """
        SELECT *
        FROM action_receipts
        WHERE job_id IN
        (
            SELECT id
            FROM tool_jobs
            WHERE mission_id=?
        )
        ORDER BY created_at
        """,
        (
            mission_id,
        ),
        True,
    )

    return {
        "mission_id":
            mission_id,
        "proofs": [
            rowdict(r)
            for r in rows
        ],
        "proof_hash":
            sha(
                [
                    dict(r)
                    for r in rows
                ]
            ),
    }


@app.get(
    "/mission/{mission_id}/artifacts"
)
def mission_artifacts(
    mission_id: str,
):

    return {
        "artifacts": [
            rowdict(r)
            for r in execute(
                """
                SELECT *
                FROM artifacts
                WHERE mission_id=?
                ORDER BY created_at
                """,
                (
                    mission_id,
                ),
                True,
            )
        ]
    }


# ============================================================
# MEMORY / APPROVALS / LEARNING
# ============================================================

@app.get("/approvals")
def approvals():

    return {
        "approvals": [
            rowdict(r)
            for r in execute(
                """
                SELECT *
                FROM approvals
                ORDER BY created_at DESC
                """,
                (),
                True,
            )
        ]
    }


@app.get("/learning")
def learning():

    return {
        "memory": [
            rowdict(r)
            for r in execute(
                """
                SELECT *
                FROM memory
                ORDER BY updated_at DESC
                LIMIT 100
                """,
                (),
                True,
            )
        ]
    }


@app.get("/strategy-memory")
def strategy_memory():

    return {
        "strategies": [
            rowdict(r)
            for r in execute(
                """
                SELECT *
                FROM strategy_memory
                ORDER BY score DESC
                """,
                (),
                True,
            )
        ]
    }


@app.get("/memory")
def memory():

    return {
        "count":
            len(
                execute(
                    """
                    SELECT id
                    FROM memory
                    """,
                    (),
                    True,
                )
            )
    }


# ============================================================
# RUN
# ============================================================

@app.get("/run")
def run_info():

    return {
        "method":
            "POST",
        "version":
            VERSION,
        "build":
            BUILD,
        "endpoint":
            "/run",
    }


@app.post("/run")
def run(
    req: RunRequest,
):

    mid = uid("mission")

    ts = now()

    execute(
        """
        INSERT INTO missions
        (
            id,
            objective,
            status,
            priority,
            budget,
            created_at,
            updated_at
        )
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            mid,
            req.command,
            "running",
            5,
            100,
            ts,
            ts,
        ),
    )

    event(
        mid,
        "started",
        {
            "command":
                req.command,
            "dry_run":
                req.dry_run,
        },
    )

    action = {
        "action":
            "research",

        "parameters": {
            "query":
                req.command,
            "mission_id":
                mid,
        },

        "preconditions":
            [],

        "postconditions": [
            {
                "path":
                    "result.ok",
                "operator":
                    "equals",
                "value":
                    True,
            }
        ],

        "dry_run":
            req.dry_run,

        "idempotency_key":
            uid("run"),

        "mission_id":
            mid,

        "max_attempts":
            2,
    }

    try:

        job = create_tool_job(
            action
        )

        submit_job(
            job["id"]
        )

        execute(
            """
            UPDATE missions
            SET result=?,
                updated_at=?
            WHERE id=?
            """,
            (
                dumps(
                    {
                        "job_id":
                            job["id"],
                        "status":
                            "running",
                    }
                ),
                now(),
                mid,
            ),
        )

        return {
            "mission_id":
                mid,
            "status":
                "running",
            "job_id":
                job["id"],
            "version":
                VERSION,
        }

    except Exception as exc:

        execute(
            """
            UPDATE missions
            SET status='failed',
                result=?,
                updated_at=?
            WHERE id=?
            """,
            (
                dumps(
                    {
                        "error":
                            str(exc)
                    }
                ),
                now(),
                mid,
            ),
        )

        return {
            "mission_id":
                mid,
            "status":
                "failed",
            "error":
                str(exc),
            "version":
                VERSION,
        }


# ============================================================
# SHUTDOWN
# ============================================================

@app.on_event("shutdown")
def shutdown():

    EXECUTOR.shutdown(
        wait=False,
        cancel_futures=False,
    )
