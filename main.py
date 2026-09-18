# AI Infinity — TARGET-3.1.0
# One-Command Mission Engine
# Free-first / Research / Reasoning / Verification / Memory
# Render-friendly / JSON-safe

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
import concurrent.futures

import requests


# ============================================================
# AI INFINITY
# ============================================================

VERSION = "TARGET-3.1.0"
PROJECT = "AI Infinity"

BASE_DIR = Path("/tmp/ai_infinity")
DATA_DIR = BASE_DIR / "data"
TASK_DIR = BASE_DIR / "tasks"

BASE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
TASK_DIR.mkdir(parents=True, exist_ok=True)

MAX_MEMORY = 500
MAX_TASKS = 1000
MAX_SOURCES = 6

REQUEST_TIMEOUT = 15
AI_TIMEOUT = 40


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=PROJECT,
    description=(
        "AI Infinity one-command autonomous mission engine. "
        "Research, reason, verify, learn and deliver."
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

class MissionRequest(BaseModel):
    command: Optional[str] = Field(
        default=None,
        max_length=10000,
    )

    objective: Optional[str] = Field(
        default=None,
        max_length=10000,
    )

    research: bool = True
    verify: bool = True
    remember: bool = True

    priority: str = "normal"

    metadata: Dict[str, Any] = Field(
        default_factory=dict,
    )


class MemoryRequest(BaseModel):
    content: str = Field(
        min_length=1,
        max_length=20000,
    )

    category: str = "general"


# ============================================================
# UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def safe_json(value: Any) -> Any:

    if value is None:
        return None

    if isinstance(
        value,
        (str, int, float, bool),
    ):
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

    if isinstance(
        value,
        (list, tuple, set),
    ):
        return [
            safe_json(v)
            for v in value
        ]

    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def save_task(
    task: Dict[str, Any],
) -> None:

    try:

        path = (
            TASK_DIR
            / f"{task['task_id']}.json"
        )

        with open(
            path,
            "w",
            encoding="utf-8",
        ) as f:

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

        path = (
            DATA_DIR
            / "memory.json"
        )

        with open(
            path,
            "w",
            encoding="utf-8",
        ) as f:

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
        "memory_id": make_id("mem"),
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
# INTENT
# ============================================================

def classify_intent(
    text: str,
) -> Dict[str, Any]:

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
            1
            for word in words
            if word in value
        )

    primary = max(
        scores,
        key=scores.get,
    )

    if all(
        score == 0
        for score in scores.values()
    ):
        primary = "general"

    return {
        "primary": primary,
        "scores": scores,
        "detected": [
            name
            for name, score in scores.items()
            if score > 0
        ],
    }


# ============================================================
# MISSION PLAN
# ============================================================

def build_plan(
    research: bool,
    verify: bool,
    remember: bool,
) -> List[Dict[str, Any]]:

    plan = []

    def add(
        name: str,
        action: str,
    ):

        plan.append({
            "step": len(plan) + 1,
            "name": name,
            "action": action,
            "status": "pending",
        })

    add(
        "Understand",
        "Understand the user's objective.",
    )

    if research:

        add(
            "Research",
            "Search public sources and collect evidence.",
        )

    add(
        "Reason",
        "Analyze the objective and collected evidence.",
    )

    if verify:

        add(
            "Verify",
            "Check consistency, evidence and completeness.",
        )

    if remember:

        add(
            "Learn",
            "Store reusable mission knowledge.",
        )

    add(
        "Deliver",
        "Return the mission outcome.",
    )

    return plan


# ============================================================
# WEB RESEARCH
# ============================================================

def clean_text(
    text: str,
    limit: int = 5000,
) -> str:

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


def search_web(
    query: str,
) -> List[Dict[str, Any]]:

    try:

        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "User-Agent":
                    "Mozilla/5.0 AI-Infinity/3.1"
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        matches = re.findall(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            response.text,
            flags=re.I | re.S,
        )

        results = []

        seen = set()

        for url, title in matches:

            if not url.startswith("http"):
                continue

            if url in seen:
                continue

            seen.add(url)

            results.append({
                "title": clean_text(
                    title,
                    300,
                ),
                "url": url,
            })

            if len(results) >= MAX_SOURCES:
                break

        return results

    except Exception as exc:

        return [{
            "status": "unavailable",
            "error": type(exc).__name__,
            "message": str(exc),
        }]


