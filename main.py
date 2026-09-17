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
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY — CORE ENGINE
# ============================================================

APP_NAME = "AI Infinity"
VERSION = "6.0"

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="Free-first AI orchestration, research, reasoning and learning engine."
)

# ============================================================
# STORAGE
# ============================================================

DATA_DIR = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

TASK_FILE = DATA_DIR / "tasks.json"
MEMORY_FILE = DATA_DIR / "memory.json"

LOCK = threading.Lock()

MAX_TASKS = 200
MAX_MEMORY = 500

TASKS: Dict[str, Dict[str, Any]] = {}
MEMORY: List[Dict[str, Any]] = []


# ============================================================
# STARTUP STORAGE
# ============================================================

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
        temp = path.with_suffix(".tmp")

        with open(temp, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        temp.replace(path)

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


# ============================================================
# BASIC HELPERS
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_id() -> str:
    return f"task-{uuid.uuid4().hex[:12]}"


def clean(text: Any, limit: int = 10000) -> str:
    return " ".join(str(text).split())[:limit]


def trim_state():
    global TASKS, MEMORY

    if len(TASKS) > MAX_TASKS:
        keys = list(TASKS.keys())

        for key in keys[:-MAX_TASKS]:
            TASKS.pop(key, None)

    if len(MEMORY) > MAX_MEMORY:
        MEMORY = MEMORY[-MAX_MEMORY:]


def remember(
    category: str,
    content: str,
    source: str = "AI Infinity"
):
    MEMORY.append({
        "id": uuid.uuid4().hex[:12],
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
    objective: str = Field(
        ...,
        min_length=1,
        max_length=10000
    )

    mode: str = Field(
        default="auto",
        max_length=50
    )

    research: bool = True

    verify: bool = True

    remember: bool = True


class ResearchRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=5000
    )


# ============================================================
# WEB RESEARCH
# ============================================================

def web_search(query: str) -> List[Dict[str, Any]]:
    """
    Free-first web search.
    Uses DuckDuckGo HTML without requiring an API key.
    """

    try:

        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "User-Agent":
                    "Mozilla/5.0 AI-Infinity/6.0"
            },
            timeout=15
        )

        if response.status_code != 200:
            return []

        from html.parser import HTMLParser

        class SearchParser(HTMLParser):

            def __init__(self):
                super().__init__()

                self.results = []
                self.active = False
                self.current = None

            def handle_starttag(self, tag, attrs):

                attrs = dict(attrs)

                if (
                    tag == "a"
                    and "result__a" in
                    attrs.get("class", "")
                ):

                    self.active = True

                    self.current = {
                        "title": "",
                        "url": attrs.get(
                            "href",
                            ""
                        )
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

                    self.current = None
                    self.active = False

        parser = SearchParser()
        parser.feed(response.text)

        return parser.results[:10]

    except Exception as exc:

        return [{
            "error": type(exc).__name__
        }]


# ============================================================
# AI PROVIDERS
# ============================================================

def pollinations(prompt: str) -> Optional[str]:
    """
    Free-first text generation attempt.
    """

    urls = [
        "https://text.pollinations.ai/"
    ]

    for url in urls:

        try:

            response = requests.get(
                url,
                params={
                    "prompt": prompt
                },
                headers={
                    "User-Agent":
                        "AI-Infinity/6.0"
                },
                timeout=40
            )

            if response.status_code == 200:

                result = response.text.strip()

                if result:
                    return result[:15000]

        except Exception:
            continue

    return None


# ============================================================
# INTENT ENGINE
# ============================================================

def classify_objective(objective: str) -> Dict[str, Any]:

    text = objective.lower()

    categories = []

    keywords = {
        "build": [
            "build",
            "create",
            "make",
            "develop",
            "code",
            "app",
            "website"
        ],
        "research": [
            "research",
            "investigate",
            "analyze",
            "study",
            "compare"
        ],
        "business": [
            "business",
            "money",
            "profit",
            "startup",
            "market"
        ],
        "technical": [
            "api",
            "server",
            "github",
            "render",
            "python",
            "deployment"
        ],
        "creative": [
            "write",
            "design",
            "video",
            "image",
            "story",
            "content"
        ]
    }

    for category, words in keywords.items():

        if any(word in text for word in words):
            categories.append(category)

    if not categories:
        categories.append("general")

    return {
        "categories": list(dict.fromkeys(categories)),
        "complexity": (
            "high"
            if len(objective) > 1000
            else "medium"
            if len(objective) > 250
            else "low"
        )
    }


# ============================================================
# SPECIALIST MINDS
# ============================================================

SPECIALISTS = [
    (
        "Research Mind",
        "Find useful information, evidence and external resources."
    ),
    (
        "Builder Mind",
        "Convert the objective into practical implementation steps."
    ),
    (
        "Critical Mind",
        "Find weaknesses, assumptions, risks and contradictions."
    ),
    (
        "Optimizer Mind",
        "Simplify the solution and reduce unnecessary cost and complexity."
    ),
    (
        "Verification Mind",
        "Check important claims and distinguish evidence from assumptions."
    ),
    (
        "Future Mind",
        "Identify scalable improvements and possible next iterations."
    )
]


def specialist_prompts(
    objective: str,
    research: str
):

    prompts = []

    for name, role in SPECIALISTS:

        prompts.append(
            (
                name,
                f"""
You are the {name} inside AI Infinity.

ROLE:
{role}

OBJECTIVE:
{objective}

AVAILABLE RESEARCH:
{research[:7000]}

Give concise, useful analysis.
Do not invent facts.
Clearly identify uncertainty.
Focus only on your specialist role.
"""
            )
        )

    return prompts


# ============================================================
# PARALLEL SPECIALISTS
# ============================================================

async def run_specialist(
    name: str,
    prompt: str
):

    result = await asyncio.to_thread(
        pollinations,
        prompt
    )

    if not result:

        result = (
            f"{name}: AI provider unavailable. "
            "No unsupported conclusion was generated."
        )

    return {
        "mind": name,
        "analysis": result[:6000]
    }


async def run_specialists(
    objective: str,
    research: str
):

    prompts = specialist_prompts(
        objective,
        research
    )

    jobs = [
        run_specialist(
            name,
            prompt
        )
        for name, prompt in prompts
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
        "stages": [
            {
                "id": 1,
                "name": "Understand",
                "purpose": "Identify the real goal and constraints."
            },
            {
                "id": 2,
                "name": "Research",
                "purpose": "Collect relevant external information."
            },
            {
                "id": 3,
                "name": "Parallel Minds",
                "purpose": "Analyze the problem from different specialist perspectives."
            },
            {
                "id": 4,
                "name": "Synthesis",
                "purpose": "Combine useful insights into one coherent strategy."
            },
            {
                "id": 5,
                "name": "Verification",
                "purpose": "Identify evidence, uncertainty and contradictions."
            },
            {
                "id": 6,
                "name": "Execution",
                "purpose": "Produce concrete next actions."
            },
            {
                "id": 7,
                "name": "Learning",
                "purpose": "Store useful reusable knowledge."
            }
        ]
    }


# ============================================================
# SYNTHESIS
# ============================================================

def synthesize(
    objective: str,
    research: List[Dict[str, Any]],
    specialist_results: List[Dict[str, Any]]
) -> Optional[str]:

    research_text = "\n".join(
        f"- {item.get('title', '')} | {item.get('url', '')}"
        for item in research
    )

    specialist_text = "\n\n".join(
        f"### {item['mind']}\n{item['analysis']}"
        for item in specialist_results
    )

    prompt = f"""
You are the central synthesis engine of AI Infinity.

OBJECTIVE:
{objective}

RESEARCH:
{research_text[:7000]}

SPECIALIST ANALYSIS:
{specialist_text[:25000]}

Create one practical final strategy.

Structure:

1. Objective
2. What is known
3. Important uncertainty
4. Key insights
5. Recommended implementation sequence
6. Concrete next actions
7. Verification checklist
8. Future upgrade opportunities

Rules:

- Do not pretend uncertain information is certain.
- Do not invent tools, APIs or results.
- Prefer free solutions where practical.
- Keep actions executable.
- If research is weak, say so.
"""

    return pollinations(prompt)


# ============================================================
# LOCAL FALLBACK SYNTHESIS
# ============================================================

def fallback_result(
    objective: str,
    intent: Dict[str, Any]
):

    return f"""
AI Infinity processed the objective successfully.

OBJECTIVE:
{objective}

CLASSIFICATION:
{json.dumps(intent, indent=2)}

EXECUTION MODEL:

1. Understand the objective.
2. Research relevant information.
3. Run multiple specialist analyses.
4. Compare their findings.
5. Verify important claims.
6. Convert the findings into executable actions.
7. Store useful knowledge for future tasks.

The external AI provider was unavailable during synthesis,
so AI Infinity intentionally avoided inventing a final answer.

NEXT ACTION:
Use the specialist results and research returned with this task.
"""


# ============================================================
# TASK EXECUTION
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

        # --------------------------------------------
        # 1. INTENT
        # --------------------------------------------

        intent = classify_objective(
            objective
        )

        task["intent"] = intent

        # --------------------------------------------
        # 2. PLAN
        # --------------------------------------------

        task["plan"] = build_plan(
            objective,
            intent
        )

        # --------------------------------------------
        # 3. RESEARCH
        # --------------------------------------------

        research = []

        if request.research:

            research = await asyncio.to_thread(
                web_search,
                objective
            )

        task["research"] = research

        # --------------------------------------------
        # 4. PARALLEL MINDS
        # --------------------------------------------

        research_text = "\n".join(
            f"{x.get('title', '')} {x.get('url', '')}"
            for x in research
        )

        specialists = await run_specialists(
            objective,
            research_text
        )

        task["specialists"] = specialists

        # --------------------------------------------
        # 5. SYNTHESIS
        # --------------------------------------------

        final_answer = await asyncio.to_thread(
            synthesize,
            objective,
            research,
            specialists
        )

        if not final_answer:

            final_answer = fallback_result(
                objective,
                intent
            )

        task["answer"] = final_answer

        # --------------------------------------------
        # 6. VERIFICATION
        # --------------------------------------------

        verification = {
            "requested": request.verify,
            "research_items": len(research),
            "specialist_minds": len(specialists),
            "verified_at": now(),
            "note": (
                "AI Infinity separates research from synthesis. "
                "External search results are evidence candidates, "
                "not automatic proof."
            )
        }

        task["verification"] = verification

        # --------------------------------------------
        # 7. EXECUTION CHECKLIST
        # --------------------------------------------

        task["execution"] = {
            "ready": True,
            "next_steps": [
                "Review the synthesized answer.",
                "Execute the first concrete action.",
                "Verify the result.",
                "Create a follow-up task if needed."
            ]
        }

        # --------------------------------------------
        # 8. LEARNING
        # --------------------------------------------

        if request.remember:

            remember(
                "task",
                (
                    f"Objective: {objective}\n"
                    f"Result: {final_answer[:1800]}"
                )
            )

        # --------------------------------------------
        # COMPLETE
        # --------------------------------------------

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

<title>AI Infinity</title>

<style>

body {
    margin: 0;
    background: #080808;
    color: white;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 850px;
    margin: auto;
    padding: 25px;
}

h1 {
    font-size: 42px;
    margin-bottom: 5px;
}

.subtitle {
    color: #aaa;
    margin-bottom: 30px;
}

.card {
    background: #151515;
    border: 1px solid #292929;
    border-radius: 18px;
    padding: 20px;
    margin-bottom: 18px;
}

textarea {
    width: 100%;
    min-height: 150px;
    box-sizing: border-box;
    background: #090909;
    color: white;
    border: 1px solid #333;
    border-radius: 12px;
    padding: 15px;
    font-size: 16px;
    resize: vertical;
}

button {
    margin-top: 12px;
    width: 100%;
    padding: 15px;
    border: 0;
    border-radius: 12px;
    font-size: 17px;
    cursor: pointer;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    color: #ddd;
}

.status {
    color: #8cffb0;
}

.small {
    color: #888;
    font-size: 13px;
}

</style>
</head>

<body>

<div class="container">

<h1>∞ AI Infinity</h1>

<div class="subtitle">
Research → Parallel Minds → Reason → Verify → Execute → Learn
</div>

<div class="card">

<h2>Give AI Infinity a mission</h2>

<textarea id="objective"
placeholder="Example: Analyze my AI Infinity project and identify the most useful next development step."></textarea>

<button onclick="runTask()">
Run AI Infinity
</button>

</div>

<div class="card">

<h3>Status</h3>

<div id="status" class="status">
Ready
</div>

</div>

<div class="card">

<h3>Result</h3>

<pre id="result">
No task executed yet.
</pre>

</div>

</div>

<script>

async function runTask() {

    const objective =
        document.getElementById("objective").value.trim();

    if (!objective) {
        alert("Enter a mission first.");
        return;
    }

    document.getElementById("status").innerText =
        "AI Infinity is thinking...";

    document.getElementById("result").innerText =
        "Running research and specialist minds...";

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

        document.getElementById("status").innerText =
            data.status || "completed";

        document.getElementById("result").innerText =
            data.answer ||
            JSON.stringify(data, null, 2);

    } catch (error) {

        document.getElementById("status").innerText =
            "Error";

        document.getElementById("result").innerText =
            error.toString();

    }

}

</script>

</body>
</html>
"""


# ============================================================
# API ROUTES
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
        "engine": [
            "intent classification",
            "planning",
            "web research",
            "parallel specialist minds",
            "AI synthesis",
            "verification",
            "execution planning",
            "persistent memory"
        ]
    }


@app.post("/task")
async def create_task(request: TaskRequest):

    tid = task_id()

    TASKS[tid] = {
        "task_id": tid,
        "status": "queued",
        "objective": request.objective,
        "mode": request.mode,
        "created_at": now()
    }

    persist_state()

    result = await execute_task(
        tid,
        request
    )

    return result


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
        1
        for task in TASKS.values()
        if task.get("status") == "completed"
    )

    failed = sum(
        1
        for task in TASKS.values()
        if task.get("status") == "failed"
    )

    running = sum(
        1
        for task in TASKS.values()
        if task.get("status") == "running"
    )

    return {
        "application": APP_NAME,
        "version": VERSION,
        "total_tasks": len(TASKS),
        "completed": completed,
        "failed": failed,
        "running": running,
        "memory_items": len(MEMORY),
        "specialist_minds": len(SPECIALISTS),
        "research": True,
        "verification": True,
        "learning": True
    }


@app.get("/capabilities")
def capabilities():

    return {
        "name": APP_NAME,
        "version": VERSION,

        "core": [
            "Mission intake",
            "Intent classification",
            "Dynamic planning",
            "Web research",
            "Parallel specialist analysis",
            "AI synthesis",
            "Verification",
            "Execution planning",
            "Persistent memory",
            "Task history",
            "Health monitoring",
            "Browser interface"
        ],

        "specialist_minds": [
            name
            for name, _ in SPECIALISTS
        ],

        "free_first": True
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    load_state()

    print("=" * 70)
    print("∞ AI INFINITY")
    print(f"Version: {VERSION}")
    print("STATUS: ONLINE")
    print("")
    print("Mission")
    print("  ↓")
    print("Intent")
    print("  ↓")
    print("Research")
    print("  ↓")
    print("Parallel Minds")
    print("  ↓")
    print("Synthesis")
    print("  ↓")
    print("Verification")
    print("  ↓")
    print("Execution")
    print("  ↓")
    print("Learning")
    print("=" * 70)


# ============================================================
# DIRECT LOCAL START
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv("PORT", "8000")
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )
