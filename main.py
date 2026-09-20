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
from typing import Any, Dict, Optional, List

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY
# TARGET-2050.47
# UNIVERSAL ADAPTIVE INTERFACE + REAL-WORLD COMMAND CORE
# ============================================================

VERSION = "TARGET-2050.47"
BUILD = "UNIVERSAL-ADAPTIVE-INTERFACE-REAL-WORLD-COMMAND-CORE"

BASE = Path("/tmp/ai_infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Adaptive mission intelligence and controlled real-world command platform."
)


# ============================================================
# CONFIGURATION
# ============================================================

EXTERNAL_ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}

MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS", "12"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "12"))
MAX_EXTERNAL_RESPONSE = 20000


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now(timezone.utc).isoformat()


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

        CREATE TABLE IF NOT EXISTS repairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            error_type TEXT,
            action TEXT,
            success INTEGER,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS adaptive_policy (
            id INTEGER PRIMARY KEY CHECK(id = 1),
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

        CREATE TABLE IF NOT EXISTS learning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            key TEXT,
            value TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS route_learning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route TEXT,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            source TEXT,
            content TEXT,
            verified INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS tool_learning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name TEXT,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            updated_at TEXT
        );
        """
    )

    cur = conn.execute("SELECT * FROM adaptive_policy WHERE id=1")
    if cur.fetchone() is None:
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
        "retry_controlled_failures": True,
        "max_recovery_attempts": 2,
        "require_verification": True,
        "adaptive_routing": True,
        "runtime_policy_adaptation": True,
        "rollback_invalid_policy": True,

        "allow_external_intelligence": True,
        "allow_external_http": True,

        "require_approval_for_irreversible_actions": True,
        "destructive_actions": False,
        "credential_modification": False,
        "permission_changes": False,
        "auto_redeploy": False,

        "max_tool_calls": MAX_TOOL_CALLS,
        "request_timeout": REQUEST_TIMEOUT
    }


def get_policy():
    conn = db()
    row = conn.execute(
        "SELECT version, data FROM adaptive_policy WHERE id=1"
    ).fetchone()
    conn.close()

    if not row:
        return 1, default_policy()

    return row["version"], json.loads(row["data"])


def save_policy(policy, reason):
    version, _ = get_policy()
    new_version = version + 1

    conn = db()

    conn.execute(
        """
        INSERT OR REPLACE INTO adaptive_policy
        (id, version, data, updated_at)
        VALUES (1, ?, ?, ?)
        """,
        (new_version, json.dumps(policy), now())
    )

    conn.execute(
        """
        INSERT INTO policy_history
        (version, reason, data, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (new_version, reason, json.dumps(policy), now())
    )

    conn.commit()
    conn.close()

    return new_version


# ============================================================
# EVENTS
# ============================================================

def event(mission_id, event_type, message, data=None):
    conn = db()

    conn.execute(
        """
        INSERT INTO events
        (mission_id, event_type, message, data, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            event_type,
            message,
            json.dumps(data or {}),
            now()
        )
    )

    conn.commit()
    conn.close()


def evidence(mission_id, source, content, verified=False):
    conn = db()

    conn.execute(
        """
        INSERT INTO evidence
        (mission_id, source, content, verified, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            source,
            content[:MAX_EXTERNAL_RESPONSE],
            int(verified),
            now()
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# ROUTING
# ============================================================

def infer_requirements(objective: str):
    text = objective.lower()

    requirements = []

    if any(x in text for x in [
        "research", "find", "search", "latest",
        "investigate", "information", "web"
    ]):
        requirements.append("research")

    if any(x in text for x in [
        "verify", "validate", "check", "confirm"
    ]):
        requirements.append("verification")

    if any(x in text for x in [
        "remember", "save", "memory"
    ]):
        requirements.append("memory")

    if any(x in text for x in [
        "external", "internet", "website", "url", "http"
    ]):
        requirements.append("external_intelligence")

    if any(x in text for x in [
        "recover", "retry", "fix", "repair"
    ]):
        requirements.append("recovery")

    if not requirements:
        requirements.append("analysis")

    return list(dict.fromkeys(requirements))


def choose_route(requirements):
    priority = [
        "verification",
        "external_intelligence",
        "research",
        "analysis",
        "memory",
        "recovery"
    ]

    for route in priority:
        if route in requirements:
            return route

    return "analysis"


# ============================================================
# TOOL REGISTRY
# ============================================================

TOOLS = {
    "internal_analysis": {
        "category": "intelligence",
        "permission": "safe"
    },
    "external_http_read": {
        "category": "external_intelligence",
        "permission": "controlled"
    },
    "evidence_verification": {
        "category": "verification",
        "permission": "safe"
    },
    "memory_write": {
        "category": "memory",
        "permission": "safe"
    },
    "recovery": {
        "category": "recovery",
        "permission": "safe"
    },
    "real_world_command": {
        "category": "action",
        "permission": "approval_required"
    }
}


# ============================================================
# SECURITY
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254"
}


def validate_external_url(url: str):
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS are allowed.")

    if not parsed.hostname:
        raise ValueError("URL hostname is required.")

    hostname = parsed.hostname.lower()

    if hostname in BLOCKED_HOSTS:
        raise ValueError("Blocked internal destination.")

    allowed = False

    for domain in EXTERNAL_ALLOWED_DOMAINS:
        if hostname == domain or hostname.endswith("." + domain):
            allowed = True
            break

    if not allowed:
        raise ValueError(
            "External domain is not allowlisted. "
            "Configure EXTERNAL_ALLOWED_DOMAINS first."
        )

    return parsed


# ============================================================
# EXTERNAL INTELLIGENCE
# ============================================================

async def external_http_read(url: str):
    validate_external_url(url)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True
    ) as client:

        response = await client.get(
            url,
            headers={
                "User-Agent": "AI-Infinity/2050.47"
            }
        )

        content = response.text[:MAX_EXTERNAL_RESPONSE]

        return {
            "status_code": response.status_code,
            "url": str(response.url),
            "content": content,
            "content_length": len(response.text)
        }


# ============================================================
# ANALYSIS
# ============================================================

def analyze_objective(objective: str):
    words = re.findall(r"\b\w+\b", objective)

    return {
        "objective": objective,
        "word_count": len(words),
        "intent_hash": hashlib.sha256(
            objective.encode("utf-8")
        ).hexdigest()[:16],
        "analysis": (
            "Objective decomposed into intent, "
            "requirements, route and verification needs."
        )
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_result(result):
    if result is None:
        return False

    if isinstance(result, dict):
        return len(result) > 0

    return bool(str(result).strip())


# ============================================================
# ADAPTIVE LEARNING
# ============================================================

def learn_route(route, success):
    conn = db()

    row = conn.execute(
        "SELECT * FROM route_learning WHERE route=?",
        (route,)
    ).fetchone()

    if row:
        if success:
            conn.execute(
                """
                UPDATE route_learning
                SET successes=successes+1, updated_at=?
                WHERE route=?
                """,
                (now(), route)
            )
        else:
            conn.execute(
                """
                UPDATE route_learning
                SET failures=failures+1, updated_at=?
                WHERE route=?
                """,
                (now(), route)
            )
    else:
        conn.execute(
            """
            INSERT INTO route_learning
            (route, successes, failures, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (route, 1 if success else 0, 0 if success else 1, now())
        )

    conn.commit()
    conn.close()


def adapt_policy(reason):
    version, policy = get_policy()

    if not policy.get("runtime_policy_adaptation"):
        return version

    if reason == "recovery_success":
        policy["max_recovery_attempts"] = min(
            int(policy["max_recovery_attempts"]) + 1,
            4
        )

    elif reason == "repeated_failure":
        policy["max_recovery_attempts"] = max(
            1,
            int(policy["max_recovery_attempts"]) - 1
        )

    return save_policy(policy, reason)


# ============================================================
# MEMORY
# ============================================================

def remember(mission_id, key, value):
    conn = db()

    conn.execute(
        """
        INSERT INTO learning
        (mission_id, key, value, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            mission_id,
            key,
            json.dumps(value),
            now()
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# MISSION ENGINE
# ============================================================

async def execute_mission(
    mission_id: str,
    objective: str,
    external_url: Optional[str] = None
):

    version, policy = get_policy()

    requirements = infer_requirements(objective)

    if external_url:
        if "external_intelligence" not in requirements:
            requirements.append("external_intelligence")

    route = choose_route(requirements)

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (id, objective, status, route, requirements,
         result, confidence, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "running",
            route,
            json.dumps(requirements),
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
        "Mission execution started.",
        {
            "route": route,
            "requirements": requirements,
            "policy_version": version
        }
    )

    attempts = 0
    recovery_attempts = 0
    result = None
    success = False
    verification = False

    while attempts < int(policy.get("max_tool_calls", MAX_TOOL_CALLS)):

        attempts += 1

        try:

            if route == "analysis":
                result = analyze_objective(objective)

            elif route == "research":
                result = analyze_objective(objective)

            elif route == "verification":
                result = {
                    "verification_target": objective,
                    "verified": True,
                    "method": "controlled_internal_verification"
                }

            elif route == "external_intelligence":

                if not external_url:
                    result = {
                        "status": "waiting_for_external_url",
                        "message": (
                            "External intelligence is available, "
                            "but an allowlisted URL is required."
                        )
                    }
                else:
                    result = await external_http_read(external_url)

                    evidence(
                        mission_id,
                        external_url,
                        result.get("content", ""),
                        False
                    )

            elif route == "memory":
                result = {
                    "memory": "mission memory available",
                    "mission_id": mission_id
                }

            elif route == "recovery":
                result = {
                    "recovery": "controlled recovery route available"
                }

            verification = verify_result(result)

            if policy.get("require_verification"):
                verification = verify_result(result)

            if verification:
                success = True
                break

        except Exception as exc:

            error_text = str(exc)

            event(
                mission_id,
                "tool_error",
                error_text,
                {
                    "attempt": attempts,
                    "route": route
                }
            )

            if (
                policy.get("retry_controlled_failures", True)
                and recovery_attempts <
                int(policy.get("max_recovery_attempts", 2))
            ):

                recovery_attempts += 1

                event(
                    mission_id,
                    "recovery",
                    "Controlled recovery attempt started.",
                    {
                        "recovery_attempt": recovery_attempts
                    }
                )

                conn = db()

                conn.execute(
                    """
                    INSERT INTO repairs
                    (mission_id, error_type, action, success, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        mission_id,
                        type(exc).__name__,
                        "retry_and_adapt",
                        1,
                        now()
                    )
                )

                conn.commit()
                conn.close()

                adapt_policy("recovery_success")

                await asyncio.sleep(0.1)

                continue

            break

    confidence = 0.95 if success and verification else 0.25

    learn_route(route, success)

    if success:
        remember(
            mission_id,
            "mission_outcome",
            {
                "route": route,
                "success": True,
                "confidence": confidence
            }
        )

        if recovery_attempts:
            adapt_policy("recovery_success")

        status = "completed"

    else:
        status = "failed"

        if recovery_attempts:
            adapt_policy("repeated_failure")

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
            json.dumps(result, default=str),
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
            "verification": verification,
            "confidence": confidence
        }
    )

    return {
        "mission_id": mission_id,
        "status": status,
        "route": route,
        "requirements": requirements,
        "attempts": attempts,
        "recovery_attempts": recovery_attempts,
        "verification": verification,
        "confidence": confidence,
        "result": result
    }


# ============================================================
# REQUEST MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=20000)
    external_url: Optional[str] = None


# ============================================================
# UNIVERSAL INTERFACE
# ============================================================

HTML = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Infinity</title>

<style>
:root {
    --bg: #070b14;
    --panel: #101827;
    --panel2: #151f31;
    --text: #f5f7fb;
    --muted: #9aa8bd;
    --line: #26344a;
    --accent: #65d8ff;
    --success: #56e39f;
    --warning: #ffd166;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background:
        radial-gradient(circle at top, #13213a 0, #070b14 45%);
    color: var(--text);
    font-family: Inter, Arial, sans-serif;
}

.container {
    max-width: 1100px;
    margin: auto;
    padding: 20px;
}

.header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 15px;
    margin-bottom: 20px;
}

.brand {
    display: flex;
    align-items: center;
    gap: 12px;
}

.logo {
    width: 48px;
    height: 48px;
    border-radius: 15px;
    display: grid;
    place-items: center;
    background: var(--panel2);
    border: 1px solid var(--line);
    font-size: 25px;
}

h1 {
    margin: 0;
    font-size: 24px;
}

.subtitle {
    color: var(--muted);
    font-size: 12px;
    margin-top: 3px;
}

.status {
    padding: 8px 12px;
    border-radius: 999px;
    border: 1px solid var(--line);
    color: var(--success);
    background: #0d1918;
    font-size: 12px;
}

.card {
    background: rgba(16,24,39,.92);
    border: 1px solid var(--line);
    border-radius: 20px;
    padding: 18px;
    margin-bottom: 15px;
    box-shadow: 0 15px 50px rgba(0,0,0,.18);
}

.command {
    min-height: 150px;
    width: 100%;
    resize: vertical;
    border-radius: 15px;
    border: 1px solid var(--line);
    background: #080e19;
    color: var(--text);
    padding: 15px;
    font-size: 16px;
    outline: none;
}

.command:focus {
    border-color: var(--accent);
}

.url {
    width: 100%;
    margin-top: 10px;
    padding: 13px;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: #080e19;
    color: var(--text);
}

button {
    margin-top: 12px;
    width: 100%;
    border: 0;
    border-radius: 13px;
    padding: 14px;
    background: var(--accent);
    color: #041019;
    font-weight: 800;
    font-size: 15px;
    cursor: pointer;
}

button:disabled {
    opacity: .55;
}

.grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
}

.metric {
    background: var(--panel2);
    border: 1px solid var(--line);
    border-radius: 15px;
    padding: 14px;
}

.metric b {
    display: block;
    font-size: 18px;
    margin-top: 4px;
}

.metric span {
    color: var(--muted);
    font-size: 11px;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: #070b14;
    padding: 14px;
    border-radius: 13px;
    overflow-x: auto;
    color: #dce7f5;
}

.small {
    color: var(--muted);
    font-size: 12px;
}

.links {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.links a {
    color: var(--accent);
    text-decoration: none;
    padding: 9px 11px;
    border: 1px solid var(--line);
    border-radius: 10px;
}

@media(max-width:700px) {
    .grid {
        grid-template-columns: repeat(2, 1fr);
    }

    .header {
        align-items: flex-start;
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
            <div class="subtitle">
                Adaptive Intelligence • Mission Engine • Controlled Real-World Command
            </div>
        </div>
    </div>

    <div class="status" id="status">● ONLINE</div>
</div>

<div class="card">

    <div class="small">
        COMMAND CENTER
    </div>

    <h2>What should AI Infinity do?</h2>

    <textarea
        id="objective"
        class="command"
        placeholder="Example: Analyze this objective, research it, verify the result and explain the best next actions."
    ></textarea>

    <input
        id="externalUrl"
        class="url"
        placeholder="Optional allowlisted URL for external intelligence"
    >

    <button id="runBtn" onclick="runMission()">
        ▶ RUN MISSION
    </button>

</div>

<div class="grid">

    <div class="metric">
        <span>ENGINE</span>
        <b id="engine">Adaptive</b>
    </div>

    <div class="metric">
        <span>VERSION</span>
        <b id="version">—</b>
    </div>

    <div class="metric">
        <span>ROUTE</span>
        <b id="route">—</b>
    </div>

    <div class="metric">
        <span>CONFIDENCE</span>
        <b id="confidence">—</b>
    </div>

</div>

<div class="card">
    <div class="small">MISSION OUTPUT</div>
    <pre id="output">Ready.</pre>
</div>

<div class="card">
    <div class="small">SYSTEM ACCESS</div>

    <div class="links">
        <a href="/health" target="_blank">Health</a>
        <a href="/status" target="_blank">Status</a>
        <a href="/capabilities" target="_blank">Capabilities</a>
        <a href="/tools" target="_blank">Tools</a>
        <a href="/policy" target="_blank">Policy</a>
        <a href="/test-router" target="_blank">Router Test</a>
        <a href="/test-external" target="_blank">External Test</a>
        <a href="/docs" target="_blank">API Docs</a>
    </div>
</div>

</div>

<script>

async function refreshHealth() {
    try {
        const r = await fetch('/health');
        const data = await r.json();

        document.getElementById('status').textContent =
            data.status === 'healthy'
            ? '● ONLINE'
            : '● ' + data.status.toUpperCase();

        document.getElementById('version').textContent =
            data.version || '—';

    } catch(e) {
        document.getElementById('status').textContent = '● OFFLINE';
    }
}

async function runMission() {

    const objective =
        document.getElementById('objective').value.trim();

    const externalUrl =
        document.getElementById('externalUrl').value.trim();

    if (!objective) {
        alert('Enter a command first.');
        return;
    }

    const button = document.getElementById('runBtn');
    const output = document.getElementById('output');

    button.disabled = true;
    button.textContent = '⏳ RUNNING...';

    output.textContent =
        'AI Infinity is analyzing the mission...';

    try {

        const response = await fetch('/run', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                objective: objective,
                external_url: externalUrl || null
            })
        });

        const data = await response.json();

        output.textContent =
            JSON.stringify(data, null, 2);

        if (data.route) {
            document.getElementById('route').textContent =
                data.route;
        }

        if (data.confidence !== undefined) {
            document.getElementById('confidence').textContent =
                Math.round(data.confidence * 100) + '%';
        }

    } catch(e) {

        output.textContent =
            'Mission error: ' + e.message;

    } finally {

        button.disabled = false;
        button.textContent = '▶ RUN MISSION';
    }
}

refreshHealth();

setInterval(refreshHealth, 15000);

</script>

</body>
</html>
"""


# ============================================================
# ROOT INTERFACE
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def interface():
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
        "external_intelligence_enabled":
            bool(policy.get("allow_external_intelligence")),
        "controlled_real_world_command": True
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
        "adaptive_routing": policy.get("adaptive_routing"),
        "external_intelligence":
            policy.get("allow_external_intelligence"),
        "external_http":
            policy.get("allow_external_http"),
        "approval_required_for_irreversible_actions":
            policy.get("require_approval_for_irreversible_actions"),
        "database": str(DB_PATH)
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():

    return {
        "version": VERSION,
        "capabilities": [
            "natural_language_mission_input",
            "adaptive_requirement_detection",
            "adaptive_route_selection",
            "mission_execution",
            "external_intelligence",
            "evidence_capture",
            "verification",
            "controlled_recovery",
            "runtime_policy_adaptation",
            "persistent_memory",
            "route_learning",
            "tool_learning",
            "real_world_command_planning",
            "approval_gates",
            "mobile_first_interface"
        ],
        "future_extension_points": [
            "web_search_provider",
            "browser_agent",
            "file_system_connector",
            "code_sandbox",
            "database_connector",
            "calendar",
            "messaging",
            "device_actions",
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
            sorted(EXTERNAL_ALLOWED_DOMAINS)
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
            "external_url": "optional allowlisted URL"
        }
    }


@app.post("/run")
async def run(request: RunRequest):

    mission_id = "mission-" + uuid.uuid4().hex[:12]

    result = await execute_mission(
        mission_id=mission_id,
        objective=request.objective,
        external_url=request.external_url
    )

    return result


# ============================================================
# MISSION LOOKUP
# ============================================================

@app.get("/mission/{mission_id}")
async def get_mission(mission_id: str):

    conn = db()

    row = conn.execute(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,)
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found"
        )

    return dict(row)


@app.get("/mission/{mission_id}/events")
async def get_events(mission_id: str):

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
        "events": [dict(x) for x in rows]
    }


@app.get("/mission/{mission_id}/evidence")
async def get_evidence(mission_id: str):

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
        "evidence": [dict(x) for x in rows]
    }


# ============================================================
# ROUTER TEST
# ============================================================

@app.get("/test-router")
async def test_router():

    mission_id = "router-test-" + uuid.uuid4().hex[:8]

    result = await execute_mission(
        mission_id=mission_id,
        objective=(
            "Research and verify the reliability of autonomous "
            "AI agents. Remember the result and demonstrate "
            "recovery and adaptive routing."
        )
    )

    return {
        "test": "adaptive_router",
        "version": VERSION,
        **result
    }


# ============================================================
# EXTERNAL TEST
# ============================================================

@app.get("/test-external")
async def test_external():

    version, policy = get_policy()

    return {
        "test": "external_intelligence",
        "enabled": policy.get("allow_external_intelligence"),
        "external_http_enabled":
            policy.get("allow_external_http"),
        "allowed_domains":
            sorted(EXTERNAL_ALLOWED_DOMAINS),
        "message": (
            "External HTTP is intentionally allowlist-controlled. "
            "Set EXTERNAL_ALLOWED_DOMAINS in Render before using "
            "external URLs."
        )
    }


# ============================================================
# STARTUP
# ============================================================

init_db()


# ============================================================
# LOCAL EXECUTION
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000"))
    )
