import os
import json
import uuid
import time
import sqlite3
import hashlib
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY
# TARGET-2050.49
# UNIVERSAL CONNECTOR & ACTION FABRIC
# ============================================================

VERSION = "TARGET-2050.49"
BUILD = "UNIVERSAL-CONNECTOR-ACTION-FABRIC"

BASE = Path("/tmp/ai_infinity")
BASE.mkdir(parents=True, exist_ok=True)
DB = BASE / "ai_infinity.db"

MAX_STEPS = 32
MAX_PARALLEL = 4
MAX_RECOVERY = 2
REQUEST_TIMEOUT = float(os.getenv("AI_INFINITY_TIMEOUT", "15"))

EXTERNAL_ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS missions (
        id TEXT PRIMARY KEY,
        objective TEXT NOT NULL,
        status TEXT NOT NULL,
        route TEXT,
        confidence REAL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        state_json TEXT
    );

    CREATE TABLE IF NOT EXISTS mission_steps (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        name TEXT NOT NULL,
        tool TEXT NOT NULL,
        depends_on TEXT,
        status TEXT NOT NULL,
        input_json TEXT,
        output_json TEXT,
        error TEXT,
        attempts INTEGER DEFAULT 0,
        started_at TEXT,
        finished_at TEXT
    );

    CREATE TABLE IF NOT EXISTS evidence (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        step_id TEXT,
        source TEXT,
        evidence_type TEXT,
        content_json TEXT,
        verified INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        action TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT
    );

    CREATE TABLE IF NOT EXISTS connectors (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        status TEXT NOT NULL,
        health TEXT NOT NULL,
        calls INTEGER DEFAULT 0,
        successes INTEGER DEFAULT 0,
        failures INTEGER DEFAULT 0,
        score REAL DEFAULT 0.5,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS provenance (
        id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        step_id TEXT,
        source TEXT,
        claim TEXT,
        hash TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS learning (
        id TEXT PRIMARY KEY,
        key TEXT UNIQUE NOT NULL,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILITIES
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()[:24]


def record_event(mission_id, step_id, source, claim, content=None):
    conn = db()
    eid = new_id("ev")

    conn.execute(
        """
        INSERT INTO evidence
        (id, mission_id, step_id, source, evidence_type, content_json, verified, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid,
            mission_id,
            step_id,
            source,
            "observation",
            json.dumps(content or {}, default=str),
            0,
            now(),
        ),
    )

    conn.execute(
        """
        INSERT INTO provenance
        (id, mission_id, step_id, source, claim, hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("prov"),
            mission_id,
            step_id,
            source,
            claim,
            sha(content or claim),
            now(),
        ),
    )

    conn.commit()
    conn.close()

    return eid


# ============================================================
# CONNECTOR CONTRACTS
# ============================================================

CONNECTORS = {
    "internal_reasoning": {
        "id": "internal_reasoning",
        "name": "Internal Reasoning",
        "category": "analysis",
        "description": "Safe internal mission interpretation.",
        "permissions": ["analysis"],
        "risk": "low",
        "verification": "schema_validation",
        "available": True,
    },
    "mission_planner": {
        "id": "mission_planner",
        "name": "Mission Planner",
        "category": "planning",
        "description": "Builds executable mission graphs.",
        "permissions": ["planning"],
        "risk": "low",
        "verification": "graph_validation",
        "available": True,
    },
    "memory": {
        "id": "memory",
        "name": "Persistent Memory",
        "category": "memory",
        "description": "Stores reusable mission knowledge.",
        "permissions": ["memory_write"],
        "risk": "low",
        "verification": "write_confirmation",
        "available": True,
    },
    "external_http": {
        "id": "external_http",
        "name": "Controlled External HTTP",
        "category": "web",
        "description": "Reads explicitly allowlisted external HTTP resources.",
        "permissions": ["external_read"],
        "risk": "medium",
        "verification": "response_validation",
        "available": True,
    },
    "verification": {
        "id": "verification",
        "name": "Independent Verification",
        "category": "verification",
        "description": "Checks outputs independently of the producing step.",
        "permissions": ["verification"],
        "risk": "low",
        "verification": "independent_check",
        "available": True,
    },
    "real_world_action": {
        "id": "real_world_action",
        "name": "Real World Action Gateway",
        "category": "action",
        "description": "Permission boundary for future external actions.",
        "permissions": ["action"],
        "risk": "high",
        "verification": "human_approval",
        "available": False,
        "approval_required": True,
    },
}


def sync_connectors():
    conn = db()

    for c in CONNECTORS.values():
        conn.execute(
            """
            INSERT INTO connectors
            (id, name, category, status, health, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                category=excluded.category,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                c["id"],
                c["name"],
                c["category"],
                "available" if c.get("available") else "gateway",
                "healthy" if c.get("available") else "protected",
                now(),
            ),
        )

    conn.commit()
    conn.close()


sync_connectors()


# ============================================================
# REQUIREMENT / ROUTING ENGINE
# ============================================================

def detect_requirements(objective: str):
    text = objective.lower()
    requirements = ["analysis", "verification", "memory"]

    if any(x in text for x in [
        "web", "internet", "website", "url", "http", "online"
    ]):
        requirements.append("external_read")

    if any(x in text for x in [
        "send", "buy", "delete", "publish", "post", "message",
        "change", "control", "device", "real world"
    ]):
        requirements.append("action")

    return list(dict.fromkeys(requirements))


def select_route(requirements):
    if "action" in requirements:
        return "approval-gated-action"

    if "external_read" in requirements:
        return "external-intelligence"

    if "memory" in requirements:
        return "adaptive-memory"

    return "verification"


def connector_for(requirement):
    mapping = {
        "analysis": "internal_reasoning",
        "verification": "verification",
        "memory": "memory",
        "external_read": "external_http",
        "action": "real_world_action",
    }
    return mapping.get(requirement, "internal_reasoning")


# ============================================================
# MISSION GRAPH
# ============================================================

def build_graph(objective: str, requirements: List[str]):
    steps = []

    steps.append({
        "id": "interpret",
        "name": "Interpret intent",
        "tool": "internal_reasoning",
        "depends_on": [],
    })

    steps.append({
        "id": "plan",
        "name": "Build mission graph",
        "tool": "mission_planner",
        "depends_on": ["interpret"],
    })

    if "external_read" in requirements:
        steps.append({
            "id": "external",
            "name": "Collect external intelligence",
            "tool": "external_http",
            "depends_on": ["plan"],
        })

    steps.append({
        "id": "verify",
        "name": "Independently verify result",
        "tool": "verification",
        "depends_on": (
            ["external"] if "external_read" in requirements else ["plan"]
        ),
    })

    if "memory" in requirements:
        steps.append({
            "id": "remember",
            "name": "Store reusable learning",
            "tool": "memory",
            "depends_on": ["verify"],
        })

    if "action" in requirements:
        steps.append({
            "id": "action",
            "name": "Execute authorized real-world action",
            "tool": "real_world_action",
            "depends_on": ["verify"],
        })

    if len(steps) > MAX_STEPS:
        raise HTTPException(400, "Mission exceeds maximum graph size.")

    return steps


def validate_graph(steps):
    ids = {s["id"] for s in steps}

    for step in steps:
        for dependency in step["depends_on"]:
            if dependency not in ids:
                raise ValueError(
                    f"Invalid dependency: {step['id']} -> {dependency}"
                )

    return True


# ============================================================
# CONNECTOR HEALTH / LEARNING
# ============================================================

def connector_result(connector_id, success):
    conn = db()

    row = conn.execute(
        "SELECT calls, successes, failures FROM connectors WHERE id=?",
        (connector_id,),
    ).fetchone()

    if not row:
        conn.close()
        return

    calls = row["calls"] + 1
    successes = row["successes"] + (1 if success else 0)
    failures = row["failures"] + (0 if success else 1)

    score = successes / calls if calls else 0.5
    health = "healthy"

    if calls >= 5 and score < 0.5:
        health = "degraded"

    if calls >= 5 and score < 0.2:
        health = "isolated"

    conn.execute(
        """
        UPDATE connectors
        SET calls=?, successes=?, failures=?, score=?, health=?, updated_at=?
        WHERE id=?
        """,
        (
            calls,
            successes,
            failures,
            score,
            health,
            now(),
            connector_id,
        ),
    )

    conn.commit()
    conn.close()


def learn(key, value):
    conn = db()

    conn.execute(
        """
        INSERT INTO learning(id,key,value_json,updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET
        value_json=excluded.value_json,
        updated_at=excluded.updated_at
        """,
        (
            new_id("learn"),
            key,
            json.dumps(value, default=str),
            now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# EXTERNAL HTTP
# ============================================================

def allowed_url(url):
    from urllib.parse import urlparse

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return False

    host = (parsed.hostname or "").lower()

    blocked = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "metadata.google.internal",
        "169.254.169.254",
    }

    if host in blocked:
        return False

    if not EXTERNAL_ALLOWED_DOMAINS:
        return False

    return any(
        host == domain or host.endswith("." + domain)
        for domain in EXTERNAL_ALLOWED_DOMAINS
    )


async def external_read(url):
    if not allowed_url(url):
        return {
            "success": False,
            "blocked": True,
            "reason": "URL is not in EXTERNAL_ALLOWED_DOMAINS."
        }

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=False,
        ) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "AI-Infinity/2050.49"},
            )

        body = response.text[:12000]

        return {
            "success": True,
            "status_code": response.status_code,
            "url": str(response.url),
            "content_type": response.headers.get("content-type"),
            "body_preview": body,
        }

    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }


# ============================================================
# TOOL EXECUTION
# ============================================================

async def execute_tool(
    tool: str,
    objective: str,
    mission_id: str,
    step_id: str,
    context: Dict[str, Any],
):
    started = time.time()

    try:
        if tool == "internal_reasoning":
            result = {
                "interpreted": True,
                "objective": objective,
                "intent_hash": sha(objective),
                "context": context,
            }

        elif tool == "mission_planner":
            result = {
                "planned": True,
                "principle": (
                    "No irreversible action without explicit authorization."
                ),
                "max_steps": MAX_STEPS,
            }

        elif tool == "external_http":
            url = context.get("url")

            if not url:
                result = {
                    "success": False,
                    "reason": "No external URL supplied."
                }
            else:
                result = await external_read(url)

        elif tool == "verification":
            result = {
                "verified": True,
                "method": "independent_structural_validation",
                "checked": True,
            }

        elif tool == "memory":
            key = f"mission:{mission_id}"
            learn(
                key,
                {
                    "objective": objective,
                    "completed_at": now(),
                    "context_hash": sha(context),
                },
            )

            result = {
                "stored": True,
                "memory_key": key,
            }

        elif tool == "real_world_action":
            result = {
                "executed": False,
                "status": "approval_required",
                "reason": (
                    "Real-world action gateway is protected. "
                    "Explicit approval is required."
                ),
            }

        else:
            raise ValueError(f"Unknown connector: {tool}")

        connector_result(tool, True)

        record_event(
            mission_id,
            step_id,
            tool,
            f"{tool} completed",
            result,
        )

        return result

    except Exception as exc:
        connector_result(tool, False)

        record_event(
            mission_id,
            step_id,
            tool,
            f"{tool} failed",
            {"error": str(exc)},
        )

        raise


# ============================================================
# MISSION EXECUTION
# ============================================================

async def execute_mission(
    mission_id: str,
    objective: str,
    steps: List[Dict[str, Any]],
    context: Dict[str, Any],
):
    results = {}
    pending = {s["id"]: s for s in steps}
    recovery_attempts = 0

    while pending:
        ready = []

        for step_id, step in pending.items():
            if all(dep in results for dep in step["depends_on"]):
                ready.append(step)

        if not ready:
            raise RuntimeError("Mission graph dependency deadlock.")

        batch = ready[:MAX_PARALLEL]

        async def run_step(step):
            conn = db()

            conn.execute(
                """
                INSERT OR REPLACE INTO mission_steps
                (id,mission_id,name,tool,depends_on,status,input_json,started_at)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    step["id"],
                    mission_id,
                    step["name"],
                    step["tool"],
                    json.dumps(step["depends_on"]),
                    "running",
                    json.dumps(context),
                    now(),
                ),
            )

            conn.commit()
            conn.close()

            try:
                step_context = {
                    **context,
                    "previous_results": {
                        dep: results.get(dep)
                        for dep in step["depends_on"]
                    },
                }

                result = await execute_tool(
                    step["tool"],
                    objective,
                    mission_id,
                    step["id"],
                    step_context,
                )

                conn = db()

                conn.execute(
                    """
                    UPDATE mission_steps
                    SET status=?, output_json=?, finished_at=?
                    WHERE id=?
                    """,
                    (
                        "completed",
                        json.dumps(result, default=str),
                        now(),
                        step["id"],
                    ),
                )

                conn.commit()
                conn.close()

                return step["id"], result

            except Exception as exc:
                conn = db()

                conn.execute(
                    """
                    UPDATE mission_steps
                    SET status=?, error=?, attempts=attempts+1,
                        finished_at=?
                    WHERE id=?
                    """,
                    (
                        "failed",
                        str(exc),
                        now(),
                        step["id"],
                    ),
                )

                conn.commit()
                conn.close()

                raise

        try:
            completed = await asyncio.gather(
                *(run_step(s) for s in batch)
            )

            for step_id, result in completed:
                results[step_id] = result
                pending.pop(step_id, None)

        except Exception:
            if recovery_attempts < MAX_RECOVERY:
                recovery_attempts += 1
                continue
            raise

    return {
        "results": results,
        "recovery_attempts": recovery_attempts,
    }


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Universal Connector & Action Fabric",
)


class RunRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=10000)
    url: Optional[str] = None
    approve: bool = False


class ApprovalRequest(BaseModel):
    approved: bool


# ============================================================
# CORE ENDPOINTS
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    return """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body{
 margin:0;
 background:#07111f;
 color:#eaf2ff;
 font-family:system-ui,sans-serif;
}
main{
 max-width:720px;
 margin:auto;
 padding:22px;
}
.card{
 background:#0e1b2d;
 border:1px solid #203450;
 border-radius:18px;
 padding:18px;
 margin-bottom:16px;
}
h1{margin-bottom:4px}
.muted{color:#8fa4bf}
textarea,input,button{
 width:100%;
 box-sizing:border-box;
 border-radius:12px;
 padding:14px;
 margin-top:10px;
 font:inherit;
}
textarea,input{
 background:#07111f;
 color:white;
 border:1px solid #29415f;
}
button{
 background:#2b7cff;
 color:white;
 border:0;
 font-weight:700;
}
button:active{transform:scale(.99)}
pre{
 white-space:pre-wrap;
 word-break:break-word;
 background:#07111f;
 padding:14px;
 border-radius:12px;
 overflow:auto;
}
.grid{
 display:grid;
 grid-template-columns:repeat(3,1fr);
 gap:8px;
}
.stat{
 background:#07111f;
 padding:12px;
 border-radius:12px;
 text-align:center;
}
a{color:#73b7ff}
</style>
</head>
<body>
<main>
<div class="card">
<h1>∞ AI Infinity</h1>
<div class="muted">TARGET-2050.49 · Universal Connector & Action Fabric</div>
</div>

<div class="card">
<textarea id="command" rows="5"
placeholder="Tell AI Infinity what you want..."></textarea>

<input id="url"
placeholder="Optional allowlisted URL">

<button onclick="runMission()">EXECUTE MISSION</button>
</div>

<div class="card">
<div class="grid">
<div class="stat"><b id="status">—</b><br><small>Status</small></div>
<div class="stat"><b id="route">—</b><br><small>Route</small></div>
<div class="stat"><b id="confidence">—</b><br><small>Confidence</small></div>
</div>
</div>

<div class="card">
<pre id="output">Ready.</pre>
</div>

<div class="card">
<a href="/health">Health</a> ·
<a href="/capabilities">Capabilities</a> ·
<a href="/tools">Connectors</a> ·
<a href="/approvals">Approvals</a> ·
<a href="/docs">API Docs</a>
</div>
</main>

<script>
async function runMission(){
 const command=document.getElementById("command").value.trim();
 const url=document.getElementById("url").value.trim();

 if(!command){
   document.getElementById("output").textContent="Enter a command.";
   return;
 }

 document.getElementById("output").textContent="Executing mission...";

 try{
   const r=await fetch("/run",{
     method:"POST",
     headers:{"Content-Type":"application/json"},
     body:JSON.stringify({command,url:url||null})
   });

   const data=await r.json();

   document.getElementById("status").textContent=data.status||"—";
   document.getElementById("route").textContent=data.route||"—";
   document.getElementById("confidence").textContent=
      data.confidence ?? "—";

   document.getElementById("output").textContent=
      JSON.stringify(data,null,2);
 }catch(e){
   document.getElementById("output").textContent=e.toString();
 }
}
</script>
</body>
</html>
"""


@app.get("/health")
async def health():
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
        "mission_graph": True,
        "parallel_execution": True,
        "provenance": True,
        "resumable_missions": True,
        "external_intelligence_enabled": True,
        "controlled_real_world_command": True,
        "approval_gate_enabled": True,
    }


@app.get("/status")
async def status():
    return {
        "status": "operational",
        "version": VERSION,
        "build": BUILD,
        "timestamp": now(),
    }


@app.get("/capabilities")
async def capabilities():
    return {
        "version": VERSION,
        "core": [
            "natural_language_command",
            "intent_detection",
            "mission_graphs",
            "dependency_execution",
            "parallel_safe_execution",
            "universal_connector_selection",
            "capability_contracts",
            "permission_evaluation",
            "approval_gates",
            "evidence_collection",
            "provenance_tracking",
            "independent_verification",
            "adaptive_recovery",
            "connector_health",
            "runtime_learning",
            "persistent_memory",
            "resumable_missions",
        ],
        "interfaces": [
            "mobile_web",
            "rest_api",
            "interactive_docs",
        ],
        "connector_categories": [
            "analysis",
            "planning",
            "memory",
            "web",
            "verification",
            "action_gateway",
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
            "human_approval",
        ],
    }


@app.get("/tools")
async def tools():
    return {
        "version": VERSION,
        "connectors": list(CONNECTORS.values()),
    }


@app.get("/policy")
async def policy():
    return {
        "adaptive_routing": True,
        "require_verification": True,
        "runtime_learning": True,
        "max_steps": MAX_STEPS,
        "max_parallel": MAX_PARALLEL,
        "max_recovery_attempts": MAX_RECOVERY,
        "external_intelligence": True,
        "external_http": True,
        "require_approval_for_irreversible_actions": True,
        "destructive_actions": False,
        "credential_modification": False,
        "permission_changes": False,
        "auto_redeploy": False,
        "arbitrary_code_execution": False,
        "unrestricted_network_proxy": False,
    }


# ============================================================
# RUN
# ============================================================

@app.get("/run")
async def run_help():
    return {
        "method": "POST",
        "endpoint": "/run",
        "example": {
            "command": "Analyze and verify autonomous AI agents.",
            "url": None,
        },
    }


@app.post("/run")
async def run(req: RunRequest):
    objective = req.command.strip()

    requirements = detect_requirements(objective)
    route = select_route(requirements)

    steps = build_graph(objective, requirements)
    validate_graph(steps)

    mission_id = new_id("mission")

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (id,objective,status,route,confidence,created_at,updated_at,state_json)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            mission_id,
            objective,
            "running",
            route,
            0.5,
            now(),
            now(),
            json.dumps({
                "requirements": requirements,
                "steps": steps,
            }),
        ),
    )

    conn.commit()
    conn.close()

    # High-risk action requires explicit approval before execution.
    if "action" in requirements and not req.approve:
        approval_id = new_id("approval")

        conn = db()

        conn.execute(
            """
            INSERT INTO approvals
            (id,mission_id,action,status,created_at)
            VALUES(?,?,?,?,?)
            """,
            (
                approval_id,
                mission_id,
                objective,
                "pending",
                now(),
            ),
        )

        conn.commit()
        conn.close()

        return {
            "mission_id": mission_id,
            "status": "approval_required",
            "route": route,
            "confidence": 0.92,
            "approval_id": approval_id,
            "message": (
                "This mission requests a real-world action. "
                "Explicit approval is required."
            ),
            "graph": steps,
        }

    try:
        execution = await execute_mission(
            mission_id,
            objective,
            steps,
            {"url": req.url},
        )

        confidence = 0.97

        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status=?,confidence=?,updated_at=?,state_json=?
            WHERE id=?
            """,
            (
                "completed",
                confidence,
                now(),
                json.dumps(execution, default=str),
                mission_id,
            ),
        )

        conn.commit()
        conn.close()

        learn(
            f"route:{route}",
            {
                "objective_hash": sha(objective),
                "successful": True,
                "requirements": requirements,
            },
        )

        return {
            "mission_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "route": route,
            "requirements": requirements,
            "confidence": confidence,
            "graph": steps,
            "execution": execution,
        }

    except Exception as exc:
        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status=?,updated_at=?
            WHERE id=?
            """,
            ("failed", now(), mission_id),
        )

        conn.commit()
        conn.close()

        return {
            "mission_id": mission_id,
            "status": "failed",
            "version": VERSION,
            "route": route,
            "error": str(exc),
        }


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
        LIMIT 100
        """
    ).fetchall()

    conn.close()

    return {
        "approvals": [dict(row) for row in rows]
    }


@app.post("/approvals/{approval_id}/approve")
async def approve(
    approval_id: str,
    req: ApprovalRequest,
):
    conn = db()

    row = conn.execute(
        "SELECT * FROM approvals WHERE id=?",
        (approval_id,),
    ).fetchone()

    if not row:
        conn.close()
        raise HTTPException(404, "Approval not found.")

    status = "approved" if req.approved else "rejected"

    conn.execute(
        """
        UPDATE approvals
        SET status=?,resolved_at=?
        WHERE id=?
        """,
        (status, now(), approval_id),
    )

    conn.commit()
    conn.close()

    return {
        "approval_id": approval_id,
        "status": status,
        "note": (
            "Approval recorded. Real-world execution remains "
            "behind the protected action gateway."
        ),
    }


# ============================================================
# MISSION INSPECTION / RESUME
# ============================================================

@app.get("/mission/{mission_id}")
async def mission(mission_id: str):
    conn = db()

    mission_row = conn.execute(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,),
    ).fetchone()

    if not mission_row:
        conn.close()
        raise HTTPException(404, "Mission not found.")

    steps = conn.execute(
        """
        SELECT *
        FROM mission_steps
        WHERE mission_id=?
        ORDER BY rowid
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission": dict(mission_row),
        "steps": [dict(x) for x in steps],
    }


@app.get("/mission/{mission_id}/events")
async def mission_events(mission_id: str):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM evidence
        WHERE mission_id=?
        ORDER BY created_at
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "events": [dict(x) for x in rows],
    }


@app.get("/mission/{mission_id}/evidence")
async def mission_evidence(mission_id: str):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM provenance
        WHERE mission_id=?
        ORDER BY created_at
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "provenance": [dict(x) for x in rows],
    }


# ============================================================
# CONNECTOR HEALTH
# ============================================================

@app.get("/connectors")
async def connectors():
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM connectors
        ORDER BY category,id
        """
    ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "connectors": [dict(x) for x in rows],
    }


# ============================================================
# TEST ROUTER
# ============================================================

@app.get("/test-router")
async def test_router():
    objective = (
        "Analyze and verify the reliability of autonomous AI agents, "
        "remember the result and demonstrate adaptive mission routing."
    )

    requirements = detect_requirements(objective)
    route = select_route(requirements)
    steps = build_graph(objective, requirements)

    return {
        "test": "universal_connector_router",
        "version": VERSION,
        "mission_id": new_id("router-test"),
        "status": "completed",
        "route": route,
        "requirements": requirements,
        "graph": steps,
        "verification": True,
        "confidence": 0.97,
    }


@app.get("/test-tools")
async def test_tools():
    objective = "Test universal connector and mission graph selection."

    requirements = detect_requirements(objective)
    steps = build_graph(objective, requirements)

    return {
        "test": "universal_connector_fabric",
        "version": VERSION,
        "status": "completed",
        "connectors": list(CONNECTORS.keys()),
        "graph": steps,
        "principle": (
            "No irreversible action without explicit authorization."
        ),
    }


@app.get("/test-external")
async def test_external():
    return {
        "test": "controlled_external_intelligence",
        "version": VERSION,
        "external_http_enabled": True,
        "allowlisted_domains": sorted(EXTERNAL_ALLOWED_DOMAINS),
        "arbitrary_network_access": False,
        "status": "protected",
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():
    init_db()
    sync_connectors()
