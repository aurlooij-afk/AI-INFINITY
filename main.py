# ============================================================
# ONE-TAP ROUTER TEST
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
        "test": "ADAPTIVE_ROUTER",
        "version": VERSION,
        "mission_id": mission_id,
        "status": result.get("status"),
        "routing": routing,
        "mission": result
    }

import os
import json
import sqlite3
import hashlib
import time
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY
# TARGET-2050.45
# ADAPTIVE MISSION INTELLIGENCE ROUTER CORE
# ============================================================

APP_NAME = "AI Infinity"
VERSION = "TARGET-2050.45"
BUILD = "ADAPTIVE-MISSION-INTELLIGENCE-ROUTER-CORE"

BASE_DIR = Path("/tmp/ai_infinity")
BASE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "ai_infinity.db"

MAX_ATTEMPTS = 5
MAX_RECOVERY_ATTEMPTS = 4


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "AI Infinity adaptive mission intelligence router with "
        "bounded autonomous recovery, verification, learning, "
        "and runtime policy adaptation."
    ),
)


# ============================================================
# TIME
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# DATABASE
# ============================================================

def db():
    connection = sqlite3.connect(str(DB_PATH))
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                mission_id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                recovery_attempts INTEGER DEFAULT 0,
                error TEXT,
                result_json TEXT,
                state_json TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS repairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                failure_type TEXT,
                diagnosis_json TEXT,
                action TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS adaptive_policy (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                version INTEGER NOT NULL,
                policy_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                policy_json TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS learning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                failure_type TEXT NOT NULL,
                strategy TEXT NOT NULL,
                success INTEGER NOT NULL,
                mission_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS route_learning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_name TEXT NOT NULL,
                objective_hash TEXT NOT NULL,
                success INTEGER NOT NULL,
                score REAL NOT NULL,
                mission_id TEXT,
                created_at TEXT NOT NULL
            );
            """
        )


init_db()


# ============================================================
# DEFAULT ADAPTIVE POLICY
# ============================================================

DEFAULT_POLICY = {
    "version": 1,

    "recovery": {
        "max_attempts": MAX_ATTEMPTS,
        "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
        "base_delay": 1,
        "max_delay": 8
    },

    "strategies": {
        "timeout": {
            "safe_retry": True,
            "action": "bounded_backoff_retry"
        },
        "connection": {
            "safe_retry": True,
            "action": "runtime_reinitialize_and_retry"
        },
        "rate_limit": {
            "safe_retry": True,
            "action": "exponential_backoff_retry"
        },
        "invalid_json": {
            "safe_retry": True,
            "action": "discard_invalid_payload_and_retry"
        },
        "database": {
            "safe_retry": True,
            "action": "reinitialize_database_and_retry"
        },
        "ControlledFailure": {
            "safe_retry": True,
            "action": "controlled_test_recovery"
        },
        "validation": {
            "safe_retry": False,
            "action": "stop_and_report"
        },
        "permission": {
            "safe_retry": False,
            "action": "stop_and_report"
        },
        "not_found": {
            "safe_retry": False,
            "action": "stop_and_report"
        },
        "unknown": {
            "safe_retry": False,
            "action": "stop_and_report"
        }
    },

    "self_modification": {
        "enabled": True,
        "runtime_policy_only": True,
        "require_validation": True,
        "automatic_rollback": True,
        "never_bypass_permissions": True,
        "never_execute_generated_code": True,
        "never_modify_credentials": True,
        "never_auto_redeploy": True
    }
}


def ensure_policy():
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM adaptive_policy WHERE id=1"
        ).fetchone()

        if row is None:
            conn.execute(
                """
                INSERT INTO adaptive_policy
                (id, version, policy_json, updated_at)
                VALUES (1, ?, ?, ?)
                """,
                (
                    DEFAULT_POLICY["version"],
                    json.dumps(DEFAULT_POLICY),
                    now()
                )
            )


ensure_policy()


def get_policy() -> Dict[str, Any]:
    ensure_policy()

    with db() as conn:
        row = conn.execute(
            "SELECT policy_json FROM adaptive_policy WHERE id=1"
        ).fetchone()

    if not row:
        return json.loads(json.dumps(DEFAULT_POLICY))

    try:
        return json.loads(row["policy_json"])
    except Exception:
        return json.loads(json.dumps(DEFAULT_POLICY))


# ============================================================
# SAFETY VALIDATION
# ============================================================

def validate_policy(policy: Dict[str, Any]) -> bool:

    if not isinstance(policy, dict):
        return False

    if "version" not in policy:
        return False

    recovery = policy.get("recovery", {})
    strategies = policy.get("strategies", {})
    safety = policy.get("self_modification", {})

    max_attempts = recovery.get("max_attempts", 0)
    max_recovery = recovery.get("max_recovery_attempts", 0)
    base_delay = recovery.get("base_delay", -1)
    max_delay = recovery.get("max_delay", 0)

    if not (1 <= int(max_attempts) <= 10):
        return False

    if not (1 <= int(max_recovery) <= 10):
        return False

    if not (0 <= float(base_delay) <= 10):
        return False

    if not (1 <= float(max_delay) <= 30):
        return False

    if not isinstance(strategies, dict):
        return False

    if safety.get("runtime_policy_only") is not True:
        return False

    if safety.get("never_execute_generated_code") is not True:
        return False

    if safety.get("never_bypass_permissions") is not True:
        return False

    if safety.get("never_modify_credentials") is not True:
        return False

    if safety.get("never_auto_redeploy") is not True:
        return False

    return True


# ============================================================
# EVENTS
# ============================================================

def log_event(
    mission_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None
):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO events
            (mission_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                mission_id,
                event_type,
                json.dumps(payload or {}),
                now()
            )
        )


# ============================================================
# MISSION STORAGE
# ============================================================

def create_mission(
    mission_id: str,
    objective: str
):
    timestamp = now()

    with db() as conn:
        conn.execute(
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
            VALUES (?, ?, ?, ?, ?, 0, 0)
            """,
            (
                mission_id,
                objective,
                "accepted",
                timestamp,
                timestamp
            )
        )


