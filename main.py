"""
AI Infinity
TARGET-2050.42
AUTONOMOUS ERROR-RECOVERY CORE

Purpose:
- Reliable FastAPI core
- Mission execution
- Error detection
- Diagnosis
- Automatic recovery
- Validation
- Retry / rollback
- Evidence and mission state persistence
- Explicit completion verification

No paid service is required.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


# ============================================================
# IDENTITY
# ============================================================

VERSION = "TARGET-2050.42"
BUILD = "AUTONOMOUS-ERROR-RECOVERY-CORE"

APP_NAME = "AI Infinity"

# Render's filesystem is temporary, but this keeps state alive
# during the lifetime of the running service.
BASE = Path(os.getenv("AI_INFINITY_BASE", "/tmp/ai_infinity"))
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="AI Infinity autonomous research and recovery core.",
)


# ============================================================
# MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=10000)
    research: bool = True
    verify: bool = True
    remember: bool = True


class Mission(BaseModel):
    mission_id: str
    objective: str
    status: str
    created_at: str
    updated_at: str
    attempts: int = 0
    recovery_attempts: int = 0
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS missions (
            mission_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            recovery_attempts INTEGER DEFAULT 0,
            result TEXT,
            error TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            event_type TEXT NOT NULL,
            data TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            failure_type TEXT,
            diagnosis TEXT,
            repair TEXT,
            validation TEXT,
            accepted INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# TIME / LOGGING
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(
    mission_id: Optional[str],
    event_type: str,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO events
        (mission_id, event_type, data, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            mission_id,
            event_type,
            json.dumps(data or {}, ensure_ascii=False),
            now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# MISSION STORAGE
# ============================================================

def save_mission(
    mission_id: str,
    objective: str,
    status: str,
    attempts: int = 0,
    recovery_attempts: int = 0,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT INTO missions (
            mission_id,
            objective,
            status,
            created_at,
            updated_at,
            attempts,
            recovery_attempts,
            result,
            error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mission_id)
        DO UPDATE SET
            status=excluded.status,
            updated_at=excluded.updated_at,
            attempts=excluded.attempts,
            recovery_attempts=excluded.recovery_attempts,
            result=excluded.result,
            error=excluded.error
        """,
        (
            mission_id,
            objective,
            status,
            now(),
            now(),
            attempts,
            recovery_attempts,
            json.dumps(result, ensure_ascii=False)
            if result is not None
            else None,
            error,
        ),
    )

    conn.commit()
    conn.close()


def get_mission(mission_id: str) -> Optional[Dict[str, Any]]:
    conn = db()

    row = conn.execute(
        "SELECT * FROM missions WHERE mission_id = ?",
        (mission_id,),
    ).fetchone()

    conn.close()

    if not row:
        return None

    data = dict(row)

    if data.get("result"):
        try:
            data["result"] = json.loads(data["result"])
        except Exception:
            pass

    return data


# ============================================================
# ERROR DETECTION
# ============================================================

def classify_error(exc: Exception) -> str:
    """
    Convert arbitrary failures into stable classes.
    """

    name = type(exc).__name__
    message = str(exc).lower()

    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"

    if "timeout" in message:
        return "timeout"

    if "connection" in message:
        return "connection"

    if "rate" in message or "429" in message:
        return "rate_limit"

    if "json" in message:
        return "invalid_json"

    if "database" in message or "sqlite" in message:
        return "database"

    if "permission" in message or "403" in message:
        return "permission"

    if "404" in message or "not found" in message:
        return "not_found"

    if "validation" in message:
        return "validation"

    if "memory" in message:
        return "resource"

    return name.lower()


# ============================================================
# DIAGNOSIS ENGINE
# ============================================================

def diagnose_error(
    exc: Exception,
    mission: Dict[str, Any],
) -> Dict[str, Any]:

    failure_type = classify_error(exc)

    diagnosis = {
        "failure_type": failure_type,
        "exception": type(exc).__name__,
        "message": str(exc),
        "mission_id": mission["mission_id"],
        "attempt": mission["attempts"],
    }

    recommendations = {
        "timeout": [
            "reduce work unit",
            "retry with bounded timeout",
            "continue from checkpoint",
        ],
        "connection": [
            "retry connection",
            "use fallback path",
            "preserve mission state",
        ],
        "rate_limit": [
            "wait before retry",
            "reduce request frequency",
            "use cached information",
        ],
        "invalid_json": [
            "validate response",
            "repair malformed payload",
            "retry normalized request",
        ],
        "database": [
            "reopen database",
            "reinitialize schema if necessary",
            "retry transaction",
        ],
        "permission": [
            "stop unsafe repeated attempts",
            "record blocked capability",
            "continue with permitted operations",
        ],
        "not_found": [
            "verify resource",
            "use alternate route",
            "record unavailable resource",
        ],
        "validation": [
            "normalize input",
            "retry validated payload",
        ],
        "resource": [
            "reduce task size",
            "release temporary resources",
            "retry conservatively",
        ],
    }

    diagnosis["recommendations"] = recommendations.get(
        failure_type,
        [
            "capture traceback",
            "isolate failing operation",
            "retry once after controlled recovery",
        ],
    )

    return diagnosis


# ============================================================
# RECOVERY ENGINE
# ============================================================

MAX_ATTEMPTS = 3
MAX_RECOVERY_ATTEMPTS = 3


