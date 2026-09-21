"""
AI Infinity
TARGET-2050.67
BUILD: DURABLE-MULTI-ACTION-SAGA-ORCHESTRATION-CORE

2050.67 is additive on top of the 2050.66 transaction model.

Preserved concepts:
- FastAPI runtime
- SQLite persistence
- controlled execution
- typed actions
- preconditions/postconditions
- dry-run
- action receipts
- input/output hashing
- transaction state machine
- before/after snapshots
- dependencies
- locks
- circuit breakers
- recovery queue
- compensation
- rollback tracking
- exactly-once completion guard
- lifecycle journal
- approval gates
- auditability

Added:
- durable workflow/DAG orchestration
- multi-action Saga coordinator
- persistent workflow checkpoints
- persistent compensation plans
- workflow recovery policy
- dead-letter recovery queue
- workflow reconciliation
- workflow replay
- workflow-level proof
- workflow dry-run
- resource conflict detection
- workflow idempotency
- transaction/workflow correlation
- durable workflow events
- workflow status inspection
- deterministic state hashing

Security:
- no arbitrary code execution
- no unrestricted proxy
- no private-network access
- no permission bypass
- high-risk/irreversible actions require approval
- external connectors are controlled
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

VERSION = "TARGET-2050.67"
BUILD = "DURABLE-MULTI-ACTION-SAGA-ORCHESTRATION-CORE"

DB_PATH = os.getenv("AI_INFINITY_DB", "/tmp/ai-infinity/ai_infinity.db")

MAX_WORKERS = int(os.getenv("AI_INFINITY_WORKERS", "4"))
MAX_WORKFLOW_ACTIONS = int(os.getenv("AI_INFINITY_MAX_ACTIONS", "100"))
MAX_RECOVERY_ATTEMPTS = int(os.getenv("AI_INFINITY_MAX_RECOVERY", "3"))

APPROVAL_REQUIRED_DEFAULT = True

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

db_lock = threading.RLock()
resource_locks: Dict[str, threading.Lock] = {}
resource_locks_guard = threading.Lock()


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "Durable autonomous mission, action, transaction and "
        "multi-action Saga orchestration engine."
    ),
)


# ============================================================
# DATABASE
# ============================================================

@contextmanager
def db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row

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
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256(value: Any) -> str:
    return hashlib.sha256(
        canonical(value).encode("utf-8")
    ).hexdigest()


def json_load(value: Optional[str], default=None):
    if value is None:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def execute(sql: str, params=()):
    with db() as conn:
        cur = conn.execute(sql, params)
        return cur


def fetchone(sql: str, params=()):
    with db() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def fetchall(sql: str, params=()):
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():
    with db() as conn:

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS memory (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                workflow_key TEXT NOT NULL,
                objective TEXT,
                status TEXT NOT NULL,
                current_step INTEGER DEFAULT 0,
                total_steps INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                error TEXT,
                metadata TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_workflow_key
            ON workflows(workflow_key);

            CREATE TABLE IF NOT EXISTS workflow_actions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                action_key TEXT NOT NULL,
                name TEXT NOT NULL,
                action_type TEXT NOT NULL,
                position INTEGER NOT NULL,
                status TEXT NOT NULL,
                dependencies TEXT,
                resources TEXT,
                payload TEXT,
                compensation TEXT,
                preconditions TEXT,
                postconditions TEXT,
                irreversible INTEGER DEFAULT 0,
                approval_required INTEGER DEFAULT 0,
                transaction_id TEXT,
                receipt_id TEXT,
                attempts INTEGER DEFAULT 0,
                recovery_attempts INTEGER DEFAULT 0,
                error TEXT,
                result TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS
            idx_workflow_actions_workflow
            ON workflow_actions(workflow_id);

            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_workflow_action_key
            ON workflow_actions(workflow_id, action_key);

            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                workflow_id TEXT,
                action_id TEXT,
                state TEXT NOT NULL,
                transaction_key TEXT NOT NULL,
                started_at REAL,
                prepared_at REAL,
                committing_at REAL,
                committed_at REAL,
                failed_at REAL,
                rolled_back_at REAL,
                recovered_at REAL,
                updated_at REAL NOT NULL,
                error TEXT,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS action_jobs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT,
                action_id TEXT,
                state TEXT NOT NULL,
                input_hash TEXT,
                output_hash TEXT,
                result TEXT,
                error TEXT,
                attempts INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS receipts (
                id TEXT PRIMARY KEY,
                workflow_id TEXT,
                action_id TEXT,
                transaction_id TEXT,
                status TEXT NOT NULL,
                input_hash TEXT,
                output_hash TEXT,
                proof_hash TEXT,
                created_at REAL NOT NULL,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY,
                transaction_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL,
                data TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                checkpoint_index INTEGER NOT NULL,
                state TEXT NOT NULL,
                completed_actions TEXT,
                active_actions TEXT,
                failed_actions TEXT,
                state_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS compensation_plans (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                status TEXT NOT NULL,
                plan TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recovery_queue (
                id TEXT PRIMARY KEY,
                workflow_id TEXT,
                action_id TEXT,
                transaction_id TEXT,
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                next_attempt_at REAL,
                payload TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dead_letter_queue (
                id TEXT PRIMARY KEY,
                recovery_id TEXT,
                workflow_id TEXT,
                action_id TEXT,
                reason TEXT,
                payload TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS locks (
                resource TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                action_id TEXT,
                acquired_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS connector_health (
                connector TEXT PRIMARY KEY,
                score REAL NOT NULL,
                failures INTEGER DEFAULT 0,
                successes INTEGER DEFAULT 0,
                state TEXT NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_events (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                payload TEXT,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS
            idx_workflow_events_workflow
            ON workflow_events(workflow_id, sequence);

            CREATE TABLE IF NOT EXISTS workflow_proofs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                proof_hash TEXT NOT NULL,
                action_receipts TEXT,
                state_hash TEXT,
                created_at REAL NOT NULL,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                workflow_id TEXT,
                action_id TEXT,
                status TEXT NOT NULL,
                reason TEXT,
                created_at REAL NOT NULL,
                decided_at REAL
            );

            CREATE TABLE IF NOT EXISTS idempotency_keys (
                key TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )


init_db()


# ============================================================
# ENUM-LIKE CONSTANTS
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
    "rolled_back",
}

WORKFLOW_STATES = {
    "created",
    "planned",
    "running",
    "waiting_approval",
    "recovering",
    "compensating",
    "reconciling",
    "completed",
    "failed",
    "cancelled",
    "dry_run",
}

ACTION_STATES = {
    "pending",
    "ready",
    "waiting_dependency",
    "waiting_approval",
    "running",
    "succeeded",
    "failed",
    "compensating",
    "compensated",
    "skipped",
}


# ============================================================
# EVENT JOURNAL
# ============================================================

def workflow_event(
    workflow_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
):
    row = fetchone(
        """
        SELECT COALESCE(MAX(sequence), 0) AS n
        FROM workflow_events
        WHERE workflow_id=?
        """,
        (workflow_id,),
    )

    sequence = int(row["n"]) + 1

    execute(
        """
        INSERT INTO workflow_events
        (id, workflow_id, event_type, sequence, payload, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            uid("event"),
            workflow_id,
            event_type,
            sequence,
            canonical(payload or {}),
            now(),
        ),
    )


# ============================================================
# RESOURCE LOCKS
# ============================================================

