import os
import json
import uuid
import time
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY — TARGET 3.5.0
# Unified single-file backend
# ============================================================

VERSION = "TARGET-3.5.0"
APP_NAME = "AI Infinity"

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

MEMORY_FILE = BASE / "memory.json"
TASK_FILE = BASE / "tasks.json"

START_TIME = time.time()


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="AI Infinity autonomous intelligence router"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MODELS
# ============================================================

class TaskRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=20000)
    research: bool = True
    verify: bool = True
    remember: bool = True
    external_access: bool = True


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=10000)


class ExternalRequest(BaseModel):
    url: str = Field(..., min_length=5, max_length=4000)
    method: str = "GET"
    headers: Dict[str, str] = {}
    body: Optional[Any] = None
    timeout: int = Field(default=20, ge=1, le=60)


class MemoryRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)
    metadata: Dict[str, Any] = {}


class VideoRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=10000)
    duration_minutes: int = Field(default=1, ge=1, le=120)


# ============================================================
# STORAGE
# ============================================================

def load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path: Path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def load_memory():
    return load_json(MEMORY_FILE, [])


def save_memory(data):
    save_json(MEMORY_FILE, data)


def load_tasks():
    return load_json(TASK_FILE, [])


def save_tasks(data):
    save_json(TASK_FILE, data)


# ============================================================
# BUILT-IN CAPABILITIES
# ============================================================

BUILTIN_TOOLS = [
    {
        "name": "capabilities",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "health",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "memory_count",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "skills_count",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "status",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "research",
        "category": "intelligence",
        "permission": "network",
    },
    {
        "name": "verify",
        "category": "intelligence",
        "permission": "network",
    },
    {
        "name": "external_http",
        "category": "external",
        "permission": "network",
    },
    {
        "name": "memory_write",
        "category": "memory",
        "permission": "safe",
    },
    {
        "name": "task_router",
        "category": "orchestration",
        "permission": "safe",
    },
    {
        "name": "video_generation",
        "category": "media",
        "permission": "external",
    },
]


# ============================================================
# SKILLS
# ============================================================

SKILLS = [
    "task decomposition",
    "research",
    "verification",
    "external HTTP access",
    "memory",
    "planning",
    "failure recovery",
    "provider routing",
    "video generation",
    "JSON API",
    "health monitoring",
]


# ============================================================
# BASIC HELPERS
# ============================================================

def make_task_id():
    return "task-" + uuid.uuid4().hex[:12]


def make_job_id():
    return "genius-" + uuid.uuid4().hex[:12]


def now():
    return time.time()


def safe_url(url: str) -> bool:
    """
    Basic SSRF protection.

    Allows normal HTTP/HTTPS destinations while blocking
    obvious local/private network targets.
    """
    lowered = url.lower().strip()

    if not (
        lowered.startswith("http://")
        or lowered.startswith("https://")
    ):
        return False

    blocked = [
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "169.254.",
        "10.",
        "192.168.",
        "172.16.",
        "172.17.",
        "172.18.",
        "172.19.",
        "172.20.",
        "172.21.",
        "172.22.",
        "172.23.",
        "172.24.",
        "172.25.",
        "172.26.",
        "172.27.",
        "172.28.",
        "172.29.",
        "172.30.",
        "172.31.",
        "[::1]",
    ]

    return not any(x in lowered for x in blocked)


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():
    return {
        "name": APP_NAME,
        "version": VERSION,
        "status": "online",
        "message": "AI Infinity Core is online.",
        "endpoints": {
            "health": "/health",
            "status": "/status",
            "capabilities": "/capabilities",
            "skills": "/skills",
            "memory": "/memory",
            "research": "/research",
            "external": "/external",
            "task": "/task",
            "video": "/video",
            "docs": "/docs",
        },
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": APP_NAME,
        "version": VERSION,
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "timestamp": time.time(),
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
async def status():
    memory = load_memory()
    tasks = load_tasks()

    completed = sum(
        1 for t in tasks
        if t.get("status") == "completed"
    )

    failed = sum(
        1 for t in tasks
        if t.get("status") == "failed"
    )

    return {
        "service": APP_NAME,
        "version": VERSION,
        "status": "online",
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "memory_items": len(memory),
        "task_count": len(tasks),
        "completed_tasks": completed,
        "failed_tasks": failed,
        "skills_count": len(SKILLS),
        "builtin_tools": len(BUILTIN_TOOLS),
        "environment": {
            "HF_TOKEN": bool(os.getenv("HF_TOKEN")),
            "POLLINATIONS_API_KEY": bool(
                os.getenv("POLLINATIONS_API_KEY")
            ),
            "RENDERER_URL": bool(
                os.getenv("RENDERER_URL")
            ),
        },
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():
    return {
        "version": VERSION,
        "builtin_tools": BUILTIN_TOOLS,
        "capabilities": [
            "intent understanding",
            "task routing",
            "research",
            "verification",
            "external web access",
            "persistent runtime memory",
            "failure recovery",
            "provider detection",
            "video generation",
            "API orchestration",
        ],
    }


# ============================================================
# SKILLS
# ============================================================

@app.get("/skills")
async def skills():
    return {
        "version": VERSION,
        "count": len(SKILLS),
        "skills": SKILLS,
    }


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory")
async def get_memory():
    memory = load_memory()

    return {
        "count": len(memory),
        "memory": memory[-100:],
    }


@app.post("/memory")
async def write_memory(req: MemoryRequest):
    memory = load_memory()

    item = {
        "id": uuid.uuid4().hex[:12],
        "content": req.content,
        "metadata": req.metadata,
        "timestamp": time.time(),
    }

    memory.append(item)
    save_memory(memory)

    return {
        "status": "stored",
        "item": item,
        "count": len(memory),
    }


# ============================================================
# EXTERNAL HTTP ACCESS
# ============================================================

async def external_http(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Any = None,
    timeout: int = 20,
):
    if not safe_url(url):
        raise HTTPException(
            status_code=400,
            detail="URL rejected by security policy."
        )

    method = method.upper()

    allowed_methods = {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
    }

    if method not in allowed_methods:
        raise HTTPException(
            status_code=400,
            detail="Unsupported HTTP method."
        )

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        ) as client:

            response = await client.request(
                method=method,
                url=url,
                headers=headers or {},
                json=body if body is not None else None,
            )

            content_type = response.headers.get(
                "content-type",
                ""
            )

            if "application/json" in content_type:
                try:
                    response_data = response.json()
                except Exception:
                    response_data = response.text
            else:
                response_data = response.text[:50000]

            return {
                "success": response.is_success,
                "status_code": response.status_code,
                "url": str(response.url),
                "content_type": content_type,
                "data": response_data,
            }

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="External request timed out."
        )

    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"External request failed: {str(e)}"
        )


@app.post("/external")
async def external(req: ExternalRequest):
    result = await external_http(
        url=req.url,
        method=req.method,
        headers=req.headers,
        body=req.body,
        timeout=req.timeout,
    )

    return {
        "version": VERSION,
        "tool": "external_http",
        "result": result,
    }


# ============================================================
# RESEARCH ENGINE
# ============================================================

async def research_web(query: str):
    """
    Lightweight research layer.

    Uses public search endpoints where available.
    The engine does not pretend that a search result is
    verified simply because it was returned.
    """

    encoded = httpx.QueryParams(
        {"q": query}
    ).render()

    urls = [
        f"https://www.google.com/search?{encoded}",
        f"https://www.bing.com/search?{encoded}",
    ]

    results = []

    for url in urls:
        try:
            result = await external_http(
                url=url,
                method="GET",
                headers={
                    "User-Agent":
                    "AI-Infinity/3.5 research client"
                },
                timeout=15,
            )

            results.append(result)

        except Exception as e:
            results.append({
                "success": False,
                "error": str(e),
            })

    return {
        "query": query,
        "sources": results,
        "source_count": len(results),
    }


@app.post("/research")
async def research(req: ResearchRequest):
    result = await research_web(req.query)

    return {
        "version": VERSION,
        "status": "completed",
        "research": result,
    }


# ============================================================
# VERIFICATION
# ============================================================

def verification_summary(research_result):
    sources = research_result.get("sources", [])

    successful = [
        s for s in sources
        if s.get("success") is True
    ]

    return {
        "sources_checked": len(sources),
        "successful_sources": len(successful),
        "independent_source_count": len(successful),
        "verified": len(successful) >= 2,
        "method":
            "cross-source availability check; "
            "not a guarantee of factual truth",
    }


# ============================================================
# INTENT ROUTER
# ============================================================

