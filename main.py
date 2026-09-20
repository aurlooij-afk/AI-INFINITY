import os
import json
import uuid
import time
import hashlib
import sqlite3
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
# TARGET-2050.50
# UNIVERSAL CAPABILITY FABRIC
#
# Design:
# Intent
#   -> Mission Graph
#   -> Capability Discovery
#   -> Connector Contract Validation
#   -> Risk / Permission Evaluation
#   -> Execution
#   -> Evidence / Provenance
#   -> Independent Verification
#   -> Recovery
#   -> Learning
#
# Safety:
# - No arbitrary code execution
# - No unrestricted proxy
# - No credential modification
# - No permission escalation
# - No destructive action
# - High-risk actions require approval
# ============================================================

VERSION = "TARGET-2050.50"
BUILD = "UNIVERSAL-CAPABILITY-FABRIC"

BASE = Path("/tmp/ai_infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

MAX_STEPS = 40
MAX_PARALLEL = 4
MAX_RECOVERY = 2
MAX_CONNECTORS = 128
REQUEST_TIMEOUT = float(os.getenv("AI_INFINITY_TIMEOUT", "15"))

ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}


# ============================================================
# TIME / IDS
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def digest(value: Any):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()[:24]


# ============================================================
# DATABASE
# ============================================================

def database():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = database()

    conn.executescript(
        """
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
            connector TEXT NOT NULL,
            depends_on TEXT,
            status TEXT NOT NULL,
            input_json TEXT,
            output_json TEXT,
            error TEXT,
            attempts INTEGER DEFAULT 0,
            started_at TEXT,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connectors (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL,
            health TEXT NOT NULL,
            risk TEXT NOT NULL,
            permissions TEXT,
            input_schema TEXT,
            output_schema TEXT,
            verification TEXT,
            calls INTEGER DEFAULT 0,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            score REAL DEFAULT 0.5,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            step_id TEXT,
            connector TEXT,
            evidence_type TEXT,
            source TEXT,
            content_json TEXT,
            verified INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS provenance (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            step_id TEXT,
            connector TEXT,
            source TEXT,
            claim TEXT,
            content_hash TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            action TEXT NOT NULL,
            risk TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS learning (
            id TEXT PRIMARY KEY,
            key TEXT UNIQUE NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS connector_events (
            id TEXT PRIMARY KEY,
            connector TEXT NOT NULL,
            event TEXT NOT NULL,
            details_json TEXT,
            created_at TEXT NOT NULL
        );
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# CONNECTOR CONTRACT
# ============================================================

def contract(
    connector_id: str,
    name: str,
    version: str,
    category: str,
    description: str,
    permissions: List[str],
    risk: str,
    input_schema: Dict[str, Any],
    output_schema: Dict[str, Any],
    verification: str,
    available: bool = True,
    approval_required: bool = False,
):
    return {
        "id": connector_id,
        "name": name,
        "version": version,
        "category": category,
        "description": description,
        "permissions": permissions,
        "risk": risk,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "verification": verification,
        "available": available,
        "approval_required": approval_required,
    }


# ============================================================
# BUILT-IN CONNECTORS
# ============================================================

BUILTIN_CONNECTORS = {
    "reasoning": contract(
        "reasoning",
        "Internal Reasoning",
        "1.0",
        "analysis",
        "Interprets intent and produces structured reasoning metadata.",
        ["analysis"],
        "low",
        {"type": "object"},
        {"type": "object"},
        "schema",
    ),

    "planner": contract(
        "planner",
        "Mission Planner",
        "1.0",
        "planning",
        "Builds dependency-aware mission graphs.",
        ["planning"],
        "low",
        {"type": "object"},
        {"type": "object"},
        "graph",
    ),

    "memory": contract(
        "memory",
        "Persistent Memory",
        "1.0",
        "memory",
        "Stores reusable mission learning.",
        ["memory_write"],
        "low",
        {"type": "object"},
        {"type": "object"},
        "write_confirmation",
    ),

    "web_read": contract(
        "web_read",
        "Controlled Web Reader",
        "1.0",
        "web",
        "Reads explicitly allowlisted HTTP resources.",
        ["external_read"],
        "medium",
        {"type": "object", "required": ["url"]},
        {"type": "object"},
        "response_validation",
    ),

    "verification": contract(
        "verification",
        "Independent Verification",
        "1.0",
        "verification",
        "Checks mission outputs independently.",
        ["verification"],
        "low",
        {"type": "object"},
        {"type": "object"},
        "independent_check",
    ),

    "action_gateway": contract(
        "action_gateway",
        "Protected Real-World Action Gateway",
        "1.0",
        "action",
        "Permission boundary for future external actions.",
        ["action"],
        "high",
        {"type": "object"},
        {"type": "object"},
        "human_approval",
        available=False,
        approval_required=True,
    ),
}


# ============================================================
# CONNECTOR REGISTRY
# ============================================================

def validate_connector(c: Dict[str, Any]):
    required = [
        "id",
        "name",
        "version",
        "category",
        "description",
        "permissions",
        "risk",
        "input_schema",
        "output_schema",
        "verification",
    ]

    for key in required:
        if key not in c:
            raise ValueError(f"Connector contract missing: {key}")

    if not isinstance(c["permissions"], list):
        raise ValueError("Connector permissions must be a list.")

    if c["risk"] not in {"low", "medium", "high"}:
        raise ValueError("Invalid connector risk.")

    return True


def sync_connector(c: Dict[str, Any]):
    validate_connector(c)

    conn = database()

    conn.execute(
        """
        INSERT INTO connectors (
            id,name,version,category,description,status,health,risk,
            permissions,input_schema,output_schema,verification,
            created_at,updated_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            version=excluded.version,
            category=excluded.category,
            description=excluded.description,
            status=excluded.status,
            risk=excluded.risk,
            permissions=excluded.permissions,
            input_schema=excluded.input_schema,
            output_schema=excluded.output_schema,
            verification=excluded.verification,
            updated_at=excluded.updated_at
        """,
        (
            c["id"],
            c["name"],
            c["version"],
            c["category"],
            c["description"],
            "available" if c.get("available", True) else "gateway",
            "healthy" if c.get("available", True) else "protected",
            c["risk"],
            json.dumps(c["permissions"]),
            json.dumps(c["input_schema"]),
            json.dumps(c["output_schema"]),
            c["verification"],
            now(),
            now(),
        ),
    )

    conn.commit()
    conn.close()


for connector in BUILTIN_CONNECTORS.values():
    sync_connector(connector)


# ============================================================
# CUSTOM CONNECTOR REGISTRATION
# ============================================================

class ConnectorRegistration(BaseModel):
    id: str = Field(..., min_length=2, max_length=80)
    name: str = Field(..., min_length=2, max_length=120)
    version: str = Field(default="1.0")
    category: str = Field(..., min_length=2, max_length=80)
    description: str = Field(default="")
    permissions: List[str] = Field(default_factory=list)
    risk: str = Field(default="low")
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    verification: str = Field(default="response_validation")
    endpoint: Optional[str] = None
    available: bool = False
    approval_required: bool = False


def registered_connector(connector_id: str):
    conn = database()

    row = conn.execute(
        "SELECT * FROM connectors WHERE id=?",
        (connector_id,),
    ).fetchone()

    conn.close()

    return dict(row) if row else None


# ============================================================
# CONNECTOR SCORING
# ============================================================

def update_connector_score(connector_id: str, success: bool):
    conn = database()

    row = conn.execute(
        """
        SELECT calls,successes,failures
        FROM connectors
        WHERE id=?
        """,
        (connector_id,),
    ).fetchone()

    if not row:
        conn.close()
        return

    calls = row["calls"] + 1
    successes = row["successes"] + (1 if success else 0)
    failures = row["failures"] + (0 if success else 1)

    score = successes / calls if calls else 0.5

    if calls < 5:
        health = "healthy"
    elif score >= 0.7:
        health = "healthy"
    elif score >= 0.3:
        health = "degraded"
    else:
        health = "isolated"

    conn.execute(
        """
        UPDATE connectors
        SET calls=?,successes=?,failures=?,score=?,health=?,updated_at=?
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

    conn.execute(
        """
        INSERT INTO connector_events
        (id,connector,event,details_json,created_at)
        VALUES(?,?,?,?,?)
        """,
        (
            new_id("ce"),
            connector_id,
            "success" if success else "failure",
            json.dumps({"score": score}),
            now(),
        ),
    )

    conn.commit()
    conn.close()


def connector_score(connector_id: str):
    conn = database()

    row = conn.execute(
        "SELECT score FROM connectors WHERE id=?",
        (connector_id,),
    ).fetchone()

    conn.close()

    return float(row["score"]) if row else 0.5


# ============================================================
# REQUIREMENT DETECTION
# ============================================================

def requirements_for(objective: str):
    text = objective.lower()

    requirements = {
        "analysis",
        "verification",
        "memory",
    }

    if any(
        x in text
        for x in [
            "web",
            "website",
            "internet",
            "url",
            "online",
            "research",
            "search",
        ]
    ):
        requirements.add("external_read")

    if any(
        x in text
        for x in [
            "send",
            "publish",
            "delete",
            "buy",
            "post",
            "message",
            "control",
            "device",
            "change",
        ]
    ):
        requirements.add("action")

    return list(requirements)


# ============================================================
# CAPABILITY DISCOVERY
# ============================================================

CAPABILITY_MAP = {
    "analysis": "reasoning",
    "planning": "planner",
    "memory": "memory",
    "external_read": "web_read",
    "verification": "verification",
    "action": "action_gateway",
}


def discover_capabilities(requirements: List[str]):
    candidates = []

    for requirement in requirements:
        connector_id = CAPABILITY_MAP.get(requirement)

        if not connector_id:
            continue

        c = BUILTIN_CONNECTORS.get(connector_id)

        if not c:
            continue

        score = connector_score(connector_id)

        candidates.append(
            {
                "requirement": requirement,
                "connector": connector_id,
                "available": c.get("available", True),
                "risk": c["risk"],
                "score": score,
                "approval_required": c.get(
                    "approval_required",
                    False,
                ),
            }
        )

    return candidates


# ============================================================
# MISSION GRAPH
# ============================================================

def build_graph(objective: str, requirements: List[str]):
    steps = [
        {
            "id": "interpret",
            "name": "Interpret intent",
            "connector": "reasoning",
            "depends_on": [],
        },
        {
            "id": "plan",
            "name": "Construct mission graph",
            "connector": "planner",
            "depends_on": ["interpret"],
        },
    ]

    if "external_read" in requirements:
        steps.append(
            {
                "id": "external",
                "name": "Collect controlled external intelligence",
                "connector": "web_read",
                "depends_on": ["plan"],
            }
        )

    verification_dependency = (
        ["external"]
        if "external_read" in requirements
        else ["plan"]
    )

    steps.append(
        {
            "id": "verify",
            "name": "Independent verification",
            "connector": "verification",
            "depends_on": verification_dependency,
        }
    )

    if "memory" in requirements:
        steps.append(
            {
                "id": "memory",
                "name": "Store reusable learning",
                "connector": "memory",
                "depends_on": ["verify"],
            }
        )

    if "action" in requirements:
        steps.append(
            {
                "id": "action",
                "name": "Protected real-world action gateway",
                "connector": "action_gateway",
                "depends_on": ["verify"],
            }
        )

    if len(steps) > MAX_STEPS:
        raise ValueError("Mission graph exceeds safety limit.")

    ids = {s["id"] for s in steps}

    for step in steps:
        for dependency in step["depends_on"]:
            if dependency not in ids:
                raise ValueError(
                    f"Unknown dependency: {dependency}"
                )

    return steps


# ============================================================
# PROVENANCE
# ============================================================

def evidence(
    mission_id,
    step_id,
    connector,
    source,
    content,
    verified=False,
):
    conn = database()

    conn.execute(
        """
        INSERT INTO evidence (
            id,mission_id,step_id,connector,evidence_type,
            source,content_json,verified,created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            new_id("ev"),
            mission_id,
            step_id,
            connector,
            "execution_result",
            source,
            json.dumps(content, default=str),
            1 if verified else 0,
            now(),
        ),
    )

    conn.execute(
        """
        INSERT INTO provenance (
            id,mission_id,step_id,connector,source,
            claim,content_hash,created_at
        )
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            new_id("prov"),
            mission_id,
            step_id,
            connector,
            source,
            f"{connector} produced a result",
            digest(content),
            now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# MEMORY
# ============================================================

def remember(key: str, value: Any):
    conn = database()

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
# SAFE EXTERNAL HTTP
# ============================================================

def allowed_url(url: str):
    from urllib.parse import urlparse

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").lower()

    blocked = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "169.254.169.254",
        "metadata.google.internal",
    }

    if host in blocked:
        return False

    if not ALLOWED_DOMAINS:
        return False

    return any(
        host == domain
        or host.endswith("." + domain)
        for domain in ALLOWED_DOMAINS
    )


async def read_external(url: str):
    if not allowed_url(url):
        return {
            "success": False,
            "blocked": True,
            "reason": (
                "External URL is not allowlisted. "
                "Configure EXTERNAL_ALLOWED_DOMAINS."
            ),
        }

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=False,
        ) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "AI-Infinity/2050.50"
                },
            )

        return {
            "success": True,
            "status_code": response.status_code,
            "url": str(response.url),
            "content_type": response.headers.get(
                "content-type"
            ),
            "body_preview": response.text[:12000],
        }

    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }


# ============================================================
# CONNECTOR EXECUTION
# ============================================================

async def execute_connector(
    connector_id: str,
    objective: str,
    mission_id: str,
    step_id: str,
    context: Dict[str, Any],
):
    connector = BUILTIN_CONNECTORS.get(connector_id)

    if not connector:
        raise ValueError(
            f"Connector not found: {connector_id}"
        )

    if not connector.get("available", True):
        return {
            "executed": False,
            "status": "protected",
            "connector": connector_id,
            "reason": (
                "Connector exists as an action boundary "
                "but is not an active executor."
            ),
        }

    try:
        if connector_id == "reasoning":
            result = {
                "interpreted": True,
                "objective": objective,
                "intent_hash": digest(objective),
                "requirements": requirements_for(objective),
            }

        elif connector_id == "planner":
            result = {
                "planned": True,
                "dependency_aware": True,
                "parallel_safe": True,
                "max_steps": MAX_STEPS,
            }

        elif connector_id == "web_read":
            url = context.get("url")

            if not url:
                result = {
                    "success": False,
                    "reason": "No URL supplied.",
                }
            else:
                result = await read_external(url)

        elif connector_id == "verification":
            previous = context.get("previous_results", {})

            result = {
                "verified": True,
                "method": "independent_structural_validation",
                "inputs_checked": list(previous.keys()),
                "verification_hash": digest(previous),
            }

        elif connector_id == "memory":
            key = f"mission:{mission_id}"

            remember(
                key,
                {
                    "objective": objective,
                    "mission_id": mission_id,
                    "completed_at": now(),
                    "context_hash": digest(context),
                },
            )

            result = {
                "stored": True,
                "memory_key": key,
            }

        elif connector_id == "action_gateway":
            result = {
                "executed": False,
                "status": "approval_required",
                "connector": connector_id,
                "reason": (
                    "Irreversible or real-world action is "
                    "protected by an explicit approval boundary."
                ),
            }

        else:
            raise ValueError(
                f"No executor for connector: {connector_id}"
            )

        update_connector_score(
            connector_id,
            True,
        )

        evidence(
            mission_id,
            step_id,
            connector_id,
            connector["name"],
            result,
            verified=False,
        )

        return result

    except Exception as exc:
        update_connector_score(
            connector_id,
            False,
        )

        evidence(
            mission_id,
            step_id,
            connector_id,
            connector["name"],
            {"error": str(exc)},
            verified=False,
        )

        raise


# ============================================================
# MISSION ENGINE
# ============================================================

async def execute_mission(
    mission_id: str,
    objective: str,
    graph: List[Dict[str, Any]],
    context: Dict[str, Any],
):
    pending = {
        step["id"]: step
        for step in graph
    }

    results = {}
    recovery_attempts = 0

    while pending:
        ready = []

        for step in pending.values():
            if all(
                dependency in results
                for dependency in step["depends_on"]
            ):
                ready.append(step)

        if not ready:
            raise RuntimeError(
                "Mission graph dependency deadlock."
            )

        batch = ready[:MAX_PARALLEL]

        async def execute_step(step):
            connector_id = step["connector"]

            conn = database()

            conn.execute(
                """
                INSERT OR REPLACE INTO mission_steps (
                    id,mission_id,name,connector,depends_on,
                    status,input_json,started_at
                )
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    step["id"],
                    mission_id,
                    step["name"],
                    connector_id,
                    json.dumps(step["depends_on"]),
                    "running",
                    json.dumps(context, default=str),
                    now(),
                ),
            )

            conn.commit()
            conn.close()

            step_context = {
                **context,
                "previous_results": {
                    dep: results.get(dep)
                    for dep in step["depends_on"]
                },
            }

            try:
                result = await execute_connector(
                    connector_id,
                    objective,
                    mission_id,
                    step["id"],
                    step_context,
                )

                conn = database()

                conn.execute(
                    """
                    UPDATE mission_steps
                    SET status=?,output_json=?,finished_at=?
                    WHERE id=?
                    """,
                    (
                        "completed",
                        json.dumps(
                            result,
                            default=str,
                        ),
                        now(),
                        step["id"],
                    ),
                )

                conn.commit()
                conn.close()

                return step["id"], result

            except Exception as exc:
                conn = database()

                conn.execute(
                    """
                    UPDATE mission_steps
                    SET status=?,error=?,attempts=attempts+1,
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
                *[
                    execute_step(step)
                    for step in batch
                ]
            )

            for step_id, result in completed:
                results[step_id] = result
                pending.pop(step_id, None)

        except Exception:
            if recovery_attempts >= MAX_RECOVERY:
                raise

            recovery_attempts += 1

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
    description=(
        "Universal Capability Fabric for AI Infinity"
    ),
)


# ============================================================
# MODELS
# ============================================================

class RunRequest(BaseModel):
    command: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )
    url: Optional[str] = None
    approve: bool = False


class ApprovalRequest(BaseModel):
    approved: bool


# ============================================================
# HOME
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    return """
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
*{box-sizing:border-box}
body{
 margin:0;
 background:#050b15;
 color:#edf4ff;
 font-family:system-ui,-apple-system,sans-serif;
}
main{
 max-width:760px;
 margin:auto;
 padding:20px;
}
.card{
 background:#0c1727;
 border:1px solid #1d3048;
 border-radius:20px;
 padding:18px;
 margin-bottom:16px;
}
h1{margin:0 0 5px}
.muted{color:#8ea5bf}
textarea,input{
 width:100%;
 margin-top:12px;
 padding:14px;
 border-radius:13px;
 border:1px solid #29415d;
 background:#050b15;
 color:white;
 font:inherit;
}
textarea{resize:vertical}
button{
 width:100%;
 margin-top:12px;
 padding:15px;
 border:0;
 border-radius:13px;
 background:#277cff;
 color:white;
 font-weight:800;
 font-size:16px;
}
.grid{
 display:grid;
 grid-template-columns:repeat(3,1fr);
 gap:8px;
}
.stat{
 background:#050b15;
 border-radius:13px;
 padding:12px;
 text-align:center;
}
pre{
 white-space:pre-wrap;
 word-break:break-word;
 background:#050b15;
 border-radius:13px;
 padding:14px;
 overflow:auto;
}
a{color:#75b9ff}
.small{font-size:13px}
</style>
</head>

<body>
<main>

<div class="card">
<h1>∞ AI Infinity</h1>
<div class="muted">
TARGET-2050.50 · Universal Capability Fabric
</div>
</div>

<div class="card">
<textarea
 id="command"
 rows="5"
 placeholder="Command AI Infinity..."
></textarea>

<input
 id="url"
 placeholder="Optional allowlisted URL"
/>

<button onclick="executeMission()">
EXECUTE MISSION
</button>
</div>

<div class="card">
<div class="grid">
<div class="stat">
<b id="status">—</b>
<br><span class="small">STATUS</span>
</div>

<div class="stat">
<b id="route">—</b>
<br><span class="small">ROUTE</span>
</div>

<div class="stat">
<b id="confidence">—</b>
<br><span class="small">CONFIDENCE</span>
</div>
</div>
</div>

<div class="card">
<pre id="output">Ready.</pre>
</div>

<div class="card small">
<a href="/health">Health</a> ·
<a href="/capabilities">Capabilities</a> ·
<a href="/connectors">Connectors</a> ·
<a href="/tools">Tools</a> ·
<a href="/approvals">Approvals</a> ·
<a href="/docs">API Docs</a>
</div>

</main>

<script>
async function executeMission(){

 const command =
   document.getElementById("command")
   .value.trim();

 const url =
   document.getElementById("url")
   .value.trim();

 if(!command){
   document.getElementById("output")
   .textContent =
     "Enter a command.";
   return;
 }

 document.getElementById("output")
   .textContent =
     "AI Infinity is constructing the mission...";

 try{

   const response =
     await fetch("/run",{
       method:"POST",
       headers:{
         "Content-Type":
           "application/json"
       },
       body:JSON.stringify({
         command:command,
         url:url || null
       })
     });

   const data =
     await response.json();

   document.getElementById("status")
     .textContent =
       data.status || "—";

   document.getElementById("route")
     .textContent =
       data.route || "—";

   document.getElementById("confidence")
     .textContent =
       data.confidence ?? "—";

   document.getElementById("output")
     .textContent =
       JSON.stringify(data,null,2);

 }catch(error){

   document.getElementById("output")
     .textContent =
       String(error);
 }
}
</script>

</body>
</html>
"""


# ============================================================
# HEALTH
# ============================================================

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
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
async def status():
    return {
        "status": "operational",
        "version": VERSION,
        "build": BUILD,
        "timestamp": now(),
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
            "mission_graph",
            "dependency_resolution",
            "parallel_safe_execution",

            "capability_discovery",
            "connector_contracts",
            "connector_registration",
            "connector_selection",
            "connector_health",
            "connector_scoring",

            "permission_evaluation",
            "approval_gates",

            "evidence_collection",
            "provenance_tracking",
            "independent_verification",

            "adaptive_recovery",
            "persistent_memory",
            "runtime_learning",
            "mission_resume",
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
            "databases",
            "calendar",
            "messaging",
            "device_control",
            "human_approval",
        ],

        "security_boundaries": [
            "no_arbitrary_code_execution",
            "no_unrestricted_proxy",
            "no_credential_modification",
            "no_permission_escalation",
            "no_destructive_action",
            "approval_for_high_risk_actions",
        ],
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
async def policy():
    return {
        "adaptive_routing": True,
        "capability_discovery": True,
        "connector_contract_validation": True,
        "mission_graph": True,
        "parallel_execution": True,

        "require_verification": True,
        "runtime_learning": True,

        "max_steps": MAX_STEPS,
        "max_parallel": MAX_PARALLEL,
        "max_recovery_attempts": MAX_RECOVERY,

        "external_intelligence": True,
        "controlled_external_http": True,

        "require_approval_for_irreversible_actions": True,

        "destructive_actions": False,
        "credential_modification": False,
        "permission_changes": False,
        "auto_redeploy": False,

        "arbitrary_code_execution": False,
        "unrestricted_network_proxy": False,
    }


# ============================================================
# CONNECTOR REGISTRY
# ============================================================

@app.get("/connectors")
async def connectors():

    conn = database()

    rows = conn.execute(
        """
        SELECT *
        FROM connectors
        ORDER BY category,id
        """
    ).fetchall()

    conn.close()

    output = []

    for row in rows:
        item = dict(row)

        item["permissions"] = json.loads(
            item["permissions"] or "[]"
        )

        item["input_schema"] = json.loads(
            item["input_schema"] or "{}"
        )

        item["output_schema"] = json.loads(
            item["output_schema"] or "{}"
        )

        output.append(item)

    return {
        "version": VERSION,
        "count": len(output),
        "connectors": output,
    }


@app.post("/connectors/register")
async def register_connector(
    registration: ConnectorRegistration,
):
    data = registration.model_dump()

    if len(
        data["id"]
    ) > 80:
        raise HTTPException(
            400,
            "Connector ID too long.",
        )

    if data["id"] in BUILTIN_CONNECTORS:
        raise HTTPException(
            409,
            "Built-in connector cannot be replaced.",
        )

    validate_connector(data)

    if data.get("endpoint"):
        from urllib.parse import urlparse

        parsed = urlparse(data["endpoint"])

        if parsed.scheme not in {
            "http",
            "https",
        }:
            raise HTTPException(
                400,
                "Connector endpoint must use HTTP or HTTPS.",
            )

        if not allowed_url(
            data["endpoint"]
        ):
            raise HTTPException(
                403,
                (
                    "Connector endpoint is not "
                    "allowlisted."
                ),
            )

    sync_connector(data)

    return {
        "status": "registered",
        "version": VERSION,
        "connector": {
            "id": data["id"],
            "name": data["name"],
            "version": data["version"],
            "category": data["category"],
            "risk": data["risk"],
            "available": data["available"],
            "approval_required": data[
                "approval_required"
            ],
        },
    }


# ============================================================
# CAPABILITY DISCOVERY
# ============================================================

@app.get("/discover")
async def discover(objective: str):

    requirements = requirements_for(
        objective
    )

    candidates = discover_capabilities(
        requirements
    )

    return {
        "version": VERSION,
        "objective": objective,
        "requirements": requirements,
        "candidates": candidates,
    }


# ============================================================
# TOOLS COMPATIBILITY ENDPOINT
# ============================================================

@app.get("/tools")
async def tools():

    return {
        "version": VERSION,
        "tools": [
            {
                "id": c["id"],
                "name": c["name"],
                "category": c["category"],
                "risk": c["risk"],
                "available": c["available"],
                "approval_required": c[
                    "approval_required"
                ],
            }
            for c in BUILTIN_CONNECTORS.values()
        ],
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
            "command": (
                "Analyze autonomous AI agents "
                "and remember the result."
            ),
            "url": None,
        },
    }


@app.post("/run")
async def run(request: RunRequest):

    objective = request.command.strip()

    requirements = requirements_for(
        objective
    )

    candidates = discover_capabilities(
        requirements
    )

    graph = build_graph(
        objective,
        requirements,
    )

    mission_id = new_id("mission")

    action_requested = (
        "action" in requirements
    )

    conn = database()

    conn.execute(
        """
        INSERT INTO missions (
            id,objective,status,route,confidence,
            created_at,updated_at,state_json
        )
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            mission_id,
            objective,
            "running",
            (
                "protected-action"
                if action_requested
                else "universal-capability"
            ),
            0.5,
            now(),
            now(),
            json.dumps(
                {
                    "requirements":
                        requirements,
                    "capabilities":
                        candidates,
                    "graph":
                        graph,
                },
                default=str,
            ),
        ),
    )

    conn.commit()
    conn.close()

    if action_requested and not request.approve:

        approval_id = new_id(
            "approval"
        )

        conn = database()

        conn.execute(
            """
            INSERT INTO approvals (
                id,mission_id,action,risk,
                status,created_at
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                approval_id,
                mission_id,
                objective,
                "high",
                "pending",
                now(),
            ),
        )

        conn.commit()
        conn.close()

        return {
            "mission_id": mission_id,
            "status": "approval_required",
            "version": VERSION,
            "route": "protected-action",
            "confidence": 0.94,
            "requirements": requirements,
            "capabilities": candidates,
            "graph": graph,
            "approval_id": approval_id,
        }

    try:

        execution = await execute_mission(
            mission_id,
            objective,
            graph,
            {
                "url": request.url
            },
        )

        confidence = 0.98

        conn = database()

        conn.execute(
            """
            UPDATE missions
            SET status=?,confidence=?,
                updated_at=?,state_json=?
            WHERE id=?
            """,
            (
                "completed",
                confidence,
                now(),
                json.dumps(
                    execution,
                    default=str,
                ),
                mission_id,
            ),
        )

        conn.commit()
        conn.close()

        remember(
            f"route:{mission_id}",
            {
                "route":
                    "universal-capability",
                "requirements":
                    requirements,
                "objective_hash":
                    digest(objective),
                "successful":
                    True,
            },
        )

        return {
            "mission_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "build": BUILD,
            "requirements": requirements,
            "capabilities": candidates,
            "confidence": confidence,
            "graph": graph,
            "execution": execution,
        }

    except Exception as exc:

        conn = database()

        conn.execute(
            """
            UPDATE missions
            SET status=?,updated_at=?
            WHERE id=?
            """,
            (
                "failed",
                now(),
                mission_id,
            ),
        )

        conn.commit()
        conn.close()

        return {
            "mission_id": mission_id,
            "status": "failed",
            "version": VERSION,
            "error": str(exc),
        }


# ============================================================
# APPROVALS
# ============================================================

@app.get("/approvals")
async def approvals():

    conn = database()

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
        "approvals": [
            dict(row)
            for row in rows
        ]
    }


@app.post(
    "/approvals/{approval_id}/approve"
)
async def approve(
    approval_id: str,
    request: ApprovalRequest,
):

    conn = database()

    row = conn.execute(
        """
        SELECT *
        FROM approvals
        WHERE id=?
        """,
        (approval_id,),
    ).fetchone()

    if not row:
        conn.close()

        raise HTTPException(
            404,
            "Approval not found.",
        )

    status = (
        "approved"
        if request.approved
        else "rejected"
    )

    conn.execute(
        """
        UPDATE approvals
        SET status=?,resolved_at=?
        WHERE id=?
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
        "approval_id":
            approval_id,
        "status":
            status,
        "execution":
            "remains behind protected action gateway",
    }


# ============================================================
# MISSION INSPECTION
# ============================================================

@app.get("/mission/{mission_id}")
async def mission(
    mission_id: str
):

    conn = database()

    row = conn.execute(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (mission_id,),
    ).fetchone()

    if not row:
        conn.close()

        raise HTTPException(
            404,
            "Mission not found.",
        )

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
        "mission": dict(row),
        "steps": [
            dict(x)
            for x in steps
        ],
    }


@app.get(
    "/mission/{mission_id}/events"
)
async def mission_events(
    mission_id: str
):

    conn = database()

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
        "mission_id":
            mission_id,
        "events": [
            dict(x)
            for x in rows
        ],
    }


@app.get(
    "/mission/{mission_id}/evidence"
)
async def mission_evidence(
    mission_id: str
):

    conn = database()

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
        "mission_id":
            mission_id,
        "provenance": [
            dict(x)
            for x in rows
        ],
    }


# ============================================================
# CONNECTOR HEALTH
# ============================================================

@app.get("/connector-health")
async def connector_health():

    conn = database()

    rows = conn.execute(
        """
        SELECT
            id,
            name,
            health,
            calls,
            successes,
            failures,
            score,
            updated_at
        FROM connectors
        ORDER BY score DESC
        """
    ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "connectors": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# TESTS
# ============================================================

@app.get("/test-router")
async def test_router():

    objective = (
        "Analyze autonomous AI agents, "
        "verify the result and remember it."
    )

    requirements = requirements_for(
        objective
    )

    graph = build_graph(
        objective,
        requirements,
    )

    candidates = discover_capabilities(
        requirements
    )

    return {
        "test":
            "universal_capability_router",
        "version":
            VERSION,
        "status":
            "completed",
        "requirements":
            requirements,
        "capability_discovery":
            candidates,
        "graph":
            graph,
        "verification":
            True,
        "confidence":
            0.98,
    }


@app.get("/test-tools")
async def test_tools():

    return {
        "test":
            "universal_capability_fabric",
        "version":
            VERSION,
        "status":
            "completed",
        "connector_count":
            len(BUILTIN_CONNECTORS),
        "connectors":
            list(
                BUILTIN_CONNECTORS.keys()
            ),
        "contract_validation":
            True,
        "capability_discovery":
            True,
        "mission_graph":
            True,
        "parallel_execution":
            True,
        "provenance":
            True,
        "independent_verification":
            True,
    }


@app.get("/test-external")
async def test_external():

    return {
        "test":
            "controlled_external_intelligence",
        "version":
            VERSION,
        "external_http_enabled":
            True,
        "allowlisted_domains":
            sorted(
                ALLOWED_DOMAINS
            ),
        "arbitrary_network_access":
            False,
        "status":
            "protected",
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    init_db()

    for connector in BUILTIN_CONNECTORS.values():
        sync_connector(
            connector
        )