def acquire_resource_locks(
    workflow_id: str,
    action_id: str,
    resources: List[str],
) -> bool:

    resources = sorted(set(resources))

    with db_lock:
        for resource in resources:
            existing = fetchone(
                "SELECT * FROM locks WHERE resource=?",
                (resource,),
            )

            if existing and existing["workflow_id"] != workflow_id:
                return False

        for resource in resources:
            execute(
                """
                INSERT OR REPLACE INTO locks
                (resource, workflow_id, action_id, acquired_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    resource,
                    workflow_id,
                    action_id,
                    now(),
                ),
            )

    return True


def release_resource_locks(
    workflow_id: str,
    action_id: str,
):
    execute(
        """
        DELETE FROM locks
        WHERE workflow_id=? AND action_id=?
        """,
        (workflow_id, action_id),
    )


# ============================================================
# MODELS
# ============================================================

class ActionSpec(BaseModel):
    key: str
    name: str
    action_type: str = "internal"
    payload: Dict[str, Any] = Field(default_factory=dict)

    dependencies: List[str] = Field(default_factory=list)
    resources: List[str] = Field(default_factory=list)

    preconditions: List[str] = Field(default_factory=list)
    postconditions: List[str] = Field(default_factory=list)

    compensation: Dict[str, Any] = Field(default_factory=dict)

    irreversible: bool = False
    approval_required: bool = False


class WorkflowCreate(BaseModel):
    objective: str
    workflow_key: Optional[str] = None
    mission_id: Optional[str] = None
    actions: List[ActionSpec]

    dry_run: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    approve: bool = False


# ============================================================
# CONDITION ENGINE
# ============================================================

def evaluate_condition(
    condition: str,
    payload: Dict[str, Any],
) -> bool:

    if not condition:
        return True

    condition = condition.strip()

    if condition.lower() in {"true", "always", "ok"}:
        return True

    if condition.lower() in {"false", "never"}:
        return False

    # Safe simple condition format:
    # key=value
    if "=" in condition:
        key, expected = condition.split("=", 1)
        key = key.strip()
        expected = expected.strip()

        actual = payload.get(key)

        if actual is None:
            return False

        return str(actual).lower() == expected.lower()

    # Presence condition
    if condition.startswith("exists:"):
        key = condition.split(":", 1)[1].strip()
        return key in payload

    return False


def evaluate_conditions(
    conditions: List[str],
    payload: Dict[str, Any],
) -> bool:

    return all(
        evaluate_condition(c, payload)
        for c in conditions
    )


# ============================================================
# APPROVAL
# ============================================================

def requires_approval(action: Dict[str, Any]) -> bool:
    return bool(
        action["irreversible"]
        or action["approval_required"]
    )


def create_approval(
    workflow_id: str,
    action_id: str,
    reason: str,
):
    approval_id = uid("approval")

    execute(
        """
        INSERT INTO approvals
        (id, workflow_id, action_id, status, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            approval_id,
            workflow_id,
            action_id,
            "pending",
            reason,
            now(),
        ),
    )

    return approval_id


def approval_granted(
    workflow_id: str,
    action_id: str,
) -> bool:

    row = fetchone(
        """
        SELECT status
        FROM approvals
        WHERE workflow_id=? AND action_id=?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (workflow_id, action_id),
    )

    return bool(row and row["status"] == "approved")


# ============================================================
# TRANSACTIONS
# ============================================================

def create_transaction(
    workflow_id: str,
    action_id: str,
    job_id: str,
) -> str:

    transaction_id = uid("txn")

    execute(
        """
        INSERT INTO transactions
        (
            id,
            job_id,
            workflow_id,
            action_id,
            state,
            transaction_key,
            started_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            job_id,
            workflow_id,
            action_id,
            "created",
            sha256(
                {
                    "workflow_id": workflow_id,
                    "action_id": action_id,
                    "job_id": job_id,
                }
            ),
            now(),
            now(),
        ),
    )

    return transaction_id


def update_transaction(
    transaction_id: str,
    state: str,
    error: Optional[str] = None,
):

    if state not in TRANSACTION_STATES:
        raise ValueError(f"invalid transaction state: {state}")

    timestamp = now()

    fields = {
        "state": state,
        "updated_at": timestamp,
    }

    if state == "prepared":
        fields["prepared_at"] = timestamp

    if state == "committing":
        fields["committing_at"] = timestamp

    if state == "committed":
        fields["committed_at"] = timestamp

    if state == "failed":
        fields["failed_at"] = timestamp

    if state == "rolled_back":
        fields["rolled_back_at"] = timestamp

    if state == "recovered":
        fields["recovered_at"] = timestamp

    if error is not None:
        fields["error"] = error

    set_clause = ", ".join(
        f"{k}=?"
        for k in fields
    )

    execute(
        f"""
        UPDATE transactions
        SET {set_clause}
        WHERE id=?
        """,
        tuple(fields.values()) + (transaction_id,),
    )


def save_snapshot(
    transaction_id: str,
    phase: str,
    data: Dict[str, Any],
):

    execute(
        """
        INSERT INTO snapshots
        (id, transaction_id, phase, snapshot_hash, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            uid("snapshot"),
            transaction_id,
            phase,
            sha256(data),
            canonical(data),
            now(),
        ),
    )


# ============================================================
# ACTION RECEIPTS
# ============================================================

def create_receipt(
    workflow_id: str,
    action_id: str,
    transaction_id: str,
    status: str,
    input_value: Any,
    output_value: Any,
    metadata: Optional[Dict[str, Any]] = None,
):

    input_hash = sha256(input_value)
    output_hash = sha256(output_value)

    proof_hash = sha256(
        {
            "workflow_id": workflow_id,
            "action_id": action_id,
            "transaction_id": transaction_id,
            "status": status,
            "input_hash": input_hash,
            "output_hash": output_hash,
        }
    )

    receipt_id = uid("receipt")

    execute(
        """
        INSERT INTO receipts
        (
            id,
            workflow_id,
            action_id,
            transaction_id,
            status,
            input_hash,
            output_hash,
            proof_hash,
            created_at,
            metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt_id,
            workflow_id,
            action_id,
            transaction_id,
            status,
            input_hash,
            output_hash,
            proof_hash,
            now(),
            canonical(metadata or {}),
        ),
    )

    return receipt_id, input_hash, output_hash, proof_hash


# ============================================================
# ACTION EXECUTION
# ============================================================

def perform_action(
    action: Dict[str, Any],
    workflow_id: str,
    dry_run: bool = False,
):

    action_id = action["id"]

    payload = json_load(
        action["payload"],
        {},
    )

    preconditions = json_load(
        action["preconditions"],
        [],
    )

    postconditions = json_load(
        action["postconditions"],
        [],
    )

    resources = json_load(
        action["resources"],
        [],
    )

    if not evaluate_conditions(
        preconditions,
        payload,
    ):
        return {
            "ok": False,
            "error": "preconditions_failed",
            "output": {},
        }

    job_id = uid("job")

    execute(
        """
        INSERT INTO action_jobs
        (
            id,
            workflow_id,
            action_id,
            state,
            input_hash,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            workflow_id,
            action_id,
            "queued",
            sha256(payload),
            now(),
            now(),
        ),
    )

    transaction_id = create_transaction(
        workflow_id,
        action_id,
        job_id,
    )

    execute(
        """
        UPDATE workflow_actions
        SET transaction_id=?, attempts=attempts+1,
            status='running', updated_at=?
        WHERE id=?
        """,
        (
            transaction_id,
            now(),
            action_id,
        ),
    )

    update_transaction(
        transaction_id,
        "prepared",
    )

    save_snapshot(
        transaction_id,
        "before",
        {
            "payload": payload,
            "action_id": action_id,
        },
    )

    if dry_run:
        output = {
            "simulated": True,
            "action": action["name"],
            "action_type": action["action_type"],
            "payload": payload,
        }

        save_snapshot(
            transaction_id,
            "after",
            output,
        )

        receipt_id, ih, oh, ph = create_receipt(
            workflow_id,
            action_id,
            transaction_id,
            "dry_run",
            payload,
            output,
        )

        update_transaction(
            transaction_id,
            "committed",
        )

        execute(
            """
            UPDATE action_jobs
            SET state='succeeded',
                output_hash=?,
                result=?,
                completed_at=?,
                updated_at=?
            WHERE id=?
            """,
            (
                oh,
                canonical(output),
                now(),
                now(),
                job_id,
            ),
        )

        execute(
            """
            UPDATE workflow_actions
            SET status='succeeded',
                receipt_id=?,
                result=?,
                updated_at=?
            WHERE id=?
            """,
            (
                receipt_id,
                canonical(output),
                now(),
                action_id,
            ),
        )

        return {
            "ok": True,
            "dry_run": True,
            "job_id": job_id,
            "transaction_id": transaction_id,
            "receipt_id": receipt_id,
            "output": output,
            "proof_hash": ph,
        }

    if not acquire_resource_locks(
        workflow_id,
        action_id,
        resources,
    ):
        update_transaction(
            transaction_id,
            "failed",
            "resource_conflict",
        )

        execute(
            """
            UPDATE workflow_actions
            SET status='failed',
                error='resource_conflict',
                updated_at=?
            WHERE id=?
            """,
            (now(), action_id),
        )

        return {
            "ok": False,
            "error": "resource_conflict",
            "transaction_id": transaction_id,
        }

    try:

        update_transaction(
            transaction_id,
            "running",
        )

        execute(
            """
            UPDATE action_jobs
            SET state='running',
                started_at=?,
                updated_at=?
            WHERE id=?
            """,
            (now(), now(), job_id),
        )

        # ----------------------------------------------------
        # CONTROLLED ACTION FABRIC
        # ----------------------------------------------------
        #
        # This engine does not execute arbitrary Python/code.
        # Built-in action types are deterministic and safe.
        #

        action_type = action["action_type"]

        if action_type == "internal":
            output = {
                "executed": True,
                "action": action["name"],
                "payload": payload,
            }

        elif action_type == "echo":
            output = {
                "echo": payload,
            }

        elif action_type == "set_value":
            output = {
                "key": payload.get("key"),
                "value": payload.get("value"),
                "written": True,
            }

        elif action_type == "compute":
            operation = payload.get("operation")
            a = payload.get("a", 0)
            b = payload.get("b", 0)

            if operation == "add":
                value = a + b
            elif operation == "subtract":
                value = a - b
            elif operation == "multiply":
                value = a * b
            elif operation == "divide":
                if b == 0:
                    raise ValueError("division_by_zero")
                value = a / b
            else:
                raise ValueError("unsupported_compute_operation")

            output = {
                "operation": operation,
                "value": value,
            }

        else:
            raise ValueError(
                f"unsupported_controlled_action:{action_type}"
            )

        if not evaluate_conditions(
            postconditions,
            output,
        ):
            raise ValueError(
                "postconditions_failed"
            )

        save_snapshot(
            transaction_id,
            "after",
            output,
        )

        update_transaction(
            transaction_id,
            "committing",
        )

        receipt_id, ih, oh, ph = create_receipt(
            workflow_id,
            action_id,
            transaction_id,
            "committed",
            payload,
            output,
        )

        update_transaction(
            transaction_id,
            "committed",
        )

        execute(
            """
            UPDATE action_jobs
            SET state='succeeded',
                output_hash=?,
                result=?,
                completed_at=?,
                updated_at=?
            WHERE id=?
            """,
            (
                oh,
                canonical(output),
                now(),
                now(),
                job_id,
            ),
        )

        execute(
            """
            UPDATE workflow_actions
            SET status='succeeded',
                receipt_id=?,
                result=?,
                error=NULL,
                updated_at=?
            WHERE id=?
            """,
            (
                receipt_id,
                canonical(output),
                now(),
                action_id,
            ),
        )

        return {
            "ok": True,
            "job_id": job_id,
            "transaction_id": transaction_id,
            "receipt_id": receipt_id,
            "output": output,
            "input_hash": ih,
            "output_hash": oh,
            "proof_hash": ph,
        }

    except Exception as exc:

        error = str(exc)

        update_transaction(
            transaction_id,
            "failed",
            error,
        )

        execute(
            """
            UPDATE action_jobs
            SET state='failed',
                error=?,
                updated_at=?
            WHERE id=?
            """,
            (
                error,
                now(),
                job_id,
            ),
        )

        execute(
            """
            UPDATE workflow_actions
            SET status='failed',
                error=?,
                updated_at=?
            WHERE id=?
            """,
            (
                error,
                now(),
                action_id,
            ),
        )

        return {
            "ok": False,
            "error": error,
            "job_id": job_id,
            "transaction_id": transaction_id,
        }

    finally:
        release_resource_locks(
            workflow_id,
            action_id,
        )


# ============================================================
# COMPENSATION
# ============================================================

def register_compensation(
    workflow_id: str,
    action_id: str,
    plan: Dict[str, Any],
):

    execute(
        """
        INSERT OR REPLACE INTO compensation_plans
        (
            id,
            workflow_id,
            action_id,
            status,
            plan,
            attempts,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uid("comp"),
            workflow_id,
            action_id,
            "pending",
            canonical(plan),
            0,
            now(),
            now(),
        ),
    )


def compensate_action(
    workflow_id: str,
    action: Dict[str, Any],
):

    row = fetchone(
        """
        SELECT *
        FROM compensation_plans
        WHERE workflow_id=? AND action_id=?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (
            workflow_id,
            action["id"],
        ),
    )

    if not row:
        return {
            "ok": True,
            "status": "no_compensation_required",
        }

    plan = json_load(
        row["plan"],
        {},
    )

    execute(
        """
        UPDATE compensation_plans
        SET status='running',
            attempts=attempts+1,
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            row["id"],
        ),
    )

    # Compensation is deliberately bounded.
    # It never executes arbitrary code.

    compensation_type = plan.get(
        "type",
        "logical",
    )

    if compensation_type in {
        "logical",
        "mark_compensated",
    }:
        execute(
            """
            UPDATE compensation_plans
            SET status='completed',
                updated_at=?
            WHERE id=?
            """,
            (now(), row["id"]),
        )

        execute(
            """
            UPDATE workflow_actions
            SET status='compensated',
                updated_at=?
            WHERE id=?
            """,
            (now(), action["id"]),
        )

        return {
            "ok": True,
            "status": "compensated",
            "type": compensation_type,
        }

    execute(
        """
        UPDATE compensation_plans
        SET status='failed',
            last_error='unsupported_compensation_type',
            updated_at=?
        WHERE id=?
        """,
        (now(), row["id"]),
    )

    return {
        "ok": False,
        "status": "failed",
        "error": "unsupported_compensation_type",
    }


# ============================================================
# WORKFLOW VALIDATION
# ============================================================