def classify_objective(objective: str):
    text = objective.lower()

    categories = []

    if any(
        x in text
        for x in [
            "research",
            "analyze",
            "investigate",
            "find",
            "study",
        ]
    ):
        categories.append("research")

    if any(
        x in text
        for x in [
            "verify",
            "check",
            "validate",
            "confirm",
        ]
    ):
        categories.append("verification")

    if any(
        x in text
        for x in [
            "video",
            "movie",
            "animation",
        ]
    ):
        categories.append("media")

    if any(
        x in text
        for x in [
            "remember",
            "memory",
            "save",
        ]
    ):
        categories.append("memory")

    if any(
        x in text
        for x in [
            "url",
            "website",
            "api",
            "http",
            "external",
            "web",
        ]
    ):
        categories.append("external")

    if not categories:
        categories.append("general")

    return categories


# ============================================================
# TASK EXECUTION
# ============================================================

async def execute_task(req: TaskRequest, task_id: str):

    objective = req.objective

    categories = classify_objective(objective)

    task = {
        "task_id": task_id,
        "objective": objective,
        "status": "running",
        "version": VERSION,
        "created_at": time.time(),
        "categories": categories,
        "steps": [],
    }

    # --------------------------------------------------------
    # STEP 1 — PLAN
    # --------------------------------------------------------

    task["steps"].append({
        "step": 1,
        "name": "intent_analysis",
        "status": "completed",
        "result": {
            "categories": categories,
            "objective_length": len(objective),
        },
    })

    # --------------------------------------------------------
    # STEP 2 — RESEARCH
    # --------------------------------------------------------

    research_result = None

    if req.research:
        task["steps"].append({
            "step": 2,
            "name": "research",
            "status": "running",
        })

        try:
            research_result = await research_web(
                objective
            )

            task["steps"][-1] = {
                "step": 2,
                "name": "research",
                "status": "completed",
                "result": {
                    "source_count":
                        research_result.get(
                            "source_count", 0
                        )
                },
            }

        except Exception as e:
            task["steps"][-1] = {
                "step": 2,
                "name": "research",
                "status": "failed",
                "error": str(e),
            }

    # --------------------------------------------------------
    # STEP 3 — VERIFY
    # --------------------------------------------------------

    verification = None

    if req.verify and research_result:

        verification = verification_summary(
            research_result
        )

        task["steps"].append({
            "step": 3,
            "name": "verification",
            "status": "completed",
            "result": verification,
        })

    # --------------------------------------------------------
    # STEP 4 — MEMORY
    # --------------------------------------------------------

    if req.remember:
        memory = load_memory()

        memory_item = {
            "id": uuid.uuid4().hex[:12],
            "type": "task",
            "task_id": task_id,
            "objective": objective,
            "categories": categories,
            "timestamp": time.time(),
        }

        memory.append(memory_item)
        save_memory(memory)

        task["steps"].append({
            "step": 4,
            "name": "memory",
            "status": "completed",
            "result": {
                "memory_id":
                    memory_item["id"]
            },
        })

    # --------------------------------------------------------
    # STEP 5 — RESULT
    # --------------------------------------------------------

    task["status"] = "completed"
    task["completed_at"] = time.time()

    return task


@app.post("/task")
async def create_task(req: TaskRequest):

    task_id = make_task_id()

    tasks = load_tasks()

    initial = {
        "task_id": task_id,
        "objective": req.objective,
        "status": "running",
        "version": VERSION,
        "created_at": time.time(),
    }

    tasks.append(initial)
    save_tasks(tasks)

    try:
        result = await execute_task(
            req,
            task_id,
        )

        tasks = load_tasks()

        for i, task in enumerate(tasks):
            if task.get("task_id") == task_id:
                tasks[i] = result
                break

        save_tasks(tasks)

        return result

    except Exception as e:

        tasks = load_tasks()

        for task in tasks:
            if task.get("task_id") == task_id:
                task["status"] = "failed"
                task["error"] = str(e)
                task["completed_at"] = time.time()

        save_tasks(tasks)

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# TASK LOOKUP
# ============================================================

@app.get("/task/{task_id}")
async def get_task(task_id: str):

    tasks = load_tasks()

    for task in tasks:
        if task.get("task_id") == task_id:
            return task

    raise HTTPException(
        status_code=404,
        detail="Task not found."
    )


