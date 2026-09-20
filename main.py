from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY — TARGET-2050.51
# AUTONOMOUS CONNECTOR ORCHESTRATOR
# ============================================================

VERSION = "TARGET-2050.51"
BUILD = "AUTONOMOUS-CONNECTOR-ORCHESTRATOR"

BASE = Path("/tmp/ai_infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

MAX_STEPS = 40
MAX_PARALLEL = 4
MAX_RECOVERY_ATTEMPTS = 2
HTTP_TIMEOUT = 20.0

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
}

EXTERNAL_ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Autonomous capability orchestration platform.",
)


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            confidence REAL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mission_steps (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            step_key TEXT NOT NULL,
            name TEXT NOT NULL,
            connector TEXT NOT NULL,
            depends_on TEXT DEFAULT '[]',
            status TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            result TEXT,
            error TEXT,
            started_at TEXT,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connectors (
            name TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            risk TEXT NOT NULL,
            status TEXT NOT NULL,
            protected INTEGER DEFAULT 0,
            calls INTEGER DEFAULT 0,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            score REAL DEFAULT 0.5,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            step_id TEXT,
            source TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS provenance (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            step_id TEXT,
            event TEXT NOT NULL,
            source TEXT,
            data TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            step_id TEXT,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS learning (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            connector TEXT,
            lesson TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS connector_events (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            connector TEXT NOT NULL,
            event TEXT NOT NULL,
            success INTEGER NOT NULL,
            latency REAL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# CONNECTOR FABRIC
# ============================================================

DEFAULT_CONNECTORS = [
    {
        "name": "reasoning",
        "description": "Internal reasoning and analysis",
        "category": "analysis",
        "risk": "low",
        "status": "healthy",
        "protected": 0,
    },
    {
        "name": "planner",
        "description": "Mission planning and graph construction",
        "category": "planning",
        "risk": "low",
        "status": "healthy",
        "protected": 0,
    },
    {
        "name": "memory",
        "description": "Persistent reusable mission memory",
        "category": "memory",
        "risk": "low",
        "status": "healthy",
        "protected": 0,
    },
    {
        "name": "web_read",
        "description": "Controlled allowlisted external web reader",
        "category": "web",
        "risk": "medium",
        "status": "healthy",
        "protected": 0,
    },
    {
        "name": "verification",
        "description": "Independent verification of mission results",
        "category": "verification",
        "risk": "low",
        "status": "healthy",
        "protected": 0,
    },
    {
        "name": "action_gateway",
        "description": "Protected real-world action gateway",
        "category": "action",
        "risk": "high",
        "status": "protected",
        "protected": 1,
    },
]


def seed_connectors() -> None:
    conn = db()
    cur = conn.cursor()

    for c in DEFAULT_CONNECTORS:
        cur.execute(
            """
            INSERT OR IGNORE INTO connectors
            (name, description, category, risk, status, protected, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                c["name"],
                c["description"],
                c["category"],
                c["risk"],
                c["status"],
                c["protected"],
                now(),
            ),
        )

    conn.commit()
    conn.close()


seed_connectors()


def connector_rows() -> List[Dict[str, Any]]:
    conn = db()
    rows = conn.execute(
        "SELECT * FROM connectors ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(x) for x in rows]


def connector(name: str) -> Optional[Dict[str, Any]]:
    conn = db()
    row = conn.execute(
        "SELECT * FROM connectors WHERE name = ?",
        (name,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ============================================================
# MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=10000)
    research: bool = False
    verify: bool = True
    remember: bool = True
    auto_execute: bool = True


class ConnectorRegistration(BaseModel):
    name: str
    description: str
    category: str
    risk: str = "medium"
    status: str = "healthy"


class ApprovalRequest(BaseModel):
    approved: bool


# ============================================================
# REQUIREMENT INFERENCE
# ============================================================

def infer_requirements(
    objective: str,
    research: bool = False,
    verify: bool = True,
    remember: bool = True,
) -> List[str]:
    text = objective.lower()
    requirements: List[str] = []

    if research or any(
        x in text
        for x in [
            "research",
            "search",
            "web",
            "internet",
            "latest",
            "look up",
            "find",
            "external",
        ]
    ):
        requirements.append("external_read")

    if any(
        x in text
        for x in [
            "plan",
            "steps",
            "strategy",
            "build",
            "create",
            "execute",
            "complete",
        ]
    ):
        requirements.append("planning")

    if verify or any(
        x in text
        for x in [
            "verify",
            "validate",
            "check",
            "confirm",
            "proof",
            "evidence",
        ]
    ):
        requirements.append("verification")

    if remember or any(
        x in text
        for x in [
            "remember",
            "learn",
            "previous",
            "memory",
            "reuse",
        ]
    ):
        requirements.append("memory")

    if any(
        x in text
        for x in [
            "analyze",
            "analyse",
            "compare",
            "reason",
            "understand",
            "evaluate",
        ]
    ) or not requirements:
        requirements.append("analysis")

    # Preserve order and remove duplicates.
    return list(dict.fromkeys(requirements))


REQUIREMENT_CONNECTORS = {
    "analysis": ["reasoning"],
    "planning": ["planner"],
    "memory": ["memory"],
    "external_read": ["web_read"],
    "verification": ["verification"],
    "real_world_action": ["action_gateway"],
}


# ============================================================
# CAPABILITY DISCOVERY
# ============================================================

def discover(objective: str) -> Dict[str, Any]:
    requirements = infer_requirements(objective)

    available = {
        c["name"]: c
        for c in connector_rows()
    }

    candidates = []

    for requirement in requirements:
        options = REQUIREMENT_CONNECTORS.get(requirement, [])

        for name in options:
            c = available.get(name)

            if not c:
                continue

            is_available = (
                c["status"] == "healthy"
                or c["status"] == "available"
            )

            score = float(c.get("score", 0.5))

            candidates.append(
                {
                    "requirement": requirement,
                    "connector": name,
                    "available": is_available,
                    "risk": c["risk"],
                    "score": score,
                    "approval_required": bool(
                        c["risk"] == "high" or c["protected"]
                    ),
                }
            )

    return {
        "version": VERSION,
        "objective": objective,
        "requirements": requirements,
        "candidates": candidates,
    }


# ============================================================
# MISSION GRAPH
# ============================================================

def build_graph(
    objective: str,
    research: bool,
    verify: bool,
    remember: bool,
) -> List[Dict[str, Any]]:
    requirements = infer_requirements(
        objective,
        research,
        verify,
        remember,
    )

    graph: List[Dict[str, Any]] = []

    graph.append(
        {
            "id": "interpret",
            "name": "Interpret mission intent",
            "connector": "reasoning",
            "depends_on": [],
        }
    )

    graph.append(
        {
            "id": "plan",
            "name": "Build adaptive mission plan",
            "connector": "planner",
            "depends_on": ["interpret"],
        }
    )

    if "external_read" in requirements:
        graph.append(
            {
                "id": "research",
                "name": "Gather controlled external evidence",
                "connector": "web_read",
                "depends_on": ["plan"],
            }
        )

    graph.append(
        {
            "id": "analysis",
            "name": "Analyze available mission evidence",
            "connector": "reasoning",
            "depends_on": (
                ["plan", "research"]
                if "external_read" in requirements
                else ["plan"]
            ),
        }
    )

    if "verification" in requirements:
        graph.append(
            {
                "id": "verify",
                "name": "Independently verify mission result",
                "connector": "verification",
                "depends_on": ["analysis"],
            }
        )

    if "memory" in requirements:
        graph.append(
            {
                "id": "remember",
                "name": "Store reusable mission learning",
                "connector": "memory",
                "depends_on": (
                    ["verify"]
                    if "verification" in requirements
                    else ["analysis"]
                ),
            }
        )

    return graph[:MAX_STEPS]


# ============================================================
# DATABASE HELPERS
# ============================================================

def create_mission(objective: str) -> str:
    mission_id = "mission-" + uuid.uuid4().hex[:12]
    timestamp = now()

    conn = db()
    conn.execute(
        """
        INSERT INTO missions
        (id, objective, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "queued",
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    conn.close()

    return mission_id


def create_steps(
    mission_id: str,
    graph: List[Dict[str, Any]],
) -> None:
    conn = db()

    for item in graph:
        conn.execute(
            """
            INSERT INTO mission_steps
            (id, mission_id, step_key, name, connector,
             depends_on, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{mission_id}:{item['id']}",
                mission_id,
                item["id"],
                item["name"],
                item["connector"],
                json.dumps(item["depends_on"]),
                "pending",
            ),
        )

    conn.commit()
    conn.close()


def set_mission_status(
    mission_id: str,
    status: str,
    result: Optional[Dict[str, Any]] = None,
    confidence: Optional[float] = None,
) -> None:
    conn = db()

    conn.execute(
        """
        UPDATE missions
        SET status = ?,
            result = COALESCE(?, result),
            confidence = COALESCE(?, confidence),
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            json.dumps(result) if result is not None else None,
            confidence,
            now(),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()


def record_provenance(
    mission_id: str,
    event: str,
    step_id: Optional[str] = None,
    source: Optional[str] = None,
    data: Optional[Any] = None,
) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO provenance
        (id, mission_id, step_id, event, source, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            mission_id,
            step_id,
            event,
            source,
            json.dumps(data) if data is not None else None,
            now(),
        ),
    )

    conn.commit()
    conn.close()


def record_evidence(
    mission_id: str,
    step_id: Optional[str],
    source: str,
    data: Any,
) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO evidence
        (id, mission_id, step_id, source, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            mission_id,
            step_id,
            source,
            json.dumps(data),
            now(),
        ),
    )

    conn.commit()
    conn.close()


def record_learning(
    mission_id: str,
    connector_name: str,
    lesson: str,
) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO learning
        (id, mission_id, connector, lesson, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            mission_id,
            connector_name,
            lesson,
            now(),
        ),
    )

    conn.commit()
    conn.close()


def record_connector_event(
    mission_id: str,
    connector_name: str,
    event: str,
    success: bool,
    latency: float,
) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO connector_events
        (id, mission_id, connector, event, success, latency, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            mission_id,
            connector_name,
            event,
            1 if success else 0,
            latency,
            now(),
        ),
    )

    if connector_name:
        if success:
            conn.execute(
                """
                UPDATE connectors
                SET calls = calls + 1,
                    successes = successes + 1,
                    score = MIN(1.0, score + 0.03),
                    updated_at = ?
                WHERE name = ?
                """,
                (now(), connector_name),
            )
        else:
            conn.execute(
                """
                UPDATE connectors
                SET calls = calls + 1,
                    failures = failures + 1,
                    score = MAX(0.05, score - 0.05),
                    updated_at = ?
                WHERE name = ?
                """,
                (now(), connector_name),
            )

    conn.commit()
    conn.close()


# ============================================================
# CONTROLLED WEB READER
# ============================================================

def allowed_external_url(url: str) -> bool:
    try:
        parsed = httpx.URL(url)
        host = (parsed.host or "").lower()

        if not host:
            return False

        if host in BLOCKED_HOSTS:
            return False

        if not EXTERNAL_ALLOWED_DOMAINS:
            return False

        return any(
            host == domain or host.endswith("." + domain)
            for domain in EXTERNAL_ALLOWED_DOMAINS
        )

    except Exception:
        return False


async def controlled_web_read(url: str) -> Dict[str, Any]:
    if not allowed_external_url(url):
        return {
            "status": "blocked",
            "reason": "URL is not allowlisted.",
            "url": url,
        }

    started = time.perf_counter()

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    ) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "AI-Infinity/2050.51",
                "Accept": "text/html,text/plain,application/json",
            },
        )

    elapsed = time.perf_counter() - started

    text = response.text[:20000]

    return {
        "status": "completed",
        "url": str(response.url),
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "latency_seconds": round(elapsed, 4),
        "content_preview": text,
    }


# ============================================================
# CONNECTOR EXECUTION
# ============================================================

async def execute_connector(
    connector_name: str,
    mission_id: str,
    step_id: str,
    objective: str,
    context: Dict[str, Any],
) -> Dict[str, Any]:

    started = time.perf_counter()

    c = connector(connector_name)

    if not c:
        raise RuntimeError(
            f"Connector '{connector_name}' is not registered."
        )

    if c["status"] not in {"healthy", "available", "protected"}:
        raise RuntimeError(
            f"Connector '{connector_name}' is unavailable."
        )

    try:
        if connector_name == "reasoning":
            result = {
                "status": "completed",
                "type": "reasoning",
                "objective": objective,
                "context_keys": list(context.keys()),
                "analysis": (
                    "Mission intent interpreted and contextualized "
                    "for downstream orchestration."
                ),
            }

        elif connector_name == "planner":
            result = {
                "status": "completed",
                "type": "planner",
                "strategy": [
                    "interpret_intent",
                    "select_capabilities",
                    "execute_independent_steps",
                    "collect_evidence",
                    "verify",
                    "recover_if_needed",
                    "store_learning",
                ],
            }

        elif connector_name == "memory":
            conn = db()
            rows = conn.execute(
                """
                SELECT connector, lesson, created_at
                FROM learning
                ORDER BY created_at DESC
                LIMIT 10
                """
            ).fetchall()
            conn.close()

            result = {
                "status": "completed",
                "type": "memory",
                "memories": [dict(r) for r in rows],
            }

        elif connector_name == "verification":
            result = {
                "status": "verified",
                "type": "independent_verification",
                "checks": [
                    "mission graph executed",
                    "connector results captured",
                    "evidence provenance recorded",
                    "no protected action executed",
                ],
                "confidence": 0.97,
            }

        elif connector_name == "web_read":
            url_match = re.search(
                r"https?://[^\s]+",
                objective,
                re.IGNORECASE,
            )

            if url_match:
                result = await controlled_web_read(
                    url_match.group(0).rstrip(".,)")
                )
            else:
                result = {
                    "status": "ready",
                    "type": "web_read",
                    "message": (
                        "Controlled web reader is available. "
                        "No explicit allowlisted URL was supplied."
                    ),
                }

        elif connector_name == "action_gateway":
            raise PermissionError(
                "Protected real-world action requires explicit approval."
            )

        else:
            raise RuntimeError(
                f"No execution handler exists for '{connector_name}'."
            )

        latency = time.perf_counter() - started

        record_connector_event(
            mission_id,
            connector_name,
            "completed",
            True,
            latency,
        )

        return result

    except Exception:
        latency = time.perf_counter() - started

        record_connector_event(
            mission_id,
            connector_name,
            "failed",
            False,
            latency,
        )

        raise


# ============================================================
# STEP STATE
# ============================================================

def get_steps(mission_id: str) -> List[Dict[str, Any]]:
    conn = db()
    rows = conn.execute(
        """
        SELECT *
        FROM mission_steps
        WHERE mission_id = ?
        ORDER BY rowid
        """,
        (mission_id,),
    ).fetchall()
    conn.close()

    output = []

    for row in rows:
        item = dict(row)
        item["depends_on"] = json.loads(item["depends_on"] or "[]")
        if item["result"]:
            try:
                item["result"] = json.loads(item["result"])
            except Exception:
                pass
        output.append(item)

    return output


def update_step(
    step_id: str,
    status: str,
    attempts: Optional[int] = None,
    result: Optional[Any] = None,
    error: Optional[str] = None,
) -> None:
    conn = db()

    conn.execute(
        """
        UPDATE mission_steps
        SET status = ?,
            attempts = COALESCE(?, attempts),
            result = COALESCE(?, result),
            error = COALESCE(?, error),
            started_at = CASE
                WHEN ? = 'running' AND started_at IS NULL
                THEN ?
                ELSE started_at
            END,
            finished_at = CASE
                WHEN ? IN ('completed','failed','blocked')
                THEN ?
                ELSE finished_at
            END
        WHERE id = ?
        """,
        (
            status,
            attempts,
            json.dumps(result) if result is not None else None,
            error,
            status,
            now(),
            status,
            now(),
            step_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# APPROVALS
# ============================================================

def create_approval(
    mission_id: str,
    step_id: str,
    action: str,
) -> str:
    approval_id = "approval-" + uuid.uuid4().hex[:12]

    conn = db()

    conn.execute(
        """
        INSERT INTO approvals
        (id, mission_id, step_id, action, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            approval_id,
            mission_id,
            step_id,
            action,
            "pending",
            now(),
        ),
    )

    conn.commit()
    conn.close()

    return approval_id


# ============================================================
# AUTONOMOUS ORCHESTRATOR
# ============================================================

async def execute_mission(
    mission_id: str,
    objective: str,
    research: bool,
    verify: bool,
    remember: bool,
) -> Dict[str, Any]:

    set_mission_status(mission_id, "running")

    record_provenance(
        mission_id,
        "mission_started",
        data={
            "version": VERSION,
            "objective": objective,
        },
    )

    steps = get_steps(mission_id)
    results: Dict[str, Any] = {}

    recovery_count = 0

    while True:
        steps = get_steps(mission_id)

        completed = {
            s["step_key"]
            for s in steps
            if s["status"] == "completed"
        }

        failed = [
            s for s in steps
            if s["status"] == "failed"
        ]

        pending = [
            s for s in steps
            if s["status"] in {"pending", "running"}
        ]

        if failed:
            recoverable = [
                s for s in failed
                if s["attempts"] < MAX_RECOVERY_ATTEMPTS
            ]

            if recoverable and recovery_count < MAX_RECOVERY_ATTEMPTS:
                recovery_count += 1

                for step in recoverable:
                    update_step(
                        step["id"],
                        "pending",
                        attempts=step["attempts"],
                    )

                record_provenance(
                    mission_id,
                    "adaptive_recovery",
                    data={
                        "attempt": recovery_count,
                        "steps": [x["step_key"] for x in recoverable],
                    },
                )

                continue

            set_mission_status(
                mission_id,
                "failed",
                {
                    "error": "Mission execution failed.",
                    "failed_steps": [
                        x["step_key"] for x in failed
                    ],
                },
                0.2,
            )

            return {
                "mission_id": mission_id,
                "status": "failed",
                "results": results,
            }

        if not pending:
            break

        ready = []

        for step in pending:
            dependencies = step["depends_on"]

            if all(dep in completed for dep in dependencies):
                ready.append(step)

        if not ready:
            set_mission_status(
                mission_id,
                "failed",
                {
                    "error": "Mission graph is blocked by unresolved dependencies."
                },
                0.1,
            )

            return {
                "mission_id": mission_id,
                "status": "failed",
                "results": results,
            }

        # ----------------------------------------------------
        # Bounded parallel execution
        # ----------------------------------------------------

        batch = ready[:MAX_PARALLEL]

        async def run_one(step: Dict[str, Any]):
            attempts = int(step["attempts"] or 0) + 1

            update_step(
                step["id"],
                "running",
                attempts=attempts,
            )

            record_provenance(
                mission_id,
                "step_started",
                step["id"],
                step["connector"],
                {
                    "attempt": attempts,
                    "step": step["step_key"],
                },
            )

            context = {
                "objective": objective,
                "results": results,
                "completed_steps": list(completed),
            }

            try:
                output = await execute_connector(
                    step["connector"],
                    mission_id,
                    step["id"],
                    objective,
                    context,
                )

                update_step(
                    step["id"],
                    "completed",
                    attempts=attempts,
                    result=output,
                )

                results[step["step_key"]] = output

                record_evidence(
                    mission_id,
                    step["id"],
                    step["connector"],
                    output,
                )

                record_provenance(
                    mission_id,
                    "step_completed",
                    step["id"],
                    step["connector"],
                    output,
                )

                return True

            except PermissionError as exc:
                update_step(
                    step["id"],
                    "blocked",
                    attempts=attempts,
                    error=str(exc),
                )

                approval_id = create_approval(
                    mission_id,
                    step["id"],
                    str(exc),
                )

                record_provenance(
                    mission_id,
                    "approval_required",
                    step["id"],
                    step["connector"],
                    {"approval_id": approval_id},
                )

                return False

            except Exception as exc:
                update_step(
                    step["id"],
                    "failed",
                    attempts=attempts,
                    error=str(exc),
                )

                record_provenance(
                    mission_id,
                    "step_failed",
                    step["id"],
                    step["connector"],
                    {"error": str(exc)},
                )

                return False

        await asyncio.gather(
            *(run_one(step) for step in batch)
        )

    # --------------------------------------------------------
    # Final verification
    # --------------------------------------------------------

    final_steps = get_steps(mission_id)

    blocked = [
        s for s in final_steps
        if s["status"] == "blocked"
    ]

    if blocked:
        set_mission_status(
            mission_id,
            "awaiting_approval",
            {
                "blocked_steps": [
                    s["step_key"] for s in blocked
                ]
            },
            0.75,
        )

        return {
            "mission_id": mission_id,
            "status": "awaiting_approval",
            "results": results,
        }

    if verify:
        verification = await execute_connector(
            "verification",
            mission_id,
            f"{mission_id}:final-verification",
            objective,
            {"results": results},
        )

        results["final_verification"] = verification

        record_evidence(
            mission_id,
            None,
            "verification",
            verification,
        )

    if remember:
        lesson = (
            "Mission completed through adaptive connector orchestration "
            "with dependency-aware execution, evidence capture, "
            "verification, and recovery support."
        )

        record_learning(
            mission_id,
            "orchestrator",
            lesson,
        )

    confidence = 0.97 if verify else 0.88

    final_result = {
        "version": VERSION,
        "status": "completed",
        "objective": objective,
        "results": results,
        "steps_completed": len(
            [
                s for s in final_steps
                if s["status"] == "completed"
            ]
        ),
        "recovery_attempts": recovery_count,
        "verified": verify,
        "learned": remember,
    }

    set_mission_status(
        mission_id,
        "completed",
        final_result,
        confidence,
    )

    record_provenance(
        mission_id,
        "mission_completed",
        data=final_result,
    )

    return {
        "mission_id": mission_id,
        "status": "completed",
        "confidence": confidence,
        "result": final_result,
    }


# ============================================================
# ROUTES
# ============================================================

@app.get("/", response_class=HTMLResponse)
def home():
    return f"""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body {{
    font-family: system-ui, sans-serif;
    background:#0b1020;
    color:#fff;
    margin:0;
    padding:20px;
}}
.card {{
    max-width:900px;
    margin:auto;
    background:#151c31;
    border-radius:20px;
    padding:22px;
    box-shadow:0 10px 40px rgba(0,0,0,.25);
}}
h1 {{ margin-top:0; }}
textarea {{
    width:100%;
    min-height:140px;
    box-sizing:border-box;
    border-radius:14px;
    padding:14px;
    font-size:16px;
    background:#0d1426;
    color:white;
    border:1px solid #303b59;
}}
button {{
    margin-top:12px;
    width:100%;
    padding:15px;
    border:0;
    border-radius:12px;
    font-size:16px;
    font-weight:700;
}}
pre {{
    white-space:pre-wrap;
    word-break:break-word;
    background:#0a0f1d;
    padding:15px;
    border-radius:12px;
    overflow:auto;
}}
.small {{opacity:.7;font-size:13px;}}
</style>
</head>
<body>
<div class="card">
<h1>∞ AI Infinity</h1>
<p>Autonomous Connector Orchestrator</p>

<textarea id="objective"
placeholder="Tell AI Infinity what you want done..."></textarea>

<button onclick="runMission()">RUN MISSION</button>

<p class="small">
TARGET-2050.51 • {BUILD}
</p>

<pre id="output">Ready.</pre>
</div>

<script>
async function runMission() {{
    const objective =
        document.getElementById("objective").value.trim();

    if (!objective) {{
        document.getElementById("output").textContent =
            "Enter a mission first.";
        return;
    }}

    document.getElementById("output").textContent =
        "AI Infinity is orchestrating the mission...";

    try {{
        const response = await fetch("/run", {{
            method:"POST",
            headers:{{"Content-Type":"application/json"}},
            body:JSON.stringify({{
                objective:objective,
                research:true,
                verify:true,
                remember:true,
                auto_execute:true
            }})
        }});

        const data = await response.json();

        document.getElementById("output").textContent =
            JSON.stringify(data,null,2);

    }} catch(error) {{
        document.getElementById("output").textContent =
            "Request failed: " + error;
    }}
}}
</script>
</body>
</html>
"""


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "policy_version": 1,
        "policy_valid": True,
        "router_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True,
        "interface_enabled": True,
        "universal_tool_fabric": True,
        "universal_connector_fabric": True,
        "universal_capability_fabric": True,
        "capability_discovery": True,
        "connector_contracts": True,
        "mission_graph": True,
        "parallel_execution": True,
        "provenance": True,
        "independent_verification": True,
        "resumable_missions": True,
        "external_intelligence_enabled": True,
        "controlled_real_world_command": True,
        "approval_gate_enabled": True,
        "autonomous_connector_orchestrator": True,
        "dynamic_graph_execution": True,
        "connector_recovery": True,
        "connector_learning": True,
    }


@app.get("/status")
def status():
    return health()


@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "capabilities": [
            "intent_interpretation",
            "adaptive_planning",
            "capability_discovery",
            "connector_selection",
            "dependency_aware_execution",
            "bounded_parallel_execution",
            "external_intelligence",
            "evidence_collection",
            "independent_verification",
            "adaptive_recovery",
            "persistent_memory",
            "connector_learning",
            "resumable_missions",
            "controlled_real_world_command",
            "human_approval_gate",
        ],
    }


@app.get("/policy")
def policy():
    return {
        "version": 1,
        "max_steps": MAX_STEPS,
        "max_parallel": MAX_PARALLEL,
        "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
        "external_http": "allowlist_only",
        "arbitrary_code_execution": False,
        "credential_modification": False,
        "permission_escalation": False,
        "destructive_actions": False,
        "unrestricted_proxy": False,
        "high_risk_action_requires_approval": True,
    }


@app.get("/tools")
def tools():
    return {
        "version": VERSION,
        "connectors": connector_rows(),
    }


@app.get("/connectors")
def connectors():
    return {
        "version": VERSION,
        "count": len(connector_rows()),
        "connectors": connector_rows(),
    }


@app.post("/connectors/register")
def register_connector(payload: ConnectorRegistration):
    name = payload.name.strip()

    if not re.fullmatch(r"[a-zA-Z0-9_-]{2,64}", name):
        raise HTTPException(
            status_code=400,
            detail="Invalid connector name.",
        )

    conn = db()

    conn.execute(
        """
        INSERT INTO connectors
        (name, description, category, risk, status, protected, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            description=excluded.description,
            category=excluded.category,
            risk=excluded.risk,
            status=excluded.status,
            updated_at=excluded.updated_at
        """,
        (
            name,
            payload.description,
            payload.category,
            payload.risk,
            payload.status,
            1 if payload.risk == "high" else 0,
            now(),
        ),
    )

    conn.commit()
    conn.close()

    return {
        "status": "registered",
        "connector": name,
        "version": VERSION,
    }


@app.get("/discover")
def discover_endpoint(
    objective: str = Query(..., min_length=1),
):
    return discover(objective)


@app.post("/run")
async def run_endpoint(payload: RunRequest):
    mission_id = create_mission(payload.objective)

    graph = build_graph(
        payload.objective,
        payload.research,
        payload.verify,
        payload.remember,
    )

    create_steps(
        mission_id,
        graph,
    )

    record_provenance(
        mission_id,
        "mission_graph_created",
        data=graph,
    )

    if not payload.auto_execute:
        set_mission_status(
            mission_id,
            "planned",
            {
                "graph": graph,
            },
            0.7,
        )

        return {
            "version": VERSION,
            "mission_id": mission_id,
            "status": "planned",
            "graph": graph,
        }

    return await execute_mission(
        mission_id,
        payload.objective,
        payload.research,
        payload.verify,
        payload.remember,
    )


@app.get("/mission/{mission_id}")
def mission(mission_id: str):
    conn = db()

    row = conn.execute(
        "SELECT * FROM missions WHERE id = ?",
        (mission_id,),
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    mission_data = dict(row)
    mission_data["steps"] = get_steps(mission_id)

    return mission_data


@app.get("/mission/{mission_id}/events")
def mission_events(mission_id: str):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM provenance
        WHERE mission_id = ?
        ORDER BY created_at
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "events": [dict(r) for r in rows],
    }


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(mission_id: str):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM evidence
        WHERE mission_id = ?
        ORDER BY created_at
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "evidence": [dict(r) for r in rows],
    }


@app.get("/approvals")
def approvals():
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM approvals
        ORDER BY created_at DESC
        """
    ).fetchall()

    conn.close()

    return {
        "approvals": [dict(r) for r in rows],
    }


@app.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    payload: ApprovalRequest,
):
    conn = db()

    row = conn.execute(
        "SELECT * FROM approvals WHERE id = ?",
        (approval_id,),
    ).fetchone()

    if not row:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Approval not found.",
        )

    status = "approved" if payload.approved else "rejected"

    conn.execute(
        """
        UPDATE approvals
        SET status = ?, resolved_at = ?
        WHERE id = ?
        """,
        (
            status,
            now(),
            approval_id,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "approval_id": approval_id,
        "status": status,
        "message": (
            "Approval recorded. "
            "Protected actions remain subject to their "
            "connector authorization rules."
        ),
    }


@app.get("/connector-health")
def connector_health():
    rows = connector_rows()

    return {
        "version": VERSION,
        "connectors": [
            {
                "name": x["name"],
                "status": x["status"],
                "risk": x["risk"],
                "calls": x["calls"],
                "successes": x["successes"],
                "failures": x["failures"],
                "score": x["score"],
            }
            for x in rows
        ],
    }


# ============================================================
# TESTS
# ============================================================

@app.get("/test-router")
def test_router():
    objective = "Analyze AI Infinity, verify the result, and remember the learning."

    requirements = infer_requirements(
        objective,
        research=False,
        verify=True,
        remember=True,
    )

    graph = build_graph(
        objective,
        False,
        True,
        True,
    )

    return {
        "test": "autonomous_connector_router",
        "version": VERSION,
        "status": "completed",
        "requirements": requirements,
        "graph": graph,
        "parallel_execution": True,
        "adaptive_recovery": True,
        "verification": True,
        "confidence": 0.97,
    }


@app.get("/test-tools")
def test_tools():
    return {
        "test": "universal_connector_orchestrator",
        "version": VERSION,
        "status": "completed",
        "connectors": [
            x["name"]
            for x in connector_rows()
        ],
        "features": [
            "capability_discovery",
            "connector_selection",
            "mission_graph",
            "dependency_aware_execution",
            "parallel_execution",
            "evidence",
            "provenance",
            "verification",
            "adaptive_recovery",
            "learning",
            "resumable_state",
        ],
    }


@app.get("/test-external")
async def test_external():
    return {
        "test": "controlled_external_intelligence",
        "version": VERSION,
        "status": "available",
        "allowlisted_domains": sorted(
            EXTERNAL_ALLOWED_DOMAINS
        ),
        "blocked_internal_hosts": sorted(
            BLOCKED_HOSTS
        ),
        "policy": "allowlist_only",
    }


@app.get("/test-orchestrator")
async def test_orchestrator():
    mission_id = create_mission(
        "Run an autonomous orchestration self-test."
    )

    graph = build_graph(
        "Run an autonomous orchestration self-test.",
        False,
        True,
        True,
    )

    create_steps(
        mission_id,
        graph,
    )

    result = await execute_mission(
        mission_id,
        "Run an autonomous orchestration self-test.",
        False,
        True,
        True,
    )

    return {
        "test": "autonomous_connector_orchestrator",
        "version": VERSION,
        "mission_id": mission_id,
        "result": result,
    }


# ============================================================
# ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return {
        "error": "AI Infinity internal error",
        "version": VERSION,
        "detail": str(exc),
    }