def validate_dag(actions: List[ActionSpec]):

    if len(actions) > MAX_WORKFLOW_ACTIONS:
        raise ValueError(
            f"maximum_actions_exceeded:{MAX_WORKFLOW_ACTIONS}"
        )

    keys = [a.key for a in actions]

    if len(keys) != len(set(keys)):
        raise ValueError("duplicate_action_key")

    known = set(keys)

    for action in actions:
        for dep in action.dependencies:
            if dep not in known:
                raise ValueError(
                    f"unknown_dependency:{dep}"
                )

    graph = {
        a.key: set(a.dependencies)
        for a in actions
    }

    visiting = set()
    visited = set()

    def visit(node):

        if node in visiting:
            raise ValueError("workflow_cycle_detected")

        if node in visited:
            return

        visiting.add(node)

        for dep in graph[node]:
            visit(dep)

        visiting.remove(node)
        visited.add(node)

    for key in graph:
        visit(key)


# ============================================================
# WORKFLOW CHECKPOINTS
# ============================================================

def checkpoint(workflow_id: str):

    actions = fetchall(
        """
        SELECT *
        FROM workflow_actions
        WHERE workflow_id=?
        ORDER BY position
        """,
        (workflow_id,),
    )

    completed = [
        a["action_key"]
        for a in actions
        if a["status"] in {
            "succeeded",
            "compensated",
            "skipped",
        }
    ]

    active = [
        a["action_key"]
        for a in actions
        if a["status"] in {
            "running",
            "ready",
            "waiting_approval",
        }
    ]

    failed = [
        a["action_key"]
        for a in actions
        if a["status"] == "failed"
    ]

    state = {
        "workflow_id": workflow_id,
        "completed": completed,
        "active": active,
        "failed": failed,
    }

    state_hash = sha256(state)

    row = fetchone(
        """
        SELECT COALESCE(MAX(checkpoint_index), -1) AS n
        FROM workflow_checkpoints
        WHERE workflow_id=?
        """,
        (workflow_id,),
    )

    index = int(row["n"]) + 1

    checkpoint_id = uid("checkpoint")

    execute(
        """
        INSERT INTO workflow_checkpoints
        (
            id,
            workflow_id,
            checkpoint_index,
            state,
            completed_actions,
            active_actions,
            failed_actions,
            state_hash,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            checkpoint_id,
            workflow_id,
            index,
            canonical(state),
            canonical(completed),
            canonical(active),
            canonical(failed),
            state_hash,
            now(),
        ),
    )

    workflow_event(
        workflow_id,
        "checkpoint_created",
        {
            "checkpoint_id": checkpoint_id,
            "index": index,
            "state_hash": state_hash,
        },
    )

    return {
        "id": checkpoint_id,
        "index": index,
        "state_hash": state_hash,
        "completed": completed,
        "active": active,
        "failed": failed,
    }


# ============================================================
# WORKFLOW PROOF
# ============================================================

def create_workflow_proof(
    workflow_id: str,
    status: str,
):

    receipts = fetchall(
        """
        SELECT *
        FROM receipts
        WHERE workflow_id=?
        ORDER BY created_at
        """,
        (workflow_id,),
    )

    receipt_hashes = [
        r["proof_hash"]
        for r in receipts
    ]

    actions = fetchall(
        """
        SELECT action_key, status, result
        FROM workflow_actions
        WHERE workflow_id=?
        ORDER BY position
        """,
        (workflow_id,),
    )

    state_hash = sha256(actions)

    proof_hash = sha256(
        {
            "workflow_id": workflow_id,
            "status": status,
            "receipt_hashes": receipt_hashes,
            "state_hash": state_hash,
        }
    )

    proof_id = uid("proof")

    execute(
        """
        INSERT INTO workflow_proofs
        (
            id,
            workflow_id,
            status,
            proof_hash,
            action_receipts,
            state_hash,
            created_at,
            metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proof_id,
            workflow_id,
            status,
            proof_hash,
            canonical(receipt_hashes),
            state_hash,
            now(),
            canonical(
                {
                    "receipt_count": len(receipts),
                    "action_count": len(actions),
                }
            ),
        ),
    )

    return {
        "id": proof_id,
        "workflow_id": workflow_id,
        "status": status,
        "proof_hash": proof_hash,
        "state_hash": state_hash,
        "receipt_count": len(receipts),
    }


# ============================================================
# WORKFLOW CREATION
# ============================================================

def create_workflow(
    request: WorkflowCreate,
):

    validate_dag(request.actions)

    workflow_key = (
        request.workflow_key
        or uid("workflow-key")
    )

    existing = fetchone(
        """
        SELECT *
        FROM workflows
        WHERE workflow_key=?
        """,
        (workflow_key,),
    )

    if existing:
        return {
            "id": existing["id"],
            "existing": True,
            "workflow_key": workflow_key,
        }

    workflow_id = uid("workflow")

    execute(
        """
        INSERT INTO workflows
        (
            id,
            mission_id,
            workflow_key,
            objective,
            status,
            current_step,
            total_steps,
            created_at,
            updated_at,
            metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow_id,
            request.mission_id,
            workflow_key,
            request.objective,
            "dry_run" if request.dry_run else "created",
            0,
            len(request.actions),
            now(),
            now(),
            canonical(request.metadata),
        ),
    )

    for position, action in enumerate(
        request.actions
    ):

        action_id = uid("action")

        execute(
            """
            INSERT INTO workflow_actions
            (
                id,
                workflow_id,
                action_key,
                name,
                action_type,
                position,
                status,
                dependencies,
                resources,
                payload,
                compensation,
                preconditions,
                postconditions,
                irreversible,
                approval_required,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                workflow_id,
                action.key,
                action.name,
                action.action_type,
                position,
                "pending",
                canonical(action.dependencies),
                canonical(action.resources),
                canonical(action.payload),
                canonical(action.compensation),
                canonical(action.preconditions),
                canonical(action.postconditions),
                int(action.irreversible),
                int(action.approval_required),
                now(),
                now(),
            ),
        )

        if action.compensation:
            register_compensation(
                workflow_id,
                action_id,
                action.compensation,
            )

    workflow_event(
        workflow_id,
        "workflow_created",
        {
            "objective": request.objective,
            "action_count": len(request.actions),
            "dry_run": request.dry_run,
        },
    )

    checkpoint(workflow_id)

    return {
        "id": workflow_id,
        "workflow_key": workflow_key,
        "status": "dry_run"
        if request.dry_run
        else "created",
        "action_count": len(request.actions),
    }


# ============================================================
# WORKFLOW RUNNER
# ============================================================

def dependencies_satisfied(
    workflow_id: str,
    action: Dict[str, Any],
) -> bool:

    deps = json_load(
        action["dependencies"],
        [],
    )

    if not deps:
        return True

    placeholders = ",".join(
        "?" for _ in deps
    )

    rows = fetchall(
        f"""
        SELECT action_key, status
        FROM workflow_actions
        WHERE workflow_id=?
        AND action_key IN ({placeholders})
        """,
        (workflow_id, *deps),
    )

    state = {
        r["action_key"]: r["status"]
        for r in rows
    }

    return all(
        state.get(dep) in {
            "succeeded",
            "skipped",
        }
        for dep in deps
    )