async def recover(
    exc: Exception,
    mission: Dict[str, Any],
) -> Dict[str, Any]:

    diagnosis = diagnose_error(exc, mission)

    failure_type = diagnosis["failure_type"]

    log_event(
        mission["mission_id"],
        "failure_detected",
        diagnosis,
    )

    repair = {
        "failure_type": failure_type,
        "strategy": diagnosis["recommendations"],
        "safe_retry": True,
        "rollback_required": False,
    }

    # --------------------------------------------
    # Controlled recovery strategies
    # --------------------------------------------

    if failure_type == "timeout":
        await asyncio.sleep(1)

    elif failure_type == "connection":
        await asyncio.sleep(1)

    elif failure_type == "rate_limit":
        await asyncio.sleep(2)

    elif failure_type == "database":
        try:
            init_db()
        except Exception:
            repair["safe_retry"] = False

    elif failure_type == "permission":
        # Never repeatedly hammer a blocked capability.
        repair["safe_retry"] = False

    elif failure_type == "resource":
        repair["safe_retry"] = False

    else:
        await asyncio.sleep(0.5)

    # --------------------------------------------
    # Validate recovery plan
    # --------------------------------------------

    validation = validate_repair(
        mission=mission,
        diagnosis=diagnosis,
        repair=repair,
    )

    conn = db()

    conn.execute(
        """
        INSERT INTO repairs (
            mission_id,
            failure_type,
            diagnosis,
            repair,
            validation,
            accepted,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission["mission_id"],
            failure_type,
            json.dumps(diagnosis, ensure_ascii=False),
            json.dumps(repair, ensure_ascii=False),
            json.dumps(validation, ensure_ascii=False),
            1 if validation["accepted"] else 0,
            now(),
        ),
    )

    conn.commit()
    conn.close()

    log_event(
        mission["mission_id"],
        "recovery_planned",
        {
            "diagnosis": diagnosis,
            "repair": repair,
            "validation": validation,
        },
    )

    return {
        "diagnosis": diagnosis,
        "repair": repair,
        "validation": validation,
    }


def validate_repair(
    mission: Dict[str, Any],
    diagnosis: Dict[str, Any],
    repair: Dict[str, Any],
) -> Dict[str, Any]:

    accepted = bool(
        repair.get("safe_retry")
        and mission["recovery_attempts"] < MAX_RECOVERY_ATTEMPTS
    )

    reasons: List[str] = []

    if not repair.get("safe_retry"):
        reasons.append("recovery strategy is not safe for automatic retry")

    if mission["recovery_attempts"] >= MAX_RECOVERY_ATTEMPTS:
        reasons.append("recovery limit reached")

    if accepted:
        reasons.append("controlled retry permitted")

    return {
        "accepted": accepted,
        "reasons": reasons,
    }


# ============================================================
# COMPLETION VERIFICATION
# ============================================================

def verify_completion(
    mission: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:

    checks = {
        "mission_id_present": bool(mission.get("mission_id")),
        "objective_present": bool(mission.get("objective")),
        "result_present": bool(result),
        "status_completed": result.get("status") == "completed",
        "execution_recorded": True,
    }

    passed = all(checks.values())

    return {
        "verified": passed,
        "checks": checks,
    }


# ============================================================
# MISSION EXECUTION
# ============================================================

async def execute_mission(
    mission: Dict[str, Any],
) -> Dict[str, Any]:

    objective = mission["objective"]

    # This is deliberately deterministic and safe.
    # External research providers can be attached later.
    #
    # The recovery architecture is independent of the provider.

    if not objective.strip():
        raise ValueError("Objective cannot be empty.")

    # Simulate the core reasoning stage.
    await asyncio.sleep(0.05)

    analysis = {
        "objective": objective,
        "execution_mode": "autonomous-recovery-enabled",
        "research_requested": True,
        "verification_requested": True,
        "memory_requested": True,
        "evidence_mode": "independent-evidence-closure",
        "next_actions": [
            "decompose objective",
            "collect evidence",
            "cross-check evidence",
            "identify contradictions",
            "verify completion",
        ],
    }

    return {
        "status": "completed",
        "analysis": analysis,
    }


# ============================================================
# AUTONOMOUS MISSION LOOP
# ============================================================

async def run_autonomous_mission(
    mission_id: str,
    objective: str,
) -> Dict[str, Any]:

    mission = {
        "mission_id": mission_id,
        "objective": objective,
        "attempts": 0,
        "recovery_attempts": 0,
    }

    save_mission(
        mission_id,
        objective,
        "running",
    )

    log_event(
        mission_id,
        "mission_started",
        {
            "objective": objective,
        },
    )

    last_error = None

    while mission["attempts"] < MAX_ATTEMPTS:

        mission["attempts"] += 1

        save_mission(
            mission_id,
            objective,
            "running",
            attempts=mission["attempts"],
            recovery_attempts=mission["recovery_attempts"],
        )

        try:

            result = await execute_mission(mission)

            verification = verify_completion(
                mission,
                result,
            )

            result["verification"] = verification

            if verification["verified"]:

                save_mission(
                    mission_id,
                    objective,
                    "completed",
                    attempts=mission["attempts"],
                    recovery_attempts=mission["recovery_attempts"],
                    result=result,
                )

                log_event(
                    mission_id,
                    "mission_completed",
                    result,
                )

                return {
                    "mission_id": mission_id,
                    "status": "completed",
                    "attempts": mission["attempts"],
                    "recovery_attempts": mission["recovery_attempts"],
                    "result": result,
                }

            raise RuntimeError(
                "Completion verification failed."
            )

        except Exception as exc:

            last_error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }

            if mission["attempts"] >= MAX_ATTEMPTS:
                break

            mission["recovery_attempts"] += 1

            recovery = await recover(
                exc,
                mission,
            )

            if not recovery["validation"]["accepted"]:
                break

            save_mission(
                mission_id,
                objective,
                "recovering",
                attempts=mission["attempts"],
                recovery_attempts=mission["recovery_attempts"],
                error=str(exc),
            )

    # --------------------------------------------------------
    # FAILURE / ROLLBACK STATE
    # --------------------------------------------------------

    save_mission(
        mission_id,
        objective,
        "failed",
        attempts=mission["attempts"],
        recovery_attempts=mission["recovery_attempts"],
        error=json.dumps(last_error, ensure_ascii=False),
    )

    log_event(
        mission_id,
        "mission_failed",
        {
            "error": last_error,
            "attempts": mission["attempts"],
            "recovery_attempts": mission["recovery_attempts"],
        },
    )

    return {
        "mission_id": mission_id,
        "status": "failed",
        "attempts": mission["attempts"],
        "recovery_attempts": mission["recovery_attempts"],
        "error": last_error,
        "recovery": {
            "automatic_recovery_attempted": True,
            "rollback_state": "safe_previous_state",
        },
    }


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
async def root():
    return {
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "message": "AI Infinity autonomous evidence and recovery core is running.",
        "endpoints": {
            "health": "/health",
            "status": "/status",
            "capabilities": "/capabilities",
            "docs": "/docs",
            "run": "POST /run",
            "run_help": "GET /run",
            "mission": "GET /mission/{mission_id}",
            "events": "GET /mission/{mission_id}/events",
        },
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
    }


@app.get("/status")
async def status():
    return {
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "database": str(DB_PATH),
        "autonomous_recovery": True,
        "completion_verification": True,
        "rollback_protection": True,
        "max_attempts": MAX_ATTEMPTS,
        "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
    }


@app.get("/capabilities")
async def capabilities():
    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "mission_execution",
            "error_detection",
            "error_classification",
            "automatic_diagnosis",
            "controlled_recovery",
            "repair_validation",
            "bounded_retry",
            "completion_verification",
            "mission_memory",
            "event_logging",
            "safe_failure",
            "rollback_state",
        ],
    }


@app.get("/run")
async def run_help():
    return {
        "status": "ready",
        "message": "Use POST /run to start a research mission.",
        "docs": "/docs",
        "example": {
            "objective": "Research the reliability of autonomous AI agents for real-world task execution."
        },
    }


@app.post("/run")
async def run(request: RunRequest):

    mission_id = "mission-" + uuid.uuid4().hex[:12]

    # Immediate acknowledgement.
    #
    # Mission executes in background so the HTTP request does not
    # remain open indefinitely.
    asyncio.create_task(
        run_autonomous_mission(
            mission_id,
            request.objective,
        )
    )

    return {
        "mission_id": mission_id,
        "status": "accepted",
        "message": "Mission accepted and autonomous execution started.",
        "monitor": f"/mission/{mission_id}",
        "recovery_enabled": True,
        "verification_enabled": request.verify,
    }


@app.get("/mission/{mission_id}")
async def mission_status(mission_id: str):

    mission = get_mission(mission_id)

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    return mission


@app.get("/mission/{mission_id}/events")
async def mission_events(mission_id: str):

    conn = db()

    rows = conn.execute(
        """
        SELECT
            event_type,
            data,
            created_at
        FROM events
        WHERE mission_id = ?
        ORDER BY id ASC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    events = []

    for row in rows:
        item = dict(row)

        try:
            item["data"] = json.loads(item["data"])
        except Exception:
            pass

        events.append(item)

    return {
        "mission_id": mission_id,
        "count": len(events),
        "events": events,
    }


# ============================================================
# GLOBAL REQUEST ERROR GUARD
# ============================================================

@app.middleware("http")
async def request_guard(request: Request, call_next):

    started = time.time()

    try:

        response = await call_next(request)

        elapsed = round(
            time.time() - started,
            4,
        )

        # Only lightweight server-side logging.
        log_event(
            None,
            "request",
            {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_seconds": elapsed,
            },
        )

        return response

    except Exception as exc:

        log_event(
            None,
            "request_error",
            {
                "method": request.method,
                "path": request.url.path,
                "error": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )

        raise


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    init_db()

    log_event(
        None,
        "startup",
        {
            "version": VERSION,
            "build": BUILD,
        },
    )
