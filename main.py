# AI Infinity — TARGET-3.0.0
# Free-first autonomous intelligence engine
# Render-friendly / JSON-safe / Web research / AI routing / Verification

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import asyncio
import json
import os
import re
import traceback
import uuid

import requests


# ============================================================
# CONFIG
# ============================================================

VERSION = "TARGET-3.0.0"
PROJECT = "AI Infinity"

BASE_DIR = Path("/tmp/ai_infinity")
DATA_DIR = BASE_DIR / "data"
TASK_DIR = BASE_DIR / "tasks"

BASE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
TASK_DIR.mkdir(parents=True, exist_ok=True)

MAX_MEMORY = 500
MAX_TASKS = 1000
MAX_SOURCES = 8
REQUEST_TIMEOUT = 15


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=PROJECT,
    description=(
        "AI Infinity free-first autonomous intelligence engine. "
        "Turns human objectives into research, reasoning, "
        "verification and reusable outcomes."
    ),
    version=VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# STATE
# ============================================================

TASKS: Dict[str, Dict[str, Any]] = {}
MEMORY: List[Dict[str, Any]] = []


# ============================================================
# MODELS
# ============================================================

class TaskRequest(BaseModel):
    command: Optional[str] = Field(default=None, max_length=10000)
    objective: Optional[str] = Field(default=None, max_length=10000)

    research: bool = True
    verify: bool = True
    remember: bool = True

    priority: str = "normal"

    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    category: str = "general"


# ============================================================
# UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_task_id() -> str:
    return f"task-{uuid.uuid4().hex[:12]}"


def safe_json(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            str(k): safe_json(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [safe_json(v) for v in value]

    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def save_task(task: Dict[str, Any]) -> None:
    try:
        path = TASK_DIR / f"{task['task_id']}.json"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                safe_json(task),
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


def save_memory() -> None:
    try:
        path = DATA_DIR / "memory.json"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                safe_json(MEMORY),
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


def add_memory(
    content: str,
    category: str = "general",
    source_task: Optional[str] = None,
) -> Dict[str, Any]:

    item = {
        "memory_id": f"mem-{uuid.uuid4().hex[:10]}",
        "content": content,
        "category": category,
        "source_task": source_task,
        "created_at": now_iso(),
    }

    MEMORY.append(item)

    if len(MEMORY) > MAX_MEMORY:
        del MEMORY[:-MAX_MEMORY]

    save_memory()

    return item


# ============================================================
# INTENT ENGINE
# ============================================================

def classify_intent(text: str) -> Dict[str, Any]:

    value = text.lower()

    groups = {
        "research": [
            "research",
            "investigate",
            "analyze",
            "analysis",
            "find",
            "compare",
            "study",
            "latest",
            "information",
            "evidence",
            "source",
        ],
        "build": [
            "build",
            "create",
            "make",
            "develop",
            "code",
            "implement",
            "deploy",
            "upgrade",
            "fix",
        ],
        "verify": [
            "verify",
            "check",
            "validate",
            "test",
            "confirm",
            "prove",
        ],
        "remember": [
            "remember",
            "save",
            "store",
            "learn",
            "reuse",
        ],
    }

    scores = {}

    for name, words in groups.items():
        scores[name] = sum(
            1 for word in words
            if word in value
        )

    primary = max(scores, key=scores.get)

    if all(score == 0 for score in scores.values()):
        primary = "general"

    return {
        "primary": primary,
        "scores": scores,
        "detected": [
            key
            for key, score in scores.items()
            if score > 0
        ],
    }


# ============================================================
# PLANNER
# ============================================================

def build_plan(
    objective: str,
    research: bool,
    verify: bool,
    remember: bool,
) -> List[Dict[str, Any]]:

    plan = []

    def add(name: str, action: str):
        plan.append({
            "step": len(plan) + 1,
            "name": name,
            "action": action,
            "status": "ready",
        })

    add(
        "Understand objective",
        "Parse the desired outcome, constraints and success criteria.",
    )

    if research:
        add(
            "Research",
            "Search public web sources and collect relevant evidence.",
        )

    add(
        "Reason",
        "Combine the objective, evidence and available intelligence.",
    )

    if verify:
        add(
            "Verify",
            "Check evidence, consistency and result quality.",
        )

    if remember:
        add(
            "Learn",
            "Store reusable knowledge from the completed mission.",
        )

    add(
        "Deliver",
        "Return a structured outcome with evidence and status.",
    )

    return plan


# ============================================================
# WEB RESEARCH
# ============================================================

def clean_text(text: str, limit: int = 5000) -> str:

    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()[:limit]


def search_web(query: str) -> List[Dict[str, Any]]:

    results = []

    try:
        url = "https://html.duckduckgo.com/html/"

        response = requests.get(
            url,
            params={"q": query},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(AI Infinity free-first research)"
                )
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        html = response.text

        pattern = re.compile(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.I | re.S,
        )

        matches = pattern.findall(html)

        for link, title in matches[:MAX_SOURCES]:

            title = clean_text(title, 300)

            if not link.startswith("http"):
                continue

            results.append({
                "title": title,
                "url": link,
            })

    except Exception as exc:

        return [{
            "status": "research_unavailable",
            "error": type(exc).__name__,
            "message": str(exc),
        }]

    return results


def fetch_source(url: str) -> Dict[str, Any]:

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(AI Infinity source reader)"
                )
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        text = clean_text(
            response.text,
            7000,
        )

        return {
            "url": url,
            "status": "ok",
            "content": text,
        }

    except Exception as exc:

        return {
            "url": url,
            "status": "failed",
            "error": type(exc).__name__,
            "message": str(exc),
        }


def perform_research(
    objective: str,
) -> Dict[str, Any]:

    search_results = search_web(objective)

    usable = [
        item
        for item in search_results
        if item.get("url")
    ]

    sources = []

    for item in usable[:MAX_SOURCES]:

        source = fetch_source(
            item["url"]
        )

        sources.append({
            "title": item.get("title"),
            "url": item.get("url"),
            "status": source.get("status"),
            "content": source.get("content", ""),
        })

    return {
        "query": objective,
        "search_results": search_results,
        "sources": sources,
        "source_count": len(sources),
    }


# ============================================================
# AI PROVIDER ROUTER
# ============================================================

def call_openai_compatible(
    prompt: str,
) -> Optional[str]:

    base_url = os.getenv(
        "AI_BASE_URL",
        "",
    ).strip()

    api_key = os.getenv(
        "AI_API_KEY",
        "",
    ).strip()

    model = os.getenv(
        "AI_MODEL",
        "",
    ).strip()

    if not base_url or not api_key or not model:
        return None

    try:

        endpoint = base_url.rstrip("/")

        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"

        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                "temperature": 0.2,
            },
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        choices = data.get("choices", [])

        if choices:

            message = choices[0].get(
                "message",
                {},
            )

            content = message.get(
                "content"
            )

            if content:
                return str(content)

    except Exception:
        return None

    return None


def call_pollinations(
    prompt: str,
) -> Optional[str]:

    try:

        response = requests.get(
            "https://text.pollinations.ai/"
            + requests.utils.quote(prompt),
            timeout=45,
        )

        response.raise_for_status()

        text = response.text.strip()

        if text:
            return text

    except Exception:
        return None

    return None


def deterministic_reasoning(
    objective: str,
    research: Dict[str, Any],
) -> str:

    sources = research.get(
        "sources",
        [],
    )

    usable = [
        item
        for item in sources
        if item.get("status") == "ok"
    ]

    if usable:

        evidence = []

        for item in usable[:5]:

            evidence.append(
                f"- {item.get('title', 'Source')}: "
                f"{item.get('url')}"
            )

        return (
            f"AI Infinity analyzed the objective:\n\n"
            f"{objective}\n\n"
            f"Research produced {len(usable)} usable "
            f"public source(s).\n\n"
            f"Sources:\n"
            + "\n".join(evidence)
        )

    return (
        f"AI Infinity processed the objective:\n\n"
        f"{objective}\n\n"
        f"No usable external sources were available, "
        f"so the result is based on the local reasoning core."
    )