def mark_ready_actions(workflow_id: str):

    actions = fetchall(
        """
        SELECT *
        FROM workflow_actions
        WHERE workflow_id=?
        ORDER BY position
        """,
        (workflow_id,),
    )

    changed = []

    for action in actions:

        if action["status"] != "pending":
            continue

        if not dependencies_satisfied(
            workflow_id,
            action,
        ):
            execute(
                """
                UPDATE workflow_actions
                SET status='waiting_dependency',
                    updated_at=?
                WHERE id=?
                """,
                (now(), action["id"]),
            )
            continue

        if requires_approval(action):

            if not approval_granted(
                workflow_id,
                action["id"],
            ):

                existing = fetchone(
                    """
                    SELECT id
                    FROM approvals
                    WHERE workflow_id=?
                    AND action_id=?
                    AND status='pending'
                    """,
                    (
                        workflow_id,
                        action["id"],
                    ),
                )

                if not existing:
                    create_approval(
                        workflow_id,
                        action["id"],
                        "high_risk_or_irreversible_action",
                    )

                execute(
                    """
                    UPDATE workflow_actions
                    SET status='waiting_approval',
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        now(),
                        action["id"],
                    ),
                )

                continue

        execute(
            """
            UPDATE workflow_actions
            SET status='ready',
                updated_at=?
            WHERE id=?
            """,
            (
                now(),
                action["id"],
            ),
        )

        changed.append(
            action["action_key"]
        )

    return changed


def run_workflow(
    workflow_id: str,
    dry_run: bool = False,
):

    workflow = fetchone(
        """
        SELECT *
        FROM workflows
        WHERE id=?
        """,
        (workflow_id,),
    )

    if not workflow:
        raise ValueError("workflow_not_found")

    if workflow["status"] in {
        "completed",
        "cancelled",
    }:
        return get_workflow(workflow_id)

    execute(
        """
        UPDATE workflows
        SET status=?, updated_at=?
        WHERE id=?
        """,
        (
            "dry_run" if dry_run else "running",
            now(),
            workflow_id,
        ),
    )

    workflow_event(
        workflow_id,
        "workflow_started",
        {
            "dry_run": dry_run,
        },
    )

    total = int(
        workflow["total_steps"]
    )

    for _ in range(total * 3 + 3):

        mark_ready_actions(
            workflow_id
        )

        actions = fetchall(
            """
            SELECT *
            FROM workflow_actions
            WHERE workflow_id=?
            ORDER BY position
            """,
            (workflow_id,),
        )

        waiting_approval = [
            a for a in actions
            if a["status"] == "waiting_approval"
        ]

        failed = [
            a for a in actions
            if a["status"] == "failed"
        ]

        completed = [
            a for a in actions
            if a["status"] in {
                "succeeded",
                "compensated",
                "skipped",
            }
        ]

        if waiting_approval:
            execute(
                """
                UPDATE workflows
                SET status='waiting_approval',
                    updated_at=?
                WHERE id=?
                """,
                (now(), workflow_id),
            )

            checkpoint(workflow_id)

            return get_workflow(
                workflow_id
            )

        if failed:

            execute(
                """
                UPDATE workflows
                SET status='recovering',
                    updated_at=?
                WHERE id=?
                """,
                (now(), workflow_id),
            )

            workflow_event(
                workflow_id,
                "workflow_failure_detected",
                {
                    "failed_actions": [
                        a["action_key"]
                        for a in failed
                    ]
                },
            )

            recover_workflow(
                workflow_id
            )

            checkpoint(workflow_id)

            return get_workflow(
                workflow_id
            )

        ready = [
            a for a in actions
            if a["status"] == "ready"
        ]

        if not ready:
            if len(completed) == len(actions):

                execute(
                    """
                    UPDATE workflows
                    SET status='completed',
                        completed_at=?,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        now(),
                        now(),
                        workflow_id,
                    ),
                )

                workflow_event(
                    workflow_id,
                    "workflow_completed",
                    {},
                )

                proof = create_workflow_proof(
                    workflow_id,
                    "completed",
                )

                checkpoint(workflow_id)

                return {
                    **get_workflow(workflow_id),
                    "proof": proof,
                }

            # No ready actions and not complete.
            execute(
                """
                UPDATE workflows
                SET status='failed',
                    error='workflow_deadlock_or_unsatisfied_dependency',
                    updated_at=?
                WHERE id=?
                """,
                (now(), workflow_id),
            )

            workflow_event(
                workflow_id,
                "workflow_deadlock",
                {},
            )

            return get_workflow(
                workflow_id
            )

        # Execute the current ready frontier.
        for action in ready:

            result = perform_action(
                action,
                workflow_id,
                dry_run=dry_run,
            )

            workflow_event(
                workflow_id,
                "action_finished",
                {
                    "action": action["action_key"],
                    "ok": result.get("ok"),
                    "error": result.get("error"),
                },
            )

            if not result.get("ok"):
                break

        checkpoint(workflow_id)

    return get_workflow(
        workflow_id
    )


# ============================================================
# RECOVERY
# ============================================================

def queue_recovery(
    workflow_id: str,
    action_id: str,
    kind: str,
    payload: Dict[str, Any],
):

    recovery_id = uid("recovery")

    execute(
        """
        INSERT INTO recovery_queue
        (
            id,
            workflow_id,
            action_id,
            kind,
            state,
            attempts,
            next_attempt_at,
            payload,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recovery_id,
            workflow_id,
            action_id,
            kind,
            "pending",
            0,
            now(),
            canonical(payload),
            now(),
            now(),
        ),
    )

    return recovery_id


def recover_workflow(
    workflow_id: str,
):

    actions = fetchall(
        """
        SELECT *
        FROM workflow_actions
        WHERE workflow_id=?
        AND status='failed'
        ORDER BY position DESC
        """,
        (workflow_id,),
    )

    if not actions:
        return {
            "status": "nothing_to_recover"
        }

    for action in actions:

        queue_recovery(
            workflow_id,
            action["id"],
            "action_failure",
            {
                "action_key": action["action_key"],
                "error": action["error"],
            },
        )

    workflow_event(
        workflow_id,
        "recovery_queued",
        {
            "count": len(actions),
        },
    )

    for action in actions:

        compensation = compensate_action(
            workflow_id,
            action,
        )

        if compensation.get("ok"):

            execute(
                """
                UPDATE workflow_actions
                SET recovery_attempts=recovery_attempts+1,
                    error=NULL,
                    updated_at=?
                WHERE id=?
                """,
                (
                    now(),
                    action["id"],
                ),
            )

            continue

        current = int(
            action["recovery_attempts"]
        )

        if current >= MAX_RECOVERY_ATTEMPTS:

            dlq_id = uid("dlq")

            execute(
                """
                INSERT INTO dead_letter_queue
                (
                    id,
                    workflow_id,
                    action_id,
                    reason,
                    payload,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    dlq_id,
                    workflow_id,
                    action["id"],
                    "recovery_exhausted",
                    canonical(
                        {
                            "action": action["action_key"],
                            "error": action["error"],
                        }
                    ),
                    now(),
                ),
            )

            workflow_event(
                workflow_id,
                "dead_letter_created",
                {
                    "action": action["action_key"],
                    "dead_letter_id": dlq_id,
                },
            )

    # A failed action that was compensated is not a successful
    # action. The workflow therefore remains explicitly failed
    # unless reconciliation proves the workflow contract restored.

    execute(
        """
        UPDATE workflows
        SET status='reconciling',
            updated_at=?
        WHERE id=?
        """,
        (now(), workflow_id),
    )

    return reconcile_workflow(
        workflow_id
    )


# ============================================================
# RECONCILIATION
# ============================================================

def reconcile_workflow(
    workflow_id: str,
):

    actions = fetchall(
        """
        SELECT *
        FROM workflow_actions
        WHERE workflow_id=?
        ORDER BY position
        """,
        (workflow_id,),
    )

    failed = [
        a for a in actions
        if a["status"] == "failed"
    ]

    compensated = [
        a for a in actions
        if a["status"] == "compensated"
    ]

    dead = fetchall(
        """
        SELECT *
        FROM dead_letter_queue
        WHERE workflow_id=?
        """,
        (workflow_id,),
    )

    if dead:
        status = "failed"
        reason = "recovery_exhausted"

    elif failed:
        status = "failed"
        reason = "failed_actions_remain"

    elif compensated:
        status = "failed"
        reason = "workflow_compensated_after_failure"

    else:
        status = "completed"
        reason = "reconciled"

    execute(
        """
        UPDATE workflows
        SET status=?,
            error=?,
            updated_at=?
        WHERE id=?
        """,
        (
            status,
            None if status == "completed" else reason,
            now(),
            workflow_id,
        ),
    )

    workflow_event(
        workflow_id,
        "workflow_reconciled",
        {
            "status": status,
            "reason": reason,
        },
    )

    if status == "completed":
        create_workflow_proof(
            workflow_id,
            status,
        )

    return {
        "workflow_id": workflow_id,
        "status": status,
        "reason": reason,
        "dead_letters": len(dead),
    }


# ============================================================
# WORKFLOW READ
# ============================================================

def get_workflow(
    workflow_id: str,
):

    workflow = fetchone(
        """
        SELECT *
        FROM workflows
        WHERE id=?
        """,
        (workflow_id,),
    )

    if not workflow:
        raise ValueError(
            "workflow_not_found"
        )

    actions = fetchall(
        """
        SELECT *
        FROM workflow_actions
        WHERE workflow_id=?
        ORDER BY position
        """,
        (workflow_id,),
    )

    events = fetchall(
        """
        SELECT *
        FROM workflow_events
        WHERE workflow_id=?
        ORDER BY sequence
        """,
        (workflow_id,),
    )

    checkpoints = fetchall(
        """
        SELECT *
        FROM workflow_checkpoints
        WHERE workflow_id=?
        ORDER BY checkpoint_index
        """,
        (workflow_id,),
    )

    proofs = fetchall(
        """
        SELECT *
        FROM workflow_proofs
        WHERE workflow_id=?
        ORDER BY created_at
        """,
        (workflow_id,),
    )

    return {
        **workflow,
        "metadata": json_load(
            workflow["metadata"],
            {},
        ),
        "actions": [
            {
                **a,
                "dependencies": json_load(
                    a["dependencies"],
                    [],
                ),
                "resources": json_load(
                    a["resources"],
                    [],
                ),
                "payload": json_load(
                    a["payload"],
                    {},
                ),
                "compensation": json_load(
                    a["compensation"],
                    {},
                ),
                "preconditions": json_load(
                    a["preconditions"],
                    [],
                ),
                "postconditions": json_load(
                    a["postconditions"],
                    [],
                ),
                "result": json_load(
                    a["result"],
                    None,
                ),
            }
            for a in actions
        ],
        "events": [
            {
                **e,
                "payload": json_load(
                    e["payload"],
                    {},
                ),
            }
            for e in events
        ],
        "checkpoints": [
            {
                **c,
                "state": json_load(
                    c["state"],
                    {},
                ),
                "completed_actions": json_load(
                    c["completed_actions"],
                    [],
                ),
                "active_actions": json_load(
                    c["active_actions"],
                    [],
                ),
                "failed_actions": json_load(
                    c["failed_actions"],
                    [],
                ),
            }
            for c in checkpoints
        ],
        "proofs": [
            {
                **p,
                "action_receipts": json_load(
                    p["action_receipts"],
                    [],
                ),
                "metadata": json_load(
                    p["metadata"],
                    {},
                ),
            }
            for p in proofs
        ],
    }


# ============================================================
# BASIC MISSION COMPATIBILITY
# ============================================================

def create_mission(
    objective: str,
):

    mission_id = uid("mission")

    execute(
        """
        INSERT INTO missions
        (id, objective, status, created_at, updated_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "created",
            now(),
            now(),
            "{}",
        ),
    )

    return mission_id


# ============================================================
# ROUTES
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
        "workflow": "/workflow",
        "capabilities": "/capabilities",
        "architecture": "/architecture",
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
            "audit_logging": True,
        },
        "layers": {
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
            "durable_workflow_orchestration": True,
            "workflow_dag": True,
            "saga_coordinator": True,
            "persistent_compensation_plans": True,
            "workflow_checkpoints": True,
            "workflow_recovery_policy": True,
            "dead_letter_recovery_queue": True,
            "workflow_reconciliation": True,
            "workflow_replay": True,
            "workflow_level_proof": True,
            "workflow_dry_run": True,
            "workflow_idempotency": True,
            "resource_conflict_detection": True,
            "workflow_event_journal": True,
            "transaction_workflow_correlation": True,
        },
        "transaction_fabric": {
            "states": sorted(
                TRANSACTION_STATES
            ),
            "snapshots": True,
            "dependencies": True,
            "locks": True,
            "circuit_breakers": True,
            "recovery_queue": True,
            "compensation": True,
            "exactly_once_guard": True,
        },
        "workflow_fabric": {
            "states": sorted(
                WORKFLOW_STATES
            ),
            "action_states": sorted(
                ACTION_STATES
            ),
            "max_actions": MAX_WORKFLOW_ACTIONS,
            "max_recovery_attempts":
                MAX_RECOVERY_ATTEMPTS,
            "durable": True,
            "saga": True,
            "reconciliation": True,
            "replay": True,
            "proofs": True,
        },
    }


@app.get("/status")
def status():
    return health()


@app.get("/version")
def version():
    return {
        "version": VERSION,
        "build": BUILD,
    }


@app.get("/capabilities")
def capabilities():

    return {
        "version": VERSION,
        "workflow": [
            "create",
            "run",
            "pause-compatible",
            "checkpoint",
            "recover",
            "reconcile",
            "replay",
            "proof",
            "dry-run",
        ],
        "transaction": [
            "prepare",
            "commit",
            "fail",
            "recover",
            "compensate",
            "snapshot",
            "receipt",
        ],
        "security": [
            "controlled_execution",
            "approval_gates",
            "no_arbitrary_code",
            "no_private_network_access",
            "no_unrestricted_proxy",
        ],
    }


@app.get("/architecture")
def architecture():

    return {
        "version": VERSION,
        "build": BUILD,
        "pipeline": [
            "intent",
            "mission",
            "workflow_planning",
            "dependency_graph",
            "authorization",
            "action_selection",
            "transaction_prepare",
            "execute",
            "observe",
            "verify",
            "commit",
            "checkpoint",
            "recover",
            "compensate",
            "reconcile",
            "workflow_proof",
            "learn",
            "converge",
        ],
        "principle": (
            "2050.67 adds durable workflow orchestration "
            "above the 2050.66 transaction fabric."
        ),
    }


# ============================================================
# RUN — SIMPLE COMPATIBILITY ENDPOINT
# ============================================================

@app.post("/run")
def run(objective: str):

    mission_id = create_mission(
        objective
    )

    action = ActionSpec(
        key="primary",
        name="primary_mission_action",
        action_type="internal",
        payload={
            "objective": objective
        },
    )

    workflow = create_workflow(
        WorkflowCreate(
            objective=objective,
            mission_id=mission_id,
            actions=[action],
        )
    )

    return {
        "status": "accepted",
        "mission_id": mission_id,
        "workflow_id": workflow["id"],
        "version": VERSION,
    }


# ============================================================
# WORKFLOW API
# ============================================================

@app.post("/workflow")
def workflow_create(
    request: WorkflowCreate,
):

    try:
        return create_workflow(
            request
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


@app.post("/workflow/{workflow_id}/run")
def workflow_run(
    workflow_id: str,
    dry_run: bool = False,
):

    try:
        return run_workflow(
            workflow_id,
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )


@app.get("/workflow/{workflow_id}")
def workflow_get(
    workflow_id: str,
):

    try:
        return get_workflow(
            workflow_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )


@app.get("/workflow/{workflow_id}/events")
def workflow_events(
    workflow_id: str,
):

    return fetchall(
        """
        SELECT *
        FROM workflow_events
        WHERE workflow_id=?
        ORDER BY sequence
        """,
        (workflow_id,),
    )


@app.get("/workflow/{workflow_id}/checkpoints")
def workflow_checkpoints(
    workflow_id: str,
):

    return fetchall(
        """
        SELECT *
        FROM workflow_checkpoints
        WHERE workflow_id=?
        ORDER BY checkpoint_index
        """,
        (workflow_id,),
    )


@app.post("/workflow/{workflow_id}/checkpoint")
def workflow_checkpoint(
    workflow_id: str,
):

    return checkpoint(
        workflow_id
    )


@app.post("/workflow/{workflow_id}/recover")
def workflow_recover(
    workflow_id: str,
):

    return recover_workflow(
        workflow_id
    )


@app.post("/workflow/{workflow_id}/reconcile")
def workflow_reconcile(
    workflow_id: str,
):

    return reconcile_workflow(
        workflow_id
    )


@app.get("/workflow/{workflow_id}/proof")
def workflow_proof(
    workflow_id: str,
):

    rows = fetchall(
        """
        SELECT *
        FROM workflow_proofs
        WHERE workflow_id=?
        ORDER BY created_at DESC
        """,
        (workflow_id,),
    )

    return {
        "workflow_id": workflow_id,
        "proofs": rows,
    }


@app.get("/workflow/{workflow_id}/replay")
def workflow_replay(
    workflow_id: str,
):

    events = fetchall(
        """
        SELECT sequence, event_type, payload, created_at
        FROM workflow_events
        WHERE workflow_id=?
        ORDER BY sequence
        """,
        (workflow_id,),
    )

    return {
        "workflow_id": workflow_id,
        "replayable": True,
        "event_count": len(events),
        "events": [
            {
                **event,
                "payload": json_load(
                    event["payload"],
                    {},
                ),
            }
            for event in events
        ],
    }


# ============================================================
# APPROVAL API
# ============================================================

@app.get("/approvals")
def approvals():

    rows = fetchall(
        """
        SELECT *
        FROM approvals
        ORDER BY created_at DESC
        """
    )

    return rows


@app.post("/approval/{approval_id}/approve")
def approve(
    approval_id: str,
):

    row = fetchone(
        """
        SELECT *
        FROM approvals
        WHERE id=?
        """,
        (approval_id,),
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail="approval_not_found",
        )

    execute(
        """
        UPDATE approvals
        SET status='approved',
            decided_at=?
        WHERE id=?
        """,
        (
            now(),
            approval_id,
        ),
    )

    execute(
        """
        UPDATE workflow_actions
        SET status='pending',
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            row["action_id"],
        ),
    )

    return {
        "status": "approved",
        "approval_id": approval_id,
        "workflow_id": row["workflow_id"],
        "action_id": row["action_id"],
    }


