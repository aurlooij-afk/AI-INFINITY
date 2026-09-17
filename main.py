import os
import re
import json
import uuid
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

APP = "AI Infinity"
VERSION = "3.0.0"
DB = os.getenv("AI_INFINITY_DB", "/tmp/ai_infinity.db")
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

app = FastAPI(
    title=APP,
    version=VERSION,
    description="Free-first Intelligence Fabric"
)


# =========================================================
# DATABASE
# =========================================================

def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    c = connect()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        objective TEXT,
        status TEXT,
        created_at TEXT,
        result TEXT
    );

    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        kind TEXT,
        content TEXT,
        source TEXT,
        confidence REAL,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS evidence (
        id TEXT PRIMARY KEY,
        task_id TEXT,
        title TEXT,
        url TEXT,
        snippet TEXT,
        source TEXT,
        retrieved_at TEXT
    );

    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        kind TEXT,
        payload TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS genomes (
        id TEXT PRIMARY KEY,
        objective TEXT,
        genome TEXT,
        created_at TEXT
    );
    """)

    c.commit()
    c.close()


init_db()


def log_event(kind, payload):
    c = connect()
    c.execute(
        "INSERT INTO events VALUES (?,?,?,?)",
        (
            str(uuid.uuid4()),
            kind,
            json.dumps(payload, ensure_ascii=False),
            now()
        )
    )
    c.commit()
    c.close()


# =========================================================
# REQUEST MODELS
# =========================================================

class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    research: bool = True
    model: Optional[str] = None


class ResearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    max_results: int = Field(default=6, ge=1, le=10)


class IntentRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=20000)


class VerifyRequest(BaseModel):
    claim: str
    evidence: List[Dict[str, Any]] = []


class MemoryRequest(BaseModel):
    content: str
    kind: str = "knowledge"
    source: str = "unknown"
    confidence: float = Field(default=0.5, ge=0, le=1)


# =========================================================
# UTILITIES
# =========================================================

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def domains(objective):
    text = objective.lower()

    groups = {
        "research": [
            "research", "investigate", "study",
            "find", "analyze"
        ],
        "software": [
            "software", "code", "app",
            "api", "website", "github"
        ],
        "business": [
            "business", "market",
            "customer", "profit", "revenue"
        ],
        "science": [
            "science", "experiment",
            "hypothesis", "scientific"
        ],
        "content": [
            "video", "content",
            "image", "article"
        ]
    }

    found = []

    for name, words in groups.items():
        if any(word in text for word in words):
            found.append(name)

    return found or ["general"]


# =========================================================
# WEB RESEARCH
# =========================================================

def web_research(query, maximum=6):
    results = []

    try:
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "User-Agent":
                "Mozilla/5.0 AI-Infinity/3.0"
            },
            timeout=TIMEOUT
        )

        if r.ok:

            blocks = re.findall(
                r'<div class="result[^>]*>(.*?)</div>\s*</div>',
                r.text,
                re.S
            )

            for block in blocks:

                a = re.search(
                    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                    block,
                    re.S
                )

                s = re.search(
                    r'class="result__snippet"[^>]*>(.*?)</a?>',
                    block,
                    re.S
                )

                if not a:
                    continue

                url = a.group(1)

                title = clean(
                    re.sub("<.*?>", "", a.group(2))
                )

                snippet = clean(
                    re.sub("<.*?>", "", s.group(1))
                ) if s else ""

                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "source": "DuckDuckGo"
                })

                if len(results) >= maximum:
                    break

    except Exception as e:
        log_event(
            "research_error",
            {"error": str(e)}
        )

    # Wikipedia fallback
    if len(results) < maximum:

        try:
            r = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "format": "json",
                    "srlimit": maximum
                },
                headers={
                    "User-Agent": "AI-Infinity/3.0"
                },
                timeout=TIMEOUT
            )

            if r.ok:

                items = (
                    r.json()
                    .get("query", {})
                    .get("search", [])
                )

                for item in items:

                    title = item.get("title", "")

                    results.append({
                        "title": title,
                        "url":
                            "https://en.wikipedia.org/wiki/"
                            + title.replace(" ", "_"),
                        "snippet": clean(
                            re.sub(
                                "<.*?>",
                                "",
                                item.get("snippet", "")
                            )
                        ),
                        "source": "Wikipedia"
                    })

                    if len(results) >= maximum:
                        break

        except Exception as e:
            log_event(
                "research_error",
                {"provider": "wikipedia", "error": str(e)}
            )

    # Remove duplicates
    output = []
    seen = set()

    for result in results:

        if result["url"] not in seen:

            seen.add(result["url"])
            output.append(result)

    log_event(
        "research_completed",
        {
            "query": query,
            "results": len(output)
        }
    )

    return output[:maximum]


# =========================================================
# AI MODEL ROUTER
# =========================================================

def ollama(prompt, model=None):

    url = os.getenv(
        "OLLAMA_URL",
        "http://127.0.0.1:11434"
    )

    model = model or os.getenv(
        "OLLAMA_MODEL",
        "llama3.2:3b"
    )

    try:

        r = requests.post(
            url.rstrip("/") + "/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False
            },
            timeout=60
        )

        if r.ok:

            return {
                "text": r.json().get("response", ""),
                "provider": "ollama",
                "model": model
            }

    except Exception as e:

        log_event(
            "model_error",
            {
                "provider": "ollama",
                "error": str(e)
            }
        )

    return None


def huggingface(prompt, model=None):

    token = os.getenv("HF_TOKEN")

    if not token:
        return None

    model = model or os.getenv(
        "HF_MODEL",
        "Qwen/Qwen2.5-0.5B-Instruct"
    )

    try:

        r = requests.post(
            "https://api-inference.huggingface.co/models/"
            + model,
            headers={
                "Authorization": "Bearer " + token
            },
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 700,
                    "return_full_text": False
                }
            },
            timeout=60
        )

        if r.ok:

            data = r.json()

            if isinstance(data, list) and data:

                return {
                    "text":
                        data[0].get(
                            "generated_text",
                            ""
                        ),
                    "provider": "huggingface",
                    "model": model
                }

    except Exception as e:

        log_event(
            "model_error",
            {
                "provider": "huggingface",
                "error": str(e)
            }
        )

    return None


def ai_generate(prompt, model=None):

    # Local-first
    result = ollama(prompt, model)

    if result:
        return result

    # Optional hosted provider
    result = huggingface(prompt, model)

    if result:
        return result

    return {
        "text": None,
        "provider": "none",
        "model": None,
        "message":
            "No AI model provider configured. "
            "Research and orchestration remain available."
    }


# =========================================================
# INTELLIGENCE PLANNER
# =========================================================

def create_plan(objective):

    return {
        "objective": objective,

        "domains": domains(objective),

        "temporary_minds": [
            "researcher",
            "strategist",
            "builder",
            "critic",
            "verifier"
        ],

        "execution_graph": [
            "understand_intent",
            "research",
            "generate_strategies",
            "simulate",
            "execute_reversible_work",
            "verify",
            "learn",
            "store_intelligence_genome"
        ],

        "governor": {
            "free_first": True,
            "local_first": True,
            "automatic_spending": False,
            "automatic_self_deployment": False,
            "consequential_actions_require_authorization": True
        }
    }


# =========================================================
# ORCHESTRATOR
# =========================================================

def orchestrate(objective, do_research=True, model=None):

    task_id = "task-" + uuid.uuid4().hex[:12]

    blueprint = create_plan(objective)

    sources = []

    if do_research:
        sources = web_research(objective, 6)

    research_text = json.dumps(
        sources,
        ensure_ascii=False
    )

    prompt = f"""
You are an intelligence specialist inside AI Infinity.

OBJECTIVE:
{objective}

AVAILABLE RESEARCH:
{research_text[:12000]}

Create a practical execution strategy.

Separate your answer into:

1. Source-backed observations
2. Unknowns
3. Hypotheses
4. Competing strategies
5. Recommended experiments
6. Verification tests
7. Failure recovery

Rules:
- Do not invent evidence.
- Simulation is not evidence.
- Prefer reversible actions.
- Do not spend money automatically.
- Do not deploy self-modifications automatically.
"""

    intelligence = ai_generate(
        prompt,
        model
    )

    result = {
        "task_id": task_id,
        "status": "planned",
        "objective": objective,
        "architecture": blueprint,
        "research": sources,
        "intelligence": intelligence,
        "verification": {
            "status": "pending",
            "required": True
        }
    }

    c = connect()

    c.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?)",
        (
            task_id,
            objective,
            "planned",
            now(),
            json.dumps(
                result,
                ensure_ascii=False
            )
        )
    )

    for source in sources:

        c.execute(
            "INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                task_id,
                source["title"],
                source["url"],
                source["snippet"],
                source["source"],
                now()
            )
        )

    c.commit()
    c.close()

    log_event(
        "task_created",
        {
            "task_id": task_id,
            "objective": objective
        }
    )

    return result


# =========================================================
# VERIFICATION
# =========================================================

def verify(claim, evidence):

    valid = [
        item
        for item in evidence
        if item.get("url") or item.get("source")
    ]

    return {
        "claim": claim,
        "status":
            "evidence_available"
            if valid
            else "unverified",

        "evidence_count": len(valid),

        "important_note":
            "Presence of a source does not prove the claim. "
            "Independent verification is required.",

        "evidence": valid
    }


# =========================================================
# API
# =========================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "service": APP,
        "version": VERSION
    }


@app.get("/v1/status")
def status():

    return {
        "service": APP,
        "version": VERSION,

        "capabilities": [
            "intent",
            "web_research",
            "ai_model_routing",
            "orchestration",
            "evidence",
            "verification",
            "memory",
            "audit_events"
        ],

        "providers": {
            "ollama": bool(
                os.getenv("OLLAMA_URL")
            ),
            "huggingface": bool(
                os.getenv("HF_TOKEN")
            )
        },

        "governor": {
            "free_first": True,
            "automatic_spending": False,
            "automatic_deployment": False
        }
    }


@app.post("/v1/intent")
def intent(req: IntentRequest):

    return {
        "objective": req.objective,
        "domains": domains(req.objective),
        "constraints": [
            "free-first",
            "auditable",
            "verification-required"
        ]
    }


@app.post("/v1/research")
def research(req: ResearchRequest):

    return {
        "query": req.query,
        "results": web_research(
            req.query,
            req.max_results
        )
    }


@app.post("/v1/orchestrate")
def api_orchestrate(req: AskRequest):

    return orchestrate(
        req.prompt,
        req.research,
        req.model
    )


@app.post("/v1/ask")
def ask(req: AskRequest):

    sources = (
        web_research(req.prompt, 6)
        if req.research
        else []
    )

    prompt = f"""
Answer the following user request.

USER:
{req.prompt}

RESEARCH:
{json.dumps(sources, ensure_ascii=False)}

Rules:
- distinguish facts from hypotheses
- do not invent sources
- mention uncertainty
"""

    answer = ai_generate(
        prompt,
        req.model
    )

    return {
        "request": req.prompt,
        "research": sources,
        "answer": answer
    }


@app.post("/v1/verify")
def api_verify(req: VerifyRequest):

    return verify(
        req.claim,
        req.evidence
    )


@app.post("/v1/memory")
def add_memory(req: MemoryRequest):

    memory_id = "mem-" + uuid.uuid4().hex[:12]

    c = connect()

    c.execute(
        "INSERT INTO memories VALUES (?,?,?,?,?,?)",
        (
            memory_id,
            req.kind,
            req.content,
            req.source,
            req.confidence,
            now()
        )
    )

    c.commit()
    c.close()

    return {
        "id": memory_id,
        "status": "stored"
    }


@app.get("/v1/memory")
def get_memory(limit: int = 50):

    c = connect()

    rows = c.execute(
        """
        SELECT *
        FROM memories
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (min(limit, 200),)
    ).fetchall()

    c.close()

    return {
        "memories": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/v1/tasks/{task_id}")
def task(task_id: str):

    c = connect()

    row = c.execute(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,)
    ).fetchone()

    c.close()

    if not row:
        raise HTTPException(
            404,
            "Task not found"
        )

    result = dict(row)

    result["result"] = json.loads(
        result["result"]
    )

    return result


@app.get("/v1/events")
def events(limit: int = 50):

    c = connect()

    rows = c.execute(
        """
        SELECT *
        FROM events
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (min(limit, 200),)
    ).fetchall()

    c.close()

    return {
        "events": [
            dict(row)
            for row in rows
        ]
    }


# =========================================================
# WEB UI
# =========================================================

PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>

body {
    margin:0;
    background:#080d1c;
    color:#eef2ff;
    font-family:system-ui;
    padding:22px;
}

main {
    max-width:760px;
    margin:auto;
}

h1 {
    font-size:42px;
    margin-bottom:5px;
}

.sub {
    opacity:.75;
    margin-bottom:20px;
}

.card {
    background:#121a32;
    border:1px solid #2b3761;
    border-radius:22px;
    padding:20px;
}

textarea {
    width:100%;
    box-sizing:border-box;
    min-height:170px;
    background:#070b17;
    color:white;
    border:1px solid #39466f;
    border-radius:14px;
    padding:16px;
    font-size:16px;
}

button {
    border:0;
    border-radius:12px;
    padding:14px 18px;
    margin:10px 5px 0 0;
    font-size:15px;
}

pre {
    white-space:pre-wrap;
    overflow:auto;
    background:#050812;
    border-radius:14px;
    padding:16px;
    margin-top:18px;
}

</style>
</head>

<body>

<main>

<h1>∞ AI Infinity</h1>

<div class="sub">
Free-first Intelligence Fabric
<br>
Research → Reason → Verify → Learn
</div>

<div class="card">

<textarea id="q"
placeholder="Tell AI Infinity what you want to accomplish..."></textarea>

<br>

<button onclick="run('orchestrate')">
Orchestrate
</button>

<button onclick="run('research')">
Research
</button>

<button onclick="run('ask')">
Ask AI
</button>

<pre id="out">Ready.</pre>

</div>

</main>

<script>

async function run(type) {

    const q =
        document.getElementById("q")
        .value.trim();

    if (!q) {
        document.getElementById("out")
        .textContent =
        "Enter an objective first.";
        return;
    }

    document.getElementById("out")
    .textContent = "AI Infinity is working...";

    let body;

    if (type === "research") {

        body = {
            query:q,
            max_results:6
        };

    } else {

        body = {
            prompt:q,
            research:true
        };

    }

    try {

        const response =
            await fetch(
                "/v1/" + type,
                {
                    method:"POST",
                    headers:{
                        "Content-Type":
                        "application/json"
                    },
                    body:JSON.stringify(body)
                }
            );

        const data =
            await response.json();

        document.getElementById("out")
        .textContent =
        JSON.stringify(
            data,
            null,
            2
        );

    } catch(error) {

        document.getElementById("out")
        .textContent =
        "Error: " + error;

    }

}

</script>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    return PAGE