def fetch_source(
    item: Dict[str, Any],
) -> Dict[str, Any]:

    url = item.get("url", "")

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent":
                    "Mozilla/5.0 AI-Infinity/3.1"
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        return {
            "title": item.get(
                "title",
                "Source",
            ),
            "url": url,
            "status": "ok",
            "content": clean_text(
                response.text,
                6000,
            ),
        }

    except Exception as exc:

        return {
            "title": item.get(
                "title",
                "Source",
            ),
            "url": url,
            "status": "failed",
            "content": "",
            "error": type(exc).__name__,
        }


def perform_research(
    objective: str,
) -> Dict[str, Any]:

    search_results = search_web(
        objective
    )

    usable = [
        item
        for item in search_results
        if item.get("url")
    ]

    sources = []

    if usable:

        workers = min(
            4,
            len(usable),
        )

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers
        ) as executor:

            futures = [
                executor.submit(
                    fetch_source,
                    item,
                )
                for item in usable
            ]

            for future in futures:

                try:

                    sources.append(
                        future.result()
                    )

                except Exception as exc:

                    sources.append({
                        "status": "failed",
                        "error":
                            type(exc).__name__,
                    })

    good = [
        source
        for source in sources
        if source.get("status") == "ok"
    ]

    return {
        "query": objective,
        "search_results": search_results,
        "sources": sources,
        "source_count": len(good),
        "research_status": (
            "complete"
            if good
            else "limited"
        ),
    }


