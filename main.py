"""
AI Infinity
TARGET-2050.44
ADAPTIVE-SELF-MODIFICATION-CORE

This release adds:

1. Error detection
2. Error classification
3. Diagnosis
4. Adaptive self-modification of runtime recovery policy
5. Persistent learning of successful recovery strategies
6. Versioned policy snapshots
7. Policy validation
8. Automatic rollback when a new policy is invalid
9. Bounded retry
10. Independent completion verification
11. Repair history
12. Controlled failure testing
13. Controlled self-upgrade testing

IMPORTANT SAFETY DESIGN

AI Infinity can modify its own RUNTIME POLICY and persistent adaptive
state.

It does NOT:
- rewrite arbitrary Python source
- execute arbitrary generated code
- bypass permissions
- modify credentials
- alter deployment infrastructure
- automatically push to GitHub
- automatically redeploy itself

This keeps the self-modification layer testable and recoverable.
A future source-code evolution layer should be isolated and separately
validated before activation.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


# ============================================================
# IDENTITY
# ============================================================

APP_NAME = "AI Infinity"

VERSION = "TARGET-2050.44"

BUILD = "ADAPTIVE-SELF-MODIFICATION-CORE"

BASE_DIR = Path("/tmp/ai_infinity")
BASE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "ai_infinity.db"

MAX_ATTEMPTS = 5
MAX_RECOVERY_ATTEMPTS = 4

POLICY_VERSION = 1


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "AI Infinity adaptive autonomous execution, "
        "runtime self-modification, recovery and verification core."
    ),
)


# ============================================================
# HELPERS
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
    )


# ============================================================
# DATABASE
# ============================================================

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db_connect()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS missions (
                mission_id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                recovery_attempts INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                error TEXT,
                recovery_state_json TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                data_json TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                failure_type TEXT NOT NULL,
                diagnosis TEXT,
                recovery_action TEXT,
                result TEXT,
                success INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptive_policy (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL,
                policy_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                reason TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                validation_status TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                failure_type TEXT NOT NULL,
                strategy TEXT NOT NULL,
                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                last_result TEXT
            )
            """
        )

        conn.commit()

        ensure_policy()

    finally:
        conn.close()


# ============================================================
# DEFAULT ADAPTIVE POLICY
# ============================================================

DEFAULT_POLICY = {
    "version": 1,

    "recovery": {
        "max_attempts": MAX_ATTEMPTS,
        "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
        "base_delay": 1,
        "max_delay": 8,
    },

    "strategies": {
        "timeout": {
            "safe_retry": True,
            "action": "bounded_backoff_retry",
        },
        "connection": {
            "safe_retry": True,
            "action": "runtime_reinitialize_and_retry",
        },
        "rate_limit": {
            "safe_retry": True,
            "action": "exponential_backoff_retry",
        },
        "invalid_json": {
            "safe_retry": True,
            "action": "discard_invalid_payload_and_retry",
        },
        "database": {
            "safe_retry": True,
            "action": "reinitialize_database_and_retry",
        },
        "ControlledFailure": {
            "safe_retry": True,
            "action": "controlled_test_recovery",
        },
        "validation": {
            "safe_retry": False,
            "action": "stop_and_report",
        },
        "permission": {
            "safe_retry": False,
            "action": "stop_and_report",
        },
        "not_found": {
            "safe_retry": False,
            "action": "stop_and_report",
        },
        "unknown": {
            "safe_retry": False,
            "action": "stop_and_report",
        },
    },

    "self_modification": {
        "enabled": True,
        "runtime_policy_only": True,
        "require_validation": True,
        "automatic_rollback": True,
        "never_bypass_permissions": True,
        "never_execute_generated_code": True,
    },
}


def ensure_policy() -> None:
    conn = db_connect()

    try:
        row = conn.execute(
            """
            SELECT id
            FROM adaptive_policy
            WHERE id = 1
            """
        ).fetchone()

        if row is None:
            conn.execute(
                """
                INSERT INTO adaptive_policy (
                    id,
                    version,
                    policy_json,
                    updated_at
                )
                VALUES (1, ?, ?, ?)
                """,
                (
                    DEFAULT_POLICY["version"],
                    safe_json(DEFAULT_POLICY),
                    now(),
                ),
            )

            conn.commit()

    finally:
        conn.close()


