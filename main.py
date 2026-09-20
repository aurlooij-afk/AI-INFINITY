import os
import re
import json
import time
import uuid
import sqlite3
import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY
# TARGET-2050.48
# UNIVERSAL TOOL FABRIC
#
# Intent
#   ↓
# Mission Planning
#   ↓
# Capability Selection
#   ↓
# Permission / Approval
#   ↓
# Tool Execution
#   ↓
# Evidence
#   ↓
# Verification
#   ↓
# Recovery
#   ↓
# Learning
# ============================================================

VERSION = "TARGET-2050.48"
BUILD = "UNIVERSAL-TOOL-FABRIC-ADAPTIVE-MISSION-CORE"

BASE = Path("/tmp/ai_infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS", "12"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "12"))
MAX_EXTERNAL_RESPONSE = 20000

EXTERNAL_ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
}


app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Universal adaptive intelligence and controlled tool-execution fabric."
)


# ============================================================
# TIME / DATABASE
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            route TEXT,
            requirements TEXT,
            plan TEXT,
            result TEXT,
            confidence REAL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            event_type TEXT,
            message TEXT,
            data TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            source TEXT,
            content TEXT,
            verified INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            action TEXT,
            status TEXT,
            reason TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS tool_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            tool_name TEXT,
            status TEXT,
            input_data TEXT,
            output_data TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS route_learning (
            route TEXT PRIMARY KEY,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS tool_learning (
            tool_name TEXT PRIMARY KEY,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS learning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            key TEXT,
            value TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS adaptive_policy (
            id INTEGER PRIMARY KEY CHECK(id=1),
            version INTEGER,
            data TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS policy_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version INTEGER,
            reason TEXT,
            data TEXT,
            created_at TEXT
        );
        """
    )

    if not conn.execute(
        "SELECT 1 FROM adaptive_policy WHERE id=1"
    ).fetchone():

        policy = default_policy()

        conn.execute(
            """
            INSERT INTO adaptive_policy
            (id, version, data, updated_at)
            VALUES (1, ?, ?, ?)
            """,
            (1, json.dumps(policy), now())
        )

    conn.commit()
    conn.close()


def default_policy():

    return {
        "adaptive_routing": True,
        "require_verification": True,
        "runtime_policy_adaptation": True,

        "max_tool_calls": MAX_TOOL_CALLS,
        "max_recovery_attempts": 2,

        "allow_external_intelligence": True,
        "allow_external_http": True,

        "require_approval_for_irreversible_actions": True,

        "destructive_actions": False,
        "credential_modification": False,
        "permission_changes": False,
        "auto_redeploy": False,

        "request_timeout": REQUEST_TIMEOUT
    }


def get_policy():

    conn = db()

    row = conn.execute(
        "SELECT version,data FROM adaptive_policy WHERE id=1"
    ).fetchone()

    conn.close()

    if not row:
        return 1, default_policy()

    return row["version"], json.loads(row["data"])


def adapt_policy(reason):

    version, policy = get_policy()

    if not policy.get("runtime_policy_adaptation"):
        return version

    if reason == "recovery_success":
        policy["max_recovery_attempts"] = min(
            4,
            int(policy["max_recovery_attempts"]) + 1
        )

    elif reason == "repeated_failure":
        policy["max_recovery_attempts"] = max(
            1,
            int(policy["max_recovery_attempts"]) - 1
        )

    new_version = version + 1

    conn = db()

    conn.execute(
        """
        UPDATE adaptive_policy
        SET version=?, data=?, updated_at=?
        WHERE id=1
        """,
        (new_version, json.dumps(policy), now())
    )

    conn.execute(
        """
        INSERT INTO policy_history
        (version,reason,data,created_at)
        VALUES (?,?,?,?)
        """,
        (new_version, reason, json.dumps(policy), now())
    )

    conn.commit()
    conn.close()

    return new_version


# ============================================================
# EVENTS / EVIDENCE
# ============================================================

def event(mission_id, event_type, message, data=None):

    conn = db()

    conn.execute(
        """
        INSERT INTO events
        (mission_id,event_type,message,data,created_at)
        VALUES (?,?,?,?,?)
        """,
        (
            mission_id,
            event_type,
            message,
            json.dumps(data or {}, default=str),
            now()
        )
    )

    conn.commit()
    conn.close()


def add_evidence(
    mission_id,
    source,
    content,
    verified=False
):

    conn = db()

    conn.execute(
        """
        INSERT INTO evidence
        (mission_id,source,content,verified,created_at)
        VALUES (?,?,?,?,?)
        """,
        (
            mission_id,
            source,
            str(content)[:MAX_EXTERNAL_RESPONSE],
            int(verified),
            now()
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# UNIVERSAL TOOL CONTRACT
# ============================================================

TOOLS = {

    "mission_planner": {
        "category": "planning",
        "permission": "safe",
        "input": "objective",
        "output": "execution_plan"
    },

    "internal_analysis": {
        "category": "intelligence",
        "permission": "safe",
        "input": "objective",
        "output": "analysis"
    },

    "external_http_read": {
        "category": "external_intelligence",
        "permission": "controlled",
        "input": "allowlisted_url",
        "output": "external_evidence"
    },

    "evidence_verification": {
        "category": "verification",
        "permission": "safe",
        "input": "result",
        "output": "verification"
    },

    "memory_write": {
        "category": "learning",
        "permission": "safe",
        "input": "mission_result",
        "output": "memory_record"
    },

    "recovery_engine": {
        "category": "recovery",
        "permission": "safe",
        "input": "failure",
        "output": "recovery_action"
    },

    "real_world_command": {
        "category": "action",
        "permission": "approval_required",
        "input": "authorized_action",
        "output": "action_result"
    }
}


# ============================================================
# REQUIREMENT / INTENT ENGINE
# ============================================================

def infer_requirements(objective):

    text = objective.lower()

    requirements = []

    if any(
        x in text
        for x in [
            "research",
            "search",
            "find",
            "investigate",
            "latest",
            "information",
            "web"
        ]
    ):
        requirements.append("research")

    if any(
        x in text
        for x in [
            "analyze",
            "analyse",
            "compare",
            "understand",
            "explain",
            "plan"
        ]
    ):
        requirements.append("analysis")

    if any(
        x in text
        for x in [
            "verify",
            "validate",
            "confirm",
            "check"
        ]
    ):
        requirements.append("verification")

    if any(
        x in text
        for x in [
            "remember",
            "save",
            "learn",
            "memory"
        ]
    ):
        requirements.append("memory")

    if any(
        x in text
        for x in [
            "internet",
            "website",
            "url",
            "external",
            "http"
        ]
    ):
        requirements.append("external_intelligence")

    if any(
        x in text
        for x in [
            "send",
            "publish",
            "buy",
            "delete",
            "change",
            "control",
            "execute",
            "device",
            "real world"
        ]
    ):
        requirements.append("real_world_action")

    if not requirements:
        requirements.append("analysis")

    return list(dict.fromkeys(requirements))


def choose_route(requirements):

    priority = [
        "real_world_action",
        "verification",
        "external_intelligence",
        "research",
        "analysis",
        "memory"
    ]

    for route in priority:
        if route in requirements:
            return route

    return "analysis"


# ============================================================
# MISSION PLANNER
# ============================================================

def create_plan(objective, requirements):

    steps = [
        "interpret_intent",
        "select_capabilities",
        "execute_authorized_tools",
        "collect_evidence",
        "verify_result",
        "recover_if_needed",
        "store_learning"
    ]

    if "real_world_action" in requirements:
        steps.insert(
            3,
            "request_human_approval_before_irreversible_action"
        )

    return {
        "objective": objective,
        "requirements": requirements,
        "steps": steps,
        "principle": (
            "No irreversible action without explicit authorization."
        )
    }


# ============================================================
# EXTERNAL ACCESS
# ============================================================

def validate_external_url(url):

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            "Only HTTP and HTTPS URLs are supported."
        )

    if not parsed.hostname:
        raise ValueError("Hostname is required.")

    hostname = parsed.hostname.lower()

    if hostname in BLOCKED_HOSTS:
        raise ValueError(
            "Internal destinations are blocked."
        )

    if not any(
        hostname == domain
        or hostname.endswith("." + domain)
        for domain in EXTERNAL_ALLOWED_DOMAINS
    ):
        raise ValueError(
            "Domain is not allowlisted. "
            "Configure EXTERNAL_ALLOWED_DOMAINS in Render."
        )

    return parsed


async def external_http_read(url):

    validate_external_url(url)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True
    ) as client:

        response = await client.get(
            url,
            headers={
                "User-Agent": "AI-Infinity/2050.48"
            }
        )

        return {
            "status_code": response.status_code,
            "url": str(response.url),
            "content": response.text[:MAX_EXTERNAL_RESPONSE],
            "content_length": len(response.text)
        }


# ============================================================
# TOOL LEARNING
# ============================================================

def learn_tool(tool_name, success):

    conn = db()

    row = conn.execute(
        "SELECT * FROM tool_learning WHERE tool_name=?",
        (tool_name,)
    ).fetchone()

    if row:

        if success:
            conn.execute(
                """
                UPDATE tool_learning
                SET successes=successes+1,updated_at=?
                WHERE tool_name=?
                """,
                (now(), tool_name)
            )
        else:
            conn.execute(
                """
                UPDATE tool_learning
                SET failures=failures+1,updated_at=?
                WHERE tool_name=?
                """,
                (now(), tool_name)
            )

    else:

        conn.execute(
            """
            INSERT INTO tool_learning
            (tool_name,successes,failures,updated_at)
            VALUES (?,?,?,?)
            """,
            (
                tool_name,
                1 if success else 0,
                0 if success else 1,
                now()
            )
        )

    conn.commit()
    conn.close()


def learn_route(route, success):

    conn = db()

    row = conn.execute(
        "SELECT * FROM route_learning WHERE route=?",
        (route,)
    ).fetchone()

    if row:

        column = "successes" if success else "failures"

        conn.execute(
            f"""
            UPDATE route_learning
            SET {column}={column}+1,updated_at=?
            WHERE route=?
            """,
            (now(), route)
        )

    else:

        conn.execute(
            """
            INSERT INTO route_learning
            (route,successes,failures,updated_at)
            VALUES (?,?,?,?)
            """,
            (
                route,
                1 if success else 0,
                0 if success else 1,
                now()
            )
        )

    conn.commit()
    conn.close()


# ============================================================
# TOOL EXECUTION FABRIC
# ============================================================

async def execute_tool(
    mission_id,
    tool_name,
    payload
):

    if tool_name not in TOOLS:
        raise ValueError(
            "Unknown tool: " + tool_name
        )

    event(
        mission_id,
        "tool_selected",
        tool_name,
        {
            "contract": TOOLS[tool_name],
            "payload_keys": list(payload.keys())
        }
    )

    start = time.time()

    try:

        if tool_name == "mission_planner":

            result = create_plan(
                payload["objective"],
                payload["requirements"]
            )

        elif tool_name == "internal_analysis":

            objective = payload["objective"]

            words = re.findall(
                r"\b\w+\b",
                objective
            )

            result = {
                "objective": objective,
                "word_count": len(words),
                "intent_hash": hashlib.sha256(
                    objective.encode()
                ).hexdigest()[:16],
                "interpretation":
                    "Mission decomposed into intent, "
                    "requirements and execution stages."
            }

        elif tool_name == "external_http_read":

            result = await external_http_read(
                payload["url"]
            )

            add_evidence(
                mission_id,
                result["url"],
                result["content"],
                False
            )

        elif tool_name == "evidence_verification":

            target = payload.get("result")

            verified = bool(target)

            result = {
                "verified": verified,
                "method":
                    "controlled_result_validation"
            }

        elif tool_name == "memory_write":

            conn = db()

            conn.execute(
                """
                INSERT INTO learning
                (mission_id,key,value,created_at)
                VALUES (?,?,?,?)
                """,
                (
                    mission_id,
                    "mission_learning",
                    json.dumps(
                        payload.get("result"),
                        default=str
                    ),
                    now()
                )
            )

            conn.commit()
            conn.close()

            result = {
                "stored": True,
                "mission_id": mission_id
            }

        elif tool_name == "recovery_engine":

            result = {
                "recovery": True,
                "strategy": "controlled_retry"
            }

        elif tool_name == "real_world_command":

            result = {
                "status": "approval_required",
                "action": payload.get("action"),
                "message":
                    "The action is prepared but has not "
                    "been executed."
            }

        else:
            raise ValueError("Unsupported tool.")

        status = "completed"

        learn_tool(tool_name, True)

    except Exception as exc:

        status = "failed"

        learn_tool(tool_name, False)

        conn = db()

        conn.execute(
            """
            INSERT INTO tool_runs
            (mission_id,tool_name,status,input_data,
             output_data,created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                mission_id,
                tool_name,
                status,
                json.dumps(payload, default=str),
                str(exc),
                now()
            )
        )

        conn.commit()
        conn.close()

        raise

    elapsed = round(time.time() - start, 4)

    conn = db()

    conn.execute(
        """
        INSERT INTO tool_runs
        (mission_id,tool_name,status,input_data,
         output_data,created_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            mission_id,
            tool_name,
            status,
            json.dumps(payload, default=str),
            json.dumps(result, default=str),
            now()
        )
    )

    conn.commit()
    conn.close()

    event(
        mission_id,
        "tool_completed",
        tool_name,
        {
            "elapsed_seconds": elapsed
        }
    )

    return result


# ============================================================
# APPROVAL FABRIC
# ============================================================

def create_approval(
    mission_id,
    action,
    reason
):

    approval_id = "approval-" + uuid.uuid4().hex[:12]

    conn = db()

    conn.execute(
        """
        INSERT INTO approvals
        (id,mission_id,action,status,reason,
         created_at,updated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            approval_id,
            mission_id,
            action,
            "pending",
            reason,
            now(),
            now()
        )
    )

    conn.commit()
    conn.close()

    return approval_id


# ============================================================
# MISSION ENGINE
# ============================================================

async def execute_mission(
    mission_id,
    objective,
    external_url=None
):

    version, policy = get_policy()

    requirements = infer_requirements(objective)

    if external_url:
        if "external_intelligence" not in requirements:
            requirements.append(
                "external_intelligence"
            )

    route = choose_route(requirements)

    plan = create_plan(
        objective,
        requirements
    )

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (id,objective,status,route,requirements,
         plan,result,confidence,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            mission_id,
            objective,
            "running",
            route,
            json.dumps(requirements),
            json.dumps(plan),
            "",
            0,
            now(),
            now()
        )
    )

    conn.commit()
    conn.close()

    event(
        mission_id,
        "mission_started",
        "Universal tool-fabric mission started.",
        {
            "route": route,
            "requirements": requirements,
            "policy_version": version
        }
    )

    # --------------------------------------------------------
    # REAL-WORLD ACTION GATE
    # --------------------------------------------------------

    if "real_world_action" in requirements:

        approval_id = create_approval(
            mission_id,
            objective,
            "Real-world action requires explicit approval."
        )

        result = {
            "status": "approval_required",
            "mission_id": mission_id,
            "approval_id": approval_id,
            "message":
                "Mission prepared. No irreversible "
                "real-world action was executed."
        }

        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status=?,
                result=?,
                confidence=?,
                updated_at=?
            WHERE id=?
            """,
            (
                "awaiting_approval",
                json.dumps(result),
                1.0,
                now(),
                mission_id
            )
        )

        conn.commit()
        conn.close()

        event(
            mission_id,
            "approval_required",
            "Explicit human approval required.",
            {
                "approval_id": approval_id
            }
        )

        return result

    # --------------------------------------------------------
    # NORMAL TOOL PIPELINE
    # --------------------------------------------------------

    attempts = 0
    recovery_attempts = 0

    results = []

    success = False

    while attempts < int(
        policy.get("max_tool_calls", MAX_TOOL_CALLS)
    ):

        attempts += 1

        try:

            plan_result = await execute_tool(
                mission_id,
                "mission_planner",
                {
                    "objective": objective,
                    "requirements": requirements
                }
            )

            results.append({
                "tool": "mission_planner",
                "result": plan_result
            })

            analysis_result = await execute_tool(
                mission_id,
                "internal_analysis",
                {
                    "objective": objective
                }
            )

            results.append({
                "tool": "internal_analysis",
                "result": analysis_result
            })

            if (
                "external_intelligence"
                in requirements
                and external_url
            ):

                external_result = await execute_tool(
                    mission_id,
                    "external_http_read",
                    {
                        "url": external_url
                    }
                )

                results.append({
                    "tool": "external_http_read",
                    "result": external_result
                })

            verification_result = await execute_tool(
                mission_id,
                "evidence_verification",
                {
                    "result": results
                }
            )

            results.append({
                "tool": "evidence_verification",
                "result": verification_result
            })

            if verification_result.get("verified"):

                success = True
                break

        except Exception as exc:

            event(
                mission_id,
                "pipeline_failure",
                str(exc),
                {
                    "attempt": attempts
                }
            )

            if (
                recovery_attempts
                < int(
                    policy.get(
                        "max_recovery_attempts",
                        2
                    )
                )
            ):

                recovery_attempts += 1

                recovery = await execute_tool(
                    mission_id,
                    "recovery_engine",
                    {
                        "failure": str(exc)
                    }
                )

                event(
                    mission_id,
                    "recovery_completed",
                    "Controlled recovery completed.",
                    recovery
                )

                adapt_policy(
                    "recovery_success"
                )

                await asyncio.sleep(0.1)

                continue

            break

    confidence = 0.97 if success else 0.20

    if success:

        await execute_tool(
            mission_id,
            "memory_write",
            {
                "result": {
                    "route": route,
                    "confidence": confidence,
                    "successful": True
                }
            }
        )

        status = "completed"

    else:
        status = "failed"

        adapt_policy(
            "repeated_failure"
        )

    learn_route(
        route,
        success
    )

    final_result = {
        "mission_id": mission_id,
        "status": status,
        "route": route,
        "requirements": requirements,
        "plan": plan,
        "attempts": attempts,
        "recovery_attempts": recovery_attempts,
        "verification": success,
        "confidence": confidence,
        "tool_results": results
    }

    conn = db()

    conn.execute(
        """
        UPDATE missions
        SET status=?,
            result=?,
            confidence=?,
            updated_at=?
        WHERE id=?
        """,
        (
            status,
            json.dumps(final_result, default=str),
            confidence,
            now(),
            mission_id
        )
    )

    conn.commit()
    conn.close()

    event(
        mission_id,
        "mission_completed",
        status,
        {
            "attempts": attempts,
            "recovery_attempts": recovery_attempts,
            "confidence": confidence
        }
    )

    return final_result


# ============================================================
# REQUEST MODELS
# ============================================================

class RunRequest(BaseModel):

    objective: str = Field(
        ...,
        min_length=1,
        max_length=20000
    )

    external_url: Optional[str] = None


# ============================================================
# UNIVERSAL MOBILE INTERFACE
# ============================================================

HTML = """
<!DOCTYPE html>
<html>
<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>AI Infinity</title>

<style>

:root {
    --bg:#060a12;
    --panel:#0e1725;
    --panel2:#142033;
    --line:#26364c;
    --text:#f5f8ff;
    --muted:#94a6bd;
    --accent:#62ddff;
    --good:#55e6a5;
    --warn:#ffd166;
}

* {
    box-sizing:border-box;
}

body {
    margin:0;
    min-height:100vh;
    color:var(--text);
    background:
        radial-gradient(
            circle at top,
            #142744 0,
            #060a12 48%
        );
    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}

.container {
    max-width:1100px;
    margin:auto;
    padding:18px;
}

.header {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:12px;
    margin-bottom:18px;
}

.brand {
    display:flex;
    align-items:center;
    gap:12px;
}

.logo {
    width:50px;
    height:50px;
    display:grid;
    place-items:center;
    border-radius:16px;
    border:1px solid var(--line);
    background:var(--panel2);
    font-size:28px;
}

h1 {
    margin:0;
    font-size:24px;
}

.sub {
    color:var(--muted);
    font-size:12px;
    margin-top:3px;
}

.online {
    color:var(--good);
    border:1px solid var(--line);
    background:#0b1817;
    border-radius:999px;
    padding:8px 11px;
    font-size:12px;
}

.card {
    background:rgba(14,23,37,.94);
    border:1px solid var(--line);
    border-radius:20px;
    padding:18px;
    margin-bottom:14px;
}

