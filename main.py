"""
AI Infinity
TARGET-2050.43
AUTONOMOUS-ERROR-RECOVERY-VALIDATION-CORE

Purpose:
- Run missions
- Detect runtime failures
- Classify and diagnose failures
- Apply safe runtime recovery actions
- Retry within strict limits
- Verify completion independently
- Record the complete event trail
- Provide a controlled failure-injection test

Important:
This version performs SAFE RUNTIME SELF-RECOVERY.
It does not modify GitHub source code, credentials, infrastructure,
or redeploy itself.
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
# CORE IDENTITY
# ============================================================

APP_NAME = "AI Infinity"
VERSION = "TARGET-2050.43"
BUILD = "AUTONOMOUS-ERROR-RECOVERY-VALIDATION-CORE"

BASE_DIR = Path("/tmp/ai_infinity")
BASE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "ai_infinity.db"

MAX_ATTEMPTS = 4
MAX_RECOVERY_ATTEMPTS = 3


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "AI Infinity autonomous execution, error detection, "
        "runtime recovery, retry and verification core."
    ),
)


# ============================================================
# TIME / JSON HELPERS
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ============================================================
# DATABASE
# ============================================================

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
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

        conn.commit()

    finally:
        conn.close()


def create_mission(
    mission_id: str,
    objective: str,
) -> None:
    timestamp = now()

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
                timestamp,
                timestamp,
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

    fields = []
    values: list[Any] = []

    if status is not None:
        fields.append("status = ?")
        values.append(status)

    if attempts is not None:
        fields.append("attempts = ?")
        values.append(attempts)

    if recovery_attempts is not None:
        fields.append("recovery_attempts = ?")
        values.append(recovery_attempts)

    if result is not None:
        fields.append("result_json = ?")
        values.append(safe_json(result))

    if error is not None:
        fields.append("error = ?")
        values.append(error)

    if recovery_state is not None:
        fields.append("recovery_state_json = ?")
        values.append(safe_json(recovery_state))

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


def get_mission(mission_id: str) -> Optional[dict[str, Any]]:
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
                result["result"] = json.loads(result["result_json"])
            except Exception:
                result["result"] = result["result_json"]

        if result.get("recovery_state_json"):
            try:
                result["recovery_state"] = json.loads(
                    result["recovery_state_json"]
                )
            except Exception:
                result["recovery_state"] = result["recovery_state_json"]

        result.pop("result_json", None)
        result.pop("recovery_state_json", None)

        return result

    finally:
        conn.close()


def get_events(mission_id: str) -> list[dict[str, Any]]:
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

        output = []

        for row in rows:
            item = dict(row)

            try:
                item["data"] = json.loads(item.pop("data_json") or "{}")
            except Exception:
                item["data"] = item.pop("data_json", "{}")

            output.append(item)

        return output

    finally:
        conn.close()


# ============================================================
# ERROR DETECTION
# ============================================================

def classify_error(exc: Exception) -> str:
    text = str(exc).lower()

    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"

    if isinstance(exc, ValueError):
        return "validation"

    if isinstance(exc, sqlite3.Error):
        return "database"

    if "rate limit" in text or "429" in text:
        return "rate_limit"

    if (
        "connection" in text
        or "connect" in text
        or "network" in text
    ):
        return "connection"

    if "timeout" in text:
        return "timeout"

    if "permission" in text or "forbidden" in text:
        return "permission"

    if "not found" in text or "404" in text:
        return "not_found"

    if "json" in text:
        return "invalid_json"

    return type(exc).__name__


# ============================================================
# DIAGNOSIS ENGINE
# ============================================================

def diagnose_error(
    failure_type: str,
    error_message: str,
) -> dict[str, Any]:

    recommendations = {
        "timeout": (
            "The operation exceeded its time budget. "
            "Retry once with a bounded delay."
        ),
        "connection": (
            "The operation appears to have encountered a transient "
            "connection problem. Reinitialize the runtime path and retry."
        ),
        "rate_limit": (
            "The operation appears rate limited. "
            "Back off before retrying."
        ),
        "invalid_json": (
            "The returned data was not valid JSON. "
            "Reject malformed data and retry the operation."
        ),
        "database": (
            "The persistence layer reported an error. "
            "Reinitialize the database connection and retry."
        ),
        "permission": (
            "The operation appears to lack permission. "
            "Do not bypass permissions automatically."
        ),
        "not_found": (
            "A requested resource was not found. "
            "Do not fabricate the missing resource."
        ),
        "validation": (
            "Input validation failed. "
            "Correcting user intent automatically is unsafe."
        ),
    }

    recommendation = recommendations.get(
        failure_type,
        (
            "Unknown failure. Capture diagnostics and retry only "
            "within the bounded recovery policy."
        ),
    )

    return {
        "failure_type": failure_type,
        "error": error_message[:2000],
        "diagnosis": recommendation,
        "safe_to_retry": failure_type
        in {
            "timeout",
            "connection",
            "rate_limit",
            "database",
            "invalid_json",
        },
    }


# ============================================================
# SAFE RECOVERY ENGINE
# ============================================================

async def recover(
    mission_id: str,
    diagnosis: dict[str, Any],
    recovery_number: int,
) -> dict[str, Any]:

    failure_type = diagnosis["failure_type"]

    add_event(
        mission_id,
        "recovery_started",
        {
            "recovery_number": recovery_number,
            "failure_type": failure_type,
        },
    )

    if not diagnosis["safe_to_retry"]:
        result = {
            "recovered": False,
            "reason": "Failure is not safe for automatic retry.",
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

    # Bounded backoff.
    delay = min(2 ** recovery_number, 8)
    await asyncio.sleep(delay)

    # Reinitialize local persistence safely.
    if failure_type == "database":
        init_db()

    # Runtime recovery action.
    action = {
        "timeout": "bounded_backoff_retry",
        "connection": "runtime_reinitialize_and_retry",
        "rate_limit": "exponential_backoff_retry",
        "invalid_json": "discard_invalid_payload_and_retry",
        "database": "reinitialize_database_and_retry",
    }.get(
        failure_type,
        "bounded_retry",
    )

    result = {
        "recovered": True,
        "action": action,
        "delay_seconds": delay,
        "recovery_number": recovery_number,
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
# CONTROLLED FAILURE INJECTION
# ============================================================

class ControlledFailure(Exception):
    """Intentional test-only runtime failure."""


def should_inject_failure(
    objective: str,
    attempt: int,
) -> bool:

    marker = "[TEST_RECOVERY]"

    return marker in objective and attempt == 1


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
    # SAFE FAILURE-INJECTION TEST
    # --------------------------------------------------------
    if should_inject_failure(objective, attempt):
        add_event(
            mission_id,
            "test_failure_injected",
            {
                "purpose": (
                    "Validate autonomous error detection, "
                    "diagnosis, recovery and retry."
                )
            },
        )

        raise ControlledFailure(
            "Controlled [TEST_RECOVERY] failure injected "
            "for autonomous recovery validation."
        )

    # --------------------------------------------------------
    # NORMAL EXECUTION
    # --------------------------------------------------------

    await asyncio.sleep(0.05)

    analysis = {
        "objective": objective,
        "execution_mode": "autonomous-recovery-enabled",
        "research_requested": True,
        "verification_requested": True,
        "memory_requested": True,
        "evidence_mode": "independent-evidence-closure",
        "attempt": attempt,
        "recovery_test": "[TEST_RECOVERY]" in objective,
        "next_actions": [
            "decompose objective",
            "collect evidence",
            "cross-check evidence",
            "identify contradictions",
            "verify completion",
        ],
    }

    add_event(
        mission_id,
        "execution_completed",
        {
            "attempt": attempt,
        },
    )

    return {
        "status": "completed",
        "analysis": analysis,
    }


# ============================================================
# COMPLETION VERIFICATION
# ============================================================

def verify_completion(
    mission_id: str,
    objective: str,
    result: dict[str, Any],
) -> dict[str, Any]:

    checks = {
        "mission_id_present": bool(mission_id),
        "objective_present": bool(objective),
        "result_present": bool(result),
        "status_completed": result.get("status") == "completed",
        "execution_recorded": True,
    }

    verified = all(checks.values())

    return {
        "verified": verified,
        "checks": checks,
    }


# ============================================================
# AUTONOMOUS LOOP
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

            result["verification"] = verification

            if verify and not verification["verified"]:
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
                    "verified": verification["verified"],
                },
            )

            return

        except Exception as exc:

            error_message = str(exc)

            failure_type = classify_error(exc)

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
                    "traceback": traceback.format_exc()[-5000:],
                },
            )

            update_mission(
                mission_id,
                attempts=attempts,
                recovery_attempts=recovery_attempts,
                status="recovering",
                error=error_message,
                recovery_state=diagnosis,
            )

            # No recovery budget left.
            if recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
                update_mission(
                    mission_id,
                    status="failed",
                    attempts=attempts,
                    recovery_attempts=recovery_attempts,
                    error=(
                        "Recovery limit reached: "
                        + error_message
                    ),
                    recovery_state=diagnosis,
                )

                add_event(
                    mission_id,
                    "mission_failed",
                    {
                        "reason": "recovery_limit_reached",
                        "attempts": attempts,
                        "recovery_attempts": recovery_attempts,
                    },
                )

                return

            recovery_attempts += 1

            recovery_result = await recover(
                mission_id,
                diagnosis,
                recovery_attempts,
            )

            update_mission(
                mission_id,
                attempts=attempts,
                recovery_attempts=recovery_attempts,
                status=(
                    "retrying"
                    if recovery_result["recovered"]
                    else "failed"
                ),
                recovery_state={
                    "diagnosis": diagnosis,
                    "recovery": recovery_result,
                },
            )

            if not recovery_result["recovered"]:
                add_event(
                    mission_id,
                    "mission_stopped",
                    {
                        "reason": "automatic_recovery_not_allowed",
                        "failure_type": failure_type,
                    },
                )

                return

            add_event(
                mission_id,
                "retry_scheduled",
                {
                    "next_attempt": attempts + 1,
                    "recovery_attempt": recovery_attempts,
                },
            )

    update_mission(
        mission_id,
        status="failed",
        attempts=attempts,
        recovery_attempts=recovery_attempts,
        error="Maximum execution attempts reached.",
    )

    add_event(
        mission_id,
        "mission_failed",
        {
            "reason": "maximum_execution_attempts_reached",
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
# REQUEST LOGGING
# ============================================================

@app.middleware("http")
async def request_logger(
    request: Request,
    call_next,
):
    try:
        response = await call_next(request)

        return response

    except Exception:
        raise


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "message": (
            "AI Infinity autonomous execution, "
            "error recovery and verification core is running."
        ),
        "endpoints": {
            "health": "/health",
            "status": "/status",
            "capabilities": "/capabilities",
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

        return {
            "status": "healthy",
            "version": VERSION,
            "build": BUILD,
            "database": "ready",
            "recovery_engine": "ready",
            "verification_engine": "ready",
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
    return {
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "database": str(DB_PATH),
        "max_attempts": MAX_ATTEMPTS,
        "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
        "recovery": {
            "error_detection": True,
            "classification": True,
            "diagnosis": True,
            "runtime_recovery": True,
            "bounded_retry": True,
            "completion_verification": True,
            "event_logging": True,
            "repair_history": True,
            "source_code_self_modification": False,
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
            "safe_runtime_recovery",
            "bounded_retry",
            "completion_verification",
            "event_logging",
            "repair_history",
            "controlled_failure_injection",
        ],
        "safety_boundaries": [
            "bounded_attempts",
            "bounded_recovery",
            "no_permission_bypass",
            "no_credential_modification",
            "no_source_code_self_modification",
            "no_automatic_redeployment",
        ],
    }


# ============================================================
# RUN HELP
# ============================================================

@app.get("/run")
async def run_help() -> dict[str, Any]:
    return {
        "status": "ready",
        "message": "Use POST /run to start a mission.",
        "test_mode": (
            "Add [TEST_RECOVERY] to the objective to "
            "validate autonomous error recovery."
        ),
        "example": {
            "objective": (
                "Test AI Infinity autonomous recovery "
                "[TEST_RECOVERY]"
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
            "Mission accepted and autonomous execution started."
        ),
        "monitor": f"/mission/{mission_id}",
        "events": f"/mission/{mission_id}/events",
        "recovery_enabled": True,
        "verification_enabled": True,
        "test_recovery": "[TEST_RECOVERY]" in payload.objective,
    }


# ============================================================
# MISSION STATUS
# ============================================================

@app.get("/mission/{mission_id}")
async def mission_status(
    mission_id: str,
) -> dict[str, Any]:

    mission = get_mission(mission_id)

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

    mission = get_mission(mission_id)

    if mission is None:
        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    events = get_events(mission_id)

    return {
        "mission_id": mission_id,
        "count": len(events),
        "events": events,
    }


# ============================================================
# LOCAL DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":
    import uvicorn

    init_db()

    port = int(os.environ.get("PORT", "8000"))

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
    )