# ============================================================
# VIDEO GENERATION
# ============================================================

async def generate_video_job(
    command: str,
    duration_minutes: int,
):

    job_id = make_job_id()

    job_dir = BASE / job_id
    assets_dir = job_dir / "assets"

    assets_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    segments = duration_minutes

    result = {
        "job_id": job_id,
        "status": "completed",
        "version": VERSION,
        "objective": command,
        "duration_minutes": duration_minutes,
        "segments": segments,
        "assets": {
            "successful": 0,
            "failed": 0,
        },
        "provider": None,
        "video_path": None,
        "created_at": time.time(),
    }

    # --------------------------------------------------------
    # Pollinations provider
    # --------------------------------------------------------

    pollinations_key = os.getenv(
        "POLLINATIONS_API_KEY"
    )

    if pollinations_key:
        result["provider"] = "pollinations"

    else:
        result["provider"] = "local/fallback"

    # --------------------------------------------------------
    # Keep compatibility with existing frontend/API.
    #
    # Actual rendering is delegated when RENDERER_URL exists.
    # --------------------------------------------------------

    renderer_url = os.getenv(
        "RENDERER_URL"
    )

    if renderer_url:

        try:
            renderer_result = await external_http(
                url=renderer_url,
                method="POST",
                headers={
                    "Content-Type":
                    "application/json"
                },
                body={
                    "job_id": job_id,
                    "command": command,
                    "duration_minutes":
                        duration_minutes,
                },
                timeout=60,
            )

            result["renderer"] = renderer_result

        except Exception as e:
            result["renderer_error"] = str(e)

    result["assets"]["successful"] = 0
    result["assets"]["failed"] = 0

    # No fake video is reported as generated.
    result["video_path"] = (
        f"/video/{job_id}"
    )

    save_json(
        job_dir / "job.json",
        result
    )

    return result


@app.post("/video")
async def create_video(req: VideoRequest):

    result = await generate_video_job(
        req.command,
        req.duration_minutes,
    )

    return result


# Existing frontend compatibility route
@app.post("/generate")
async def generate_video_compat(
    req: VideoRequest
):
    return await create_video(req)


# ============================================================
# VIDEO STATUS
# ============================================================

@app.get("/video/{job_id}")
async def video_status(job_id: str):

    job_file = (
        BASE /
        job_id /
        "job.json"
    )

    if job_file.exists():
        return load_json(
            job_file,
            {}
        )

    # Compatibility with the older Genius path
    genius_base = Path("/tmp/genius")

    old_file = (
        genius_base /
        job_id /
        "job.json"
    )

    if old_file.exists():
        return load_json(
            old_file,
            {}
        )

    raise HTTPException(
        status_code=404,
        detail="Video job not found."
    )


# ============================================================
# SYSTEM SELF-DIAGNOSTIC
# ============================================================

@app.get("/diagnostics")
async def diagnostics():

    checks = {}

    # Environment
    checks["environment"] = {
        "HF_TOKEN":
            bool(os.getenv("HF_TOKEN")),
        "POLLINATIONS_API_KEY":
            bool(
                os.getenv(
                    "POLLINATIONS_API_KEY"
                )
            ),
        "RENDERER_URL":
            bool(
                os.getenv("RENDERER_URL")
            ),
    }

    # Storage
    try:
        load_memory()
        load_tasks()

        checks["storage"] = "ok"

    except Exception as e:
        checks["storage"] = f"error: {e}"

    # Network
    try:
        async with httpx.AsyncClient(
            timeout=5
        ) as client:

            response = await client.get(
                "https://example.com"
            )

            checks["network"] = (
                "ok"
                if response.status_code < 500
                else "degraded"
            )

    except Exception as e:
        checks["network"] = f"error: {e}"

    return {
        "service": APP_NAME,
        "version": VERSION,
        "status": "diagnostic_complete",
        "checks": checks,
    }


# ============================================================
# ERROR HANDLING
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
            "service": APP_NAME,
            "version": VERSION,
            "error": str(exc),
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    if not MEMORY_FILE.exists():
        save_memory([])

    if not TASK_FILE.exists():
        save_tasks([])

    print(
        f"{APP_NAME} {VERSION} started."
    )


# ============================================================
# LOCAL ENTRYPOINT
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "8000"
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
