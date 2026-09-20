import asyncio
import hashlib
import json
import math
import os
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY
# TARGET-2050.45
# ADAPTIVE-MISSION-INTELLIGENCE-ROUTER-CORE
# ============================================================

VERSION = "TARGET-2050.45"
BUILD = "ADAPTIVE-MISSION-INTELLIGENCE-ROUTER-CORE"

BASE = Path("/tmp/ai_infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Adaptive autonomous mission intelligence core."
)


# ============================================================
# DATABASE
# ============================================================

def db():
    connection = sqlite3.connect(
        DB_PATH,
        check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            mission_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL,
            updated_at REAL,
            attempts INTEGER DEFAULT 0,
            recovery_attempts INTEGER DEFAULT 0,
            result_json TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            event_type TEXT,
            data_json TEXT,
            created_at REAL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS repairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            attempt INTEGER,
            error_type TEXT,
            diagnosis_json TEXT,
            recovery_json TEXT,
            created_at REAL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS adaptive_policy (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER,
            policy_json TEXT,
            updated_at REAL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS policy_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version INTEGER,
            policy_json TEXT,
            reason TEXT,
            created_at REAL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS learning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            key TEXT,
            value_json TEXT,
            created_at REAL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS route_learning (
            route TEXT PRIMARY KEY,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            updated_at REAL
        )
    """)

    cursor.execute("""
        SELECT id
        FROM adaptive_policy
        WHERE id = 1
    """)

    if cursor.fetchone() is None:
        policy = {
            "retry_controlled_failures": True,
            "max_recovery_attempts": 2,
            "require_verification": True,
            "allow_runtime_policy_adaptation": True,
            "rollback_invalid_policy": True,
            "never_modify_credentials": True,
            "never_auto_redeploy": True
        }

        cursor.execute(
            """
            INSERT INTO adaptive_policy
            (
                id,
                version,
                policy_json,
                updated_at
            )
            VALUES
            (1, 1, ?, ?)
            """,
            (
                json.dumps(policy),
                time.time()
            )
        )

    connection.commit()
    connection.close()


init_db()


# ============================================================
# MODELS
# ============================================================

class CreateRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=10000)
    research: bool = True
    verify: bool = True
    remember: bool = True


# ============================================================
# DATABASE HELPERS
# ============================================================

def log_event(
    mission_id: str,
    event_type: str,
    data: Optional[Dict[str, Any]] = None
):
    connection = db()

    connection.execute(
        """
        INSERT INTO events
        (
            mission_id,
            event_type,
            data_json,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            mission_id,
            event_type,
            json.dumps(data or {}),
            time.time()
        )
    )

    connection.commit()
    connection.close()


def create_mission(
    mission_id: str,
    objective: str
):
    now = time.time()

    connection = db()

    connection.execute(
        """
        INSERT INTO missions
        (
            mission_id,
            objective,
            status,
            created_at,
            updated_at,
            attempts,
            recovery_attempts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "queued",
            now,
            now,
            0,
            0
        )
    )

    connection.commit()
    connection.close()


def update_mission(
    mission_id: str,
    status: Optional[str] = None,
    attempts: Optional[int] = None,
    recovery_attempts: Optional[int] = None,
    result: Optional[Dict[str, Any]] = None
):
    connection = db()

    fields = []
    values = []

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
        values.append(json.dumps(result))

    fields.append("updated_at = ?")
    values.append(time.time())

    values.append(mission_id)

    connection.execute(
        f"""
        UPDATE missions
        SET {", ".join(fields)}
        WHERE mission_id = ?
        """,
        values
    )

    connection.commit()
    connection.close()


def get_mission(
    mission_id: str
):
    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM missions
        WHERE mission_id = ?
        """,
        (mission_id,)
    ).fetchone()

    connection.close()

    if row is None:
        return None

    result = dict(row)

    if result.get("result_json"):
        try:
            result["result"] = json.loads(
                result["result_json"]
            )
        except Exception:
            result["result"] = result["result_json"]

    result.pop("result_json", None)

    return result


def get_events(
    mission_id: str
):
    connection = db()

    rows = connection.execute(
        """
        SELECT
            event_type,
            data_json,
            created_at
        FROM events
        WHERE mission_id = ?
        ORDER BY id ASC
        """,
        (mission_id,)
    ).fetchall()

    connection.close()

    events = []

    for row in rows:
        data = {}

        try:
            data = json.loads(row["data_json"])
        except Exception:
            pass

        events.append({
            "event_type": row["event_type"],
            "data": data,
            "created_at": row["created_at"]
        })

    return events


# ============================================================
# ADAPTIVE POLICY
# ============================================================

def get_policy():
    connection = db()

    row = connection.execute(
        """
        SELECT
            version,
            policy_json,
            updated_at
        FROM adaptive_policy
        WHERE id = 1
        """
    ).fetchone()

    connection.close()

    if row is None:
        return {
            "version": 0,
            "policy": {}
        }

    return {
        "version": row["version"],
        "policy": json.loads(row["policy_json"]),
        "updated_at": row["updated_at"]
    }


def validate_policy(
    policy: Dict[str, Any]
):
    required = [
        "retry_controlled_failures",
        "max_recovery_attempts",
        "require_verification",
        "allow_runtime_policy_adaptation",
        "rollback_invalid_policy",
        "never_modify_credentials",
        "never_auto_redeploy"
    ]

    for key in required:
        if key not in policy:
            return False

    if not isinstance(
        policy["max_recovery_attempts"],
        int
    ):
        return False

    if policy["max_recovery_attempts"] < 0:
        return False

    if policy["max_recovery_attempts"] > 10:
        return False

    if policy["never_modify_credentials"] is not True:
        return False

    if policy["never_auto_redeploy"] is not True:
        return False

    return True


def propose_policy_upgrade(
    reason: str
):
    current = get_policy()

    old_policy = dict(
        current["policy"]
    )

    candidate = dict(old_policy)

    if reason == "ControlledFailure":
        candidate[
            "max_recovery_attempts"
        ] = min(
            old_policy.get(
                "max_recovery_attempts",
                2
            ) + 1,
            3
        )

    if not validate_policy(candidate):
        return {
            "activated": False,
            "rolled_back": True,
            "reason": "candidate_policy_invalid",
            "version": current["version"]
        }

    new_version = current["version"] + 1

    connection = db()

    connection.execute(
        """
        INSERT INTO policy_history
        (
            version,
            policy_json,
            reason,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            new_version,
            json.dumps(candidate),
            reason,
            time.time()
        )
    )

    connection.execute(
        """
        UPDATE adaptive_policy
        SET
            version = ?,
            policy_json = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            new_version,
            json.dumps(candidate),
            time.time()
        )
    )

    connection.commit()
    connection.close()

    return {
        "activated": True,
        "rolled_back": False,
        "version_before": current["version"],
        "version_after": new_version,
        "reason": reason,
        "policy_valid": True,
        "candidate": candidate
    }


# ============================================================
# ERROR INTELLIGENCE
# ============================================================

def classify_error(
    error: Exception
):
    error_type = type(error).__name__

    if error_type == "ControlledFailure":
        return "recoverable_controlled_failure"

    if isinstance(error, TimeoutError):
        return "recoverable_timeout"

    if isinstance(error, ConnectionError):
        return "recoverable_connection"

    return "unknown_failure"


def diagnose_error(
    error: Exception
):
    category = classify_error(error)

    return {
        "error_type": type(error).__name__,
        "category": category,
        "safe_to_retry": category.startswith(
            "recoverable_"
        ),
        "message": str(error)
    }


# ============================================================
# TEST FAILURE INJECTION
# ============================================================

def should_inject_failure(
    objective: str,
    attempt: int
):
    markers = [
        "[TEST_RECOVERY]",
        "[TEST_SELF_UPGRADE]",
        "[TEST_ROUTER]"
    ]

    return (
        attempt == 1
        and any(
            marker in objective
            for marker in markers
        )
    )


class ControlledFailure(Exception):
    pass


# ============================================================
# ADAPTIVE ROUTER
# ============================================================

ROUTES = {
    "research": {
        "description":
            "Information gathering and evidence collection.",
        "base_score": 0.90
    },
    "analysis": {
        "description":
            "Reasoning, synthesis and structured analysis.",
        "base_score": 0.88
    },
    "verification": {
        "description":
            "Validation and evidence checking.",
        "base_score": 0.92
    },
    "recovery": {
        "description":
            "Failure recovery and retry strategy.",
        "base_score": 0.91
    },
    "memory": {
        "description":
            "Persistent learning and reusable knowledge.",
        "base_score": 0.86
    }
}


def get_route_learning(
    route: str
):
    connection = db()

    row = connection.execute(
        """
        SELECT
            successes,
            failures,
            total
        FROM route_learning
        WHERE route = ?
        """,
        (route,)
    ).fetchone()

    connection.close()

    if row is None:
        return {
            "successes": 0,
            "failures": 0,
            "total": 0
        }

    return dict(row)


def update_route_learning(
    route: str,
    success: bool
):
    current = get_route_learning(route)

    successes = current["successes"]
    failures = current["failures"]

    if success:
        successes += 1
    else:
        failures += 1

    total = successes + failures

    connection = db()

    connection.execute(
        """
        INSERT INTO route_learning
        (
            route,
            successes,
            failures,
            total,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(route)
        DO UPDATE SET
            successes = excluded.successes,
            failures = excluded.failures,
            total = excluded.total,
            updated_at = excluded.updated_at
        """,
        (
            route,
            successes,
            failures,
            total,
            time.time()
        )
    )

    connection.commit()
    connection.close()


def score_route(
    route: str,
    objective: str
):
    data = ROUTES[route]

    score = data["base_score"]

    learning = get_route_learning(route)

    if learning["total"] > 0:
        success_rate = (
            learning["successes"]
            / learning["total"]
        )

        score += (
            success_rate - 0.5
        ) * 0.10

    text = objective.lower()

    keywords = {
        "research": [
            "research",
            "find",
            "investigate",
            "evidence"
        ],
        "analysis": [
            "analyze",
            "analysis",
            "compare",
            "evaluate"
        ],
        "verification": [
            "verify",
            "validate",
            "check",
            "test"
        ],
        "recovery": [
            "recover",
            "failure",
            "repair"
        ],
        "memory": [
            "remember",
            "memory",
            "learn"
        ]
    }

    for keyword in keywords.get(route, []):
        if keyword in text:
            score += 0.08

    return round(
        min(score, 1.0),
        4
    )


def route_mission(
    objective: str,
    research: bool,
    verify: bool,
    remember: bool
):
    requirements = []

    if research:
        requirements.append("research")

    if verify:
        requirements.append("verification")

    if remember:
        requirements.append("memory")

    requirements.append("recovery")

    if any(
        word in objective.lower()
        for word in [
            "analyze",
            "analysis",
            "compare",
            "evaluate"
        ]
    ):
        requirements.append("analysis")

    scores = {
        route: score_route(
            route,
            objective
        )
        for route in requirements
    }

    ranked = sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True
    )

    primary = ranked[0][0]

    fallback_routes = [
        route
        for route, _ in ranked[1:]
    ]

    return {
        "requirements": requirements,
        "scores": scores,
        "primary_route": primary,
        "fallback_routes": fallback_routes,
        "route_count": len(requirements)
    }


# ============================================================
# MISSION EXECUTION
# ============================================================

async def execute_mission(
    mission_id: str,
    objective: str,
    attempt: int,
    research: bool,
    verify: bool,
    remember: bool,
    routing: Dict[str, Any]
):
    await asyncio.sleep(0.05)

    if should_inject_failure(
        objective,
        attempt
    ):
        raise ControlledFailure(
            "Controlled test failure injected."
        )

    primary = routing["primary_route"]

    log_event(
        mission_id,
        "route_selected",
        {
            "route": primary,
            "attempt": attempt,
            "routing": routing
        }
    )

    await asyncio.sleep(0.05)

    result = {
        "objective": objective,
        "route_used": primary,
        "research_enabled": research,
        "verification_enabled": verify,
        "memory_enabled": remember,
        "analysis": (
            "AI Infinity completed the mission "
            "through the adaptive mission router."
        ),
        "evidence": [
            "Mission accepted",
            "Adaptive route selected",
            "Execution completed"
        ],
        "confidence": 0.95
    }

    if remember:
        connection = db()

        connection.execute(
            """
            INSERT INTO learning
            (
                mission_id,
                key,
                value_json,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                mission_id,
                "mission_result",
                json.dumps(result),
                time.time()
            )
        )

        connection.commit()
        connection.close()

    update_route_learning(
        primary,
        True
    )

    return result


# ============================================================
# VERIFICATION
# ============================================================

def verify_result(
    result: Dict[str, Any]
):
    required = [
        "objective",
        "route_used",
        "analysis",
        "evidence",
        "confidence"
    ]

    missing = [
        key
        for key in required
        if key not in result
    ]

    return {
        "verified": len(missing) == 0,
        "missing": missing,
        "confidence": result.get(
            "confidence",
            0
        )
    }


# ============================================================
# RECOVERY
# ============================================================

async def recover_mission(
    mission_id: str,
    attempt: int,
    error: Exception
):
    diagnosis = diagnose_error(error)

    log_event(
        mission_id,
        "failure_diagnosed",
        diagnosis
    )

    if not diagnosis["safe_to_retry"]:
        return {
            "recovered": False,
            "diagnosis": diagnosis,
            "reason": "unsafe_to_retry"
        }

    policy = get_policy()

    upgrade = None

    if policy["policy"].get(
        "allow_runtime_policy_adaptation",
        False
    ):
        upgrade = propose_policy_upgrade(
            diagnosis["error_type"]
        )

    recovery = {
        "recovered": True,
        "diagnosis": diagnosis,
        "policy_upgrade": upgrade,
        "retry_allowed": True
    }

    connection = db()

    connection.execute(
        """
        INSERT INTO repairs
        (
            mission_id,
            attempt,
            error_type,
            diagnosis_json,
            recovery_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            attempt,
            diagnosis["error_type"],
            json.dumps(diagnosis),
            json.dumps(recovery),
            time.time()
        )
    )

    connection.commit()
    connection.close()

    log_event(
        mission_id,
        "recovery_completed",
        recovery
    )

    return recovery


# ============================================================
# AUTONOMOUS MISSION LOOP
# ============================================================

async def autonomous_mission(
    mission_id: str,
    objective: str,
    research: bool,
    verify: bool,
    remember: bool
):
    policy = get_policy()

    routing = route_mission(
        objective,
        research,
        verify,
        remember
    )

    log_event(
        mission_id,
        "mission_routing",
        routing
    )

    max_attempts = (
        policy["policy"].get(
            "max_recovery_attempts",
            2
        ) + 1
    )

    attempts = 0
    recovery_attempts = 0

    while attempts < max_attempts:
        attempts += 1

        update_mission(
            mission_id,
            status="running",
            attempts=attempts,
            recovery_attempts=recovery_attempts
        )

        log_event(
            mission_id,
            "attempt_started",
            {
                "attempt": attempts,
                "routing": routing
            }
        )

        try:
            result = await execute_mission(
                mission_id,
                objective,
                attempts,
                research,
                verify,
                remember,
                routing
            )

            verification = verify_result(
                result
            )

            log_event(
                mission_id,
                "verification_completed",
                verification
            )

            if (
                policy["policy"].get(
                    "require_verification",
                    True
                )
                and not verification["verified"]
            ):
                raise RuntimeError(
                    "Mission verification failed."
                )

            final_result = {
                **result,
                "verification": verification,
                "attempts": attempts,
                "recovery_attempts":
                    recovery_attempts,
                "policy_version":
                    get_policy()["version"],
                "routing": routing
            }

            update_mission(
                mission_id,
                status="completed",
                attempts=attempts,
                recovery_attempts=recovery_attempts,
                result=final_result
            )

            log_event(
                mission_id,
                "mission_completed",
                final_result
            )

            return final_result

        except Exception as error:
            update_route_learning(
                routing["primary_route"],
                False
            )

            log_event(
                mission_id,
                "attempt_failed",
                {
                    "attempt": attempts,
                    "error_type":
                        type(error).__name__,
                    "error":
                        str(error)
                }
            )

            if recovery_attempts >= (
                policy["policy"].get(
                    "max_recovery_attempts",
                    2
                )
            ):
                failure = {
                    "error":
                        str(error),
                    "error_type":
                        type(error).__name__,
                    "attempts":
                        attempts,
                    "recovery_attempts":
                        recovery_attempts
                }

                update_mission(
                    mission_id,
                    status="failed",
                    attempts=attempts,
                    recovery_attempts=recovery_attempts,
                    result=failure
                )

                return failure

            recovery_attempts += 1

            recovery = await recover_mission(
                mission_id,
                attempts,
                error
            )

            if not recovery["recovered"]:
                failure = {
                    "error":
                        str(error),
                    "diagnosis":
                        recovery,
                    "attempts":
                        attempts,
                    "recovery_attempts":
                        recovery_attempts
                }

                update_mission(
                    mission_id,
                    status="failed",
                    attempts=attempts,
                    recovery_attempts=recovery_attempts,
                    result=failure
                )

                return failure

            policy = get_policy()

            max_attempts = (
                policy["policy"].get(
                    "max_recovery_attempts",
                    2
                ) + 1
            )

            log_event(
                mission_id,
                "retry_scheduled",
                {
                    "next_attempt":
                        attempts + 1,
                    "policy_version":
                        policy["version"]
                }
            )

    failure = {
        "error": "Maximum attempts exhausted.",
        "attempts": attempts,
        "recovery_attempts": recovery_attempts
    }

    update_mission(
        mission_id,
        status="failed",
        attempts=attempts,
        recovery_attempts=recovery_attempts,
        result=failure
    )

    return failure


# ============================================================
# BACKGROUND MISSION
# ============================================================

async def _background_mission(
    mission_id: str,
    objective: str,
    research: bool,
    verify: bool,
    remember: bool
):
    try:
        await autonomous_mission(
            mission_id,
            objective,
            research,
            verify,
            remember
        )

    except Exception as error:
        traceback.print_exc()

        failure = {
            "error":
                str(error),
            "error_type":
                type(error).__name__
        }

        update_mission(
            mission_id,
            status="failed",
            result=failure
        )

        log_event(
            mission_id,
            "fatal_mission_error",
            failure
        )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():
    return {
        "name": "AI Infinity",
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "docs": "/docs",
        "health": "/health",
        "run": "/run",
        "router_test": "/test-router"
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():
    policy = get_policy()

    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "policy_version":
            policy["version"],
        "policy_valid":
            validate_policy(
                policy["policy"]
            ),
        "router_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
async def status():
    policy = get_policy()

    return {
        "status": "operational",
        "version": VERSION,
        "build": BUILD,
        "policy": policy,
        "routes": list(
            ROUTES.keys()
        )
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():
    return {
        "version": VERSION,
        "capabilities": [
            "mission_execution",
            "adaptive_routing",
            "research_routing",
            "analysis_routing",
            "verification_routing",
            "recovery_routing",
            "memory_routing",
            "failure_diagnosis",
            "autonomous_recovery",
            "validated_runtime_policy_adaptation",
            "rollback_protection",
            "mission_verification",
            "route_learning"
        ],
        "safety_boundaries": [
            "never_modify_credentials",
            "never_auto_redeploy",
            "validated_policy_only"
        ]
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
async def policy():
    current = get_policy()

    return {
        "version":
            current["version"],
        "policy":
            current["policy"],
        "valid":
            validate_policy(
                current["policy"]
            ),
        "updated_at":
            current["updated_at"]
    }


# ============================================================
# RUN — GET
# ============================================================

@app.get("/run")
async def run_info():
    return {
        "status": "ready",
        "message":
            "Use POST /run to start an adaptive AI Infinity mission.",
        "docs": "/docs",
        "example": {
            "objective":
                "Analyze the reliability of autonomous AI agents.",
            "research": True,
            "verify": True,
            "remember": True
        },
        "test_markers": {
            "[TEST_RECOVERY]":
                "validates autonomous failure recovery",
            "[TEST_SELF_UPGRADE]":
                "validates adaptive policy modification",
            "[TEST_ROUTER]":
                "validates adaptive mission routing"
        }
    }


# ============================================================
# RUN — POST
# ============================================================

@app.post("/run")
async def run(
    request: CreateRequest
):
    mission_id = (
        "mission-"
        + hashlib.sha256(
            (
                request.objective
                + str(time.time_ns())
            ).encode()
        ).hexdigest()[:13]
    )

    create_mission(
        mission_id,
        request.objective
    )

    log_event(
        mission_id,
        "mission_created",
        {
            "objective":
                request.objective
        }
    )

    asyncio.create_task(
        _background_mission(
            mission_id,
            request.objective,
            request.research,
            request.verify,
            request.remember
        )
    )

    return {
        "status": "accepted",
        "mission_id": mission_id,
        "version": VERSION,
        "build": BUILD,
        "mission_url":
            f"/mission/{mission_id}",
        "events_url":
            f"/mission/{mission_id}/events"
    }


# ============================================================
# MISSION
# ============================================================

@app.get("/mission/{mission_id}")
async def mission(
    mission_id: str
):
    result = get_mission(
        mission_id
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Mission not found."
        )

    return result


# ============================================================
# MISSION EVENTS
# ============================================================

@app.get(
    "/mission/{mission_id}/events"
)
async def mission_events(
    mission_id: str
):
    result = get_mission(
        mission_id
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Mission not found."
        )

    return {
        "mission_id":
            mission_id,
        "events":
            get_events(mission_id)
    }


# ============================================================
# ONE-TAP ADAPTIVE ROUTER TEST
# ============================================================

@app.get("/test-router")
async def test_router():

    objective = (
        "Test adaptive mission routing and autonomous "
        "capability selection [TEST_ROUTER]"
    )

    mission_id = (
        "mission-"
        + hashlib.sha256(
            (
                objective
                + str(time.time_ns())
            ).encode()
        ).hexdigest()[:13]
    )

    create_mission(
        mission_id,
        objective
    )

    routing = route_mission(
        objective,
        True,
        True,
        True
    )

    log_event(
        mission_id,
        "one_tap_router_test_started",
        {
            "routing": routing
        }
    )

    await _background_mission(
        mission_id,
        objective,
        True,
        True,
        True
    )

    result = get_mission(
        mission_id
    )

    return {
        "test":
            "ADAPTIVE_ROUTER",
        "version":
            VERSION,
        "build":
            BUILD,
        "mission_id":
            mission_id,
        "status":
            result.get("status"),
        "routing":
            routing,
        "mission":
            result
    }


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                "8000"
            )
        )
    )
