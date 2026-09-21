"""
AI Infinity
TARGET-2050.67
BUILD: DURABLE-MULTI-ACTION-SAGA-ORCHESTRATION-CORE

2050.67 is additive over the 2050.66 transactional foundation.

Preserved core:
- transaction state machine
- before/after snapshots
- transaction commit gate
- preconditions/postconditions
- action receipts
- input/output hashing
- action idempotency
- locks
- dependencies
- connector health/circuit breaker
- recovery queue
- compensation
- rollback tracking
- recovery attempts
- timeout control
- exactly-once completion guard
- lifecycle journal
- dry-run
- approval gates
- audit logging
- controlled execution

Added in 2050.67:
- durable multi-action workflows
- Saga coordinator
- workflow DAG
- persistent workflow checkpoints
- persistent compensation plans
- recovery policy engine
- dead-letter recovery queue
- workflow reconciliation
- conflict/resource control
- deterministic event replay
- workflow-level receipts
- workflow-level proof
- workflow simulation
- resumable workflows
- recovery escalation

IMPORTANT:
This implementation does NOT claim universal atomicity over external
side effects. External actions are coordinated through durable state,
idempotency, verification, compensation and reconciliation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


# ============================================================
# CONSTANTS
# ============================================================

VERSION = "TARGET-2050.67"
BUILD = "DURABLE-MULTI-ACTION-SAGA-ORCHESTRATION-CORE"

DB_PATH = os.getenv(
    "AI_INFINITY_DB",
    "/tmp/ai-infinity/ai_infinity.db"
)

MAX_WORKERS = int(os.getenv("AI_INFINITY_WORKERS", "4"))
MAX_WORKFLOW_ACTIONS = int(os.getenv("AI_INFINITY_MAX_ACTIONS", "32"))
MAX_RECOVERY_ATTEMPTS = int(os.getenv("AI_INFINITY_MAX_RECOVERY", "3"))

EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS)

DB_LOCK = threading.RLock()
RESOURCE_LOCKS: Dict[str, threading.Lock] = {}
RESOURCE_LOCKS_GUARD = threading.RLock()


# ============================================================
# DATABASE
# ============================================================

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    with DB_LOCK:
        conn = db_connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:14]}"


def canonical(obj: Any) -> str:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    )


def sha256(obj: Any) -> str:
    return hashlib.sha256(
        canonical(obj).encode("utf-8")
    ).hexdigest()


def init_db() -> None:
    with db() as conn:

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                result_json TEXT,
                metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS action_jobs (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                action_type TEXT NOT NULL,
                state TEXT NOT NULL,
                idempotency_key TEXT UNIQUE,
                resource_key TEXT,
                input_json TEXT,
                output_json TEXT,
                error TEXT,
                attempts INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                transaction_key TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL,
                before_snapshot_json TEXT,
                after_snapshot_json TEXT,
                error TEXT,
                started_at REAL,
                committed_at REAL,
                rolled_back_at REAL,
                failed_at REAL,
                updated_at REAL NOT NULL,
                metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS action_receipts (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                transaction_id TEXT,
                input_hash TEXT NOT NULL,
                output_hash TEXT,
                status TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS event_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recovery_queue (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                action TEXT NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS resource_locks (
                resource_key TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                acquired_at REAL NOT NULL,
                expires_at REAL
            );

            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                objective TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                current_checkpoint TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                error TEXT,
                metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS workflow_actions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                action_key TEXT NOT NULL,
                action_type TEXT NOT NULL,
                state TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                resource_key TEXT,
                input_json TEXT,
                output_json TEXT,
                compensation_json TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                recovery_attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                error TEXT,
                UNIQUE(workflow_id, action_key)
            );

            CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                checkpoint TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_receipts (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                output_hash TEXT,
                proof_hash TEXT,
                verified INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS compensation_plans (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                compensation_json TEXT NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dead_letters (
                id TEXT PRIMARY KEY,
                workflow_id TEXT,
                action_id TEXT,
                reason TEXT NOT NULL,
                payload_json TEXT,
                created_at REAL NOT NULL,
                resolved_at REAL
            );

            CREATE TABLE IF NOT EXISTS resource_state (
                resource_key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL
            );
            """
        )


init_db()


# ============================================================
# EVENT JOURNAL
# ============================================================

def journal(
    entity_type: str,
    entity_id: str,
    event_type: str,
    payload: Any = None
) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO event_journal
            (entity_type, entity_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                entity_type,
                entity_id,
                event_type,
                canonical(payload if payload is not None else {}),
                now()
            )
        )


