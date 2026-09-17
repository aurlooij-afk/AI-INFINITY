import os
import re
import json
import uuid
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY — v3.1.0
# Free-first Intelligence Fabric
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version="3.1.0",
    description="Free-first Intelligence Fabric"
)

DB_PATH = Path("/tmp/ai_infinity.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_MODEL = os.getenv(
    "HF_MODEL",
    "Qwen/Qwen2.5-7B-Instruct"
).strip()

HF_URL = "https://router.huggingface.co/v1/chat/completions"

TIMEOUT = 45


# ============================================================
# DATABASE
# ============================================================

def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            objective TEXT,
            status TEXT,
            result TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT,
            source TEXT,
            confidence REAL,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            title TEXT,
            url TEXT,
            snippet TEXT,
            source TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            event TEXT,
            details TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS genomes (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            genome TEXT,
            created_at TEXT
        )
    """)

    con.commit()
    con.close()


init_db()


# ============================================================
# UTILITIES
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def log_event(event, details=None):
    con = db()
    con.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            event,
            json.dumps(details or {}),
            now()
        )
    )
    con.commit()
    con.close()


def detect_domains(text: str):
    t = text.lower()
    result = []

    mapping = {
        "research": [
            "research", "study", "investigate", "compare",
            "latest", "find", "analyze"
        ],
        "coding": [
            "code", "coding", "python", "program", "software",
            "github", "api", "developer"
        ],
        "business": [
            "business", "money", "profit", "startup", "market"
        ],
        "science": [
            "science", "scientific", "experiment", "physics",
            "biology", "chemistry"
        ],
        "creative": [
            "write", "story", "video", "image", "creative",
            "design"
        ]
    }

    for domain, words in mapping.items():
        if any(w in t for w in words):
            result.append(domain)

    return result or ["general"]


# ============================================================
# WEB RESEARCH
# ============================================================

def web_research(query: str, limit: int = 6):
    """
    Free research layer.
    Uses DuckDuckGo HTML search.
    Falls back gracefully if search is unavailable.
    """

    results = []

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; AI-Infinity/3.1)"
            )
        }

        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=headers,
            timeout=15
        )

        if response.ok:
            html = response.text

            blocks = re.findall(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                html,
                flags=re.S
            )

            for url, title in blocks[:limit]:
                title = re.sub("<.*?>", "", title)
                title = title.replace("&quot;", '"')
                title = title.replace("&amp;", "&")

                results.append({
                    "title": title.strip(),
                    "url": url,
                    "snippet": "",
                    "source": "DuckDuckGo"
                })

    except Exception as exc:
        log_event("research_error", {"error": str(exc)})

    # Wikipedia fallback
    if not results:
        try:
            response = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "format": "json",
                    "srlimit": limit
                },
                timeout=15
            )

            if response.ok:
                data = response.json()

                for item in data.get("query", {}).get("search", []):
                    title = item.get("title", "")
                    results.append({
                        "title": title,
                        "url": (
                            "https://en.wikipedia.org/wiki/"
                            + title.replace(" ", "_")
                        ),
                        "snippet": re.sub(
                            "<.*?>",
                            "",
                            item.get("snippet", "")
                        ),
                        "source": "Wikipedia"
                    })

        except Exception as exc:
            log_event("wikipedia_error", {"error": str(exc)})

    return results


# ============================================================
# HUGGING FACE AI
# ============================================================

def huggingface_chat(messages):
    """
    Current Hugging Face Inference Providers OpenAI-compatible API.
    """

    if not HF_TOKEN:
        return {
            "ok": False,
            "provider": "none",
            "model": None,
            "text": None,
            "message": "HF_TOKEN is not configured."
        }

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": HF_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1200
    }

    try:
        response = requests.post(
            HF_URL,
            headers=headers,
            json=payload,
            timeout=TIMEOUT
        )

        if not response.ok:
            return {
                "ok": False,
                "provider": "huggingface",
                "model": HF_MODEL,
                "text": None,
                "message": (
                    f"Hugging Face HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )
            }

        data = response.json()

        choices = data.get("choices", [])

        if not choices:
            return {
                "ok": False,
                "provider": "huggingface",
                "model": HF_MODEL,
                "text": None,
                "message": "Hugging Face returned no choices."
            }

        message = choices[0].get("message", {})
        text = message.get("content")

        if isinstance(text, list):
            text = "".join(
                part.get("text", "")
                for part in text
                if isinstance(part, dict)
            )

        return {
            "ok": True,
            "provider": "huggingface",
            "model": HF_MODEL,
            "text": text or "",
            "message": "AI response generated."
        }

    except Exception as exc:
        return {
            "ok": False,
            "provider": "huggingface",
            "model": HF_MODEL,
            "text": None,
            "message": str(exc)
        }


# ============================================================
# AI ROUTER
# ============================================================

def ai_generate(objective, research=None):

    research = research or []

    evidence_text = "\n".join(
        [
            f"- {x['title']} | {x['url']} | {x.get('snippet', '')}"
            for x in research
        ]
    )

    system = """
