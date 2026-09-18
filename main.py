# AI Infinity — TARGET-2.5.0
# Production-ready single-file FastAPI core
# Free-first / Render-friendly / JSON-safe / autonomous task router

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
import uuid
import traceback


# ============================================================
# AI INFINITY CONFIG
# ============================================================

VERSION = "TARGET-2.5.0"
PROJECT = "AI Infinity"

BASE_DIR = Path("/tmp/ai_infinity")
DATA_DIR = BASE_DIR / "data"
TASK_DIR = BASE_DIR / "tasks"

BASE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
TASK_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    description=(
        "AI Infinity autonomous intelligence router. "
        "Turns human objectives into structured, researchable, "
        "verifiable and reusable tasks."
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
# IN-MEMORY STATE
# ============================================================

TASKS: Dict[str, Dict[str, Any]] = {}
MEMORY: List[Dict[str, Any]] = []

MAX_MEMORY = 500
MAX_TASKS = 1000


# ============================================================
# REQUEST MODELS
# ============================================================

class TaskRequest(BaseModel):
    command: Optional[str] = Field(
        default=None,
        max_length=10000
    )

    objective: Optional[str] = Field(
        default=None,
        max_length=10000
    )

    research: bool = True
    verify: bool = True
    remember: bool = True

    priority: str = "normal"

    metadata: Dict[str, Any] = Field(
        default_factory=dict
    )


class MemoryRequest(BaseModel):
    content: str = Field(
        min_length=1,
        max_length=20000
    )

    category: str = "general"


# ============================================================
# UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_id() -> str:
    return f"task-{uuid.uuid4().hex[:12]}"


def safe_json(value: Any) -> Any:
    """
    Convert arbitrary Python objects into JSON-safe data.
    Prevents errors such as:

        Unexpected token 'I', "Internal S"... is not valid JSON
    """

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
        return [
            safe_json(v)
            for v in value
        ]

    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def save_task(task: Dict[str, Any]) -> None:
    """
    Persist task state locally.
    Render filesystem is temporary, but this gives the
    running instance recovery/state visibility.
    """

    try:
        path = TASK_DIR / f"{task['task_id']}.json"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                safe_json(task),
                f,
                ensure_ascii=False,
                indent=2
            )
    except Exception:
        pass


def add_memory(
    content: str,
    category: str = "general",
    source_task: Optional[str] = None
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

    try:
        path = DATA_DIR / "memory.json"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                safe_json(MEMORY),
                f,
                ensure_ascii=False,
                indent=2
            )
    except Exception:
        pass

    return item


# ============================================================
# INTENT ENGINE
# ============================================================

def classify_intent(text: str) -> Dict[str, Any]:

    text_lower = text.lower()

    research_words = [
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
    ]

    build_words = [
        "build",
        "create",
        "make",
        "develop",
        "code",
        "implement",
        "deploy",
        "upgrade",
        "fix",
    ]

    verify_words = [
        "verify",
        "check",
        "validate",
        "test",
        "confirm",
        "prove",
    ]

    remember_words = [
        "remember",
        "save",
        "store",
        "learn",
        "reuse",
    ]

    research_score = sum(
        1 for x in research_words if x in text_lower
    )

    build_score = sum(
        1 for x in build_words if x in text_lower
    )

    verify_score = sum(
        1 for x in verify_words if x in text_lower
    )

    remember_score = sum(
        1 for x in remember_words if x in text_lower
    )

    scores = {
        "research": research_score,
        "build": build_score,
        "verify": verify_score,
        "remember": remember_score,
    }

    primary = max(
        scores,
        key=scores.get
    )

    if all(v == 0 for v in scores.values()):
        primary = "general"

    return {
        "primary": primary,
        "scores": scores,
        "detected": [
            key for key, value in scores.items()
            if value > 0
        ],
    }


# ============================================================
# TASK PLANNER
# ============================================================

def build_plan(
    objective: str,
    research: bool,
    verify: bool,
    remember: bool
) -> List[Dict[str, Any]]:

    plan: List[Dict[str, Any]] = []

    plan.append({
        "step": 1,
        "name": "Understand objective",
        "action": "Parse the requested outcome, constraints and success criteria.",
        "status": "ready",
    })

    if research:
        plan.append({
            "step": len(plan) + 1,
            "name": "Research",
            "action": "Gather relevant information and candidate approaches.",
            "status": "ready",
        })

    plan.append({
        "step": len(plan) + 1,
        "name": "Reason",
        "action": "Evaluate available information and construct a solution.",
        "status": "ready",
    })

    if verify:
        plan.append({
            "step": len(plan) + 1,
            "name": "Verify",
            "action": "Check consistency, completeness and success criteria.",
            "status": "ready",
        })

    if remember:
        plan.append({
            "step": len(plan) + 1,
            "name": "Learn",
            "action": "Store reusable task knowledge.",
            "status": "ready",
        })

    plan.append({
        "step": len(plan) + 1,
        "name": "Deliver",
        "action": "Return structured results.",
        "status": "ready",
    })

    return plan


# ============================================================
# LOCAL REASONING CORE
# ============================================================