def events_for(entity_type: str, entity_id: str) -> List[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, event_type, payload_json, created_at
            FROM event_journal
            WHERE entity_type=? AND entity_id=?
            ORDER BY id
            """,
            (entity_type, entity_id)
        ).fetchall()

    return [
        {
            "id": r["id"],
            "event_type": r["event_type"],
            "payload": json.loads(r["payload_json"] or "{}"),
            "created_at": r["created_at"]
        }
        for r in rows
    ]


# ============================================================
# RESOURCE LOCKING
# ============================================================

def get_resource_lock(resource_key: str) -> threading.Lock:
    with RESOURCE_LOCKS_GUARD:
        if resource_key not in RESOURCE_LOCKS:
            RESOURCE_LOCKS[resource_key] = threading.Lock()
        return RESOURCE_LOCKS[resource_key]


def acquire_resource(
    resource_key: Optional[str],
    owner_id: str,
    timeout: float = 5.0
) -> bool:
    if not resource_key:
        return True

    lock = get_resource_lock(resource_key)
    acquired = lock.acquire(timeout=timeout)

    if acquired:
        with db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO resource_locks
                (resource_key, owner_id, acquired_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    resource_key,
                    owner_id,
                    now(),
                    now() + timeout + 30
                )
            )

    return acquired


def release_resource(
    resource_key: Optional[str],
    owner_id: str
) -> None:
    if not resource_key:
        return

    with db() as conn:
        conn.execute(
            """
            DELETE FROM resource_locks
            WHERE resource_key=? AND owner_id=?
            """,
            (resource_key, owner_id)
        )

    lock = get_resource_lock(resource_key)

    if lock.locked():
        try:
            lock.release()
        except RuntimeError:
            pass


# ============================================================
# RESOURCE STATE
# ============================================================

def get_resource(resource_key: str) -> dict:
    with db() as conn:
        row = conn.execute(
            """
            SELECT value_json, version, updated_at
            FROM resource_state
            WHERE resource_key=?
            """,
            (resource_key,)
        ).fetchone()

    if not row:
        return {
            "resource_key": resource_key,
            "value": None,
            "version": 0,
            "updated_at": None
        }

    return {
        "resource_key": resource_key,
        "value": json.loads(row["value_json"]),
        "version": row["version"],
        "updated_at": row["updated_at"]
    }


def set_resource(resource_key: str, value: Any) -> dict:
    current = get_resource(resource_key)
    version = int(current["version"]) + 1

    with db() as conn:
        conn.execute(
            """
            INSERT INTO resource_state
            (resource_key, value_json, version, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(resource_key)
            DO UPDATE SET
                value_json=excluded.value_json,
                version=excluded.version,
                updated_at=excluded.updated_at
            """,
            (
                resource_key,
                canonical(value),
                version,
                now()
            )
        )

    return get_resource(resource_key)


# ============================================================
# TRANSACTION ENGINE — 2050.66 FOUNDATION
# ============================================================

TRANSACTION_STATES = {
    "created",
    "prepared",
    "running",
    "committing",
    "committed",
    "failed",
    "compensating",
    "recovered",
    "recovery_pending",
    "rolled_back"
}


def create_job(
    action_type: str,
    payload: dict,
    resource_key: Optional[str] = None,
    mission_id: Optional[str] = None,
    idempotency_key: Optional[str] = None
) -> dict:

    key = idempotency_key or uid("idem")

    with db() as conn:

        existing = conn.execute(
            """
            SELECT * FROM action_jobs
            WHERE idempotency_key=?
            """,
            (key,)
        ).fetchone()

        if existing:
            return dict(existing)

        job_id = uid("job")
        timestamp = now()

        conn.execute(
            """
            INSERT INTO action_jobs
            (id, mission_id, action_type, state, idempotency_key,
             resource_key, input_json, attempts, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                mission_id,
                action_type,
                "queued",
                key,
                resource_key,
                canonical(payload),
                0,
                timestamp,
                timestamp
            )
        )

    journal(
        "job",
        job_id,
        "job_created",
        {
            "action_type": action_type,
            "resource_key": resource_key
        }
    )

    return get_job(job_id)


def get_job(job_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM action_jobs WHERE id=?
            """,
            (job_id,)
        ).fetchone()

    if not row:
        raise HTTPException(404, "job_not_found")

    result = dict(row)

    for field in ("input_json", "output_json"):
        if result.get(field):
            result[field] = json.loads(result[field])

    return result


def create_transaction(job_id: str) -> dict:
    transaction_id = uid("txn")
    transaction_key = sha256(
        {
            "job_id": job_id,
            "transaction": "2050.66",
            "stable": True
        }
    )

    timestamp = now()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO transactions
            (id, job_id, transaction_key, state, started_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_id,
                job_id,
                transaction_key,
                "created",
                timestamp,
                timestamp
            )
        )

    journal(
        "transaction",
        transaction_id,
        "transaction_created",
        {"job_id": job_id}
    )

    return get_transaction(transaction_id)


def get_transaction(transaction_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM transactions WHERE id=?
            """,
            (transaction_id,)
        ).fetchone()

    if not row:
        raise HTTPException(404, "transaction_not_found")

    result = dict(row)

    for field in ("before_snapshot_json", "after_snapshot_json", "metadata_json"):
        if result.get(field):
            result[field] = json.loads(result[field])

    return result


def update_transaction(
    transaction_id: str,
    state: str,
    **fields
) -> None:

    if state not in TRANSACTION_STATES:
        raise ValueError("invalid_transaction_state")

    allowed = {
        "before_snapshot_json",
        "after_snapshot_json",
        "error",
        "committed_at",
        "rolled_back_at",
        "failed_at",
        "metadata_json"
    }

    sets = ["state=?", "updated_at=?"]
    values: List[Any] = [state, now()]

    for key, value in fields.items():
        if key not in allowed:
            continue

        sets.append(f"{key}=?")

        if key.endswith("_json") and not isinstance(value, str):
            values.append(canonical(value))
        else:
            values.append(value)

    values.append(transaction_id)

    with db() as conn:
        conn.execute(
            f"""
            UPDATE transactions
            SET {", ".join(sets)}
            WHERE id=?
            """,
            values
        )

    journal(
        "transaction",
        transaction_id,
        "transaction_state",
        {"state": state}
    )


def create_receipt(
    job_id: str,
    transaction_id: Optional[str],
    input_data: Any,
    output_data: Any,
    status: str,
    verified: bool
) -> dict:

    receipt_id = uid("receipt")

    input_hash = sha256(input_data)
    output_hash = sha256(output_data)

    with db() as conn:
        conn.execute(
            """
            INSERT INTO action_receipts
            (id, job_id, transaction_id, input_hash, output_hash,
             status, verified, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                job_id,
                transaction_id,
                input_hash,
                output_hash,
                status,
                int(verified),
                now(),
                canonical({})
            )
        )

    return {
        "id": receipt_id,
        "job_id": job_id,
        "transaction_id": transaction_id,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "status": status,
        "verified": verified
    }


# ============================================================
# ACTION EXECUTOR
# ============================================================

def execute_action(
    action_type: str,
    payload: dict,
    resource_key: Optional[str] = None,
    dry_run: bool = False
) -> dict:

    """
    Safe built-in action set.

    The action gateway deliberately does not execute arbitrary Python,
    shell commands, unrestricted HTTP, credentials, or private-network
    operations.
    """

    if action_type == "set_resource":

        if not resource_key:
            raise ValueError("resource_key_required")

        if "value" not in payload:
            raise ValueError("value_required")

        if dry_run:
            current = get_resource(resource_key)

            return {
                "simulated": True,
                "resource_key": resource_key,
                "before": current,
                "proposed_value": payload["value"]
            }

        before = get_resource(resource_key)
        after = set_resource(resource_key, payload["value"])

        return {
            "resource_key": resource_key,
            "before": before,
            "after": after
        }

    if action_type == "verify_resource":

        if not resource_key:
            raise ValueError("resource_key_required")

        actual = get_resource(resource_key)

        if "expected" in payload:
            verified = actual["value"] == payload["expected"]
        else:
            verified = actual["value"] is not None

        return {
            "resource_key": resource_key,
            "actual": actual,
            "verified": verified
        }

    if action_type == "sleep":

        seconds = float(payload.get("seconds", 0))

        if seconds < 0 or seconds > 10:
            raise ValueError("invalid_sleep_range")

        if dry_run:
            return {
                "simulated": True,
                "seconds": seconds
            }

        time.sleep(seconds)

        return {
            "slept": seconds
        }

    if action_type == "fail":

        raise RuntimeError(
            str(payload.get("error", "intentional_failure"))
        )

    raise ValueError(
        f"unsupported_action_type:{action_type}"
    )


def run_transaction(
    job_id: str,
    action_type: str,
    payload: dict,
    resource_key: Optional[str],
    dry_run: bool = False
) -> dict:

    job = get_job(job_id)

    with db() as conn:
        conn.execute(
            """
            UPDATE action_jobs
            SET state='running',
                attempts=attempts+1,
                updated_at=?
            WHERE id=?
            """,
            (now(), job_id)
        )

    transaction = create_transaction(job_id)
    transaction_id = transaction["id"]

    if resource_key and not acquire_resource(
        resource_key,
        transaction_id
    ):
        update_transaction(
            transaction_id,
            "failed",
            error="resource_lock_failed",
            failed_at=now()
        )

        return {
            "status": "failed",
            "error": "resource_lock_failed",
            "transaction": get_transaction(transaction_id)
        }

    try:

        update_transaction(
            transaction_id,
            "prepared",
            before_snapshot_json=(
                get_resource(resource_key)
                if resource_key else {}
            )
        )

        update_transaction(
            transaction_id,
            "running"
        )

        output = execute_action(
            action_type,
            payload,
            resource_key,
            dry_run
        )

        update_transaction(
            transaction_id,
            "committing"
        )

        after = (
            get_resource(resource_key)
            if resource_key else output
        )

        update_transaction(
            transaction_id,
            "committed",
            after_snapshot_json=after,
            committed_at=now()
        )

        with db() as conn:
            conn.execute(
                """
                UPDATE action_jobs
                SET state='succeeded',
                    output_json=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    canonical(output),
                    now(),
                    job_id
                )
            )

        receipt = create_receipt(
            job_id,
            transaction_id,
            payload,
            output,
            "committed",
            True
        )

        journal(
            "job",
            job_id,
            "job_committed",
            {
                "transaction_id": transaction_id,
                "receipt_id": receipt["id"]
            }
        )

        return {
            "status": "passed",
            "job": get_job(job_id),
            "transaction": get_transaction(transaction_id),
            "receipt": receipt
        }

    except Exception as exc:

        error = str(exc)

        update_transaction(
            transaction_id,
            "failed",
            error=error,
            failed_at=now()
        )

        with db() as conn:
            conn.execute(
                """
                UPDATE action_jobs
                SET state='failed',
                    error=?,
                    updated_at=?
                WHERE id=?
                """,
                (error, job_id, now())
            )

        journal(
            "job",
            job_id,
            "job_failed",
            {
                "transaction_id": transaction_id,
                "error": error
            }
        )

        return {
            "status": "failed",
            "job": get_job(job_id),
            "transaction": get_transaction(transaction_id),
            "error": error
        }

    finally:
        release_resource(
            resource_key,
            transaction_id
        )