You are the reasoning engine inside AI Infinity.

Your job:
1. Understand the user's objective.
2. Use the supplied research as evidence.
3. Separate facts from assumptions.
4. Identify uncertainty.
5. Produce practical steps.
6. Never claim that a simulation is real-world evidence.
7. Never invent sources.
8. Respect the free-first policy.
9. Do not automatically spend money.
10. Do not perform consequential actions without authorization.

Return a useful, concise but intelligent answer.
"""

    user = f"""
OBJECTIVE:
{objective}

RESEARCH:
{evidence_text if evidence_text else "No external research was available."}

Create the best practical response.
"""

    return huggingface_chat([
        {
            "role": "system",
            "content": system
        },
        {
            "role": "user",
            "content": user
        }
    ])


# ============================================================
# INTELLIGENCE PLAN
# ============================================================

def create_plan(objective: str):

    domains = detect_domains(objective)

    return {
        "objective": objective,
        "domains": domains,

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


# ============================================================
# REQUEST MODELS
# ============================================================

class AskRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=10000)


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class IntentRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=10000)


class VerifyRequest(BaseModel):
    task_id: str


class MemoryRequest(BaseModel):
    content: str
    source: str = "AI Infinity"
    confidence: float = 0.5


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "AI Infinity",
        "version": "3.1.0"
    }


@app.get("/v1/status")
def status():

    return {
        "service": "AI Infinity",
        "version": "3.1.0",

        "providers": {
            "huggingface": bool(HF_TOKEN),
            "huggingface_model": HF_MODEL
        },

        "governor": {
            "free_first": True,
            "automatic_spending": False,
            "automatic_self_deployment": False
        },

        "capabilities": [
            "intent_planning",
            "web_research",
            "ai_reasoning",
            "temporary_minds",
            "verification",
            "memory",
            "audit_events",
            "intelligence_genome"
        ]
    }


# ============================================================
# INTENT
# ============================================================

@app.post("/v1/intent")
def intent(req: IntentRequest):

    plan = create_plan(req.objective)

    log_event(
        "intent_created",
        {"objective": req.objective}
    )

    return {
        "status": "planned",
        "architecture": plan
    }


# ============================================================
# RESEARCH
# ============================================================

@app.post("/v1/research")
def research(req: ResearchRequest):

    results = web_research(req.query)

    log_event(
        "research_completed",
        {
            "query": req.query,
            "results": len(results)
        }
    )

    return {
        "query": req.query,
        "results": results,
        "count": len(results)
    }


# ============================================================
# ORCHESTRATE
# ============================================================

@app.post("/v1/orchestrate")
def orchestrate(req: AskRequest):

    task_id = "task-" + uuid.uuid4().hex[:12]

    objective = req.command

    plan = create_plan(objective)

    research_results = web_research(objective)

    ai = ai_generate(
        objective,
        research_results
    )

    status_value = "completed" if ai["ok"] else "planned"

    result = {
        "task_id": task_id,
        "status": status_value,
        "objective": objective,
        "architecture": plan,

        "research": research_results,

        "intelligence": {
            "text": ai["text"],
            "provider": ai["provider"],
            "model": ai["model"],
            "message": ai["message"]
        },

        "verification": {
            "status": "pending",
            "required": True,
            "note": (
                "Research sources provide evidence candidates; "
                "they do not automatically prove every claim."
            )
        }
    }

    con = db()

    con.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
        (
            task_id,
            objective,
            status_value,
            json.dumps(result),
            now()
        )
    )

    for item in research_results:
        con.execute(
            "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                task_id,
                item["title"],
                item["url"],
                item.get("snippet", ""),
                item["source"],
                now()
            )
        )

    genome = {
        "objective": objective,
        "domains": plan["domains"],
        "strategy": plan["execution_graph"],
        "provider": ai["provider"],
        "model": ai["model"],
        "evidence_count": len(research_results),
        "governor": plan["governor"]
    }

    con.execute(
        "INSERT INTO genomes VALUES (?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            task_id,
            json.dumps(genome),
            now()
        )
    )

    con.commit()
    con.close()

    log_event(
        "orchestration_completed",
        {
            "task_id": task_id,
            "provider": ai["provider"],
            "model": ai["model"]
        }
    )

    return result


# ============================================================
# ASK AI
# ============================================================

@app.post("/v1/ask")
def ask(req: AskRequest):

    results = web_research(req.command)

    ai = ai_generate(
        req.command,
        results
    )

    return {
        "status": "completed" if ai["ok"] else "error",
        "objective": req.command,
        "research": results,
        "intelligence": ai
    }


# ============================================================
# VERIFY
# ============================================================

@app.post("/v1/verify")
def verify(req: VerifyRequest):

    con = db()

    row = con.execute(
        "SELECT objective, result FROM tasks WHERE id=?",
        (req.task_id,)
    ).fetchone()

    evidence = con.execute(
        "SELECT title, url, source FROM evidence WHERE task_id=?",
        (req.task_id,)
    ).fetchall()

    con.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Task not found"
        )

    return {
        "task_id": req.task_id,
        "status": "review_required",
        "objective": row[0],
        "evidence_count": len(evidence),
        "evidence": [
            {
                "title": x[0],
                "url": x[1],
                "source": x[2]
            }
            for x in evidence
        ],
        "note": (
            "AI Infinity requires independent verification "
            "before treating consequential claims as verified."
        )
    }


# ============================================================
# MEMORY
# ============================================================

@app.post("/v1/memory")
def save_memory(req: MemoryRequest):

    memory_id = "mem-" + uuid.uuid4().hex[:12]

    con = db()

    con.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?)",
        (
            memory_id,
            req.content,
            req.source,
            req.confidence,
            now()
        )
    )

    con.commit()
    con.close()

    return {
        "status": "stored",
        "memory_id": memory_id
    }


@app.get("/v1/memory")
def get_memory():

    con = db()

    rows = con.execute(
        """
        SELECT id, content, source, confidence, created_at
        FROM memories
        ORDER BY created_at DESC
        LIMIT 100
        """
    ).fetchall()

    con.close()

    return {
        "memories": [
            {
                "id": x[0],
                "content": x[1],
                "source": x[2],
                "confidence": x[3],
                "created_at": x[4]
            }
            for x in rows
        ]
    }


# ============================================================
# TASK
# ============================================================

@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str):

    con = db()

    row = con.execute(
        "SELECT id, objective, status, result, created_at "
        "FROM tasks WHERE id=?",
        (task_id,)
    ).fetchone()

    con.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Task not found"
        )

    return {
        "task_id": row[0],
        "objective": row[1],
        "status": row[2],
        "result": json.loads(row[3]),
        "created_at": row[4]
    }


# ============================================================
# EVENTS
# ============================================================

@app.get("/v1/events")
def events():

    con = db()

    rows = con.execute(
        """
        SELECT id, event, details, created_at
        FROM events
        ORDER BY created_at DESC
        LIMIT 100
        """
    ).fetchall()

    con.close()

    return {
        "events": [
            {
                "id": x[0],
                "event": x[1],
                "details": json.loads(x[2]),
                "created_at": x[3]
            }
            for x in rows
        ]
    }


# ============================================================
# UI
# ============================================================

HTML = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Infinity</title>

<style>
body{
    margin:0;
    background:#05070b;
    color:#f5f5f5;
    font-family:Arial,sans-serif;
}

.container{
    max-width:900px;
    margin:auto;
    padding:25px;
}

h1{
    font-size:38px;
    margin-bottom:5px;
}

.subtitle{
    color:#9ca3af;
    margin-bottom:25px;
}

textarea{
    width:100%;
    min-height:150px;
    box-sizing:border-box;
    background:#111827;
    color:white;
    border:1px solid #374151;
    border-radius:14px;
    padding:16px;
    font-size:16px;
}

button{
    margin-top:12px;
    margin-right:8px;
    padding:13px 18px;
    border:0;
    border-radius:10px;
    cursor:pointer;
    font-weight:bold;
}

#result{
    white-space:pre-wrap;
    margin-top:25px;
    padding:18px;
    background:#111827;
    border-radius:14px;
    overflow:auto;
}
</style>
</head>

<body>

<div class="container">

<h1>∞ AI Infinity</h1>

<div class="subtitle">
Free-first Intelligence Fabric<br>
Orchestrate → Research → Reason → Verify → Learn
</div>

<textarea id="command"
placeholder="Tell AI Infinity what you want to accomplish..."></textarea>

<br>

<button onclick="run('orchestrate')">
Orchestrate
</button>

<button onclick="run('ask')">
Ask AI
</button>

<button onclick="research()">
Research
</button>

<div id="result">
Ready.
</div>

</div>

<script>

async function run(type){

    const command =
        document.getElementById("command").value.trim();

    if(!command){
        alert("Enter an objective first.");
        return;
    }

    document.getElementById("result").textContent =
        "AI Infinity is working...";

    const endpoint =
        type === "ask"
        ? "/v1/ask"
        : "/v1/orchestrate";

    try{

        const response = await fetch(endpoint,{
            method:"POST",
            headers:{
                "Content-Type":"application/json"
            },
            body:JSON.stringify({
                command:command
            })
        });

        const data = await response.json();

        document.getElementById("result").textContent =
            JSON.stringify(data,null,2);

    }catch(error){

        document.getElementById("result").textContent =
            "Error: " + error;
    }
}


async function research(){

    const command =
        document.getElementById("command").value.trim();

    if(!command){
        alert("Enter a research question first.");
        return;
    }

    document.getElementById("result").textContent =
        "Researching...";

    try{

        const response = await fetch("/v1/research",{
            method:"POST",
            headers:{
                "Content-Type":"application/json"
            },
            body:JSON.stringify({
                query:command
            })
        });

        const data = await response.json();

        document.getElementById("result").textContent =
            JSON.stringify(data,null,2);

    }catch(error){

        document.getElementById("result").textContent =
            "Error: " + error;
    }
}

</script>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML
