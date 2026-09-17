import os
import json
import uuid
import time
import asyncio
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY 7 — REAL EXECUTION CORE
# ============================================================

APP_NAME = "AI Infinity"
VERSION = "7.0"

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="Free-first AI orchestration, research, reasoning, verification and execution engine."
)

DATA_DIR = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

TASK_FILE = DATA_DIR / "tasks.json"
MEMORY_FILE = DATA_DIR / "memory.json"

LOCK = threading.Lock()

MAX_TASKS = 200
MAX_MEMORY = 500
MAX_RESEARCH = 8
MAX_SPECIALISTS = 6

TASKS: Dict[str, Dict[str, Any]] = {}
MEMORY: List[Dict[str, Any]] = []


# ============================================================
# TIME / STORAGE
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def clean(value: Any, limit: int = 10000) -> str:
    return " ".join(str(value).split())[:limit]


def load_json(path: Path, default):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path: Path, data):
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception:
        pass


def load_state():
    global TASKS, MEMORY

    TASKS = load_json(TASK_FILE, {})
    MEMORY = load_json(MEMORY_FILE, [])

    if not isinstance(TASKS, dict):
        TASKS = {}

    if not isinstance(MEMORY, list):
        MEMORY = []


def persist_state():
    with LOCK:
        save_json(TASK_FILE, TASKS)
        save_json(MEMORY_FILE, MEMORY)


def trim_state():
    global TASKS, MEMORY

    if len(TASKS) > MAX_TASKS:
        keys = list(TASKS.keys())
        for key in keys[:-MAX_TASKS]:
            TASKS.pop(key, None)

    if len(MEMORY) > MAX_MEMORY:
        MEMORY = MEMORY[-MAX_MEMORY:]


def remember(category: str, content: str, source: str = APP_NAME):
    MEMORY.append({
        "id": make_id("mem"),
        "timestamp": now(),
        "category": category,
        "content": clean(content, 3000),
        "source": source
    })

    trim_state()
    persist_state()


# ============================================================
# REQUEST MODELS
# ============================================================

class TaskRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=10000)
    mode: str = Field(default="auto", max_length=50)
    research: bool = True
    verify: bool = True
    remember: bool = True


class ResearchRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)


# ============================================================
# FREE AI PROVIDER ROUTER
# ============================================================

def provider_pollinations(prompt: str) -> Optional[str]:
    urls = [
        "https://text.pollinations.ai/"
    ]

    for url in urls:
        try:
            response = requests.get(
                url,
                params={"prompt": prompt},
                headers={"User-Agent": "AI-Infinity/7.0"},
                timeout=35
            )

            if response.status_code == 200:
                text = response.text.strip()

                if text:
                    return text[:16000]

        except Exception:
            continue

    return None


def provider_huggingface(prompt: str) -> Optional[str]:
    token = os.getenv("HF_TOKEN")

    if not token:
        return None

    models = [
        "Qwen/Qwen2.5-7B-Instruct",
        "microsoft/Phi-3.5-mini-instruct"
    ]

    for model in models:
        try:
            url = f"https://api-inference.huggingface.co/models/{model}"

            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                json={
                    "inputs": prompt,
                    "parameters": {
                        "max_new_tokens": 1200,
                        "temperature": 0.2,
                        "return_full_text": False
                    }
                },
                timeout=45
            )

            if response.status_code != 200:
                continue

            data = response.json()

            if isinstance(data, list) and data:
                text = data[0].get("generated_text")

                if text:
                    return str(text)[:16000]

            if isinstance(data, dict):
                text = data.get("generated_text")

                if text:
                    return str(text)[:16000]

        except Exception:
            continue

    return None


def ai_generate(prompt: str) -> Dict[str, Any]:
    """
    Multi-provider free-first router.

    Provider order:
    1. Pollinations
    2. Hugging Face when HF_TOKEN exists
    """

    providers = [
        ("pollinations", provider_pollinations),
        ("huggingface", provider_huggingface)
    ]

    started = time.time()

    for name, provider in providers:
        result = provider(prompt)

        if result:
            return {
                "ok": True,
                "provider": name,
                "text": result,
                "latency_seconds": round(time.time() - started, 2)
            }

    return {
        "ok": False,
        "provider": None,
        "text": None,
        "latency_seconds": round(time.time() - started, 2)
    }


# ============================================================
# WEB RESEARCH
# ============================================================

def web_search(query: str) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 AI-Infinity/7.0"
            },
            timeout=15
        )

        if response.status_code != 200:
            return []

        from html.parser import HTMLParser

        class Parser(HTMLParser):

            def __init__(self):
                super().__init__()
                self.results = []
                self.active = False
                self.current = None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)

                if (
                    tag == "a"
                    and "result__a" in attrs.get("class", "")
                ):
                    self.active = True
                    self.current = {
                        "title": "",
                        "url": attrs.get("href", "")
                    }

            def handle_data(self, data):
                if self.active and self.current:
                    self.current["title"] += data

            def handle_endtag(self, tag):
                if tag == "a" and self.active:
                    if self.current:
                        title = clean(
                            self.current["title"],
                            500
                        )

                        if title:
                            self.results.append({
                                "title": title,
                                "url": self.current["url"]
                            })

                    self.active = False
                    self.current = None

        parser = Parser()
        parser.feed(response.text)

        return parser.results[:MAX_RESEARCH]

    except Exception:
        return []


# ============================================================
# INTELLIGENCE CLASSIFIER
# ============================================================

def classify_objective(objective: str) -> Dict[str, Any]:

    text = objective.lower()

    keywords = {
        "build": [
            "build", "create", "make", "develop",
            "code", "app", "website", "software"
        ],
        "research": [
            "research", "investigate", "analyze",
            "study", "compare", "find"
        ],
        "business": [
            "business", "money", "profit",
            "startup", "market", "customer"
        ],
        "technical": [
            "api", "server", "github", "render",
            "python", "deployment", "database"
        ],
        "creative": [
            "write", "design", "video",
            "image", "story", "content"
        ],
        "automation": [
            "automate", "automation", "agent",
            "workflow", "automatic"
        ]
    }

    categories = []

    for category, words in keywords.items():
        if any(word in text for word in words):
            categories.append(category)

    if not categories:
        categories = ["general"]

    complexity = (
        "high" if len(objective) > 1000
        else "medium" if len(objective) > 250
        else "low"
    )

    return {
        "categories": list(dict.fromkeys(categories)),
        "complexity": complexity
    }


# ============================================================
# SPECIALIST MINDS
# ============================================================

SPECIALISTS = [
    (
        "Research Mind",
        "Find useful evidence, sources and missing information."
    ),
    (
        "Builder Mind",
        "Turn the objective into concrete implementation steps."
    ),
    (
        "Critical Mind",
        "Find weaknesses, risks, contradictions and assumptions."
    ),
    (
        "Optimizer Mind",
        "Reduce cost, complexity and unnecessary work."
    ),
    (
        "Verification Mind",
        "Separate evidence, uncertainty and unsupported claims."
    ),
    (
        "Future Mind",
        "Identify scalable improvements and future capabilities."
    )
]


def specialist_prompt(
    name: str,
    role: str,
    objective: str,
    research: str,
    memory: str
) -> str:

    return f"""
You are the {name} inside AI Infinity 7.

YOUR ROLE:
{role}

MISSION:
{objective}

WEB RESEARCH:
{research[:7000]}

RELEVANT MEMORY:
{memory[:4000]}

Return concise, practical analysis.

Rules:
- Do not invent facts.
- Identify uncertainty.
- Stay within your specialist role.
- Prefer free solutions.
- Give actionable information.
"""


async def run_specialist(
    name: str,
    role: str,
    objective: str,
    research: str,
    memory: str
):

    prompt = specialist_prompt(
        name,
        role,
        objective,
        research,
        memory
    )

    result = await asyncio.to_thread(
        ai_generate,
        prompt
    )

    if result["ok"]:
        return {
            "mind": name,
            "provider": result["provider"],
            "analysis": result["text"][:7000]
        }

    return {
        "mind": name,
        "provider": None,
        "analysis": (
            f"{name}: provider unavailable. "
            "No unsupported conclusion generated."
        )
    }