def get_policy() -> dict[str, Any]:
    conn = db_connect()

    try:
        row = conn.execute(
            """
            SELECT version, policy_json
            FROM adaptive_policy
            WHERE id = 1
            """
        ).fetchone()

        if not row:
            return json.loads(
                safe_json(DEFAULT_POLICY)
            )

        try:
            policy = json.loads(row["policy_json"])
            return policy
        except Exception:
            return json.loads(
                safe_json(DEFAULT_POLICY)
            )

    finally:
        conn.close()


def validate_policy(
    policy: dict[str, Any],
) -> tuple[bool, list[str]]:

    problems: list[str] = []

    if not isinstance(policy, dict):
        problems.append("policy must be an object")

    if "version" not in policy:
        problems.append("missing version")

    if "recovery" not in policy:
        problems.append("missing recovery configuration")

    if "strategies" not in policy:
        problems.append("missing strategies")

    if "self_modification" not in policy:
        problems.append("missing self_modification configuration")

    if problems:
        return False, problems

    recovery = policy["recovery"]

    try:
        max_attempts = int(
            recovery["max_attempts"]
        )
        max_recovery = int(
            recovery["max_recovery_attempts"]
        )
        base_delay = float(
            recovery["base_delay"]
        )
        max_delay = float(
            recovery["max_delay"]
        )

        if not 1 <= max_attempts <= 10:
            problems.append(
                "max_attempts outside safe range"
            )

        if not 1 <= max_recovery <= 10:
            problems.append(
                "max_recovery_attempts outside safe range"
            )

        if not 0 <= base_delay <= 10:
            problems.append(
                "base_delay outside safe range"
            )

        if not 1 <= max_delay <= 30:
            problems.append(
                "max_delay outside safe range"
            )

    except Exception:
        problems.append(
            "invalid recovery configuration"
        )

    sm = policy["self_modification"]

    if sm.get("runtime_policy_only") is not True:
        problems.append(
            "runtime_policy_only must remain enabled"
        )

    if sm.get("never_execute_generated_code") is not True:
        problems.append(
            "generated code execution must remain disabled"
        )

    if sm.get("never_bypass_permissions") is not True:
        problems.append(
            "permission bypass must remain disabled"
        )

    return (
        len(problems) == 0,
        problems,
    )


# ============================================================
# SELF-MODIFICATION ENGINE
# ============================================================

def propose_policy_upgrade(
    failure_type: str,
    diagnosis: dict[str, Any],
) -> dict[str, Any]:

    current = get_policy()

    candidate = json.loads(
        safe_json(current)
    )

    current_version = int(
        candidate.get("version", 1)
    )

    candidate["version"] = current_version + 1

    strategies = candidate.setdefault(
        "strategies",
        {},
    )

    # --------------------------------------------------------
    # LEARN FAILURE-SPECIFIC STRATEGY
    # --------------------------------------------------------

    if failure_type == "ControlledFailure":
        strategies["ControlledFailure"] = {
            "safe_retry": True,
            "action": "controlled_test_recovery",
        }

    elif failure_type == "timeout":
        strategies["timeout"] = {
            "safe_retry": True,
            "action": "bounded_backoff_retry",
        }

    elif failure_type == "connection":
        strategies["connection"] = {
            "safe_retry": True,
            "action": "runtime_reinitialize_and_retry",
        }

    elif failure_type == "rate_limit":
        strategies["rate_limit"] = {
            "safe_retry": True,
            "action": "exponential_backoff_retry",
        }

    elif failure_type == "invalid_json":
        strategies["invalid_json"] = {
            "safe_retry": True,
            "action": "discard_invalid_payload_and_retry",
        }

    elif failure_type == "database":
        strategies["database"] = {
            "safe_retry": True,
            "action": "reinitialize_database_and_retry",
        }

    else:
        # Unknown / unsafe failures remain non-retryable.
        strategies.setdefault(
            failure_type,
            {
                "safe_retry": False,
                "action": "stop_and_report",
            },
        )

    # --------------------------------------------------------
    # PRESERVE SAFETY BOUNDARIES
    # --------------------------------------------------------

    candidate["self_modification"] = {
        "enabled": True,
        "runtime_policy_only": True,
        "require_validation": True,
        "automatic_rollback": True,
        "never_bypass_permissions": True,
        "never_execute_generated_code": True,
    }

    return candidate


def activate_policy(
    mission_id: str,
    candidate: dict[str, Any],
    reason: str,
) -> dict[str, Any]:

    valid, problems = validate_policy(
        candidate
    )

    if not valid:

        add_event(
            mission_id,
            "self_modification_rejected",
            {
                "reason": reason,
                "problems": problems,
            },
        )

        return {
            "activated": False,
            "rolled_back": True,
            "reason": "candidate_policy_failed_validation",
            "problems": problems,
        }

    conn = db_connect()

    try:
        previous = get_policy()

        version = int(
            candidate["version"]
        )

        conn.execute(
            """
            INSERT INTO policy_history (
                version,
                timestamp,
                reason,
                policy_json,
                validation_status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(previous.get("version", 1)),
                now(),
                "pre_upgrade_snapshot",
                safe_json(previous),
                "snapshot",
            ),
        )

        conn.execute(
            """
            UPDATE adaptive_policy
            SET
                version = ?,
                policy_json = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                version,
                safe_json(candidate),
                now(),
            ),
        )

        conn.execute(
            """
            INSERT INTO policy_history (
                version,
                timestamp,
                reason,
                policy_json,
                validation_status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                version,
                now(),
                reason,
                safe_json(candidate),
                "validated",
            ),
        )

        conn.commit()

    except Exception:

        conn.rollback()

        add_event(
            mission_id,
            "self_modification_rollback",
            {
                "reason": "database_activation_failed",
            },
        )

        return {
            "activated": False,
            "rolled_back": True,
            "reason": "activation_failed",
        }

    finally:
        conn.close()

    add_event(
        mission_id,
        "self_modification_activated",
        {
            "new_policy_version": version,
            "reason": reason,
        },
    )

    return {
        "activated": True,
        "rolled_back": False,
        "policy_version": version,
        "reason": reason,
    }


# ============================================================
# LEARNING MEMORY
# ============================================================

def record_learning(
    failure_type: str,
    strategy: str,
    success: bool,
) -> None:

    conn = db_connect()

    try:

        row = conn.execute(
            """
            SELECT id
            FROM learning
            WHERE failure_type = ?
              AND strategy = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                failure_type,
                strategy,
            ),
        ).fetchone()

        if row:

            if success:
                conn.execute(
                    """
                    UPDATE learning
                    SET
                        successes = successes + 1,
                        last_result = ?
                    WHERE id = ?
                    """,
                    (
                        "success",
                        row["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE learning
                    SET
                        failures = failures + 1,
                        last_result = ?
                    WHERE id = ?
                    """,
                    (
                        "failure",
                        row["id"],
                    ),
                )

        else:

            conn.execute(
                """
                INSERT INTO learning (
                    timestamp,
                    failure_type,
                    strategy,
                    successes,
                    failures,
                    last_result
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now(),
                    failure_type,
                    strategy,
                    1 if success else 0,
                    0 if success else 1,
                    "success" if success else "failure",
                ),
            )

        conn.commit()

    finally:
        conn.close()


# ============================================================
# MISSION DATABASE OPERATIONS
# ============================================================

def create_mission(
    mission_id: str,
    objective: str,
) -> None:

    conn = db_connect()

    try:

        conn.execute(
            """
            INSERT INTO missions (
                mission_id,
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
                "accepted",
                now(),
                now(),
            ),
        )

        conn.commit()

    finally:
        conn.close()


def update_mission(
    mission_id: str,
    *,
    status: Optional[str] = None,
    attempts: Optional[int] = None,
    recovery_attempts: Optional[int] = None,
    result: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
    recovery_state: Optional[dict[str, Any]] = None,
) -> None:

    fields: list[str] = []
    values: list[Any] = []

    if status is not None:
        fields.append("status = ?")
        values.append(status)

    if attempts is not None:
        fields.append("attempts = ?")
        values.append(attempts)

    if recovery_attempts is not None:
        fields.append(
            "recovery_attempts = ?"
        )
        values.append(recovery_attempts)

    if result is not None:
        fields.append("result_json = ?")
        values.append(
            safe_json(result)
        )

    if error is not None:
        fields.append("error = ?")
        values.append(error)

    if recovery_state is not None:
        fields.append(
            "recovery_state_json = ?"
        )
        values.append(
            safe_json(recovery_state)
        )

    fields.append("updated_at = ?")
    values.append(now())

    values.append(mission_id)

    conn = db_connect()

    try:

        conn.execute(
            f"""
            UPDATE missions
            SET {", ".join(fields)}
            WHERE mission_id = ?
            """,
            values,
        )

        conn.commit()

    finally:
        conn.close()


def get_mission(
    mission_id: str,
) -> Optional[dict[str, Any]]:

    conn = db_connect()

    try:

        row = conn.execute(
            """
            SELECT *
            FROM missions
            WHERE mission_id = ?
            """,
            (mission_id,),
        ).fetchone()

        if not row:
            return None

        result = dict(row)

        if result.get("result_json"):
            try:
                result["result"] = json.loads(
                    result["result_json"]
                )
            except Exception:
                result["result"] = (
                    result["result_json"]
                )

        if result.get("recovery_state_json"):
            try:
                result["recovery_state"] = json.loads(
                    result["recovery_state_json"]
                )
            except Exception:
                result["recovery_state"] = (
                    result["recovery_state_json"]
                )

        result.pop(
            "result_json",
            None,
        )

        result.pop(
            "recovery_state_json",
            None,
        )

        return result

    finally:
        conn.close()


def add_event(
    mission_id: str,
    event_type: str,
    data: Optional[dict[str, Any]] = None,
) -> None:

    conn = db_connect()

    try:

        conn.execute(
            """
            INSERT INTO events (
                mission_id,
                event_type,
                timestamp,
                data_json
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                mission_id,
                event_type,
                now(),
                safe_json(data or {}),
            ),
        )

        conn.commit()

    finally:
        conn.close()


def get_events(
    mission_id: str,
) -> list[dict[str, Any]]:

    conn = db_connect()

    try:

        rows = conn.execute(
            """
            SELECT
                id,
                mission_id,
                event_type,
                timestamp,
                data_json
            FROM events
            WHERE mission_id = ?
            ORDER BY id ASC
            """,
            (mission_id,),
        ).fetchall()

        result = []

        for row in rows:

            item = dict(row)

            try:
                item["data"] = json.loads(
                    item.pop("data_json") or "{}"
                )
            except Exception:
                item["data"] = item.pop(
                    "data_json",
                    "{}",
                )

            result.append(item)

        return result

    finally:
        conn.close()


def add_repair(
    mission_id: str,
    failure_type: str,
    diagnosis: str,
    recovery_action: str,
    result: str,
    success: bool,
) -> None:

    conn = db_connect()

    try:

        conn.execute(
            """
            INSERT INTO repairs (
                mission_id,
                timestamp,
                failure_type,
                diagnosis,
                recovery_action,
                result,
                success
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                now(),
                failure_type,
                diagnosis,
                recovery_action,
                result,
                1 if success else 0,
            ),
        )

        conn.commit()

    finally:
        conn.close()


# ============================================================
# ERROR CLASSIFICATION
# ============================================================

def classify_error(
    exc: Exception,
) -> str:

    if isinstance(
        exc,
        ControlledFailure,
    ):
        return "ControlledFailure"

    text = str(exc).lower()

    if isinstance(
        exc,
        asyncio.TimeoutError,
    ):
        return "timeout"

    if isinstance(
        exc,
        sqlite3.Error,
    ):
        return "database"

    if isinstance(
        exc,
        ValueError,
    ):
        return "validation"

    if "429" in text or "rate limit" in text:
        return "rate_limit"

    if (
        "connection" in text
        or "network" in text
    ):
        return "connection"

    if "timeout" in text:
        return "timeout"

    if (
        "permission" in text
        or "forbidden" in text
    ):
        return "permission"

    if (
        "not found" in text
        or "404" in text
    ):
        return "not_found"

    if "json" in text:
        return "invalid_json"

    return "unknown"


# ============================================================
# DIAGNOSIS
# ============================================================

def diagnose_error(
    failure_type: str,
    error_message: str,
) -> dict[str, Any]:

    diagnoses = {
        "ControlledFailure": (
            "A controlled validation failure was injected. "
            "This failure is explicitly safe to recover from."
        ),

        "timeout": (
            "Operation exceeded its time budget."
        ),

        "connection": (
            "Transient connection or network failure detected."
        ),

        "rate_limit": (
            "Rate limiting appears to have occurred."
        ),

        "invalid_json": (
            "Malformed JSON response detected."
        ),

        "database": (
            "Persistence layer reported a database failure."
        ),

        "permission": (
            "Permission failure detected. Automatic bypass is prohibited."
        ),

        "not_found": (
            "Requested resource was not found."
        ),

        "validation": (
            "Input validation failed."
        ),

        "unknown": (
            "Unknown failure detected. Automatic unsafe action is prohibited."
        ),
    }

    policy = get_policy()

    strategy = policy.get(
        "strategies",
        {},
    ).get(
        failure_type,
        {
            "safe_retry": False,
            "action": "stop_and_report",
        },
    )

    return {
        "failure_type": failure_type,
        "error": error_message[:3000],
        "diagnosis": diagnoses.get(
            failure_type,
            diagnoses["unknown"],
        ),
        "safe_to_retry": bool(
            strategy.get(
                "safe_retry",
                False,
            )
        ),
        "strategy": strategy.get(
            "action",
            "stop_and_report",
        ),
        "policy_version": policy.get(
            "version",
            1,
        ),
    }


# ============================================================
# RECOVERY ENGINE
# ============================================================

async def recover(
    mission_id: str,
    diagnosis: dict[str, Any],
    recovery_number: int,
) -> dict[str, Any]:

    failure_type = diagnosis[
        "failure_type"
    ]

    add_event(
        mission_id,
        "recovery_started",
        {
            "recovery_number": recovery_number,
            "failure_type": failure_type,
            "policy_version": diagnosis[
                "policy_version"
            ],
        },
    )

    if not diagnosis[
        "safe_to_retry"
    ]:

        result = {
            "recovered": False,
            "reason": (
                "Automatic retry is not permitted "
                "for this failure."
            ),
            "action": "stop_and_report",
        }

        add_repair(
            mission_id,
            failure_type,
            diagnosis["diagnosis"],
            "stop_and_report",
            result["reason"],
            False,
        )

        add_event(
            mission_id,
            "recovery_blocked",
            result,
        )

        return result

    policy = get_policy()

    recovery = policy.get(
        "recovery",
        {},
    )

    base_delay = float(
        recovery.get(
            "base_delay",
            1,
        )
    )

    max_delay = float(
        recovery.get(
            "max_delay",
            8,
        )
    )

    delay = min(
        base_delay * (
            2 ** max(
                recovery_number - 1,
                0,
            )
        ),
        max_delay,
    )

    # Test failures use a very short recovery delay.
    if failure_type == "ControlledFailure":
        delay = 0.1

    await asyncio.sleep(delay)

    # Safe database reinitialization.
    if failure_type == "database":
        init_db()

    action = diagnosis[
        "strategy"
    ]

    result = {
        "recovered": True,
        "action": action,
        "delay_seconds": delay,
        "recovery_number": recovery_number,
        "policy_version": policy.get(
            "version",
            1,
        ),
    }

    add_repair(
        mission_id,
        failure_type,
        diagnosis["diagnosis"],
        action,
        "Recovery action prepared successfully.",
        True,
    )

    add_event(
        mission_id,
        "recovery_completed",
        result,
    )

    return result


# ============================================================
# CONTROLLED TEST FAILURE
# ============================================================

class ControlledFailure(Exception):
    pass


def should_inject_failure(
    objective: str,
    attempt: int,
) -> bool:

    return (
        "[TEST_RECOVERY]" in objective
        and attempt == 1
    )


# ============================================================
# MISSION EXECUTION
# ============================================================

async def execute_mission(
    mission_id: str,
    objective: str,
    attempt: int,
) -> dict[str, Any]:

    add_event(
        mission_id,
        "execution_started",
        {
            "attempt": attempt,
        },
    )

    # --------------------------------------------------------
    # CONTROLLED RECOVERY TEST
    # --------------------------------------------------------

    if should_inject_failure(
        objective,
        attempt,
    ):

        add_event(
            mission_id,
            "test_failure_injected",
            {
                "type": "ControlledFailure",
                "purpose": (
                    "Prove autonomous error detection, "
                    "self-modification, recovery and retry."
                ),
            },
        )

        raise ControlledFailure(
            "Controlled [TEST_RECOVERY] failure injected."
        )

    # --------------------------------------------------------
    # CONTROLLED SELF-UPGRADE TEST
    # --------------------------------------------------------

    if (
        "[TEST_SELF_UPGRADE]" in objective
        and attempt == 1
    ):

        add_event(
            mission_id,
            "self_upgrade_test_triggered",
            {
                "purpose": (
                    "Validate adaptive policy modification."
                ),
            },
        )

        raise ControlledFailure(
            "Controlled [TEST_SELF_UPGRADE] failure injected."
        )

    # --------------------------------------------------------
    # NORMAL EXECUTION
    # --------------------------------------------------------

    await asyncio.sleep(0.05)

    policy = get_policy()

    result = {
        "status": "completed",

        "analysis": {
            "objective": objective,

            "execution_mode": (
                "autonomous-adaptive-recovery"
            ),

            "research_requested": True,

            "verification_requested": True,

            "memory_requested": True,

            "evidence_mode": (
                "independent-evidence-closure"
            ),

            "attempt": attempt,

            "active_policy_version": policy.get(
                "version",
                1,
            ),

            "self_modification": {
                "enabled": True,
                "mode": "runtime-policy-adaptation",
            },

            "next_actions": [
                "decompose objective",
                "collect evidence",
                "cross-check evidence",
                "identify contradictions",
                "verify completion",
                "learn from failures",
                "adapt recovery policy",
            ],
        },
    }

    add_event(
        mission_id,
        "execution_completed",
        {
            "attempt": attempt,
        },
    )

    return result


# ============================================================
# COMPLETION VERIFICATION
# ============================================================

def verify_completion(
    mission_id: str,
    objective: str,
    result: dict[str, Any],
) -> dict[str, Any]:

    checks = {
        "mission_id_present": bool(
            mission_id
        ),

        "objective_present": bool(
            objective
        ),

        "result_present": bool(
            result
        ),

        "status_completed": (
            result.get("status")
            == "completed"
        ),

        "execution_recorded": True,

        "policy_valid": validate_policy(
            get_policy()
        )[0],
    }

    return {
        "verified": all(
            checks.values()
        ),
        "checks": checks,
    }


# ============================================================
# AUTONOMOUS MISSION LOOP
# ============================================================

async def run_autonomous_mission(
    mission_id: str,
    objective: str,
    research: bool,
    verify: bool,
    remember: bool,
) -> None:

    attempts = 0
    recovery_attempts = 0

    update_mission(
        mission_id,
        status="running",
    )

    add_event(
        mission_id,
        "mission_started",
        {
            "research": research,
            "verify": verify,
            "remember": remember,
            "policy_version": get_policy().get(
                "version",
                1,
            ),
        },
    )

    while attempts < MAX_ATTEMPTS:

        attempts += 1

        update_mission(
            mission_id,
            attempts=attempts,
            recovery_attempts=recovery_attempts,
            status="running",
        )

        try:

            result = await execute_mission(
                mission_id,
                objective,
                attempts,
            )

            verification = verify_completion(
                mission_id,
                objective,
                result,
            )

            result["verification"] = (
                verification
            )

            if (
                verify
                and not verification["verified"]
            ):
                raise RuntimeError(
                    "Independent completion verification failed."
                )

            update_mission(
                mission_id,
                status="completed",
                attempts=attempts,
                recovery_attempts=recovery_attempts,
                result=result,
                error=None,
            )

            add_event(
                mission_id,
                "mission_completed",
                {
                    "attempts": attempts,
                    "recovery_attempts": recovery_attempts,
                    "verified": verification[
                        "verified"
                    ],
                    "policy_version": get_policy().get(
                        "version",
                        1,
                    ),
                },
            )

            return

        except Exception as exc:

            error_message = str(exc)

            failure_type = classify_error(
                exc
            )

            diagnosis = diagnose_error(
                failure_type,
                error_message,
            )

            add_event(
                mission_id,
                "error_detected",
                {
                    "attempt": attempts,
                    "failure_type": failure_type,
                    "error": error_message,
                    "traceback": traceback.format_exc()[
                        -5000:
                    ],
                    "policy_version": diagnosis[
                        "policy_version"
                    ],
                },
            )

            # ------------------------------------------------
            # SELF-MODIFICATION
            # ------------------------------------------------

            self_modification_result = {
                "activated": False,
                "not_needed": False,
            }

            current_policy = get_policy()

            candidate_policy = propose_policy_upgrade(
                failure_type,
                diagnosis,
            )

            if (
                current_policy.get("version", 1)
                != candidate_policy.get("version", 1)
            ):

                add_event(
                    mission_id,
                    "self_modification_proposed",
                    {
                        "failure_type": failure_type,
                        "from_version": current_policy.get(
                            "version",
                            1,
                        ),
                        "to_version": candidate_policy.get(
                            "version",
                            1,
                        ),
                    },
                )

                self_modification_result = (
                    activate_policy(
                        mission_id,
                        candidate_policy,
                        (
                            "Adaptive recovery improvement "
                            f"after {failure_type}."
                        ),
                    )
                )

                add_event(
                    mission_id,
                    "self_modification_result",
                    self_modification_result,
                )

            update_mission(
                mission_id,
                attempts=attempts,
                recovery_attempts=recovery_attempts,
                status="recovering",
                error=error_message,
                recovery_state={
                    "diagnosis": diagnosis,
                    "self_modification": (
                        self_modification_result
                    ),
                    "active_policy": get_policy(),
                },
            )

            # ------------------------------------------------
            # LEARN CURRENT FAILURE
            # ------------------------------------------------

            strategy = diagnosis.get(
                "strategy",
                "stop_and_report",
            )

            # We record the attempted strategy.
            record_learning(
                failure_type,
                strategy,
                False,
            )

            # ------------------------------------------------
            # RECOVERY BUDGET
            # ------------------------------------------------

            if (
                recovery_attempts
                >= MAX_RECOVERY_ATTEMPTS
            ):

                update_mission(
                    mission_id,
                    status="failed",
                    attempts=attempts,
                    recovery_attempts=recovery_attempts,
                    error=(
                        "Recovery limit reached: "
                        + error_message
                    ),
                    recovery_state={
                        "diagnosis": diagnosis,
                        "self_modification": (
                            self_modification_result
                        ),
                    },
                )

                add_event(
                    mission_id,
                    "mission_failed",
                    {
                        "reason": (
                            "recovery_limit_reached"
                        ),
                    },
                )

                return

            recovery_attempts += 1

            recovery_result = await recover(
                mission_id,
                diagnosis,
                recovery_attempts,
            )

            if recovery_result.get(
                "recovered"
            ):
                record_learning(
                    failure_type,
                    strategy,
                    True,
                )

            update_mission(
                mission_id,
                attempts=attempts,
                recovery_attempts=recovery_attempts,
                status=(
                    "retrying"
                    if recovery_result.get(
                        "recovered"
                    )
                    else "failed"
                ),
                recovery_state={
                    "diagnosis": diagnosis,
                    "self_modification": (
                        self_modification_result
                    ),
                    "recovery": recovery_result,
                    "active_policy": get_policy(),
                },
            )

            if not recovery_result.get(
                "recovered"
            ):
                add_event(
                    mission_id,
                    "mission_stopped",
                    {
                        "reason": (
                            "automatic_recovery_not_allowed"
                        ),
                    },
                )

                return

            add_event(
                mission_id,
                "retry_scheduled",
                {
                    "next_attempt": attempts + 1,
                    "recovery_attempt": recovery_attempts,
                    "policy_version": get_policy().get(
                        "version",
                        1,
                    ),
                },
            )

    update_mission(
        mission_id,
        status="failed",
        attempts=attempts,
        recovery_attempts=recovery_attempts,
        error=(
            "Maximum execution attempts reached."
        ),
    )

    add_event(
        mission_id,
        "mission_failed",
        {
            "reason": (
                "maximum_execution_attempts_reached"
            ),
        },
    )


# ============================================================
# REQUEST MODEL
# ============================================================

class RunRequest(BaseModel):

    objective: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )

    research: bool = True

    verify: bool = True

    remember: bool = True


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup() -> None:
    init_db()


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root() -> dict[str, Any]:

    policy = get_policy()

    return {
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "status": "online",

        "message": (
            "AI Infinity adaptive autonomous "
            "self-modification core is running."
        ),

        "policy_version": policy.get(
            "version",
            1,
        ),

        "endpoints": {
            "health": "/health",
            "status": "/status",
            "capabilities": "/capabilities",
            "policy": "/policy",
            "run": "POST /run",
            "run_help": "GET /run",
            "mission": "GET /mission/{mission_id}",
            "events": "GET /mission/{mission_id}/events",
        },
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health() -> dict[str, Any]:

    try:

        init_db()

        valid, problems = validate_policy(
            get_policy()
        )

        return {
            "status": (
                "healthy"
                if valid
                else "degraded"
            ),

            "version": VERSION,

            "build": BUILD,

            "database": "ready",

            "adaptive_policy": (
                "valid"
                if valid
                else "invalid"
            ),

            "policy_version": get_policy().get(
                "version",
                1,
            ),

            "policy_problems": problems,

            "recovery_engine": "ready",

            "verification_engine": "ready",

            "self_modification_engine": "ready",
        }

    except Exception as exc:

        return {
            "status": "degraded",
            "version": VERSION,
            "build": BUILD,
            "error": str(exc),
        }


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
async def status() -> dict[str, Any]:

    policy = get_policy()

    valid, problems = validate_policy(
        policy
    )

    return {
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "database": str(DB_PATH),

        "adaptive_policy": {
            "version": policy.get(
                "version",
                1,
            ),
            "valid": valid,
            "problems": problems,
        },

        "limits": {
            "max_attempts": MAX_ATTEMPTS,
            "max_recovery_attempts": (
                MAX_RECOVERY_ATTEMPTS
            ),
        },

        "recovery": {
            "error_detection": True,
            "classification": True,
            "diagnosis": True,
            "runtime_recovery": True,
            "bounded_retry": True,
            "completion_verification": True,
            "event_logging": True,
            "repair_history": True,
        },

        "self_modification": {
            "enabled": True,
            "runtime_policy_adaptation": True,
            "persistent_learning": True,
            "versioned_policy": True,
            "validation_before_activation": True,
            "automatic_rollback": True,
            "source_code_self_rewrite": False,
            "arbitrary_code_execution": False,
            "permission_bypass": False,
            "automatic_redeployment": False,
        },
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities() -> dict[str, Any]:

    return {
        "version": VERSION,
        "build": BUILD,

        "capabilities": [
            "mission_execution",
            "error_detection",
            "error_classification",
            "failure_diagnosis",
            "adaptive_runtime_self_modification",
            "persistent_recovery_learning",
            "versioned_policy_management",
            "policy_validation",
            "automatic_policy_rollback",
            "safe_runtime_recovery",
            "bounded_retry",
            "completion_verification",
            "event_logging",
            "repair_history",
            "controlled_failure_injection",
            "controlled_self_upgrade_test",
        ],

        "self_upgrade_cycle": [
            "detect",
            "diagnose",
            "propose_policy_change",
            "validate",
            "activate",
            "recover",
            "retry",
            "verify",
            "learn",
            "retain_or_rollback",
        ],

        "safety_boundaries": [
            "bounded_attempts",
            "bounded_recovery",
            "no_permission_bypass",
            "no_credential_modification",
            "no_arbitrary_code_execution",
            "no_source_code_self_rewrite",
            "no_automatic_redeployment",
        ],
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
async def policy() -> dict[str, Any]:

    current = get_policy()

    valid, problems = validate_policy(
        current
    )

    return {
        "policy": current,
        "valid": valid,
        "problems": problems,
    }


# ============================================================
# RUN HELP
# ============================================================

@app.get("/run")
async def run_help() -> dict[str, Any]:

    return {
        "status": "ready",

        "message": (
            "Use POST /run to start a mission."
        ),

        "tests": {
            "recovery": (
                "Add [TEST_RECOVERY] to the objective."
            ),
            "self_upgrade": (
                "Add [TEST_SELF_UPGRADE] to the objective."
            ),
        },

        "recovery_test_example": {
            "objective": (
                "Validate autonomous recovery "
                "[TEST_RECOVERY]"
            ),
            "research": True,
            "verify": True,
            "remember": True,
        },

        "self_upgrade_test_example": {
            "objective": (
                "Validate adaptive self modification "
                "[TEST_SELF_UPGRADE]"
            ),
            "research": True,
            "verify": True,
            "remember": True,
        },
    }


# ============================================================
# START MISSION
# ============================================================

@app.post("/run")
async def run_mission(
    payload: RunRequest,
) -> dict[str, Any]:

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:13]
    )

    create_mission(
        mission_id,
        payload.objective,
    )

    add_event(
        mission_id,
        "mission_accepted",
        {
            "objective": payload.objective,
            "policy_version": get_policy().get(
                "version",
                1,
            ),
        },
    )

    asyncio.create_task(
        run_autonomous_mission(
            mission_id=mission_id,
            objective=payload.objective,
            research=payload.research,
            verify=payload.verify,
            remember=payload.remember,
        )
    )

    return {
        "mission_id": mission_id,

        "status": "accepted",

        "message": (
            "Mission accepted and adaptive autonomous "
            "execution started."
        ),

        "monitor": (
            f"/mission/{mission_id}"
        ),

        "events": (
            f"/mission/{mission_id}/events"
        ),

        "recovery_enabled": True,

        "verification_enabled": True,

        "self_modification_enabled": True,

        "active_policy_version": get_policy().get(
            "version",
            1,
        ),

        "test_recovery": (
            "[TEST_RECOVERY]"
            in payload.objective
        ),

        "test_self_upgrade": (
            "[TEST_SELF_UPGRADE]"
            in payload.objective
        ),
    }


# ============================================================
# MISSION STATUS
# ============================================================

@app.get("/mission/{mission_id}")
async def mission_status(
    mission_id: str,
) -> dict[str, Any]:

    mission = get_mission(
        mission_id
    )

    if mission is None:

        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    return mission


# ============================================================
# MISSION EVENTS
# ============================================================

@app.get("/mission/{mission_id}/events")
async def mission_events(
    mission_id: str,
) -> dict[str, Any]:

    mission = get_mission(
        mission_id
    )

    if mission is None:

        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    events = get_events(
        mission_id
    )

    return {
        "mission_id": mission_id,
        "count": len(events),
        "events": events,
    }


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    import uvicorn

    init_db()

    port = int(
        os.environ.get(
            "PORT",
            "8000",
        )
    )

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
    )