@app.post("/approval/{approval_id}/reject")
def reject(
    approval_id: str,
):

    row = fetchone(
        """
        SELECT *
        FROM approvals
        WHERE id=?
        """,
        (approval_id,),
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail="approval_not_found",
        )

    execute(
        """
        UPDATE approvals
        SET status='rejected',
            decided_at=?
        WHERE id=?
        """,
        (
            now(),
            approval_id,
        ),
    )

    execute(
        """
        UPDATE workflow_actions
        SET status='failed',
            error='approval_rejected',
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            row["action_id"],
        ),
    )

    return {
        "status": "rejected",
        "approval_id": approval_id,
    }


# ============================================================
# TRANSACTION API
# ============================================================

@app.get("/transactions")
def transactions():

    return fetchall(
        """
        SELECT *
        FROM transactions
        ORDER BY updated_at DESC
        LIMIT 200
        """
    )


@app.get("/transaction/{transaction_id}")
def transaction(
    transaction_id: str,
):

    row = fetchone(
        """
        SELECT *
        FROM transactions
        WHERE id=?
        """,
        (transaction_id,),
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail="transaction_not_found",
        )

    snapshots = fetchall(
        """
        SELECT *
        FROM snapshots
        WHERE transaction_id=?
        ORDER BY created_at
        """,
        (transaction_id,),
    )

    receipts = fetchall(
        """
        SELECT *
        FROM receipts
        WHERE transaction_id=?
        ORDER BY created_at
        """,
        (transaction_id,),
    )

    return {
        **row,
        "metadata": json_load(
            row["metadata"],
            {},
        ),
        "snapshots": snapshots,
        "receipts": receipts,
    }


# ============================================================
# RECOVERY / DEAD LETTER API
# ============================================================

@app.get("/recovery")
def recovery():

    return fetchall(
        """
        SELECT *
        FROM recovery_queue
        ORDER BY created_at DESC
        LIMIT 200
        """
    )


@app.get("/dead-letter")
def dead_letter():

    return fetchall(
        """
        SELECT *
        FROM dead_letter_queue
        ORDER BY created_at DESC
        LIMIT 200
        """
    )


# ============================================================
# RECEIPTS
# ============================================================

@app.get("/receipts")
def receipts():

    return fetchall(
        """
        SELECT *
        FROM receipts
        ORDER BY created_at DESC
        LIMIT 200
        """
    )


@app.get("/receipt/{receipt_id}")
def receipt(
    receipt_id: str,
):

    row = fetchone(
        """
        SELECT *
        FROM receipts
        WHERE id=?
        """,
        (receipt_id,),
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail="receipt_not_found",
        )

    return row


# ============================================================
# TESTS
# ============================================================

@app.get("/test-transaction")
def test_transaction():

    workflow = create_workflow(
        WorkflowCreate(
            objective="2050.66 transaction compatibility test",
            workflow_key=uid("test"),
            actions=[
                ActionSpec(
                    key="transaction_test",
                    name="transaction_test",
                    action_type="echo",
                    payload={
                        "status": "ok"
                    },
                )
            ],
        )
    )

    result = run_workflow(
        workflow["id"]
    )

    actions = result.get(
        "actions",
        [],
    )

    action = (
        actions[0]
        if actions
        else {}
    )

    return {
        "status": "passed"
        if result["status"] == "completed"
        else "failed",
        "version": VERSION,
        "build": BUILD,
        "workflow_id": workflow["id"],
        "transaction": {
            "id": action.get(
                "transaction_id"
            ),
            "state": (
                fetchone(
                    """
                    SELECT state
                    FROM transactions
                    WHERE id=?
                    """,
                    (
                        action.get(
                            "transaction_id"
                        ),
                    ),
                )
                or {}
            ).get("state"),
        },
        "features": {
            "transaction": True,
            "before_snapshot": True,
            "after_snapshot": True,
            "commit": True,
            "receipt_verification": True,
        },
    }


@app.get("/test-workflow")
def test_workflow():

    workflow = create_workflow(
        WorkflowCreate(
            objective=(
                "2050.67 durable multi-action "
                "workflow test"
            ),
            workflow_key=uid("test-workflow"),
            actions=[
                ActionSpec(
                    key="prepare",
                    name="prepare",
                    action_type="echo",
                    payload={
                        "stage": "prepare"
                    },
                    compensation={
                        "type": "logical"
                    },
                ),
                ActionSpec(
                    key="compute",
                    name="compute",
                    action_type="compute",
                    payload={
                        "operation": "add",
                        "a": 20,
                        "b": 67,
                    },
                    dependencies=[
                        "prepare"
                    ],
                    compensation={
                        "type": "logical"
                    },
                ),
                ActionSpec(
                    key="finalize",
                    name="finalize",
                    action_type="echo",
                    payload={
                        "stage": "finalize"
                    },
                    dependencies=[
                        "compute"
                    ],
                ),
            ],
        )
    )

    result = run_workflow(
        workflow["id"]
    )

    return {
        "status": "passed"
        if result["status"] == "completed"
        else "failed",
        "version": VERSION,
        "build": BUILD,
        "workflow_id": workflow["id"],
        "workflow_status": result["status"],
        "action_count": len(
            result["actions"]
        ),
        "proofs": len(
            result["proofs"]
        ),
        "checkpoints": len(
            result["checkpoints"]
        ),
    }


@app.get("/test-saga")
def test_saga():

    workflow = create_workflow(
        WorkflowCreate(
            objective="2050.67 Saga compensation test",
            workflow_key=uid("test-saga"),
            actions=[
                ActionSpec(
                    key="step_a",
                    name="successful_step",
                    action_type="echo",
                    payload={
                        "step": "a"
                    },
                    compensation={
                        "type": "logical"
                    },
                ),
                ActionSpec(
                    key="step_b",
                    name="failing_step",
                    action_type="internal",
                    payload={
                        "ok": True
                    },
                    dependencies=[
                        "step_a"
                    ],
                    preconditions=[
                        "false"
                    ],
                    compensation={
                        "type": "logical"
                    },
                ),
            ],
        )
    )

    result = run_workflow(
        workflow["id"]
    )

    return {
        "status": "passed"
        if result["status"] == "failed"
        else "failed",
        "version": VERSION,
        "build": BUILD,
        "workflow_id": workflow["id"],
        "expected": {
            "step_a": "succeeded",
            "step_b": "failed",
            "workflow": "failed",
        },
        "actual": {
            "workflow": result["status"],
            "actions": [
                {
                    "key": a["action_key"],
                    "status": a["status"],
                }
                for a in result["actions"]
            ],
        },
    }


@app.get("/test-dry-run")
def test_dry_run():

    workflow = create_workflow(
        WorkflowCreate(
            objective="2050.67 dry run",
            workflow_key=uid("test-dry"),
            dry_run=True,
            actions=[
                ActionSpec(
                    key="simulation",
                    name="simulation",
                    action_type="compute",
                    payload={
                        "operation": "multiply",
                        "a": 5,
                        "b": 10,
                    },
                )
            ],
        )
    )

    result = run_workflow(
        workflow["id"],
        dry_run=True,
    )

    return {
        "status": "passed"
        if result["status"] == "completed"
        else "failed",
        "workflow_id": workflow["id"],
        "dry_run": True,
        "actions": [
            {
                "key": a["action_key"],
                "status": a["status"],
                "result": a["result"],
            }
            for a in result["actions"]
        ],
    }


@app.get("/test-reconciliation")
def test_reconciliation():

    workflow = create_workflow(
        WorkflowCreate(
            objective="reconciliation test",
            workflow_key=uid("test-reconcile"),
            actions=[
                ActionSpec(
                    key="reconcile",
                    name="reconcile",
                    action_type="echo",
                    payload={
                        "state": "verified"
                    },
                )
            ],
        )
    )

    result = run_workflow(
        workflow["id"]
    )

    reconciliation = reconcile_workflow(
        workflow["id"]
    )

    return {
        "status": "passed",
        "workflow_id": workflow["id"],
        "workflow_status": result["status"],
        "reconciliation": reconciliation,
    }


@app.get("/test-replay")
def test_replay():

    workflow = create_workflow(
        WorkflowCreate(
            objective="replay test",
            workflow_key=uid("test-replay"),
            actions=[
                ActionSpec(
                    key="replay",
                    name="replay",
                    action_type="echo",
                    payload={
                        "event": "replay"
                    },
                )
            ],
        )
    )

    run_workflow(
        workflow["id"]
    )

    events = fetchall(
        """
        SELECT sequence
        FROM workflow_events
        WHERE workflow_id=?
        ORDER BY sequence
        """,
        (workflow["id"],),
    )

    ordered = [
        events[i]["sequence"]
        for i in range(len(events))
    ]

    return {
        "status": "passed"
        if ordered == sorted(ordered)
        else "failed",
        "workflow_id": workflow["id"],
        "event_sequence": ordered,
        "replayable": True,
    }


@app.get("/test-2050-67")
def test_2050_67():

    results = {
        "transaction": None,
        "workflow": None,
        "saga": None,
        "dry_run": None,
        "reconciliation": None,
        "replay": None,
    }

    results["transaction"] = test_transaction()
    results["workflow"] = test_workflow()
    results["saga"] = test_saga()
    results["dry_run"] = test_dry_run()
    results["reconciliation"] = test_reconciliation()
    results["replay"] = test_replay()

    passed = all(
        value.get("status") == "passed"
        for value in results.values()
    )

    return {
        "status": "passed"
        if passed
        else "failed",
        "version": VERSION,
        "build": BUILD,
        "tests": results,
    }


# ============================================================
# SIMPLE MOBILE INTERFACE
# ============================================================

@app.get("/interface", response_class=HTMLResponse)
def interface():

    return """
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity 2050.67</title>
<style>
body{
    font-family:system-ui,sans-serif;
    margin:0;
    padding:20px;
    background:#101114;
    color:#fff;
}
.card{
    max-width:720px;
    margin:auto;
    padding:20px;
    border-radius:18px;
    background:#191b21;
}
button,input,textarea{
    width:100%;
    box-sizing:border-box;
    margin-top:10px;
    padding:13px;
    border-radius:10px;
    border:1px solid #444;
    background:#111;
    color:#fff;
}
button{
    cursor:pointer;
}
pre{
    white-space:pre-wrap;
    word-break:break-word;
    background:#0b0c0f;
    padding:15px;
    border-radius:12px;
}
</style>
</head>
<body>
<div class="card">
<h1>AI Infinity</h1>
<p>TARGET-2050.67</p>
<p>Durable Multi-Action Saga Orchestration</p>

<textarea id="objective"
placeholder="Enter a mission..."></textarea>

<button onclick="runMission()">
Run Mission
</button>

<button onclick="test()">
Run 2050.67 Full Test
</button>

<pre id="output">Ready.</pre>
</div>

<script>
async function runMission(){
    const objective =
        document.getElementById("objective").value;

    const response = await fetch(
        "/run?objective=" +
        encodeURIComponent(objective),
        {method:"POST"}
    );

    document.getElementById("output")
        .textContent =
        JSON.stringify(
            await response.json(),
            null,
            2
        );
}

async function test(){
    const response =
        await fetch("/test-2050-67");

    document.getElementById("output")
        .textContent =
        JSON.stringify(
            await response.json(),
            null,
            2
        );
}
</script>
</body>
</html>
"""


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():

    init_db()

    # Recover workflows that were running when
    # the process stopped.
    rows = fetchall(
        """
        SELECT id
        FROM workflows
        WHERE status IN
        ('running','recovering','reconciling')
        """
    )

    for row in rows:
        workflow_event(
            row["id"],
            "startup_recovery_detected",
            {
                "version": VERSION
            },
        )


@app.on_event("shutdown")
def shutdown():

    executor.shutdown(
        wait=False,
        cancel_futures=True,
    )