async def execute_core(
    task: Dict[str, Any]
) -> Dict[str, Any]:

    objective = task["objective"]

    intent = classify_intent(objective)

    plan = build_plan(
        objective=objective,
        research=task["research"],
        verify=task["verify"],
        remember=task["remember"],
    )

    await asyncio.sleep(0)

    result = {
        "objective": objective,

        "intent": intent,

        "architecture": {
            "input": "Human objective",
            "controller": "AI Infinity Intent Router",
            "planner": "Adaptive Mission Planner",
            "reasoner": "Multi-stage Reasoning Core",
            "verification": (
                "Enabled"
                if task["verify"]
                else "Disabled"
            ),
            "memory": (
                "Enabled"
                if task["remember"]
                else "Disabled"
            ),
        },

        "plan": plan,

        "success_criteria": [
            "Objective understood",
            "Task decomposed",
            "Relevant reasoning performed",
            "Verification applied when enabled",
            "Reusable knowledge stored when enabled",
            "Structured response returned",
        ],

        "next_action": (
            "Execute the generated plan using available "
            "research, tools and external services."
        ),

        "free_first": True,

        "status": "planned",
    }

    return result


# ============================================================
# TASK EXECUTION
# ============================================================

async def run_task(
    task: Dict[str, Any]
) -> None:

    task_id_value = task["task_id"]

    try:

        task["status"] = "running"
        task["started_at"] = now_iso()

        save_task(task)

        result = await execute_core(task)

        task["result"] = safe_json(result)

        if task["remember"]:

            memory_text = (
                f"AI Infinity task objective: "
                f"{task['objective']}"
            )

            add_memory(
                content=memory_text,
                category="task",
                source_task=task_id_value,
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
            "task-planning",
            "research-routing",
            "verification",
            "memory",
            "structured-json",
            "free-first",
            "autonomous-task-execution",
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
async def create_task(request: TaskRequest):

    objective = (
        request.command
        or request.objective
    )

    if not objective:
        raise HTTPException(
            status_code=422,
            detail="Either 'command' or 'objective' is required."
        )

    new_id = task_id()

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

    # Prevent unlimited memory growth.
    if len(TASKS) > MAX_TASKS:
        oldest = list(TASKS.keys())[
            :len(TASKS) - MAX_TASKS
        ]

        for old_id in oldest:
            TASKS.pop(old_id, None)

    save_task(task)

    # Start asynchronously.
    asyncio.create_task(
        run_task(task)
    )

    return JSONResponse(
        status_code=202,
        content=safe_json({
            "task_id": new_id,
            "status": "queued",
            "version": VERSION,
            "message": "AI Infinity task accepted.",
            "poll": f"/task/{new_id}",
        })
    )


# ============================================================
# TASK STATUS
# ============================================================

@app.get("/task/{task_id_value}")
@app.get("/tasks/{task_id_value}")
async def get_task(task_id_value: str):

    task = TASKS.get(task_id_value)

    if not task:

        # Try local recovery.
        path = TASK_DIR / f"{task_id_value}.json"

        if path.exists():

            try:

                with open(
                    path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    task = json.load(f)

                TASKS[task_id_value] = task

            except Exception:

                task = None

    if not task:

        raise HTTPException(
            status_code=404,
            detail="Task not found."
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
# MEMORY — ADD
# ============================================================

@app.post("/memory")
async def create_memory(
    request: MemoryRequest
):

    item = add_memory(
        content=request.content,
        category=request.category,
    )

    return {
        "status": "stored",
        "memory": safe_json(item),
    }


# ============================================================
# MEMORY — READ
# ============================================================

@app.get("/memory")
async def get_memory():

    return {
        "project": PROJECT,
        "count": len(MEMORY),
        "memory": safe_json(MEMORY),
    }


# ============================================================
# MEMORY SEARCH
# ============================================================

@app.get("/memory/search")
async def search_memory(
    q: str = ""
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
            item.get("content", "")
        ).lower()

        if query in content:

            results.append(item)

    return {
        "query": q,
        "count": len(results),
        "results": safe_json(results),
    }


# ============================================================
# INTENT API
# ============================================================

@app.post("/intent")
async def detect_intent(
    request: TaskRequest
):

    objective = (
        request.command
        or request.objective
    )

    if not objective:

        raise HTTPException(
            status_code=422,
            detail="Objective is required."
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
# PLAN API
# ============================================================

@app.post("/plan")
async def generate_plan(
    request: TaskRequest
):

    objective = (
        request.command
        or request.objective
    )

    if not objective:

        raise HTTPException(
            status_code=422,
            detail="Objective is required."
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
# SYSTEM CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():

    return {
        "project": PROJECT,
        "version": VERSION,

        "core": [
            "Human Intent",
            "Intent Classification",
            "Mission Planning",
            "Task Routing",
            "Verification",
            "Memory",
            "Failure Reporting",
            "JSON Safety",
        ],

        "ready_for": [
            "Web research",
            "External AI models",
            "Free APIs",
            "GitHub automation",
            "Tool execution",
            "Long-running missions",
            "Multi-agent orchestration",
            "Future model routing",
        ],

        "architecture": {
            "layer_1": "Human Intent",
            "layer_2": "Universal Context",
            "layer_3": "Intent Router",
            "layer_4": "Mission Planner",
            "layer_5": "Execution Layer",
            "layer_6": "Verification Layer",
            "layer_7": "Memory Layer",
            "layer_8": "Outcome Layer",
        },
    }


# ============================================================
# ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request,
    exc
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

    # Attempt to restore locally persisted memory.
    global MEMORY

    memory_file = DATA_DIR / "memory.json"

    if memory_file.exists():

        try:

            with open(
                memory_file,
                "r",
                encoding="utf-8"
            ) as f:

                loaded = json.load(f)

            if isinstance(loaded, list):

                MEMORY = loaded[-MAX_MEMORY:]

        except Exception:

            MEMORY = []

    print(
        f"{PROJECT} {VERSION} ONLINE"
    )


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            "8000"
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