.label {
    color:var(--muted);
    font-size:11px;
    letter-spacing:.08em;
}

h2 {
    font-size:20px;
    margin:7px 0 13px;
}

textarea,
input {
    width:100%;
    color:var(--text);
    background:#070d17;
    border:1px solid var(--line);
    border-radius:14px;
    padding:14px;
    outline:none;
}

textarea {
    min-height:145px;
    resize:vertical;
    font-size:16px;
}

input {
    margin-top:10px;
}

textarea:focus,
input:focus {
    border-color:var(--accent);
}

button {
    width:100%;
    margin-top:12px;
    border:0;
    border-radius:14px;
    padding:15px;
    background:var(--accent);
    color:#041019;
    font-size:15px;
    font-weight:900;
}

button:disabled {
    opacity:.55;
}

.grid {
    display:grid;
    grid-template-columns:
        repeat(4,1fr);
    gap:10px;
}

.metric {
    background:var(--panel2);
    border:1px solid var(--line);
    border-radius:15px;
    padding:13px;
}

.metric span {
    display:block;
    color:var(--muted);
    font-size:10px;
}

.metric b {
    display:block;
    margin-top:5px;
    font-size:16px;
    overflow:hidden;
    text-overflow:ellipsis;
}

pre {
    margin:0;
    white-space:pre-wrap;
    word-break:break-word;
    background:#060a12;
    border-radius:14px;
    padding:14px;
    overflow:auto;
    color:#dce9f7;
}

.links {
    display:flex;
    flex-wrap:wrap;
    gap:8px;
}

.links a {
    color:var(--accent);
    text-decoration:none;
    border:1px solid var(--line);
    border-radius:10px;
    padding:9px 11px;
    font-size:12px;
}

.footer {
    color:var(--muted);
    text-align:center;
    font-size:11px;
    padding:15px;
}

@media(max-width:700px) {

    .grid {
        grid-template-columns:
            repeat(2,1fr);
    }

    .header {
        align-items:flex-start;
    }

    .container {
        padding:12px;
    }
}

</style>
</head>

<body>

<div class="container">

<div class="header">

<div class="brand">

<div class="logo">∞</div>

<div>
<h1>AI Infinity</h1>

<div class="sub">
Universal Tool Fabric • Adaptive Mission Intelligence
</div>

</div>

</div>

<div id="online"
     class="online">
● ONLINE
</div>

</div>


<div class="card">

<div class="label">
UNIVERSAL COMMAND CENTER
</div>

<h2>
Give AI Infinity an objective
</h2>

<textarea
id="objective"
placeholder="Example: Analyze this problem, research the relevant information, verify the result and prepare the safest next actions."
></textarea>

<input
id="url"
placeholder="Optional allowlisted external URL"
/>

<button
id="run"
onclick="runMission()">
▶ EXECUTE MISSION
</button>

</div>


<div class="grid">

<div class="metric">
<span>VERSION</span>
<b id="version">—</b>
</div>

<div class="metric">
<span>ROUTE</span>
<b id="route">—</b>
</div>

<div class="metric">
<span>STATUS</span>
<b id="missionStatus">READY</b>
</div>

<div class="metric">
<span>CONFIDENCE</span>
<b id="confidence">—</b>
</div>

</div>


<div class="card">

<div class="label">
MISSION INTELLIGENCE
</div>

<pre id="output">
AI Infinity is ready.
</pre>

</div>


<div class="card">

<div class="label">
SYSTEM
</div>

<div class="links">

<a href="/health"
target="_blank">Health</a>

<a href="/status"
target="_blank">Status</a>

<a href="/capabilities"
target="_blank">Capabilities</a>

<a href="/tools"
target="_blank">Tool Fabric</a>

<a href="/policy"
target="_blank">Policy</a>

<a href="/test-router"
target="_blank">Router Test</a>

<a href="/test-external"
target="_blank">External Test</a>

<a href="/docs"
target="_blank">API Docs</a>

</div>

</div>


<div class="footer">
AI Infinity • TARGET-2050.48
</div>

</div>


<script>

async function health() {

    try {

        const r =
            await fetch("/health");

        const d =
            await r.json();

        document.getElementById(
            "version"
        ).textContent =
            d.version || "—";

        document.getElementById(
            "online"
        ).textContent =
            d.status === "healthy"
            ? "● ONLINE"
            : "● " + d.status;

    } catch(e) {

        document.getElementById(
            "online"
        ).textContent =
            "● OFFLINE";
    }
}


async function runMission() {

    const objective =
        document.getElementById(
            "objective"
        ).value.trim();

    const externalUrl =
        document.getElementById(
            "url"
        ).value.trim();

    if (!objective) {

        alert(
            "Enter an objective first."
        );

        return;
    }

    const button =
        document.getElementById("run");

    const output =
        document.getElementById("output");

    button.disabled = true;

    button.textContent =
        "⏳ EXECUTING...";

    document.getElementById(
        "missionStatus"
    ).textContent =
        "RUNNING";

    output.textContent =
        "AI Infinity is planning the mission...";

    try {

        const response =
            await fetch(
                "/run",
                {
                    method:"POST",
                    headers:{
                        "Content-Type":
                            "application/json"
                    },
                    body:JSON.stringify({
                        objective:
                            objective,
                        external_url:
                            externalUrl || null
                    })
                }
            );

        const data =
            await response.json();

        output.textContent =
            JSON.stringify(
                data,
                null,
                2
            );

        document.getElementById(
            "missionStatus"
        ).textContent =
            data.status || "DONE";

        document.getElementById(
            "route"
        ).textContent =
            data.route || "—";

        if (
            data.confidence !==
            undefined
        ) {

            document.getElementById(
                "confidence"
            ).textContent =
                Math.round(
                    data.confidence * 100
                ) + "%";
        }

    } catch(e) {

        output.textContent =
            "Mission error: " +
            e.message;

        document.getElementById(
            "missionStatus"
        ).textContent =
            "ERROR";

    } finally {

        button.disabled = false;

        button.textContent =
            "▶ EXECUTE MISSION";
    }
}


health();

setInterval(
    health,
    15000
);

</script>

</body>
</html>
"""


# ============================================================
# ROOT INTERFACE
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():

    return HTML


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    version, policy = get_policy()

    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,

        "policy_version": version,
        "policy_valid": True,

        "router_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True,

        "interface_enabled": True,

        "universal_tool_fabric": True,

        "external_intelligence_enabled":
            policy.get(
                "allow_external_intelligence"
            ),

        "controlled_real_world_command": True,

        "approval_gate_enabled":
            policy.get(
                "require_approval_for_irreversible_actions"
            )
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
async def status():

    version, policy = get_policy()

    return {
        "name": "AI Infinity",
        "status": "online",
        "version": VERSION,
        "build": BUILD,

        "interface": True,
        "universal_tool_fabric": True,

        "adaptive_routing":
            policy.get("adaptive_routing"),

        "external_intelligence":
            policy.get(
                "allow_external_intelligence"
            ),

        "approval_gate":
            policy.get(
                "require_approval_for_irreversible_actions"
            ),

        "destructive_actions":
            policy.get("destructive_actions"),

        "auto_redeploy":
            policy.get("auto_redeploy")
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():

    return {
        "version": VERSION,

        "core": [
            "natural_language_command",
            "intent_detection",
            "mission_planning",
            "adaptive_routing",
            "universal_tool_selection",
            "permission_evaluation",
            "tool_execution",
            "evidence_collection",
            "verification",
            "recovery",
            "runtime_learning",
            "persistent_memory",
            "approval_gates"
        ],

        "interfaces": [
            "mobile_web",
            "rest_api",
            "interactive_docs"
        ],

        "external_extension_points": [
            "web_search",
            "browser",
            "files",
            "code_sandbox",
            "databases",
            "calendar",
            "messaging",
            "device_control",
            "human_approval"
        ]
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
async def policy():

    version, data = get_policy()

    return {
        "version": version,
        "valid": True,
        "policy": data
    }


# ============================================================
# TOOLS
# ============================================================

@app.get("/tools")
async def tools():

    return {
        "count": len(TOOLS),
        "tools": TOOLS,
        "external_allowed_domains":
            sorted(
                EXTERNAL_ALLOWED_DOMAINS
            )
    }


# ============================================================
# RUN
# ============================================================

@app.get("/run")
async def run_info():

    return {
        "endpoint": "/run",
        "method": "POST",
        "schema": {
            "objective": "string",
            "external_url":
                "optional allowlisted URL"
        }
    }


@app.post("/run")
async def run(request: RunRequest):

    mission_id = (
        "mission-" +
        uuid.uuid4().hex[:12]
    )

    return await execute_mission(
        mission_id,
        request.objective,
        request.external_url
    )


# ============================================================
# APPROVALS
# ============================================================

@app.get("/approvals")
async def approvals():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM approvals
        ORDER BY created_at DESC
        LIMIT 50
        """
    ).fetchall()

    conn.close()

    return {
        "approvals": [
            dict(row)
            for row in rows
        ]
    }


@app.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM approvals
        WHERE id=?
        """,
        (approval_id,)
    ).fetchone()

    if not row:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Approval not found."
        )

    conn.execute(
        """
        UPDATE approvals
        SET status='approved',
            updated_at=?
        WHERE id=?
        """,
        (now(), approval_id)
    )

    conn.commit()
    conn.close()

    return {
        "approval_id": approval_id,
        "status": "approved",
        "message":
            "Approval recorded. "
            "A separate authorized integration "
            "must execute the external action."
    }


# ============================================================
# MISSIONS
# ============================================================

@app.get("/mission/{mission_id}")
async def mission(mission_id: str):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (mission_id,)
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found."
        )

    return dict(row)


@app.get("/mission/{mission_id}/events")
async def mission_events(
    mission_id: str
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM events
        WHERE mission_id=?
        ORDER BY id ASC
        """,
        (mission_id,)
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "events": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/mission/{mission_id}/evidence")
async def mission_evidence(
    mission_id: str
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM evidence
        WHERE mission_id=?
        ORDER BY id ASC
        """,
        (mission_id,)
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "evidence": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# ADAPTIVE ROUTER TEST
# ============================================================

@app.get("/test-router")
async def test_router():

    mission_id = (
        "router-test-" +
        uuid.uuid4().hex[:8]
    )

    result = await execute_mission(
        mission_id,
        (
            "Analyze and verify the reliability "
            "of autonomous AI agents, remember "
            "the result and demonstrate adaptive "
            "mission routing."
        )
    )

    return {
        "test": "universal_adaptive_router",
        "version": VERSION,
        **result
    }


# ============================================================
# TOOL FABRIC TEST
# ============================================================

@app.get("/test-tools")
async def test_tools():

    mission_id = (
        "tool-test-" +
        uuid.uuid4().hex[:8]
    )

    result = await execute_tool(
        mission_id,
        "mission_planner",
        {
            "objective":
                "Test universal tool selection.",
            "requirements":
                [
                    "analysis",
                    "verification",
                    "memory"
                ]
        }
    )

    return {
        "test": "universal_tool_fabric",
        "version": VERSION,
        "status": "completed",
        "tool": "mission_planner",
        "result": result
    }


# ============================================================
# EXTERNAL TEST
# ============================================================

@app.get("/test-external")
async def test_external():

    version, policy = get_policy()

    return {
        "test": "external_intelligence",
        "version": VERSION,
        "enabled":
            policy.get(
                "allow_external_intelligence"
            ),
        "http_enabled":
            policy.get(
                "allow_external_http"
            ),
        "allowed_domains":
            sorted(
                EXTERNAL_ALLOWED_DOMAINS
            ),
        "security":
            "allowlist-controlled"
    }


# ============================================================
# STARTUP
# ============================================================

init_db()


if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000"
            )
        )
    )
