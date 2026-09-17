import os
import json
import sqlite3
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

APP_NAME = "AI Infinity"
VERSION = "3.0.0"

DB_PATH = Path(os.getenv("AI_INFINITY_DB", "/tmp/ai_infinity.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="Free-first Autonomous Intelligence Fabric"
)


# =========================
# DATABASE
# =========================

def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            plan TEXT NOT NULL,
            result TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            source TEXT,
            confidence REAL DEFAULT 0.5,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS resources (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            endpoint TEXT,
            capabilities TEXT NOT NULL,
            cost TEXT DEFAULT 'free',
            enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS genomes (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            genome TEXT NOT NULL,
            score REAL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dreams (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            ideas TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS simulations (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            assumptions TEXT NOT NULL,
            scenarios TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS hypotheses (
            id TEXT PRIMARY KEY,
            statement TEXT NOT NULL,
            status TEXT NOT NULL,
            evidence TEXT,
            created_at TEXT NOT NULL
        );
        """)


init_db()


def event(event_type: str, payload: Dict[str, Any]):
    with db() as c:
        c.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                event_type,
                json.dumps(payload),
                now()
            )
        )


# =========================
# REQUEST MODELS
# =========================

class IntentRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=10000)


class ResourceRequest(BaseModel):
    name: str
    kind: str = "model"
    endpoint: Optional[str] = None
    capabilities: List[str] = []
    cost: str = "free"


class VerifyRequest(BaseModel):
    claim: str
    evidence: List[str] = []


class MemoryRequest(BaseModel):
    content: str
    source: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0, le=1)


class GenomeRequest(BaseModel):
    objective: str
    strategy: Dict[str, Any]
    score: Optional[float] = None


class SimulationRequest(BaseModel):
    objective: str
    assumptions: List[str] = []


class DreamRequest(BaseModel):
    objective: str


class HypothesisRequest(BaseModel):
    statement: str


# =========================
# INTELLIGENCE CORE
# =========================

SPECIALISTS = [
    "researcher",
    "strategist",
    "builder",
    "critic",
    "verifier"
]


def understand_intent(objective: str):

    text = objective.strip()
    low = text.lower()

    domains = []

    mapping = {
        "software": [
            "code",
            "app",
            "software",
            "api",
            "github",
            "website"
        ],
        "research": [
            "research",
            "study",
            "paper",
            "investigate",
            "find"
        ],
        "business": [
            "business",
            "profit",
            "customer",
            "market",
            "startup"
        ],
        "content": [
            "video",
            "content",
            "youtube",
            "article",
            "write"
        ],
        "science": [
            "science",
            "experiment",
            "hypothesis",
            "laboratory"
        ]
    }

    for domain, words in mapping.items():
        if any(word in low for word in words):
            domains.append(domain)

    return {
        "objective": text,
        "domains": domains or ["general"],
        "constraints": [
            "free-first",
            "no automatic spending",
            "auditable"
        ],
        "specialists": SPECIALISTS,
        "timestamp": now()
    }


def create_plan(objective: str):

    intent = understand_intent(objective)
    domain = intent["domains"][0]

    return [
        {
            "step": 1,
            "action": "understand",
            "owner": "researcher",
            "description":
                f"Understand the {domain} objective and requirements."
        },
        {
            "step": 2,
            "action": "resource_discovery",
            "owner": "strategist",
            "description":
                "Identify available free/local resources."
        },
        {
            "step": 3,
            "action": "strategy",
            "owner": "strategist",
            "description":
                "Generate multiple executable strategies."
        },
        {
            "step": 4,
            "action": "simulation",
            "owner": "critic",
            "description":
                "Test assumptions and alternative scenarios."
        },
        {
            "step": 5,
            "action": "execution",
            "owner": "builder",
            "description":
                "Execute authorized and reversible actions."
        },
        {
            "step": 6,
            "action": "verification",
            "owner": "verifier",
            "description":
                "Check the result and record evidence."
        },
        {
            "step": 7,
            "action": "learning",
            "owner": "researcher",
            "description":
                "Store reusable lessons and Intelligence Genome."
        }
    ]


def choose_resources(capability=None):

    with db() as c:
        rows = c.execute(
            """
            SELECT * FROM resources
            WHERE enabled=1 AND cost='free'
            """
        ).fetchall()

    results = []

    for r in rows:

        caps = json.loads(r["capabilities"])

        if (
            not capability
            or capability.lower()
            in [x.lower() for x in caps]
        ):
            results.append(dict(r))

    return results


# =========================
# HOME
# =========================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "service": APP_NAME,
        "version": VERSION,
        "time": now()
    }


@app.get("/", response_class=HTMLResponse)
def home():

    return """
<!DOCTYPE html>
<html>
<head>

<meta name="viewport"
      content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>

body {
    font-family: system-ui;
    margin: 0;
    background: #0b1020;
    color: #eef2ff;
}

main {
    max-width: 850px;
    margin: auto;
    padding: 28px;
}

.card {
    background: #141b31;
    border: 1px solid #2a3558;
    border-radius: 18px;
    padding: 20px;
}

textarea {
    width: 100%;
    min-height: 130px;
    box-sizing: border-box;
    border-radius: 12px;
    padding: 14px;
    background: #0e1426;
    color: white;
    border: 1px solid #354264;
}

button {
    margin: 8px 8px 0 0;
    padding: 12px 16px;
    border: 0;
    border-radius: 10px;
    cursor: pointer;
}

pre {
    white-space: pre-wrap;
    background: #080c18;
    padding: 14px;
    border-radius: 12px;
}

</style>

</head>

<body>

<main>

<h1>∞ AI Infinity</h1>

<p>
Free-first Intelligence Fabric
<br>
Orchestrate → Simulate → Verify → Learn
</p>

<div class="card">

<textarea
id="objective"
placeholder="Tell AI Infinity what you want to accomplish..."
></textarea>

<button onclick="run('/v1/orchestrate')">
Create Intelligence Plan
</button>

<button onclick="run('/v1/dream')">
Dream / Discover Strategies
</button>

<button onclick="run('/v1/simulate')">
Counterfactual Simulation
</button>

<pre id="out">Ready.</pre>

</div>

<script>

async function run(path) {

    const objective =
        document.getElementById("objective")
        .value.trim();

    if (!objective) {

        document.getElementById("out")
        .textContent =
        "Enter an objective first.";

        return;
    }

    const r = await fetch(
        path,
        {
            method: "POST",
            headers: {
                "Content-Type":
                "application/json"
            },
            body: JSON.stringify({
                objective: objective
            })
        }
    );

    document.getElementById("out")
        .textContent =
        await r.text();
}

</script>

</main>

</body>
</html>
"""


# =========================
# INTENT
# =========================

@app.post("/v1/intent")
def intent(req: IntentRequest):

    result = understand_intent(
        req.objective
    )

    event(
        "intent.created",
        result
    )

    return result


# =========================
# ORCHESTRATOR
# =========================

@app.post("/v1/orchestrate")
def orchestrate(req: IntentRequest):

    task_id = (
        "task-" +
        uuid.uuid4().hex[:12]
    )

    plan = create_plan(
        req.objective
    )

    with db() as c:

        timestamp = now()

        c.execute(
            """
            INSERT INTO tasks
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                req.objective,
                "planned",
                json.dumps(plan),
                None,
                timestamp,
                timestamp
            )
        )

    result = {

        "task_id": task_id,

        "status": "planned",

        "objective":
            req.objective,

        "intent":
            understand_intent(
                req.objective
            ),

        "plan":
            plan,

        "safety": {

            "automatic_spending": False,

            "automatic_self_deployment":
                False,

            "simulation_is_evidence":
                False,

            "consequential_actions_require_authorization":
                True
        }
    }

    event(
        "task.created",
        result
    )

    return result


@app.get("/v1/tasks/{task_id}")
def task(task_id: str):

    with db() as c:

        row = c.execute(
            "SELECT * FROM tasks WHERE id=?",
            (task_id,)
        ).fetchone()

    if not row:

        raise HTTPException(
            404,
            "Task not found"
        )

    return dict(row)


# =========================
# RESOURCES
# =========================

@app.post("/v1/resources")
def register_resource(
    req: ResourceRequest
):

    rid = (
        "res-" +
        uuid.uuid4().hex[:12]
    )

    with db() as c:

        c.execute(
            """
            INSERT INTO resources
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid,
                req.name,
                req.kind,
                req.endpoint,
                json.dumps(
                    req.capabilities
                ),
                req.cost,
                1,
                now()
            )
        )

    event(
        "resource.registered",
        {
            "id": rid,
            "name": req.name
        }
    )

    return {
        "id": rid,
        **req.model_dump()
    }


@app.get("/v1/resources")
def resources():

    with db() as c:

        rows = c.execute(
            """
            SELECT * FROM resources
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [
        dict(r)
        for r in rows
    ]


@app.get("/v1/resources/select")
def resource_select(
    capability: Optional[str] = None
):

    return {
        "capability": capability,
        "resources":
            choose_resources(
                capability
            )
    }


# =========================
# DREAM ENGINE
# =========================

@app.post("/v1/dream")
def dream(req: DreamRequest):

    ideas = [

        "Reverse the problem and ask what would make the objective unnecessary.",

        "Create three competing strategies and benchmark them on the same test.",

        "Remove the most expensive dependency and search for a local/open alternative.",

        "Change the representation of the problem and test whether a simpler solution appears.",

        "Use an independent critic to identify hidden assumptions and failure modes."
    ]

    did = (
        "dream-" +
        uuid.uuid4().hex[:12]
    )

    with db() as c:

        c.execute(
            """
            INSERT INTO dreams
            VALUES (?, ?, ?, ?)
            """,
            (
                did,
                req.objective,
                json.dumps(ideas),
                now()
            )
        )

    event(
        "dream.created",
        {
            "id": did,
            "objective":
                req.objective
        }
    )

    return {

        "id": did,

        "objective":
            req.objective,

        "ideas":
            ideas,

        "note":
            "Dream outputs are hypotheses for testing, not verified facts."
    }


@app.get("/v1/dreams")
def dreams():

    with db() as c:

        rows = c.execute(
            """
            SELECT * FROM dreams
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [
        dict(r)
        for r in rows
    ]


# =========================
# SIMULATION
# =========================

@app.post("/v1/simulate")
def simulate(
    req: SimulationRequest
):

    assumptions = (
        req.assumptions
        or
        [
            "free resources remain available",
            "required inputs are accessible",
            "actions are reversible"
        ]
    )

    scenarios = [

        {
            "name": "baseline",
            "assumption_change": "none",
            "expected":
                "Execute the current plan and measure results."
        },

        {
            "name":
                "resource_constrained",

            "assumption_change":
                "preferred resource unavailable",

            "expected":
                "Fall back to another free/local resource."
        },

        {
            "name": "failure",

            "assumption_change":
                "critical step fails",

            "expected":
                "Diagnose, retry alternatively, or rollback."
        }
    ]

    sid = (
        "sim-" +
        uuid.uuid4().hex[:12]
    )

    with db() as c:

        c.execute(
            """
            INSERT INTO simulations
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                sid,
                req.objective,
                json.dumps(
                    assumptions
                ),
                json.dumps(
                    scenarios
                ),
                now()
            )
        )

    event(
        "simulation.created",
        {
            "id": sid,
            "objective":
                req.objective
        }
    )

    return {

        "id": sid,

        "objective":
            req.objective,

        "assumptions":
            assumptions,

        "scenarios":
            scenarios,

        "warning":
            "Simulation is not evidence of what will happen in reality."
    }


@app.get("/v1/simulations")
def simulations():

    with db() as c:

        rows = c.execute(
            """
            SELECT * FROM simulations
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [
        dict(r)
        for r in rows
    ]


# =========================
# MEMORY
# =========================

@app.post("/v1/memory")
def save_memory(
    req: MemoryRequest
):

    mid = (
        "mem-" +
        uuid.uuid4().hex[:12]
    )

    with db() as c:

        c.execute(
            """
            INSERT INTO memories
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mid,
                req.content,
                req.source,
                req.confidence,
                now()
            )
        )

    event(
        "memory.saved",
        {"id": mid}
    )

    return {
        "id": mid,
        **req.model_dump()
    }


@app.get("/v1/memory")
def memories(limit: int = 50):

    limit = max(
        1,
        min(limit, 500)
    )

    with db() as c:

        rows = c.execute(
            """
            SELECT * FROM memories
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()

    return [
        dict(r)
        for r in rows
    ]


# =========================
# VERIFICATION
# =========================

@app.post("/v1/verify")
def verify(
    req: VerifyRequest
):

    evidence = [
        x.strip()
        for x in req.evidence
        if x.strip()
    ]

    if not evidence:

        status = "unverified"

        confidence = 0.0

        reason = (
            "No evidence supplied."
        )

    else:

        status = (
            "supported_by_supplied_evidence"
        )

        confidence = min(
            0.95,
            0.5 + 0.1 * len(evidence)
        )

        reason = (
            "Evidence was supplied, "
            "but this endpoint does not "
            "independently prove the claim."
        )

    result = {

        "claim":
            req.claim,

        "status":
            status,

        "confidence":
            confidence,

        "evidence":
            evidence,

        "reason":
            reason
    }

    event(
        "verification.completed",
        result
    )

    return result


# =========================
# INTELLIGENCE GENOME
# =========================

@app.post("/v1/genomes")
def genome(
    req: GenomeRequest
):

    gid = (
        "genome-" +
        uuid.uuid4().hex[:12]
    )

    payload = {

        "objective":
            req.objective,

        "strategy":
            req.strategy,

        "score":
            req.score,

        "created_at":
            now(),

        "version":
            1
    }

    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True
        ).encode()
    ).hexdigest()

    payload["fingerprint"] = digest

    with db() as c:

        c.execute(
            """
            INSERT INTO genomes
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                gid,
                req.objective,
                json.dumps(payload),
                req.score,
                now()
            )
        )

    return {
        "id": gid,
        "genome": payload
    }


@app.get("/v1/genomes")
def genomes():

    with db() as c:

        rows = c.execute(
            """
            SELECT * FROM genomes
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [
        dict(r)
        for r in rows
    ]


# =========================
# HYPOTHESES
# =========================

@app.post("/v1/hypotheses")
def hypothesis(
    req: HypothesisRequest
):

    hid = (
        "hyp-" +
        uuid.uuid4().hex[:12]
    )

    with db() as c:

        c.execute(
            """
            INSERT INTO hypotheses
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                hid,
                req.statement,
                "proposed",
                None,
                now()
            )
        )

    return {

        "id": hid,

        "statement":
            req.statement,

        "status":
            "proposed"
    }


@app.get("/v1/hypotheses")
def hypotheses():

    with db() as c:

        rows = c.execute(
            """
            SELECT * FROM hypotheses
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [
        dict(r)
        for r in rows
    ]


# =========================
# SECURITY
# =========================

@app.get("/v1/security/policy")
def security_policy():

    return {

        "mode":
            "free-first",

        "automatic_spending":
            False,

        "automatic_paid_services":
            False,

        "credential_exfiltration":
            False,

        "uncontrolled_self_modification":
            False,

        "automatic_self_deployment":
            False,

        "consequential_actions":
            "authorization_required",

        "simulation_is_evidence":
            False,

        "audit_log":
            True
    }


# =========================
# SYSTEM STATUS
# =========================

@app.get("/v1/status")
def status():

    with db() as c:

        counts = {}

        tables = [
            "tasks",
            "memories",
            "resources",
            "genomes",
            "events",
            "dreams",
            "simulations",
            "hypotheses"
        ]

        for table in tables:

            counts[table] = c.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

    return {

        "name":
            APP_NAME,

        "version":
            VERSION,

        "status":
            "online",

        "storage":
            str(DB_PATH),

        "counts":
            counts,

        "capabilities": [

            "intent modeling",

            "task planning",

            "free-resource registry",

            "temporary specialist configurations",

            "dream engine",

            "counterfactual simulation",

            "memory",

            "evidence recording",

            "intelligence genomes",

            "hypothesis tracking",

            "audit events",

            "free-first safety governor"
        ],

        "not_yet_implemented": [

            "full external web research",

            "arbitrary tool execution",

            "true OS/container sandbox",

            "cryptographic mesh identity",

            "distributed synchronization",

            "physical-world actuation",

            "automatic model routing"
        ]
    }


# =========================
# AUDIT EVENTS
# =========================

@app.get("/v1/events")
def events(limit: int = 100):

    limit = max(
        1,
        min(limit, 500)
    )

    with db() as c:

        rows = c.execute(
            """
            SELECT * FROM events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()

    return [
        dict(r)
        for r in rows
    ]