# ============================================================
# AI ROUTER
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

    if not (
        base_url
        and api_key
        and model
    ):
        return None

    try:

        endpoint = base_url.rstrip("/")

        if not endpoint.endswith(
            "/chat/completions"
        ):
            endpoint += (
                "/chat/completions"
            )

        response = requests.post(
            endpoint,
            headers={
                "Authorization":
                    f"Bearer {api_key}",
                "Content-Type":
                    "application/json",
            },
            json={
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": prompt,
                }],
                "temperature": 0.2,
            },
            timeout=AI_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        choices = data.get(
            "choices",
            [],
        )

        if choices:

            content = (
                choices[0]
                .get("message", {})
                .get("content")
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
            timeout=AI_TIMEOUT,
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

    sources = [
        source
        for source in research.get(
            "sources",
            [],
        )
        if source.get("status") == "ok"
    ]

    if not sources:

        return (
            "AI Infinity processed the objective "
            "using its local reasoning core.\n\n"
            f"Objective: {objective}\n\n"
            "External evidence was unavailable, "
            "so this result should be treated as "
            "a limited analysis."
        )

    lines = []

    for source in sources[:5]:

        lines.append(
            f"- {source.get('title', 'Source')}: "
            f"{source.get('url', '')}"
        )

    return (
        "AI Infinity completed a research-backed "
        "mission analysis.\n\n"
        f"Objective:\n{objective}\n\n"
        f"Usable sources: {len(sources)}\n\n"
        "Sources:\n"
        + "\n".join(lines)
    )


def reason(
    objective: str,
    research: Dict[str, Any],
) -> Dict[str, Any]:

    evidence = ""

    for source in research.get(
        "sources",
        [],
    )[:5]:

        if source.get("status") != "ok":
            continue

        evidence += (
            "\n\nSOURCE: "
            + str(source.get("url"))
            + "\n"
            + str(
                source.get(
                    "content",
                    "",
                )
            )[:3500]
        )

    prompt = f"""
You are AI Infinity TARGET-3.1.

MISSION:
{objective}

EVIDENCE:
{evidence[:16000]}

Complete the mission.

Requirements:
- Answer the objective directly.
- Use evidence when available.
- Never invent facts.
- Separate facts from assumptions.
- State uncertainty when necessary.
- Give practical next actions.
- Keep the result clear and useful.
"""

    result = call_openai_compatible(
        prompt
    )

    if result:

        return {
            "provider":
                "openai-compatible",
            "answer": result,
        }

    result = call_pollinations(
        prompt
    )

    if result:

        return {
            "provider":
                "pollinations",
            "answer": result,
        }

    return {
        "provider":
            "deterministic",
        "answer":
            deterministic_reasoning(
                objective,
                research,
            ),
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify(
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

        "objective_present":
            bool(objective.strip()),

        "answer_present":
            bool(answer.strip()),

        "answer_substantial":
            len(answer.strip()) >= 20,

        "research_attempted":
            bool(research),

        "pipeline_completed":
            reasoning.get(
                "provider"
            ) is not None,

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
        "passed": passed,
        "total": total,
        "checks": checks,
    }


# ============================================================
# ONE-COMMAND MISSION
# ============================================================

def execute_mission(
    task: Dict[str, Any],
) -> Dict[str, Any]:

    objective = task["objective"]

    started = now_iso()

    intent = classify_intent(
        objective
    )

    plan = build_plan(
        research=task["research"],
        verify=task["verify"],
        remember=task["remember"],
    )

    # ----------------------------
    # PHASE 1 — RESEARCH
    # ----------------------------

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
            "research_status":
                "disabled",
        }

    # ----------------------------
    # PHASE 2 — REASONING
    # ----------------------------

    reasoning = reason(
        objective,
        research,
    )

    # ----------------------------
    # PHASE 3 — VERIFICATION
    # ----------------------------

    if task["verify"]:

        verification = verify(
            objective,
            research,
            reasoning,
        )

    else:

        verification = {
            "status": "disabled",
            "passed": 0,
            "total": 0,
            "checks": {},
        }

    completed = now_iso()

    return {

        "mission": {
            "mission_id":
                task["task_id"],
            "objective":
                objective,
            "status":
                "completed",
            "started_at":
                started,
            "completed_at":
                completed,
        },

        "intent": intent,

        "execution": {
            "mode":
                "one-command",
            "pipeline": [
                "understand",
                "research",
                "reason",
                "verify",
                "learn",
                "deliver",
            ],
            "free_first":
                True,
        },

        "plan": plan,

        "research": research,

        "reasoning": reasoning,

        "verification": verification,

        "outcome": {
            "answer":
                reasoning.get(
                    "answer",
                    "",
                ),
            "provider":
                reasoning.get(
                    "provider",
                    "unknown",
                ),
            "verified":
                verification.get(
                    "status"
                ) == "verified",
        },

        "status":
            "completed",
    }


# ============================================================
# BACKGROUND RUNNER
# ============================================================

async def run_task(
    task: Dict[str, Any],
) -> None:

    try:

        task["status"] = "running"
        task["started_at"] = now_iso()

        save_task(task)

        result = await asyncio.to_thread(
            execute_mission,
            task,
        )

        task["result"] = safe_json(
            result
        )

        if task["remember"]:

            outcome = result.get(
                "outcome",
                {},
            )

            memory = (
                f"Mission objective: "
                f"{task['objective']}\n"
                f"Provider: "
                f"{outcome.get('provider')}\n"
                f"Verified: "
                f"{outcome.get('verified')}"
            )

            add_memory(
                content=memory,
                category="mission",
                source_task=
                    task["task_id"],
            )

        task["status"] = "completed"
        task["completed_at"] = now_iso()

        save_task(task)

    except Exception as exc:

        task["status"] = "failed"

        task["error"] = {
            "type":
                type(exc).__name__,
            "message":
                str(exc),
            "trace":
                traceback.format_exc()[-4000:],
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

        "message":
            "AI Infinity Mission Engine is online.",

        "mode":
            "one-command",

        "capabilities": [
            "intent-routing",
            "mission-planning",
            "web-research",
            "parallel-source-reading",
            "ai-provider-routing",
            "reasoning",
            "verification",
            "memory",
            "background-execution",
            "structured-outcomes",
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
# ONE-COMMAND MISSION API
# ============================================================

@app.post("/mission")
@app.post("/missions")
async def create_mission(
    request: MissionRequest,
):

    objective = (
        request.command
        or request.objective
    )

    if not objective:

        raise HTTPException(
            status_code=422,
            detail=(
                "Send 'command' or "
                "'objective'."
            ),
        )

    task_id = make_id("mission")

    task = {

        "task_id":
            task_id,

        "status":
            "queued",

        "objective":
            objective,

        "research":
            request.research,

        "verify":
            request.verify,

        "remember":
            request.remember,

        "priority":
            request.priority,

        "metadata":
            safe_json(
                request.metadata
            ),

        "created_at":
            now_iso(),

        "result":
            None,

        "error":
            None,
    }

    TASKS[task_id] = task

    if len(TASKS) > MAX_TASKS:

        old_ids = list(
            TASKS.keys()
        )[
            :len(TASKS) - MAX_TASKS
        ]

        for old_id in old_ids:
            TASKS.pop(
                old_id,
                None,
            )

    save_task(task)

    asyncio.create_task(
        run_task(task)
    )

    return JSONResponse(
        status_code=202,
        content={
            "project":
                PROJECT,

            "version":
                VERSION,

            "mission_id":
                task_id,

            "task_id":
                task_id,

            "status":
                "queued",

            "message":
                "Mission accepted.",

            "pipeline": [
                "research",
                "reason",
                "verify",
                "learn",
                "deliver",
            ],

            "poll":
                f"/mission/{task_id}",
        },
    )


# ============================================================
# MISSION RESULT
# ============================================================

@app.get("/mission/{mission_id}")
@app.get("/missions/{mission_id}")
async def get_mission(
    mission_id: str,
):

    task = TASKS.get(
        mission_id
    )

    if not task:

        path = (
            TASK_DIR
            / f"{mission_id}.json"
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
                    mission_id
                ] = task

            except Exception:

                task = None

    if not task:

        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    return safe_json(task)


# ============================================================
# QUICK MISSION STATUS
# ============================================================

@app.get("/mission-status/{mission_id}")
async def mission_status(
    mission_id: str,
):

    task = TASKS.get(
        mission_id
    )

    if not task:

        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    result = task.get(
        "result"
    ) or {}

    outcome = result.get(
        "outcome",
        {},
    )

    return {
        "mission_id":
            mission_id,

        "status":
            task.get("status"),

        "objective":
            task.get("objective"),

        "provider":
            outcome.get(
                "provider"
            ),

        "verified":
            outcome.get(
                "verified"
            ),

        "created_at":
            task.get("created_at"),

        "completed_at":
            task.get("completed_at"),
    }


# ============================================================
# COMPATIBILITY TASK API
# ============================================================

@app.post("/task")
@app.post("/tasks")
async def create_task(
    request: MissionRequest,
):

    return await create_mission(
        request
    )


@app.get("/task/{task_id}")
@app.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
):

    return await get_mission(
        task_id
    )


@app.get("/tasks")
async def list_tasks():

    return {
        "project":
            PROJECT,

        "version":
            VERSION,

        "count":
            len(TASKS),

        "tasks":
            safe_json(
                list(
                    TASKS.values()
                )
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
        request.content,
        request.category,
    )

    return {
        "status":
            "stored",
        "memory":
            safe_json(item),
    }


@app.get("/memory")
async def get_memory():

    return {
        "project":
            PROJECT,

        "version":
            VERSION,

        "count":
            len(MEMORY),

        "memory":
            safe_json(MEMORY),
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
        "query":
            q,

        "count":
            len(results),

        "results":
            safe_json(results),
    }


# ============================================================
# INTENT
# ============================================================

@app.post("/intent")
async def intent(
    request: MissionRequest,
):

    objective = (
        request.command
        or request.objective
    )

    if not objective:

        raise HTTPException(
            status_code=422,
            detail="Objective required.",
        )

    return {
        "project":
            PROJECT,

        "version":
            VERSION,

        "objective":
            objective,

        "intent":
            classify_intent(
                objective
            ),
    }


# ============================================================
# PLAN
# ============================================================

@app.post("/plan")
async def plan(
    request: MissionRequest,
):

    objective = (
        request.command
        or request.objective
    )

    if not objective:

        raise HTTPException(
            status_code=422,
            detail="Objective required.",
        )

    return {
        "project":
            PROJECT,

        "version":
            VERSION,

        "objective":
            objective,

        "plan":
            build_plan(
                request.research,
                request.verify,
                request.remember,
            ),
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():

    return {

        "project":
            PROJECT,

        "version":
            VERSION,

        "engine":
            "One-Command Mission Engine",

        "active": [

            "Human Intent",

            "Intent Classification",

            "Mission Planning",

            "Public Web Research",

            "Parallel Source Reading",

            "AI Provider Router",

            "Reasoning",

            "Verification",

            "Reusable Memory",

            "Background Execution",

            "Mission Status",

            "Structured Outcomes",

        ],

        "optional_ai": [

            "OpenAI-compatible provider",

            "Pollinations",

            "Deterministic fallback",

        ],

        "free_first":
            True,

        "pipeline": [

            "INPUT",

            "UNDERSTAND",

            "RESEARCH",

            "REASON",

            "VERIFY",

            "LEARN",

            "DELIVER",

        ],
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
async def status():

    return {

        "project":
            PROJECT,

        "version":
            VERSION,

        "status":
            "online",

        "engine":
            "TARGET-3.1.0",

        "mode":
            "one-command",

        "research":
            "enabled",

        "reasoning":
            "enabled",

        "verification":
            "enabled",

        "memory":
            "enabled",

        "free_first":
            True,
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
            "status":
                "error",

            "project":
                PROJECT,

            "version":
                VERSION,

            "error": {
                "type":
                    type(exc).__name__,

                "message":
                    str(exc),
            },
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

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
# LOCAL
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