async def run_specialists(
    objective: str,
    research: str,
    memory: str
):

    jobs = [
        run_specialist(
            name,
            role,
            objective,
            research,
            memory
        )
        for name, role in SPECIALISTS
    ]

    return await asyncio.gather(*jobs)


# ============================================================
# PLAN ENGINE
# ============================================================

def build_plan(
    objective: str,
    intent: Dict[str, Any]
):

    return {
        "goal": objective,
        "classification": intent,
        "pipeline": [
            "Understand",
            "Research",
            "Parallel Minds",
            "Synthesis",
            "Verification",
            "Execution",
            "Learning"
        ]
    }


# ============================================================
# SYNTHESIS
# ============================================================

def synthesize(
    objective: str,
    research: List[Dict[str, Any]],
    specialists: List[Dict[str, Any]],
    memory: str
):

    research_text = "\n".join(
        f"- {x.get('title', '')} | {x.get('url', '')}"
        for x in research
    )

    specialist_text = "\n\n".join(
        f"### {x['mind']}\n{x['analysis']}"
        for x in specialists
    )

    prompt = f"""
You are the central intelligence engine of AI Infinity 7.

MISSION:
{objective}

RESEARCH:
{research_text[:7000]}

SPECIALIST MINDS:
{specialist_text[:30000]}

MEMORY:
{memory[:5000]}

Produce one practical result.

FORMAT:

OBJECTIVE
What the user is trying to accomplish.

KNOWN
Facts/evidence available.

UNCERTAINTY
What cannot be verified.

INSIGHTS
Most useful conclusions from the available evidence.

ACTION PLAN
Concrete implementation sequence.

NEXT ACTION
The single most useful immediate action.

VERIFICATION
How the result should be checked.

FUTURE
Useful upgrades.

RULES:
- Never invent facts.
- Never claim an action happened when it did not.
- Clearly distinguish evidence from assumptions.
- Prefer free-first solutions.
- Be concise but useful.
"""

    result = ai_generate(prompt)

    if result["ok"]:
        return {
            "answer": result["text"],
            "provider": result["provider"]
        }

    return {
        "answer": fallback_answer(
            objective,
            len(research),
            len(specialists)
        ),
        "provider": None
    }


def fallback_answer(
    objective: str,
    research_count: int,
    specialist_count: int
):

    return f"""
AI Infinity 7 completed its orchestration pipeline.

OBJECTIVE:
{objective}

PIPELINE:
✓ Mission understood
✓ Intent classified
✓ Research attempted
✓ {specialist_count} specialist minds executed
✓ {research_count} research results collected
✓ Verification layer prepared
✓ Execution plan prepared
✓ Learning layer available

The external AI generation providers were unavailable during final
synthesis, so AI Infinity did not invent a final answer.

NEXT ACTION:
Retry the mission when an AI provider is available.
"""


# ============================================================
# VERIFICATION ENGINE
# ============================================================

def verify_result(
    objective: str,
    research: List[Dict[str, Any]],
    specialists: List[Dict[str, Any]],
    answer: str
):

    warnings = []

    if not research:
        warnings.append("No external research results were returned.")

    if not specialists:
        warnings.append("No specialist results were returned.")

    if not answer.strip():
        warnings.append("Final answer is empty.")

    return {
        "passed": len(warnings) == 0,
        "warnings": warnings,
        "research_count": len(research),
        "specialist_count": len(specialists),
        "checked_at": now()
    }


# ============================================================
# TASK ENGINE
# ============================================================

async def execute_task(
    tid: str,
    request: TaskRequest
):

    task = TASKS[tid]

    task["status"] = "running"
    task["started_at"] = now()

    try:

        objective = clean(
            request.objective,
            10000
        )

        # 1 — UNDERSTAND

        intent = classify_objective(objective)

        task["intent"] = intent

        # 2 — PLAN

        task["plan"] = build_plan(
            objective,
            intent
        )

        # 3 — MEMORY CONTEXT

        memory_items = MEMORY[-20:]

        memory_text = "\n".join(
            f"- {x.get('category')}: {x.get('content')}"
            for x in memory_items
        )

        task["memory_context_items"] = len(memory_items)

        # 4 — RESEARCH

        research = []

        if request.research:
            research = await asyncio.to_thread(
                web_search,
                objective
            )

        task["research"] = research

        research_text = "\n".join(
            f"{x.get('title', '')} {x.get('url', '')}"
            for x in research
        )

        # 5 — PARALLEL MINDS

        specialists = await run_specialists(
            objective,
            research_text,
            memory_text
        )

        task["specialists"] = specialists

        # 6 — SYNTHESIS

        synthesis = await asyncio.to_thread(
            synthesize,
            objective,
            research,
            specialists,
            memory_text
        )

        task["answer"] = synthesis["answer"]
        task["provider"] = synthesis["provider"]

        # 7 — VERIFICATION

        if request.verify:

            verification = verify_result(
                objective,
                research,
                specialists,
                synthesis["answer"]
            )

        else:

            verification = {
                "passed": True,
                "warnings": ["Verification was disabled."],
                "checked_at": now()
            }

        task["verification"] = verification

        # 8 — EXECUTION PLAN

        task["execution"] = {
            "ready": True,
            "controlled": True,
            "actions": [
                "Review the generated strategy.",
                "Execute the next action.",
                "Verify the real-world result.",
                "Feed the result back into AI Infinity."
            ]
        }

        # 9 — LEARNING

        if request.remember:

            remember(
                "task",
                (
                    f"Objective: {objective}\n"
                    f"Provider: {synthesis['provider']}\n"
                    f"Result: {synthesis['answer'][:1800]}"
                )
            )

        # 10 — COMPLETE

        task["status"] = "completed"
        task["completed_at"] = now()

        trim_state()
        persist_state()

        return task

    except Exception as exc:

        task["status"] = "failed"

        task["error"] = {
            "type": type(exc).__name__,
            "message": str(exc)
        }

        task["failed_at"] = now()

        persist_state()

        return task


# ============================================================
# WEB UI
# ============================================================

HTML = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>AI Infinity 7</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #070707;
    color: #fff;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 20px;
}

.logo {
    font-size: 44px;
    font-weight: bold;
}

.subtitle {
    color: #999;
    margin: 8px 0 25px;
}

.card {
    background: #141414;
    border: 1px solid #292929;
    border-radius: 18px;
    padding: 18px;
    margin-bottom: 16px;
}

textarea {
    width: 100%;
    min-height: 160px;
    resize: vertical;
    padding: 15px;
    border-radius: 12px;
    border: 1px solid #333;
    background: #090909;
    color: #fff;
    font-size: 16px;
}

button {
    width: 100%;
    padding: 16px;
    margin-top: 12px;
    border: 0;
    border-radius: 12px;
    background: #fff;
    color: #000;
    font-size: 17px;
    font-weight: bold;
}

button:disabled {
    opacity: .5;
}

#status {
    color: #8cffb0;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    color: #ddd;
    line-height: 1.5;
}

.badge {
    display: inline-block;
    padding: 7px 10px;
    border-radius: 10px;
    background: #222;
    margin: 4px;
    font-size: 13px;
}

</style>
</head>

<body>

<div class="container">

<div class="logo">∞ AI Infinity 7</div>

<div class="subtitle">
One mission → research → parallel minds → synthesis → verification → execution → learning
</div>

<div class="card">

<h2>Give AI Infinity a mission</h2>

<textarea id="objective"
placeholder="Tell AI Infinity what you want done..."></textarea>

<button id="run" onclick="runTask()">
RUN AI INFINITY
</button>

</div>

<div class="card">

<h3>Status</h3>

<div id="status">
Ready
</div>

</div>

<div class="card">

<h3>Result</h3>

<pre id="result">No mission executed yet.</pre>

</div>

<div class="card">

<h3>Capabilities</h3>

<div class="badge">Multi-provider AI</div>
<div class="badge">Web research</div>
<div class="badge">6 specialist minds</div>
<div class="badge">Verification</div>
<div class="badge">Memory</div>
<div class="badge">Execution planning</div>
<div class="badge">Free-first</div>

</div>

</div>

<script>

async function runTask() {

    const box = document.getElementById("objective");
    const button = document.getElementById("run");
    const status = document.getElementById("status");
    const result = document.getElementById("result");

    const objective = box.value.trim();

    if (!objective) {
        alert("Enter a mission first.");
        return;
    }

    button.disabled = true;

    status.innerText = "AI Infinity 7 is working...";
    result.innerText =
        "Researching + running specialist minds + synthesizing...";

    try {

        const response = await fetch("/task", {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                objective: objective,
                research: true,
                verify: true,
                remember: true
            })

        });

        const data = await response.json();

        status.innerText =
            data.status || "completed";

        result.innerText =
            data.answer ||
            JSON.stringify(data, null, 2);

    } catch (error) {

        status.innerText = "Error";

        result.innerText =
            String(error);

    } finally {

        button.disabled = false;

    }
}

</script>

</body>
</html>
"""


# ============================================================
# ROUTES
# ============================================================

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.get("/health")
def health():

    return {
        "status": "online",
        "name": APP_NAME,
        "version": VERSION,
        "timestamp": now(),
        "tasks": len(TASKS),
        "memory": len(MEMORY),
        "providers": [
            "pollinations",
            "huggingface-if-token-configured"
        ],
        "engine": [
            "intent",
            "planning",
            "research",
            "parallel minds",
            "multi-provider synthesis",
            "verification",
            "execution planning",
            "learning"
        ]
    }


@app.post("/task")
async def create_task(request: TaskRequest):

    tid = make_id("task")

    TASKS[tid] = {
        "task_id": tid,
        "status": "queued",
        "objective": clean(request.objective),
        "mode": request.mode,
        "created_at": now()
    }

    persist_state()

    return await execute_task(
        tid,
        request
    )


@app.get("/task/{tid}")
def get_task(tid: str):

    task = TASKS.get(tid)

    if not task:
        raise HTTPException(
            status_code=404,
            detail="Task not found"
        )

    return task


@app.get("/tasks")
def list_tasks():

    return {
        "count": len(TASKS),
        "tasks": list(TASKS.values())[-50:]
    }


@app.post("/research")
async def research(request: ResearchRequest):

    results = await asyncio.to_thread(
        web_search,
        request.question
    )

    return {
        "question": request.question,
        "results": results,
        "count": len(results),
        "timestamp": now()
    }


@app.get("/memory")
def get_memory():

    return {
        "count": len(MEMORY),
        "memory": MEMORY[-100:]
    }


@app.delete("/memory")
def clear_memory():

    MEMORY.clear()
    persist_state()

    return {
        "status": "cleared"
    }


@app.get("/stats")
def stats():

    completed = sum(
        1 for x in TASKS.values()
        if x.get("status") == "completed"
    )

    failed = sum(
        1 for x in TASKS.values()
        if x.get("status") == "failed"
    )

    return {
        "application": APP_NAME,
        "version": VERSION,
        "total_tasks": len(TASKS),
        "completed": completed,
        "failed": failed,
        "memory_items": len(MEMORY),
        "specialist_minds": len(SPECIALISTS),
        "free_first": True
    }


@app.get("/capabilities")
def capabilities():

    return {
        "name": APP_NAME,
        "version": VERSION,
        "free_first": True,

        "core": [
            "Mission intake",
            "Intent classification",
            "Dynamic planning",
            "Web research",
            "Multi-provider AI",
            "Parallel specialist minds",
            "Synthesis",
            "Verification",
            "Execution planning",
            "Persistent memory",
            "Task history",
            "Health monitoring",
            "Mobile browser interface"
        ],

        "specialist_minds": [
            name for name, _ in SPECIALISTS
        ],

        "providers": [
            "Pollinations",
            "Hugging Face when HF_TOKEN is configured"
        ]
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    load_state()

    print("=" * 60)
    print("∞ AI INFINITY 7")
    print("STATUS: ONLINE")
    print("FREE-FIRST MULTI-PROVIDER ENGINE")
    print("=" * 60)


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000"))
    )