def update_mission(
    mission_id: str,
    **fields
):
    if not fields:
        return

    fields["updated_at"] = now()

    assignments = ", ".join(
        f"{key} = ?" for key in fields.keys()
    )

    values = list(fields.values())
    values.append(mission_id)

    with db() as conn:
        conn.execute(
            f"""
            UPDATE missions
            SET {assignments}
            WHERE mission_id = ?
            """,
            values
        )


def get_mission(mission_id: str):
    with db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM missions
            WHERE mission_id=?
            """,
            (mission_id,)
        ).fetchone()

    if row is None:
        return None

    data = dict(row)

    for key in ("result_json", "state_json"):
        if data.get(key):
            try:
                data[key.replace("_json", "")] = json.loads(data[key])
            except Exception:
                data[key.replace("_json", "")] = data[key]

        data.pop(key, None)

    return data


# ============================================================
# ERROR TYPES
# ============================================================

class ControlledFailure(Exception):
    pass


# ============================================================
# ERROR CLASSIFICATION
# ============================================================

def classify_error(error: Exception) -> str:

    if isinstance(error, ControlledFailure):
        return "ControlledFailure"

    text = str(error).lower()

    if "timeout" in text:
        return "timeout"

    if "database" in text or "sqlite" in text:
        return "database"

    if "json" in text:
        return "invalid_json"

    if "rate" in text or "429" in text:
        return "rate_limit"

    if "connection" in text:
        return "connection"

    if "permission" in text or "403" in text:
        return "permission"

    if "not found" in text or "404" in text:
        return "not_found"

    if "validation" in text or "invalid" in text:
        return "validation"

    return "unknown"


# ============================================================
# DIAGNOSIS
# ============================================================

def diagnose_error(
    error: Exception,
    policy: Dict[str, Any]
) -> Dict[str, Any]:

    failure_type = classify_error(error)

    strategy = policy.get(
        "strategies",
        {}
    ).get(
        failure_type,
        policy.get("strategies", {}).get("unknown", {})
    )

    safe = bool(strategy.get("safe_retry", False))

    if failure_type == "ControlledFailure":
        diagnosis = (
            "A controlled validation failure was injected. "
            "The failure is explicitly safe to recover from."
        )
    elif failure_type == "timeout":
        diagnosis = "Execution timed out; bounded retry may be appropriate."
    elif failure_type == "connection":
        diagnosis = "A connection failure occurred; runtime reinitialization may recover it."
    elif failure_type == "rate_limit":
        diagnosis = "A rate limit was detected; bounded backoff is required."
    elif failure_type == "database":
        diagnosis = "A database failure was detected; database reinitialization may recover it."
    elif failure_type == "invalid_json":
        diagnosis = "Invalid structured output was detected; discard and retry."
    elif failure_type == "permission":
        diagnosis = "A permission boundary was encountered; automatic bypass is forbidden."
    elif failure_type == "validation":
        diagnosis = "Validation failed; automatic retry is disabled unless explicitly safe."
    elif failure_type == "not_found":
        diagnosis = "A requested resource was not found; automatic retry is not justified."
    else:
        diagnosis = "Unknown failure. Capture diagnostics and retry only if policy permits."

    return {
        "failure_type": failure_type,
        "error": str(error),
        "diagnosis": diagnosis,
        "safe_to_retry": safe,
        "strategy": strategy.get("action", "stop_and_report"),
        "policy_version": policy.get("version")
    }


# ============================================================
# ADAPTIVE POLICY MODIFICATION
# ============================================================

def propose_policy_upgrade(
    failure_type: str,
    diagnosis: Dict[str, Any]
) -> Dict[str, Any]:

    current = get_policy()

    candidate = json.loads(
        json.dumps(current)
    )

    candidate["version"] = int(
        current.get("version", 1)
    ) + 1

    strategies = candidate.setdefault(
        "strategies",
        {}
    )

    # Safe adaptation only.
    if failure_type == "ControlledFailure":
        strategies[failure_type] = {
            "safe_retry": True,
            "action": "controlled_test_recovery"
        }

    elif failure_type in {
        "timeout",
        "connection",
        "rate_limit",
        "invalid_json",
        "database"
    }:
        existing = strategies.get(
            failure_type,
            {}
        )

        strategies[failure_type] = {
            "safe_retry": True,
            "action": existing.get(
                "action",
                "bounded_retry"
            )
        }

    else:
        # Unknown or sensitive failures remain non-retryable.
        strategies[failure_type] = {
            "safe_retry": False,
            "action": "stop_and_report"
        }

    # Safety invariants cannot be weakened.
    safety = candidate.setdefault(
        "self_modification",
        {}
    )

    safety["enabled"] = True
    safety["runtime_policy_only"] = True
    safety["require_validation"] = True
    safety["automatic_rollback"] = True
    safety["never_bypass_permissions"] = True
    safety["never_execute_generated_code"] = True
    safety["never_modify_credentials"] = True
    safety["never_auto_redeploy"] = True

    return candidate


def activate_policy(
    candidate: Dict[str, Any],
    reason: str
) -> Dict[str, Any]:

    if not validate_policy(candidate):
        raise RuntimeError(
            "Candidate adaptive policy failed safety validation."
        )

    current = get_policy()

    with db() as conn:

        conn.execute(
            """
            INSERT INTO policy_history
            (version, policy_json, reason, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                current["version"],
                json.dumps(current),
                "previous_policy:" + reason,
                now()
            )
        )

        conn.execute(
            """
            UPDATE adaptive_policy
            SET version=?,
                policy_json=?,
                updated_at=?
            WHERE id=1
            """,
            (
                candidate["version"],
                json.dumps(candidate),
                now()
            )
        )

    return candidate