def reason(
    objective: str,
    research: Dict[str, Any],
) -> Dict[str, Any]:

    source_text = ""

    for source in research.get("sources", [])[:5]:

        content = source.get(
            "content",
            "",
        )

        if content:
            source_text += (
                "\n\nSOURCE: "
                + str(source.get("url"))
                + "\n"
                + content[:4000]
            )

    prompt = f"""
You are the reasoning engine of AI Infinity.

Objective:
{objective}

Public research evidence:
{source_text[:18000]}

Produce a concise, useful result.

Rules:
1. Answer the objective directly.
2. Separate evidence from assumptions.
3. Do not invent facts.
4. Mention uncertainty when evidence is insufficient.
5. Prefer actionable conclusions.
"""

    provider = "deterministic"

    result = call_openai_compatible(prompt)

    if result:
        provider = "openai-compatible"

    if not result:

        result = call_pollinations(prompt)

        if result:
            provider = "pollinations"

    if not result:

        result = deterministic_reasoning(
            objective,
            research,
        )

    return {
        "provider": provider,
        "answer": result,
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_result(
    objective: str,
    research: Dict[str, Any],
    reasoning: Dict[str, Any],
) -> Dict[str, Any]:

    answer = str(
        reasoning.get(
            "answer",
            "",
        )
    )

    checks = {
        "objective_present": bool(
            objective.strip()
        ),
        "answer_present": bool(
            answer.strip()
        ),
        "research_attempted": bool(
            research
        ),
        "sources_available": (
            research.get(
                "source_count",
                0,
            ) > 0
        ),
        "no_empty_result": len(
            answer.strip()
        ) > 20,
    }

    passed = sum(
        1
        for value in checks.values()
        if value
    )

    total = len(checks)

    return {
        "status": (
            "verified"
            if passed == total
            else "partially_verified"
        ),
        "checks": checks,
        "passed": passed,
        "total": total,
    }


# ============================================================
# SYNCHRONOUS EXECUTION CORE
# ============================================================

def execute_sync(
    task: Dict[str, Any],
) -> Dict[str, Any]:

    objective = task["objective"]

    intent = classify_intent(
        objective
    )

    plan = build_plan(
        objective=objective,
        research=task["research"],
        verify=task["verify"],
        remember=task["remember"],
    )

    research = {}

    if task["research"]:
        research = perform_research(
            objective
        )
    else:
        research = {
            "query": objective,
            "search_results": [],
            "sources": [],
            "source_count": 0,
            "skipped": True,
        }

    reasoning = reason(
        objective,
        research,
    )

    verification = {}

    if task["verify"]:

        verification = verify_result(
            objective,
            research,
            reasoning,
        )

    else:

        verification = {
            "status": "disabled",
            "checks": {},
        }

    return {
        "objective": objective,

        "intent": intent,

        "architecture": {
            "input": "Human Intent",
            "controller": "AI Infinity Intent Router",
            "planner": "Adaptive Mission Planner",
            "research": "Free Web Research Layer",
            "reasoner": "Provider Router + Reasoning Core",
            "verification": "Outcome Verification Layer",
            "memory": "Reusable Intelligence Layer",
            "delivery": "Structured JSON Outcome",
        },

        "plan": plan,

        "research": research,

        "reasoning": reasoning,

        "verification": verification,

        "success_criteria": [
            "Objective understood",
            "Plan generated",
            "Research attempted when enabled",
            "Reasoning performed",
            "Verification applied when enabled",
            "Reusable knowledge stored when enabled",
            "Structured result returned",
        ],

        "free_first": True,

        "status": "completed",
    }


# ============================================================
# TASK RUNNER
# ============================================================

async def run_task(
    task: Dict[str, Any],
) -> None:

    task_value = task["task_id"]

    try:

        task["status"] = "running"
        task["started_at"] = now_iso()

        save_task(task)

        result = await asyncio.to_thread(
            execute_sync,
            task,
        )

        task["result"] = safe_json(
            result
        )

        if task["remember"]:

            summary = (
                f"Objective: {task['objective']}\n"
                f"Intent: "
                f"{result['intent']['primary']}\n"
                f"Provider: "
                f"{result['reasoning']['provider']}\n"
                f"Verification: "
                f"{result['verification']['status']}"
            )

            add_memory(
                content=summary,
                category="completed_task",
                source_task=task_value,
            )

        task["status"] = "completed"
        task["completed_at"] = now_iso()

        save_task(task)

    except Exception as exc:

        task["status"] = "failed"

        task["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "trace": traceback.format_exc()[-4000:],
        }

        task["completed_at"] = now_iso()

        save_task(task)


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():

    return {
        "project": PROJECT,
        "version": VERSION,
        "status": "online",
        "message": "AI Infinity Core is online.",
        "capabilities": [
            "intent-routing",
            "adaptive-planning",
            "web-research",
            "source-fetching",
            "ai-provider-routing",
            "reasoning",
            "verification",
            "memory",
            "autonomous-task-execution",
            "structured-json",
            "free-first",
        ],
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {
        "status": "healthy",
        "project": PROJECT,
        "version": VERSION,
        "timestamp": now_iso(),
        "tasks": len(TASKS),
        "memory_items": len(MEMORY),
    }


# ============================================================
# VERSION
# ============================================================

@app.get("/version")
async def version():

    return {
        "project": PROJECT,
        "version": VERSION,
        "target": VERSION,
        "status": "production",
    }


# ============================================================
# CREATE TASK
# ============================================================

@app.post("/task")
@app.post("/tasks")
async def create_task(
    request: TaskRequest,
):

    objective = (
        request.command
        or request.objective
    )

    if not objective:

        raise HTTPException(
            status_code=422,
            detail=(
                "Either 'command' or "
                "'objective' is required."
            ),
        )

    new_id = make_task_id()

    task = {
        "task_id": new_id,
        "status": "queued",
        "objective": objective,
        "research": request.research,
        "verify": request.verify,
        "remember": request.remember,
        "priority": request.priority,
        "metadata": safe_json(
            request.metadata
        ),
        "created_at": now_iso(),
        "result": None,
        "error": None,
    }

    TASKS[new_id] = task

    if len(TASKS) > MAX_TASKS:

        old_ids = list(TASKS.keys())[
            :len(TASKS) - MAX_TASKS
        ]

        for old_id in old_ids:
            TASKS.pop(old_id, None)

    save_task(task)

    asyncio.create_task(
        run_task(task)
    )

    return JSONResponse(
        status_code=202,
        content={
            "task_id": new_id,
            "status": "queued",
            "version": VERSION,
            "message": "AI Infinity task accepted.",
            "poll": f"/task/{new_id}",
        },
    )


# ============================================================
# GET TASK
# ============================================================

@app.get("/task/{task_id_value}")
@app.get("/tasks/{task_id_value}")
async def get_task(
    task_id_value: str,
):

    task = TASKS.get(
        task_id_value
    )

    if not task:

        path = (
            TASK_DIR
            / f"{task_id_value}.json"
        )

        if path.exists():

            try:

                with open(
                    path,
                    "r",
                    encoding="utf-8",
                ) as f:
                    task = json.load(f)

                TASKS[
                    task_id_value
                ] = task

            except Exception:

                task = None

    if not task:

        raise HTTPException(
            status_code=404,
            detail="Task not found.",
        )

    return safe_json(task)


# ============================================================
# LIST TASKS
# ============================================================

@app.get("/tasks")
async def list_tasks():

    return {
        "project": PROJECT,
        "version": VERSION,
        "count": len(TASKS),
        "tasks": safe_json(
            list(TASKS.values())
        ),
    }


# ============================================================
# MEMORY
# ============================================================

@app.post("/memory")
async def create_memory(
    request: MemoryRequest,
):

    item = add_memory(
        content=request.content,
        category=request.category,
    )

    return {
        "status": "stored",
        "memory": safe_json(item),
    }


@app.get("/memory")
async def get_memory():

    return {
        "project": PROJECT,
        "version": VERSION,
        "count": len(MEMORY),
        "memory": safe_json(MEMORY),
    }


@app.get("/memory/search")
async def search_memory(
    q: str = "",
):

    query = q.strip().lower()

    if not query:

        return {
            "query": q,
            "count": 0,
            "results": [],
        }

    results = []

    for item in MEMORY:

        content = str(
            item.get(
                "content",
                "",
            )
        ).lower()

        if query in content:
            results.append(item)

    return {
        "query": q,
        "count": len(results),
        "results": safe_json(results),
    }


# ============================================================
# INTENT
# ============================================================

@app.post("/intent")
async def detect_intent(
    request: TaskRequest,
):

    objective = (
        request.command
        or request.objective
    )

    if not objective:

        raise HTTPException(
            status_code=422,
            detail="Objective is required.",
        )

    return {
        "project": PROJECT,
        "version": VERSION,
        "objective": objective,
        "intent": classify_intent(
            objective
        ),
    }


# ============================================================
# PLAN
# ============================================================

@app.post("/plan")
async def generate_plan(
    request: TaskRequest,
):

    objective = (
        request.command
        or request.objective
    )

    if not objective:

        raise HTTPException(
            status_code=422,
            detail="Objective is required.",
        )

    return {
        "project": PROJECT,
        "version": VERSION,
        "objective": objective,
        "plan": build_plan(
            objective=objective,
            research=request.research,
            verify=request.verify,
            remember=request.remember,
        ),
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():

    return {
        "project": PROJECT,
        "version": VERSION,

        "active": [
            "Human Intent",
            "Intent Classification",
            "Adaptive Planning",
            "Public Web Research",
            "Source Fetching",
            "AI Provider Routing",
            "Reasoning",
            "Verification",
            "Memory",
            "Autonomous Background Tasks",
            "JSON-Safe API",
        ],

        "optional_providers": [
            "OpenAI-compatible API",
            "Pollinations",
        ],

        "free_first": True,

        "architecture": {
            "layer_1": "Human Intent",
            "layer_2": "Context",
            "layer_3": "Intent Router",
            "layer_4": "Mission Planner",
            "layer_5": "Research + Execution",
            "layer_6": "Reasoning",
            "layer_7": "Verification",
            "layer_8": "Reusable Memory",
            "layer_9": "Outcome",
        },
    }


# ============================================================
# SYSTEM STATUS
# ============================================================

@app.get("/status")
async def system_status():

    return {
        "project": PROJECT,
        "version": VERSION,
        "status": "online",
        "engine": "TARGET-3.0.0",
        "research": "enabled",
        "verification": "enabled",
        "memory": "enabled",
        "ai_router": "enabled",
        "free_first": True,
    }


# ============================================================
# ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request,
    exc,
):

    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "project": PROJECT,
            "version": VERSION,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():

    global MEMORY

    memory_file = (
        DATA_DIR
        / "memory.json"
    )

    if memory_file.exists():

        try:

            with open(
                memory_file,
                "r",
                encoding="utf-8",
            ) as f:
                loaded = json.load(f)

            if isinstance(
                loaded,
                list,
            ):
                MEMORY = loaded[
                    -MAX_MEMORY:
                ]

        except Exception:

            MEMORY = []

    print(
        f"{PROJECT} {VERSION} ONLINE"
    )


# ============================================================
# LOCAL RUN
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            "8000",
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