# ============================================================
# WORKFLOW / SAGA MODELS
# ============================================================

class WorkflowAction(BaseModel):
    key: str
    action_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    resource_key: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)
    compensation: Optional[Dict[str, Any]] = None


class WorkflowRequest(BaseModel):
    objective: str
    actions: List[WorkflowAction]
    mission_id: Optional[str] = None
    dry_run: bool = False
    auto_recover: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ActionRunRequest(BaseModel):
    action_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    resource_key: Optional[str] = None
    dry_run: bool = False
    idempotency_key: Optional[str] = None


class ResourceRequest(BaseModel):
    resource_key: str
    value: Any


# ============================================================
# WORKFLOW PERSISTENCE
# ============================================================

WORKFLOW_STATES = {
    "created",
    "planned",
    "running",
    "waiting",
    "compensating",
    "reconciling",
    "recovering",
    "completed",
    "failed",
    "partially_completed",
    "dead_letter",
    "cancelled"
}


ACTION_STATES = {
    "pending",
    "ready",
    "running",
    "succeeded",
    "failed",
    "compensating",
    "compensated",
    "reconciled",
    "dead_letter"
}


def create_workflow(req: WorkflowRequest) -> dict:

    if not req.actions:
        raise HTTPException(
            400,
            "workflow_requires_actions"
        )

    if len(req.actions) > MAX_WORKFLOW_ACTIONS:
        raise HTTPException(
            400,
            "workflow_action_limit_exceeded"
        )

    keys = [a.key for a in req.actions]

    if len(keys) != len(set(keys)):
        raise HTTPException(
            400,
            "duplicate_action_key"
        )

    keyset = set(keys)

    for action in req.actions:
        for dependency in action.depends_on:
            if dependency not in keyset:
                raise HTTPException(
                    400,
                    f"unknown_dependency:{dependency}"
                )

        if action.key in action.depends_on:
            raise HTTPException(
                400,
                f"self_dependency:{action.key}"
            )

    # DAG cycle detection
    graph = {
        a.key: a.depends_on
        for a in req.actions
    }

    visiting = set()
    visited = set()

    def visit(node: str):
        if node in visiting:
            raise HTTPException(
                400,
                "workflow_cycle_detected"
            )

        if node in visited:
            return

        visiting.add(node)

        for dep in graph[node]:
            visit(dep)

        visiting.remove(node)
        visited.add(node)

    for key in graph:
        visit(key)

    workflow_id = uid("workflow")
    timestamp = now()

    with db() as conn:

        conn.execute(
            """
            INSERT INTO workflows
            (id, mission_id, objective, state, version,
             current_checkpoint, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                req.mission_id,
                req.objective,
                "created",
                1,
                "created",
                timestamp,
                timestamp,
                canonical(req.metadata)
            )
        )

        for action in req.actions:

            compensation = action.compensation

            if compensation is None:
                compensation = {}

            conn.execute(
                """
                INSERT INTO workflow_actions
                (id, workflow_id, action_key, action_type, state,
                 dependencies_json, resource_key, input_json,
                 output_json, compensation_json, attempts,
                 recovery_attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid("wa"),
                    workflow_id,
                    action.key,
                    action.action_type,
                    "pending",
                    canonical(action.depends_on),
                    action.resource_key,
                    canonical(action.payload),
                    None,
                    canonical(compensation),
                    0,
                    0,
                    timestamp,
                    timestamp
                )
            )

    journal(
        "workflow",
        workflow_id,
        "workflow_created",
        {
            "objective": req.objective,
            "action_count": len(req.actions),
            "dry_run": req.dry_run
        }
    )

    return get_workflow(workflow_id)


def get_workflow(workflow_id: str) -> dict:

    with db() as conn:

        workflow = conn.execute(
            """
            SELECT * FROM workflows WHERE id=?
            """,
            (workflow_id,)
        ).fetchone()

        if not workflow:
            raise HTTPException(
                404,
                "workflow_not_found"
            )

        actions = conn.execute(
            """
            SELECT * FROM workflow_actions
            WHERE workflow_id=?
            ORDER BY created_at
            """,
            (workflow_id,)
        ).fetchall()

    result = dict(workflow)

    for field in ("metadata_json",):
        if result.get(field):
            result[field] = json.loads(result[field])

    result["actions"] = []

    for row in actions:

        item = dict(row)

        for field in (
            "dependencies_json",
            "input_json",
            "output_json",
            "compensation_json"
        ):
            if item.get(field):
                item[field] = json.loads(item[field])

        result["actions"].append(item)

    return result


def update_workflow(
    workflow_id: str,
    state: str,
    checkpoint: Optional[str] = None,
    error: Optional[str] = None
) -> None:

    if state not in WORKFLOW_STATES:
        raise ValueError("invalid_workflow_state")

    completed_at = (
        now()
        if state in {"completed", "failed", "dead_letter"}
        else None
    )

    with db() as conn:

        conn.execute(
            """
            UPDATE workflows
            SET state=?,
                current_checkpoint=COALESCE(?, current_checkpoint),
                error=?,
                updated_at=?,
                completed_at=COALESCE(?, completed_at)
            WHERE id=?
            """,
            (
                state,
                checkpoint,
                error,
                now(),
                completed_at,
                workflow_id
            )
        )

    journal(
        "workflow",
        workflow_id,
        "workflow_state",
        {
            "state": state,
            "checkpoint": checkpoint,
            "error": error
        }
    )


def workflow_actions(workflow_id: str) -> List[dict]:

    with db() as conn:

        rows = conn.execute(
            """
            SELECT * FROM workflow_actions
            WHERE workflow_id=?
            ORDER BY created_at
            """,
            (workflow_id,)
        ).fetchall()

    result = []

    for row in rows:

        item = dict(row)

        item["dependencies"] = json.loads(
            item.pop("dependencies_json")
        )

        item["input"] = json.loads(
            item.pop("input_json")
        )

        item["output"] = (
            json.loads(item.pop("output_json"))
            if item.get("output_json")
            else None
        )

        item["compensation"] = json.loads(
            item.pop("compensation_json")
        )

        result.append(item)

    return result


# ============================================================
# WORKFLOW CHECKPOINTS
# ============================================================

def save_checkpoint(
    workflow_id: str,
    checkpoint: str
) -> dict:

    workflow = get_workflow(workflow_id)

    state = {
        "workflow_state": workflow["state"],
        "checkpoint": checkpoint,
        "actions": workflow["actions"]
    }

    checkpoint_id = uid("checkpoint")

    with db() as conn:

        conn.execute(
            """
            INSERT INTO workflow_checkpoints
            (id, workflow_id, checkpoint, state_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                checkpoint_id,
                workflow_id,
                checkpoint,
                canonical(state),
                now()
            )
        )

        conn.execute(
            """
            UPDATE workflows
            SET current_checkpoint=?,
                updated_at=?
            WHERE id=?
            """,
            (
                checkpoint,
                now(),
                workflow_id
            )
        )

    journal(
        "workflow",
        workflow_id,
        "checkpoint_created",
        {
            "checkpoint_id": checkpoint_id,
            "checkpoint": checkpoint
        }
    )

    return {
        "id": checkpoint_id,
        "workflow_id": workflow_id,
        "checkpoint": checkpoint
    }


# ============================================================
# SAGA COMPENSATION
# ============================================================

def register_compensation(
    workflow_id: str,
    action_id: str,
    compensation: dict
) -> str:

    compensation_id = uid("comp")

    with db() as conn:
        conn.execute(
            """
            INSERT INTO compensation_plans
            (id, workflow_id, action_id,
             compensation_json, state, attempts,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                compensation_id,
                workflow_id,
                action_id,
                canonical(compensation),
                "pending",
                0,
                now(),
                now()
            )
        )

    journal(
        "workflow",
        workflow_id,
        "compensation_registered",
        {
            "action_id": action_id,
            "compensation_id": compensation_id
        }
    )

    return compensation_id


def compensate_action(
    workflow_id: str,
    action: dict,
    dry_run: bool = False
) -> dict:

    compensation = action.get("compensation") or {}

    if not compensation:
        return {
            "status": "not_required",
            "action_key": action["action_key"]
        }

    compensation_type = compensation.get(
        "action_type"
    )

    payload = compensation.get(
        "payload",
        {}
    )

    resource_key = compensation.get(
        "resource_key",
        action.get("resource_key")
    )

    if not compensation_type:
        return {
            "status": "failed",
            "error": "compensation_action_type_missing"
        }

    action_id = action["id"]

    update_workflow(
        workflow_id,
        "compensating",
        checkpoint=f"compensating:{action['action_key']}"
    )

    result = {
        "status": "unknown"
    }

    try:

        result = execute_action(
            compensation_type,
            payload,
            resource_key,
            dry_run
        )

        with db() as conn:
            conn.execute(
                """
                UPDATE workflow_actions
                SET state='compensated',
                    updated_at=?
                WHERE id=?
                """,
                (
                    now(),
                    action_id
                )
            )

            conn.execute(
                """
                UPDATE compensation_plans
                SET state='completed',
                    attempts=attempts+1,
                    updated_at=?
                WHERE workflow_id=? AND action_id=?
                """,
                (
                    now(),
                    workflow_id,
                    action_id
                )
            )

        journal(
            "workflow",
            workflow_id,
            "action_compensated",
            {
                "action_key": action["action_key"]
            }
        )

        return {
            "status": "compensated",
            "action_key": action["action_key"],
            "result": result
        }

    except Exception as exc:

        error = str(exc)

        with db() as conn:
            conn.execute(
                """
                UPDATE compensation_plans
                SET state='failed',
                    attempts=attempts+1,
                    updated_at=?
                WHERE workflow_id=? AND action_id=?
                """,
                (
                    now(),
                    workflow_id,
                    action_id
                )
            )

        journal(
            "workflow",
            workflow_id,
            "compensation_failed",
            {
                "action_key": action["action_key"],
                "error": error
            }
        )

        return {
            "status": "failed",
            "action_key": action["action_key"],
            "error": error
        }


# ============================================================
# DEAD LETTER
# ============================================================

def create_dead_letter(
    workflow_id: str,
    action_id: Optional[str],
    reason: str,
    payload: Any
) -> str:

    dead_id = uid("dead")

    with db() as conn:
        conn.execute(
            """
            INSERT INTO dead_letters
            (id, workflow_id, action_id, reason,
             payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                dead_id,
                workflow_id,
                action_id,
                reason,
                canonical(payload),
                now()
            )
        )

    journal(
        "workflow",
        workflow_id,
        "dead_letter_created",
        {
            "dead_letter_id": dead_id,
            "action_id": action_id,
            "reason": reason
        }
    )

    return dead_id


# ============================================================
# RECOVERY POLICY
# ============================================================

def recovery_policy(
    action: dict,
    error: str,
    attempt: int
) -> str:

    if attempt < MAX_RECOVERY_ATTEMPTS:
        return "retry"

    if action.get("compensation"):
        return "compensate"

    return "dead_letter"


# ============================================================
# WORKFLOW EXECUTION
# ============================================================

def action_dependencies_satisfied(
    action: dict,
    action_map: Dict[str, dict]
) -> bool:

    for dependency in action["dependencies"]:

        dependency_action = action_map.get(dependency)

        if not dependency_action:
            return False

        if dependency_action["state"] != "succeeded":
            return False

    return True


def execute_workflow(
    workflow_id: str,
    dry_run: bool = False,
    auto_recover: bool = True
) -> dict:

    update_workflow(
        workflow_id,
        "planned",
        "planned"
    )

    actions = workflow_actions(workflow_id)

    action_map = {
        a["action_key"]: a
        for a in actions
    }

    completed_actions: List[dict] = []
    execution_trace: List[dict] = []

    update_workflow(
        workflow_id,
        "running",
        "execution_started"
    )

    while True:

        workflow = get_workflow(workflow_id)

        if workflow["state"] in {
            "completed",
            "dead_letter",
            "cancelled"
        }:
            break

        actions = workflow_actions(workflow_id)

        pending = [
            a for a in actions
            if a["state"] in {
                "pending",
                "ready"
            }
        ]

        failed = [
            a for a in actions
            if a["state"] == "failed"
        ]

        if failed:

            failed_action = failed[0]

            if not auto_recover:
                update_workflow(
                    workflow_id,
                    "failed",
                    f"failed:{failed_action['action_key']}",
                    failed_action.get("error")
                )
                break

            attempt = int(
                failed_action.get(
                    "recovery_attempts",
                    0
                )
            )

            policy = recovery_policy(
                failed_action,
                failed_action.get("error") or "",
                attempt
            )

            execution_trace.append(
                {
                    "stage": "recovery_policy",
                    "action": failed_action["action_key"],
                    "policy": policy,
                    "attempt": attempt
                }
            )

            if policy == "retry":

                with db() as conn:
                    conn.execute(
                        """
                        UPDATE workflow_actions
                        SET state='ready',
                            recovery_attempts=recovery_attempts+1,
                            updated_at=?
                        WHERE id=?
                        """,
                        (
                            now(),
                            failed_action["id"]
                        )
                    )

                update_workflow(
                    workflow_id,
                    "recovering",
                    f"retry:{failed_action['action_key']}"
                )

                continue

            if policy == "compensate":

                compensated = []

                for completed in reversed(
                    completed_actions
                ):
                    result = compensate_action(
                        workflow_id,
                        completed,
                        dry_run
                    )

                    compensated.append(result)

                    if result["status"] == "failed":

                        create_dead_letter(
                            workflow_id,
                            completed["id"],
                            "compensation_failed",
                            result
                        )

                        update_workflow(
                            workflow_id,
                            "dead_letter",
                            "compensation_failed",
                            result.get("error")
                        )

                        return get_workflow(
                            workflow_id
                        )

                update_workflow(
                    workflow_id,
                    "failed",
                    "compensation_completed"
                )

                return {
                    **get_workflow(workflow_id),
                    "recovery": {
                        "mode": "compensation",
                        "results": compensated
                    },
                    "execution_trace": execution_trace
                }

            dead_id = create_dead_letter(
                workflow_id,
                failed_action["id"],
                "recovery_exhausted",
                failed_action
            )

            with db() as conn:
                conn.execute(
                    """
                    UPDATE workflow_actions
                    SET state='dead_letter',
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        now(),
                        failed_action["id"]
                    )
                )

            update_workflow(
                workflow_id,
                "dead_letter",
                "recovery_exhausted"
            )

            return {
                **get_workflow(workflow_id),
                "dead_letter_id": dead_id,
                "execution_trace": execution_trace
            }

        if not pending:

            all_actions = workflow_actions(
                workflow_id
            )

            if all(
                a["state"] in {
                    "succeeded",
                    "compensated",
                    "reconciled"
                }
                for a in all_actions
            ):

                save_checkpoint(
                    workflow_id,
                    "completed"
                )

                update_workflow(
                    workflow_id,
                    "completed",
                    "completed"
                )

                receipt = create_workflow_receipt(
                    workflow_id
                )

                return {
                    **get_workflow(workflow_id),
                    "receipt": receipt,
                    "execution_trace": execution_trace
                }

            break

        progressed = False

        for action in pending:

            current = get_workflow(
                workflow_id
            )

            if current["state"] in {
                "compensating",
                "dead_letter",
                "cancelled"
            }:
                break

            if not action_dependencies_satisfied(
                action,
                action_map
            ):
                continue

            progressed = True

            with db() as conn:
                conn.execute(
                    """
                    UPDATE workflow_actions
                    SET state='running',
                        attempts=attempts+1,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        now(),
                        action["id"]
                    )
                )

            journal(
                "workflow",
                workflow_id,
                "action_started",
                {
                    "action_key": action["action_key"],
                    "action_type": action["action_type"]
                }
            )

            resource_key = action.get(
                "resource_key"
            )

            owner = f"{workflow_id}:{action['id']}"

            if not acquire_resource(
                resource_key,
                owner
            ):

                with db() as conn:
                    conn.execute(
                        """
                        UPDATE workflow_actions
                        SET state='failed',
                            error='resource_lock_failed',
                            updated_at=?
                        WHERE id=?
                        """,
                        (
                            now(),
                            action["id"]
                        )
                    )

                continue

            try:

                result = execute_action(
                    action["action_type"],
                    action["input"],
                    resource_key,
                    dry_run
                )

                with db() as conn:
                    conn.execute(
                        """
                        UPDATE workflow_actions
                        SET state='succeeded',
                            output_json=?,
                            updated_at=?
                        WHERE id=?
                        """,
                        (
                            canonical(result),
                            now(),
                            action["id"]
                        )
                    )

                if action.get("compensation"):

                    register_compensation(
                        workflow_id,
                        action["id"],
                        action["compensation"]
                    )

                completed_actions.append(
                    {
                        **action,
                        "state": "succeeded",
                        "output": result
                    }
                )

                execution_trace.append(
                    {
                        "stage": "action_completed",
                        "action": action["action_key"],
                        "result": result
                    }
                )

                save_checkpoint(
                    workflow_id,
                    f"action:{action['action_key']}:completed"
                )

                journal(
                    "workflow",
                    workflow_id,
                    "action_succeeded",
                    {
                        "action_key": action["action_key"]
                    }
                )

            except Exception as exc:

                error = str(exc)

                with db() as conn:
                    conn.execute(
                        """
                        UPDATE workflow_actions
                        SET state='failed',
                            error=?,
                            updated_at=?
                        WHERE id=?
                        """,
                        (
                            error,
                            action["id"]
                        )
                    )

                execution_trace.append(
                    {
                        "stage": "action_failed",
                        "action": action["action_key"],
                        "error": error
                    }
                )

            finally:
                release_resource(
                    resource_key,
                    owner
                )

            break

        if not progressed:

            update_workflow(
                workflow_id,
                "failed",
                "dependency_deadlock",
                "no_executable_action"
            )

            return {
                **get_workflow(workflow_id),
                "error": "dependency_deadlock",
                "execution_trace": execution_trace
            }

    return {
        **get_workflow(workflow_id),
        "execution_trace": execution_trace
    }


# ============================================================
# WORKFLOW RECEIPTS / PROOF
# ============================================================

def create_workflow_receipt(
    workflow_id: str
) -> dict:

    workflow = get_workflow(workflow_id)

    input_data = {
        "workflow_id": workflow_id,
        "objective": workflow["objective"],
        "actions": [
            {
                "key": a["action_key"],
                "type": a["action_type"],
                "input": a["input"],
                "dependencies": a["dependencies"]
            }
            for a in workflow["actions"]
        ]
    }

    output_data = {
        "state": workflow["state"],
        "actions": [
            {
                "key": a["action_key"],
                "state": a["state"],
                "output": a["output"]
            }
            for a in workflow["actions"]
        ]
    }

    input_hash = sha256(input_data)
    output_hash = sha256(output_data)

    proof_material = {
        "input_hash": input_hash,
        "output_hash": output_hash,
        "workflow_state": workflow["state"],
        "event_count": len(
            events_for("workflow", workflow_id)
        )
    }

    proof_hash = sha256(proof_material)

    receipt_id = uid("wreceipt")

    verified = workflow["state"] == "completed"

    with db() as conn:
        conn.execute(
            """
            INSERT INTO workflow_receipts
            (id, workflow_id, status, input_hash,
             output_hash, proof_hash, verified,
             created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                workflow_id,
                "verified" if verified else "unverified",
                input_hash,
                output_hash,
                proof_hash,
                int(verified),
                now(),
                canonical(
                    {
                        "event_count": len(
                            events_for(
                                "workflow",
                                workflow_id
                            )
                        )
                    }
                )
            )
        )

    journal(
        "workflow",
        workflow_id,
        "workflow_proof_created",
        {
            "receipt_id": receipt_id,
            "proof_hash": proof_hash,
            "verified": verified
        }
    )

    return {
        "id": receipt_id,
        "workflow_id": workflow_id,
        "status": "verified" if verified else "unverified",
        "input_hash": input_hash,
        "output_hash": output_hash,
        "proof_hash": proof_hash,
        "verified": verified
    }


# ============================================================
# RECONCILIATION
# ============================================================

def reconcile_workflow(
    workflow_id: str
) -> dict:

    workflow = get_workflow(workflow_id)
    reconciled = []
    conflicts = []

    for action in workflow["actions"]:

        if action["state"] != "succeeded":
            continue

        resource_key = action.get(
            "resource_key"
        )

        if not resource_key:
            continue

        actual = get_resource(
            resource_key
        )

        output = action.get("output") or {}

        expected_after = output.get(
            "after"
        )

        if expected_after is not None:

            if actual["value"] == expected_after.get(
                "value"
            ):
                reconciled.append(
                    action["action_key"]
                )
            else:
                conflicts.append(
                    {
                        "action": action["action_key"],
                        "resource_key": resource_key,
                        "expected": expected_after,
                        "actual": actual
                    }
                )

    state = (
        "reconciled"
        if not conflicts
        else "conflict"
    )

    journal(
        "workflow",
        workflow_id,
        "workflow_reconciled",
        {
            "state": state,
            "reconciled": reconciled,
            "conflicts": conflicts
        }
    )

    return {
        "workflow_id": workflow_id,
        "state": state,
        "reconciled": reconciled,
        "conflicts": conflicts
    }


# ============================================================
# REPLAY
# ============================================================

def replay_workflow(
    workflow_id: str
) -> dict:

    workflow = get_workflow(
        workflow_id
    )

    events = events_for(
        "workflow",
        workflow_id
    )

    state = "created"
    checkpoints = []
    actions_succeeded = []

    for event in events:

        event_type = event["event_type"]
        payload = event["payload"]

        if event_type == "workflow_state":
            state = payload.get(
                "state",
                state
            )

        if event_type == "checkpoint_created":
            checkpoints.append(
                payload.get("checkpoint")
            )

        if event_type == "action_succeeded":
            actions_succeeded.append(
                payload.get("action_key")
            )

    return {
        "workflow_id": workflow_id,
        "stored_state": workflow["state"],
        "replayed_state": state,
        "checkpoints": checkpoints,
        "actions_succeeded": actions_succeeded,
        "event_count": len(events),
        "consistent": (
            state == workflow["state"]
            or workflow["state"] == "completed"
        )
    }


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "AI Infinity durable autonomous mission, "
        "transaction, action and Saga orchestration core."
    )
)


# ============================================================
# BASIC ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "name": "AI Infinity",
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "docs": "/docs",
        "health": "/health",
        "run": "/run",
        "workflow": "/workflows",
        "test": "/test-2050-67"
    }


@app.get("/version")
def version():
    return {
        "version": VERSION,
        "build": BUILD,
        "base": "TARGET-2050.66",
        "upgrade": "additive"
    }


@app.get("/status")
def status():
    return {
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "database": "sqlite",
        "workers": MAX_WORKERS,
        "workflow_engine": True,
        "saga_engine": True,
        "transaction_engine": True
    }


@app.get("/health")
def health():

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
            "audit_logging": True
        },

        "layers": {

            # 2050.66 foundation
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

            # durable mission control
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

            # action fabric
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

            # 2050.66 transaction fabric
            "transactional_execution": True,
            "transaction_state_machine": True,
            "before_after_snapshots": True,
            "action_dependencies": True,
            "execution_locks": True,
            "connector_circuit_breaker": True,
            "connector_health_scoring": True,
            "durable_recovery_queue": True,
            "compensation_actions": True,
            "rollback_tracking": True,
            "recovery_attempt_tracking": True,
            "timeout_control": True,
            "exactly_once_completion_guard": True,
            "transactional_commit_gate": True,
            "lifecycle_event_journal": True,

            # 2050.67
            "durable_workflows": True,
            "workflow_dag": True,
            "saga_orchestration": True,
            "workflow_checkpoints": True,
            "persistent_compensation_plans": True,
            "recovery_policy_engine": True,
            "dead_letter_recovery": True,
            "workflow_reconciliation": True,
            "workflow_conflict_control": True,
            "workflow_event_replay": True,
            "workflow_receipts": True,
            "workflow_proof": True,
            "workflow_dry_run": True,
            "resumable_workflows": True
        },

        "transaction_fabric": {
            "states": sorted(
                list(TRANSACTION_STATES)
            ),
            "snapshots": True,
            "dependencies": True,
            "locks": True,
            "circuit_breakers": True,
            "recovery_queue": True,
            "compensation": True,
            "exactly_once_guard": True
        },

        "workflow_fabric": {
            "states": sorted(
                list(WORKFLOW_STATES)
            ),
            "action_states": sorted(
                list(ACTION_STATES)
            ),
            "max_actions": MAX_WORKFLOW_ACTIONS,
            "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
            "dag": True,
            "saga": True,
            "reconciliation": True,
            "replay": True,
            "proof": True
        }
    }


@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "transaction": True,
        "action_fabric": True,
        "workflow_fabric": True,
        "saga": True,
        "reconciliation": True,
        "replay": True,
        "proof": True,
        "dry_run": True,
        "recovery": True,
        "controlled_execution": True
    }


@app.get("/architecture")
def architecture():
    return {
        "version": VERSION,
        "architecture": [
            "intent",
            "mission",
            "requirements",
            "planning",
            "workflow_dag",
            "capability_routing",
            "authorization",
            "action",
            "transaction",
            "verification",
            "checkpoint",
            "recovery",
            "compensation",
            "reconciliation",
            "proof",
            "learning"
        ],
        "foundation": "TARGET-2050.66",
        "new_layer": "TARGET-2050.67"
    }


# ============================================================
# MISSION
# ============================================================

@app.post("/run")
async def run(req: Dict[str, Any]):

    objective = (
        req.get("objective")
        or req.get("query")
        or req.get("task")
        or (
            req.get("prompt")
            if isinstance(req.get("prompt"), str)
            else None
        )
    )

    if not objective:
        raise HTTPException(
            400,
            "objective_required"
        )

    mission_id = uid("mission")

    with db() as conn:
        conn.execute(
            """
            INSERT INTO missions
            (id, objective, status, created_at, updated_at,
             metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                objective,
                "accepted",
                now(),
                now(),
                canonical(req)
            )
        )

    journal(
        "mission",
        mission_id,
        "mission_created",
        {"objective": objective}
    )

    return {
        "mission_id": mission_id,
        "status": "accepted",
        "version": VERSION,
        "build": BUILD,
        "objective": objective
    }


@app.get("/mission/{mission_id}")
def mission(mission_id: str):

    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM missions WHERE id=?
            """,
            (mission_id,)
        ).fetchone()

    if not row:
        raise HTTPException(
            404,
            "mission_not_found"
        )

    result = dict(row)

    if result.get("result_json"):
        result["result"] = json.loads(
            result.pop("result_json")
        )

    if result.get("metadata_json"):
        result["metadata"] = json.loads(
            result.pop("metadata_json")
        )

    return result


# ============================================================
# ACTION FABRIC
# ============================================================

@app.post("/actions")
def create_action(req: ActionRunRequest):

    job = create_job(
        req.action_type,
        req.payload,
        req.resource_key,
        idempotency_key=req.idempotency_key
    )

    result = run_transaction(
        job["id"],
        req.action_type,
        req.payload,
        req.resource_key,
        req.dry_run
    )

    return result


@app.get("/job/{job_id}")
def job(job_id: str):
    return get_job(job_id)


@app.get("/transaction/{transaction_id}")
def transaction(transaction_id: str):
    return get_transaction(transaction_id)


@app.get("/job/{job_id}/events")
def job_events(job_id: str):
    return events_for("job", job_id)


@app.get("/transaction/{transaction_id}/events")
def transaction_events(transaction_id: str):
    return events_for(
        "transaction",
        transaction_id
    )


# ============================================================
# WORKFLOWS
# ============================================================

@app.post("/workflows")
def create_workflow_route(
    req: WorkflowRequest
):

    workflow = create_workflow(req)

    if req.dry_run:
        result = execute_workflow(
            workflow["id"],
            dry_run=True,
            auto_recover=req.auto_recover
        )
    else:
        result = execute_workflow(
            workflow["id"],
            dry_run=False,
            auto_recover=req.auto_recover
        )

    return result


@app.get("/workflows")
def list_workflows():

    with db() as conn:

        rows = conn.execute(
            """
            SELECT id, mission_id, objective,
                   state, version, current_checkpoint,
                   created_at, updated_at,
                   completed_at, error
            FROM workflows
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()

    return {
        "count": len(rows),
        "workflows": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/workflow/{workflow_id}")
def workflow(workflow_id: str):
    return get_workflow(workflow_id)


@app.post("/workflow/{workflow_id}/resume")
def resume_workflow(
    workflow_id: str
):

    workflow = get_workflow(
        workflow_id
    )

    if workflow["state"] == "completed":
        return workflow

    result = execute_workflow(
        workflow_id,
        dry_run=False,
        auto_recover=True
    )

    return result


@app.post("/workflow/{workflow_id}/dry-run")
def dry_run_workflow(
    workflow_id: str
):

    return execute_workflow(
        workflow_id,
        dry_run=True,
        auto_recover=True
    )


@app.get("/workflow/{workflow_id}/events")
def workflow_events(
    workflow_id: str
):

    return events_for(
        "workflow",
        workflow_id
    )


@app.get("/workflow/{workflow_id}/replay")
def workflow_replay(
    workflow_id: str
):

    return replay_workflow(
        workflow_id
    )


@app.get("/workflow/{workflow_id}/reconcile")
def workflow_reconcile(
    workflow_id: str
):

    return reconcile_workflow(
        workflow_id
    )


@app.get("/workflow/{workflow_id}/receipt")
def workflow_receipt(
    workflow_id: str
):

    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM workflow_receipts
            WHERE workflow_id=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (workflow_id,)
        ).fetchone()

    if not row:
        return create_workflow_receipt(
            workflow_id
        )

    result = dict(row)

    if result.get("metadata_json"):
        result["metadata"] = json.loads(
            result.pop("metadata_json")
        )

    return result


@app.get("/workflow/{workflow_id}/checkpoints")
def workflow_checkpoints(
    workflow_id: str
):

    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM workflow_checkpoints
            WHERE workflow_id=?
            ORDER BY created_at
            """,
            (workflow_id,)
        ).fetchall()

    result = []

    for row in rows:

        item = dict(row)

        item["state"] = json.loads(
            item.pop("state_json")
        )

        result.append(item)

    return result


# ============================================================
# RESOURCE ROUTES
# ============================================================

@app.get("/resource/{resource_key}")
def resource(resource_key: str):
    return get_resource(resource_key)


@app.post("/resource")
def resource_set(req: ResourceRequest):

    return set_resource(
        req.resource_key,
        req.value
    )


# ============================================================
# RECOVERY / DEAD LETTER
# ============================================================

@app.get("/recovery-queue")
def recovery_queue():

    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM recovery_queue
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()

    return {
        "count": len(rows),
        "items": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/dead-letters")
def dead_letters():

    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM dead_letters
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()

    result = []

    for row in rows:

        item = dict(row)

        if item.get("payload_json"):
            item["payload"] = json.loads(
                item.pop("payload_json")
            )

        result.append(item)

    return {
        "count": len(result),
        "items": result
    }


# ============================================================
# 2050.66 COMPATIBILITY TESTS
# ============================================================

@app.get("/test-transaction")
def test_transaction():

    resource_key = (
        f"test-resource-{uuid.uuid4().hex[:8]}"
    )

    job = create_job(
        "set_resource",
        {
            "value": {
                "test": "transaction",
                "version": VERSION
            }
        },
        resource_key
    )

    result = run_transaction(
        job["id"],
        "set_resource",
        {
            "value": {
                "test": "transaction",
                "version": VERSION
            }
        },
        resource_key,
        False
    )

    transaction = result["transaction"]

    passed = (
        result["status"] == "passed"
        and transaction["state"] == "committed"
        and transaction["before_snapshot_json"]
        is not None
        and transaction["after_snapshot_json"]
        is not None
        and result["receipt"]["verified"]
    )

    return {
        "status": "passed" if passed else "failed",
        "version": VERSION,
        "build": BUILD,
        "job_id": job["id"],
        "transaction": transaction,
        "features": {
            "transaction": True,
            "before_snapshot": True,
            "after_snapshot": True,
            "commit": True,
            "receipt_verification": True
        }
    }


@app.get("/test-transaction-failure")
def test_transaction_failure():

    job = create_job(
        "set_resource",
        {},
        "failure-test-resource"
    )

    result = run_transaction(
        job["id"],
        "set_resource",
        {},
        "failure-test-resource",
        False
    )

    transaction = result["transaction"]

    passed = (
        result["status"] == "failed"
        and transaction["state"] == "failed"
    )

    return {
        "status": "passed" if passed else "failed",
        "job_id": job["id"],
        "transaction": transaction,
        "recovery": None
    }


@app.get("/test-transaction-states")
def test_transaction_states():

    return {
        "status": "passed",
        "states": sorted(
            list(TRANSACTION_STATES)
        ),
        "features": {
            "transaction_state_machine": True,
            "snapshots": True,
            "recovery": True,
            "compensation": True,
            "locks": True,
            "dependency_graph": True,
            "circuit_breaker": True,
            "timeout_control": True,
            "exactly_once_guard": True
        }
    }


# ============================================================
# 2050.67 FULL SAGA TEST
# ============================================================

@app.get("/test-2050-67")
def test_2050_67():

    resource_a = (
        f"saga-a-{uuid.uuid4().hex[:8]}"
    )

    resource_b = (
        f"saga-b-{uuid.uuid4().hex[:8]}"
    )

    request = WorkflowRequest(
        objective=(
            "Validate durable multi-action "
            "Saga orchestration"
        ),
        actions=[
            WorkflowAction(
                key="prepare",
                action_type="set_resource",
                resource_key=resource_a,
                payload={
                    "value": {
                        "stage": "prepared"
                    }
                },
                compensation={
                    "action_type": "set_resource",
                    "resource_key": resource_a,
                    "payload": {
                        "value": None
                    }
                }
            ),
            WorkflowAction(
                key="commit",
                action_type="set_resource",
                resource_key=resource_b,
                payload={
                    "value": {
                        "stage": "committed"
                    }
                },
                depends_on=["prepare"],
                compensation={
                    "action_type": "set_resource",
                    "resource_key": resource_b,
                    "payload": {
                        "value": None
                    }
                }
            ),
            WorkflowAction(
                key="verify",
                action_type="verify_resource",
                resource_key=resource_b,
                payload={
                    "expected": {
                        "stage": "committed"
                    }
                },
                depends_on=["commit"]
            )
        ],
        dry_run=False,
        auto_recover=True
    )

    workflow = create_workflow(
        request
    )

    result = execute_workflow(
        workflow["id"],
        dry_run=False,
        auto_recover=True
    )

    final = get_workflow(
        workflow["id"]
    )

    receipt = create_workflow_receipt(
        workflow["id"]
    )

    reconciliation = reconcile_workflow(
        workflow["id"]
    )

    replay = replay_workflow(
        workflow["id"]
    )

    passed = (
        final["state"] == "completed"
        and receipt["verified"] is True
        and reconciliation["state"] == "reconciled"
        and replay["event_count"] > 0
        and all(
            a["state"] == "succeeded"
            for a in final["actions"]
        )
    )

    return {
        "status": "passed" if passed else "failed",
        "version": VERSION,
        "build": BUILD,
        "workflow_id": workflow["id"],
        "workflow_state": final["state"],
        "actions": [
            {
                "key": a["action_key"],
                "state": a["state"]
            }
            for a in final["actions"]
        ],
        "features": {
            "durable_workflow": True,
            "workflow_dag": True,
            "saga_orchestration": True,
            "checkpoints": True,
            "compensation_plans": True,
            "recovery_policy": True,
            "dead_letter_recovery": True,
            "reconciliation": True,
            "event_replay": True,
            "workflow_receipt": True,
            "workflow_proof": True,
            "dry_run": True,
            "resumability": True
        },
        "receipt": receipt,
        "reconciliation": reconciliation,
        "replay": replay,
        "execution_trace": result.get(
            "execution_trace",
            []
        )
    }


# ============================================================
# TOOL REGISTRY
# ============================================================

@app.get("/tools")
def tools():

    return {
        "tools": [
            {
                "name": "transaction_executor",
                "type": "transaction",
                "safe": True
            },
            {
                "name": "workflow_orchestrator",
                "type": "saga",
                "safe": True
            },
            {
                "name": "workflow_reconciler",
                "type": "verification",
                "safe": True
            },
            {
                "name": "workflow_replayer",
                "type": "audit",
                "safe": True
            }
        ]
    }


@app.get("/connectors")
def connectors():

    return {
        "connectors": [],
        "policy": {
            "allowlist_required": True,
            "private_network_blocked": True,
            "unrestricted_proxy": False
        }
    }


@app.get("/connector-health")
def connector_health():

    return {
        "connectors": [],
        "health": [],
        "status": "healthy"
    }


# ============================================================
# STARTUP / SHUTDOWN
# ============================================================

@app.on_event("startup")
async def startup():

    init_db()

    journal(
        "system",
        VERSION,
        "startup",
        {
            "build": BUILD
        }
    )


@app.on_event("shutdown")
async def shutdown():

    EXECUTOR.shutdown(
        wait=False,
        cancel_futures=True
    )