# ============================================================
# LEARNING
# ============================================================

def record_learning(
    failure_type: str,
    strategy: str,
    success: bool,
    mission_id: str
):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO learning
            (failure_type, strategy, success, mission_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                failure_type,
                strategy,
                1 if success else 0,
                mission_id,
                now()
            )
        )


# ============================================================
# ROUTER INTELLIGENCE
# ============================================================

ROUTES = {
    "research": {
        "name": "research",
        "purpose": "decompose, gather evidence, cross-check, verify",
        "capabilities": [
            "objective_decomposition",
            "evidence_collection",
            "cross_checking",
            "contradiction_detection",
            "verification"
        ],
        "base_score": 0.90
    },

    "analysis": {
        "name": "analysis",
        "purpose": "reason over available information and produce structured findings",
        "capabilities": [
            "reasoning",
            "comparison",
            "synthesis",
            "verification"
        ],
        "base_score": 0.88
    },

    "verification": {
        "name": "verification",
        "purpose": "independently verify completion and consistency",
        "capabilities": [
            "schema_validation",
            "consistency_check",
            "completion_check"
        ],
        "base_score": 0.92
    },

    "recovery": {
        "name": "recovery",
        "purpose": "diagnose failures and select bounded recovery actions",
        "capabilities": [
            "error_classification",
            "diagnosis",
            "policy_adaptation",
            "bounded_retry"
        ],
        "base_score": 0.91
    },

    "memory": {
        "name": "memory",
        "purpose": "retain validated mission learning",
        "capabilities": [
            "learning_record",
            "route_learning",
            "policy_history"
        ],
        "base_score": 0.86
    }
}


def objective_hash(objective: str) -> str:
    return hashlib.sha256(
        objective.encode("utf-8")
    ).hexdigest()[:16]


def infer_route_requirements(
    objective: str,
    research: bool,
    verify: bool,
    remember: bool
) -> List[str]:

    text = objective.lower()

    requirements = []

    if research:
        requirements.append("research")

    if any(
        word in text
        for word in [
            "analyze",
            "analysis",
            "compare",
            "evaluate",
            "reason",
            "investigate"
        ]
    ):
        requirements.append("analysis")

    if verify or any(
        word in text
        for word in [
            "verify",
            "validate",
            "check",
            "prove",
            "test"
        ]
    ):
        requirements.append("verification")

    if remember:
        requirements.append("memory")

    if not requirements:
        requirements.append("analysis")

    # Recovery is always available as a supervisory layer.
    requirements.append("recovery")

    # Deduplicate while preserving order.
    return list(dict.fromkeys(requirements))


def historical_route_score(
    route_name: str,
    obj_hash: str
) -> Optional[float]:

    with db() as conn:
        rows = conn.execute(
            """
            SELECT success, score
            FROM route_learning
            WHERE route_name=?
              AND objective_hash=?
            ORDER BY id DESC
            LIMIT 10
            """,
            (
                route_name,
                obj_hash
            )
        ).fetchall()

    if not rows:
        return None

    total = 0.0
    weight = 0.0

    for index, row in enumerate(rows):
        w = 1.0 / (index + 1)
        total += float(row["score"]) * w
        weight += w

    return total / weight if weight else None


def score_route(
    route_name: str,
    objective: str,
    obj_hash: str
) -> float:

    route = ROUTES[route_name]

    score = float(
        route.get("base_score", 0.5)
    )

    historical = historical_route_score(
        route_name,
        obj_hash
    )

    if historical is not None:
        score = (
            score * 0.6
            + historical * 0.4
        )

    text = objective.lower()

    if route_name == "research":
        if any(
            x in text
            for x in [
                "research",
                "evidence",
                "sources",
                "latest",
                "investigate"
            ]
        ):
            score += 0.08

    if route_name == "analysis":
        if any(
            x in text
            for x in [
                "analyze",
                "analysis",
                "compare",
                "evaluate"
            ]
        ):
            score += 0.08

    if route_name == "verification":
        if any(
            x in text
            for x in [
                "verify",
                "validate",
                "test",
                "check"
            ]
        ):
            score += 0.08

    if route_name == "memory":
        if any(
            x in text
            for x in [
                "remember",
                "learn",
                "retain"
            ]
        ):
            score += 0.06

    return round(
        min(score, 1.0),
        4
    )


def route_mission(
    objective: str,
    research: bool,
    verify: bool,
    remember: bool
) -> Dict[str, Any]:

    requirements = infer_route_requirements(
        objective,
        research,
        verify,
        remember
    )

    obj_hash = objective_hash(
        objective
    )

    candidates = []

    for route_name in requirements:
        score = score_route(
            route_name,
            objective,
            obj_hash
        )

        candidates.append(
            {
                "route": route_name,
                "score": score,
                "purpose": ROUTES[route_name]["purpose"],
                "capabilities": ROUTES[route_name]["capabilities"]
            }
        )

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    primary = candidates[0]["route"]

    return {
        "router": "adaptive-mission-intelligence-router",
        "objective_hash": obj_hash,
        "requirements": requirements,
        "primary_route": primary,
        "route_plan": candidates,
        "routing_logic": [
            "infer objective requirements",
            "score compatible capabilities",
            "use historical route performance",
            "select primary route",
            "retain fallback routes",
            "verify route outcome",
            "learn route performance"
        ]
    }


def record_route_learning(
    route_name: str,
    obj_hash: str,
    success: bool,
    score: float,
    mission_id: str
):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO route_learning
            (
                route_name,
                objective_hash,
                success,
                score,
                mission_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                route_name,
                obj_hash,
                1 if success else 0,
                score,
                mission_id,
                now()
            )
        )


# ============================================================
# FAILURE INJECTION
# ============================================================

def should_inject_failure(
    objective: str,
    attempt: int
) -> bool:

    if attempt != 1:
        return False

    return (
        "[TEST_RECOVERY]" in objective
        or "[TEST_SELF_UPGRADE]" in objective
        or "[TEST_ROUTER]" in objective
    )


# ============================================================
# EXECUTION
# ============================================================

def execute_mission(
    mission_id: str,
    objective: str,
    attempt: int,
    research: bool,
    verify: bool,
    remember: bool
) -> Dict[str, Any]:

    log_event(
        mission_id,
        "execution_started",
        {
            "attempt": attempt
        }
    )

    if should_inject_failure(
        objective,
        attempt
    ):
        log_event(
            mission_id,
            "controlled_failure_injected",
            {
                "attempt": attempt
            }
        )

        raise ControlledFailure(
            "Controlled failure injected for autonomous adaptive validation."
        )

    policy = get_policy()

    routing = route_mission(
        objective,
        research,
        verify,
        remember
    )

    log_event(
        mission_id,
        "route_selected",
        routing
    )

    # Deterministic internal execution layer.
    # External tools can later attach to these capability names.
    route_results = []

    for item in routing["route_plan"]:
        route_results.append(
            {
                "route": item["route"],
                "status": "available",
                "score": item["score"],
                "capabilities": item["capabilities"]
            }
        )

    analysis = {
        "objective": objective,
        "execution_mode": "adaptive-intelligence-routing",
        "research_requested": research,
        "verification_requested": verify,
        "memory_requested": remember,
        "evidence_mode": "independent-evidence-closure",
        "attempt": attempt,
        "active_policy_version": policy["version"],

        "router": {
            "enabled": True,
            "version": VERSION,
            "primary_route": routing["primary_route"],
            "requirements": routing["requirements"],
            "fallback_routes": [
                x["route"]
                for x in routing["route_plan"][1:]
            ],
            "route_count": len(
                routing["route_plan"]
            )
        },

        "self_modification": {
            "enabled": True,
            "mode": "runtime-policy-adaptation"
        },

        "next_actions": [
            "decompose objective",
            "select capability route",
            "execute selected route",
            "cross-check evidence",
            "identify contradictions",
            "verify completion",
            "learn route performance",
            "adapt recovery policy when required"
        ],

        "route_results": route_results
    }

    log_event(
        mission_id,
        "execution_completed",
        {
            "attempt": attempt,
            "primary_route": routing["primary_route"]
        }
    )

    return {
        "status": "completed",
        "analysis": analysis,
        "routing": routing
    }


# ============================================================
# COMPLETION VERIFICATION
# ============================================================

def verify_completion(
    mission_id: str,
    objective: str,
    result: Dict[str, Any]
) -> Dict[str, Any]:

    policy = get_policy()

    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM events
            WHERE mission_id=?
              AND event_type='execution_completed'
            """,
            (mission_id,)
        ).fetchone()

    execution_recorded = (
        row["count"] > 0
    )

    checks = {
        "mission_id_present": bool(mission_id),
        "objective_present": bool(objective),
        "result_present": bool(result),
        "status_completed": (
            result.get("status") == "completed"
        ),
        "execution_recorded": execution_recorded,
        "policy_valid": validate_policy(policy),
        "routing_present": (
            "routing" in result
        ),
        "primary_route_present": bool(
            result.get(
                "routing",
                {}
            ).get(
                "primary_route"
            )
        )
    }

    verified = all(
        checks.values()
    )

    return {
        "verified": verified,
        "checks": checks
    }


# ============================================================
# RECOVERY
# ============================================================

def recover(
    mission_id: str,
    diagnosis: Dict[str, Any],
    recovery_number: int
) -> Dict[str, Any]:

    policy = get_policy()

    action = diagnosis["strategy"]

    if not diagnosis["safe_to_retry"]:
        return {
            "recovered": False,
            "reason": "Failure is not safe for automatic retry.",
            "action": "stop_and_report"
        }

    base_delay = float(
        policy["recovery"]["base_delay"]
    )

    max_delay = float(
        policy["recovery"]["max_delay"]
    )

    if diagnosis["failure_type"] == "ControlledFailure":
        delay = 0.1
    else:
        delay = min(
            base_delay * (
                2 ** max(
                    recovery_number - 1,
                    0
                )
            ),
            max_delay
        )

    if diagnosis["failure_type"] == "database":
        init_db()

    time.sleep(
        min(delay, 1.0)
    )

    log_event(
        mission_id,
        "recovery_completed",
        {
            "recovery_number": recovery_number,
            "action": action,
            "delay_seconds": delay,
            "policy_version": policy["version"]
        }
    )

    return {
        "recovered": True,
        "action": action,
        "delay_seconds": delay,
        "recovery_number": recovery_number,
        "policy_version": policy["version"]
    }


# ============================================================
# AUTONOMOUS MISSION ENGINE
# ============================================================

def run_autonomous_mission(
    mission_id: str,
    objective: str,
    research: bool,
    verify: bool,
    remember: bool
):

    policy = get_policy()

    max_attempts = min(
        int(
            policy["recovery"]["max_attempts"]
        ),
        MAX_ATTEMPTS
    )

    max_recovery = min(
        int(
            policy["recovery"]["max_recovery_attempts"]
        ),
        MAX_RECOVERY_ATTEMPTS
    )

    attempts = 0
    recovery_attempts = 0
    last_error = None
    recovery_state = {}

    while attempts < max_attempts:

        attempts += 1

        update_mission(
            mission_id,
            status="running",
            attempts=attempts,
            recovery_attempts=recovery_attempts
        )

        try:

            result = execute_mission(
                mission_id,
                objective,
                attempts,
                research,
                verify,
                remember
            )

            verification = verify_completion(
                mission_id,
                objective,
                result
            )

            result["verification"] = verification

            if verification["verified"]:

                # Record successful route learning.
                routing = result.get(
                    "routing",
                    {}
                )

                obj_hash = routing.get(
                    "objective_hash",
                    objective_hash(objective)
                )

                for route in routing.get(
                    "route_plan",
                    []
                ):
                    record_route_learning(
                        route["route"],
                        obj_hash,
                        True,
                        route["score"],
                        mission_id
                    )

                recovery_state["final_policy_version"] = (
                    get_policy()["version"]
                )

                final_result = result

                update_mission(
                    mission_id,
                    status="completed",
                    attempts=attempts,
                    recovery_attempts=recovery_attempts,
                    error=last_error,
                    result_json=json.dumps(
                        final_result
                    ),
                    state_json=json.dumps(
                        recovery_state
                    )
                )

                log_event(
                    mission_id,
                    "mission_completed",
                    {
                        "attempts": attempts,
                        "recovery_attempts": recovery_attempts,
                        "policy_version": get_policy()["version"]
                    }
                )

                return

            raise RuntimeError(
                "Completion verification failed."
            )

        except Exception as error:

            last_error = str(error)

            failure_type = classify_error(
                error
            )

            current_policy = get_policy()

            diagnosis = diagnose_error(
                error,
                current_policy
            )

            log_event(
                mission_id,
                "error_detected",
                diagnosis
            )

            # =================================================
            # ADAPTIVE POLICY CYCLE
            # =================================================

            candidate = propose_policy_upgrade(
                failure_type,
                diagnosis
            )

            activated = False
            rolled_back = False

            try:

                if (
                    candidate["version"]
                    != current_policy["version"]
                ):

                    activated_policy = activate_policy(
                        candidate,
                        f"Adaptive recovery improvement after {failure_type}."
                    )

                    activated = True

                    recovery_state[
                        "self_modification"
                    ] = {
                        "activated": True,
                        "rolled_back": False,
                        "policy_version": activated_policy["version"],
                        "reason": (
                            f"Adaptive recovery improvement "
                            f"after {failure_type}."
                        )
                    }

                    log_event(
                        mission_id,
                        "policy_adapted",
                        {
                            "old_version": current_policy["version"],
                            "new_version": activated_policy["version"],
                            "failure_type": failure_type
                        }
                    )

            except Exception as policy_error:

                rolled_back = True

                recovery_state[
                    "self_modification"
                ] = {
                    "activated": False,
                    "rolled_back": True,
                    "reason": str(policy_error),
                    "policy_version": current_policy["version"]
                }

                log_event(
                    mission_id,
                    "policy_adaptation_rejected",
                    {
                        "error": str(policy_error),
                        "rollback": True
                    }
                )

            record_learning(
                failure_type,
                diagnosis["strategy"],
                False,
                mission_id
            )

            recovery_state[
                "diagnosis"
            ] = diagnosis

            if (
                diagnosis["safe_to_retry"]
                and recovery_attempts < max_recovery
                and attempts < max_attempts
            ):

                recovery_attempts += 1

                recovery_result = recover(
                    mission_id,
                    diagnosis,
                    recovery_attempts
                )

                recovery_state[
                    "recovery"
                ] = recovery_result

                update_mission(
                    mission_id,
                    recovery_attempts=recovery_attempts,
                    state_json=json.dumps(
                        recovery_state
                    )
                )

                if recovery_result.get(
                    "recovered"
                ):
                    continue

            # No safe recovery available.
            update_mission(
                mission_id,
                status="failed",
                attempts=attempts,
                recovery_attempts=recovery_attempts,
                error=last_error,
                state_json=json.dumps(
                    recovery_state
                )
            )

            log_event(
                mission_id,
                "mission_failed",
                {
                    "error": last_error,
                    "attempts": attempts,
                    "recovery_attempts": recovery_attempts
                }
            )

            return


# ============================================================
# REQUEST MODEL
# ============================================================

class CreateRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=10000
    )

    research: bool = True
    verify: bool = True
    remember: bool = True


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():

    policy = get_policy()

    return {
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "message": (
            "AI Infinity adaptive mission intelligence "
            "router is running."
        ),

        "active_policy_version": policy["version"],

        "endpoints": {
            "health": "/health",
            "status": "/status",
            "capabilities": "/capabilities",
            "policy": "/policy",
            "docs": "/docs",
            "run": "POST /run",
            "run_help": "GET /run",
            "mission": "GET /mission/{mission_id}",
            "events": "GET /mission/{mission_id}/events"
        }
    }


@app.get("/health")
def health():

    policy = get_policy()

    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "policy_version": policy["version"],
        "policy_valid": validate_policy(policy),
        "router_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True
    }


@app.get("/status")
def status():

    policy = get_policy()

    return {
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,

        "status": "online",

        "adaptive_router": {
            "enabled": True,
            "route_count": len(ROUTES),
            "routes": list(ROUTES.keys())
        },

        "adaptive_policy": {
            "version": policy["version"],
            "valid": validate_policy(policy)
        },

        "recovery": policy["recovery"],

        "self_modification": policy[
            "self_modification"
        ],

        "safety": {
            "bounded_attempts": True,
            "bounded_recovery": True,
            "no_permission_bypass": True,
            "no_credential_modification": True,
            "no_arbitrary_code_execution": True,
            "no_source_code_self_rewrite": True,
            "no_automatic_redeployment": True
        }
    }


@app.get("/capabilities")
def capabilities():

    return {
        "version": VERSION,

        "core": [
            "adaptive mission routing",
            "objective requirement inference",
            "capability scoring",
            "historical route learning",
            "fallback route planning",
            "independent verification",
            "error classification",
            "failure diagnosis",
            "runtime policy adaptation",
            "bounded autonomous recovery",
            "learning retention",
            "policy history"
        ],

        "autonomous_cycle": [
            "understand objective",
            "infer requirements",
            "select capabilities",
            "build route plan",
            "execute",
            "detect failure",
            "diagnose",
            "propose adaptation",
            "validate adaptation",
            "activate safely",
            "recover",
            "reroute",
            "retry",
            "verify",
            "learn",
            "retain validated strategy"
        ],

        "safety_boundaries": [
            "bounded attempts",
            "bounded recovery",
            "runtime policy adaptation only",
            "no permission bypass",
            "no credential modification",
            "no arbitrary generated-code execution",
            "no source-code self-rewrite",
            "no automatic redeployment"
        ]
    }


@app.get("/policy")
def policy():

    current = get_policy()

    with db() as conn:
        history = conn.execute(
            """
            SELECT
                version,
                reason,
                created_at
            FROM policy_history
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

    return {
        "active": current,
        "valid": validate_policy(current),
        "history": [
            dict(row)
            for row in history
        ]
    }


@app.get("/run")
def run_help():

    return {
        "status": "ready",
        "message": (
            "Use POST /run to start an adaptive AI Infinity mission."
        ),
        "docs": "/docs",
        "example": {
            "objective": (
                "Analyze the reliability of autonomous AI agents."
            ),
            "research": True,
            "verify": True,
            "remember": True
        },

        "test_markers": {
            "[TEST_RECOVERY]": (
                "validates autonomous failure recovery"
            ),
            "[TEST_SELF_UPGRADE]": (
                "validates adaptive policy modification"
            ),
            "[TEST_ROUTER]": (
                "validates adaptive mission routing"
            )
        }
    }


@app.post("/run")
async def run(request: CreateRequest):

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

    routing = route_mission(
        request.objective,
        request.research,
        request.verify,
        request.remember
    )

    log_event(
        mission_id,
        "mission_accepted",
        {
            "routing": routing
        }
    )

    # Start execution in background so the API responds immediately.
    import asyncio

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
        "mission_id": mission_id,
        "status": "accepted",
        "message": (
            "Mission accepted and adaptive intelligence "
            "routing started."
        ),
        "monitor": (
            f"/mission/{mission_id}"
        ),
        "events": (
            f"/mission/{mission_id}/events"
        ),
        "router_enabled": True,
        "recovery_enabled": True,
        "verification_enabled": True,
        "self_modification_enabled": True,
        "active_policy_version": get_policy()["version"],
        "primary_route": routing["primary_route"],
        "route_plan": routing["route_plan"],
        "test_recovery": (
            "[TEST_RECOVERY]"
            in request.objective
        ),
        "test_self_upgrade": (
            "[TEST_SELF_UPGRADE]"
            in request.objective
        ),
        "test_router": (
            "[TEST_ROUTER]"
            in request.objective
        )
    }


async def _background_mission(
    mission_id: str,
    objective: str,
    research: bool,
    verify: bool,
    remember: bool
):

    import asyncio

    await asyncio.to_thread(
        run_autonomous_mission,
        mission_id,
        objective,
        research,
        verify,
        remember
    )


@app.get("/mission/{mission_id}")
def mission(mission_id: str):

    result = get_mission(
        mission_id
    )

    if result is None:
        return {
            "error": "Mission not found."
        }

    return result


@app.get("/mission/{mission_id}/events")
def mission_events(
    mission_id: str
):

    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                event_type,
                payload_json,
                created_at
            FROM events
            WHERE mission_id=?
            ORDER BY id ASC
            """,
            (mission_id,)
        ).fetchall()

    events = []

    for row in rows:

        payload = {}

        try:
            payload = json.loads(
                row["payload_json"]
                or "{}"
            )
        except Exception:
            payload = {
                "raw": row["payload_json"]
            }

        events.append(
            {
                "event": row["event_type"],
                "payload": payload,
                "created_at": row["created_at"]
            }
        )

    return {
        "mission_id": mission_id,
        "count": len(events),
        "events": events
    }


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "8000"
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )
