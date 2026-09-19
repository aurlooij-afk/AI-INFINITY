import os
import re
import json
import uuid
import time
import asyncio
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY
# TARGET 3.5.0
# Clean single-file runtime
# ============================================================

VERSION = "TARGET-3.5.0"
NAME = "AI Infinity"

BASE_DIR = Path("/tmp/ai-infinity")
TASK_DIR = BASE_DIR / "tasks"
MEMORY_DIR = BASE_DIR / "memory"
VIDEO_DIR = BASE_DIR / "videos"
ASSET_DIR = BASE_DIR / "assets"

for directory in (
    BASE_DIR,
    TASK_DIR,
    MEMORY_DIR,
    VIDEO_DIR,
    ASSET_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)


START_TIME = time.time()


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=NAME,
    version=VERSION,
    description="AI Infinity autonomous task and intelligence runtime.",
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

class RunRequest(BaseModel):
    objective: Optional[str] = None
    command: Optional[str] = None
    research: bool = False
    verify: bool = False
    remember: bool = False
    external_access: bool = True
    max_sources: int = Field(default=5, ge=1, le=20)


class TaskRequest(BaseModel):
    objective: Optional[str] = None
    command: Optional[str] = None
    research: bool = False
    verify: bool = False
    remember: bool = False
    external_access: bool = True


class MemoryRequest(BaseModel):
    key: str
    value: Any


# ============================================================
# UTILITY
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "task") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def clean_text(value: Any, maximum: int = 20000) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if len(text) > maximum:
        text = text[:maximum]

    return text


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary = path.with_suffix(".tmp")

    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(
            data,
            handle,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    temporary.replace(path)


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def task_path(task_id: str) -> Path:
    return TASK_DIR / f"{task_id}.json"


def load_task(task_id: str) -> Optional[Dict[str, Any]]:
    return load_json(task_path(task_id))


def save_task(task: Dict[str, Any]) -> None:
    save_json(task_path(task["task_id"]), task)


def normalize_objective(data: Dict[str, Any]) -> str:
    objective = data.get("objective")

    if not objective:
        objective = data.get("command")

    objective = clean_text(objective)

    if not objective:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'objective' or 'command'.",
        )

    return objective


def sha256_text(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# ============================================================
# CAPABILITIES
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
        "name": "status",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "memory",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "research",
        "category": "external",
        "permission": "network",
    },
    {
        "name": "verify",
        "category": "reasoning",
        "permission": "safe",
    },
    {
        "name": "external_fetch",
        "category": "external",
        "permission": "network",
    },
    {
        "name": "task_engine",
        "category": "core",
        "permission": "safe",
    },
]


# ============================================================
# SIMPLE LOCAL MEMORY
# ============================================================

def memory_file() -> Path:
    return MEMORY_DIR / "memory.json"


def load_memory() -> Dict[str, Any]:
    data = load_json(memory_file())

    if not data:
        return {}

    return data


def save_memory(data: Dict[str, Any]) -> None:
    save_json(memory_file(), data)


def remember(key: str, value: Any) -> Dict[str, Any]:
    data = load_memory()

    data[key] = {
        "value": value,
        "updated_at": utc_now(),
    }

    save_memory(data)

    return data[key]


# ============================================================
# EXTERNAL ACCESS
# ============================================================

def fetch_url(url: str, timeout: int = 15) -> Dict[str, Any]:
    url = clean_text(url, 2000)

    if not re.match(r"^https?://", url, re.IGNORECASE):
        return {
            "success": False,
            "url": url,
            "error": "Only http:// and https:// URLs are supported.",
        }

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "AI-Infinity/3.5 "
                    "(autonomous research client)"
                )
            },
            allow_redirects=True,
        )

        content_type = response.headers.get(
            "content-type",
            "",
        )

        text = response.text

        if len(text) > 30000:
            text = text[:30000]

        return {
            "success": response.ok,
            "status_code": response.status_code,
            "url": response.url,
            "content_type": content_type,
            "content_length": len(response.content),
            "text": text,
        }

    except Exception as exc:
        return {
            "success": False,
            "url": url,
            "error": str(exc),
        }


def extract_urls(text: str) -> List[str]:
    pattern = r"https?://[^\s<>\"]+"

    urls = re.findall(pattern, text or "")

    result = []

    for url in urls:
        url = url.rstrip(".,;:!?)]}")

        if url not in result:
            result.append(url)

    return result


# ============================================================
# RESEARCH ENGINE
# ============================================================

def generate_search_links(objective: str) -> List[str]:
    query = requests.utils.quote(objective)

    return [
        f"https://www.google.com/search?q={query}",
        f"https://www.bing.com/search?q={query}",
        f"https://duckduckgo.com/?q={query}",
    ]


def research_objective(
    objective: str,
    max_sources: int = 5,
) -> Dict[str, Any]:

    sources = []
    search_links = generate_search_links(objective)

    for url in search_links[:max_sources]:
        result = fetch_url(url, timeout=10)

        sources.append(
            {
                "url": result.get("url", url),
                "success": result.get("success", False),
                "status_code": result.get("status_code"),
                "content_type": result.get("content_type"),
                "content_length": result.get("content_length"),
            }
        )

    return {
        "objective": objective,
        "search_links": search_links,
        "sources": sources,
        "source_count": len(sources),
        "completed_at": utc_now(),
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_result(
    objective: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:

    checks = []

    if objective:
        checks.append(
            {
                "check": "objective_present",
                "passed": True,
            }
        )
    else:
        checks.append(
            {
                "check": "objective_present",
                "passed": False,
            }
        )

    if isinstance(result, dict):
        checks.append(
            {
                "check": "result_is_structured",
                "passed": True,
            }
        )
    else:
        checks.append(
            {
                "check": "result_is_structured",
                "passed": False,
            }
        )

    passed = all(
        item["passed"]
        for item in checks
    )

    return {
        "verified": passed,
        "checks": checks,
        "verification_method": "runtime consistency checks",
        "verified_at": utc_now(),
    }


# ============================================================
# INTELLIGENCE PIPELINE
# ============================================================

def build_analysis(
    objective: str,
    research_enabled: bool,
    verify_enabled: bool,
    remember_enabled: bool,
    external_access: bool,
    max_sources: int,
) -> Dict[str, Any]:

    result: Dict[str, Any] = {
        "objective": objective,
        "version": VERSION,
        "status": "completed",
        "created_at": utc_now(),
        "engine": {
            "name": NAME,
            "mode": "autonomous-task-runtime",
            "external_access": external_access,
        },
        "plan": [
            "Understand objective",
            "Determine required capabilities",
            "Collect available evidence",
            "Produce structured result",
            "Verify result",
            "Persist useful memory when requested",
        ],
        "reasoning": {
            "goal": objective,
            "intent_hash": sha256_text(objective),
            "constraints": [
                "Use available runtime capabilities",
                "Do not claim unavailable capabilities",
                "Return structured machine-readable output",
            ],
        },
    }

    if research_enabled and external_access:
        result["research"] = research_objective(
            objective,
            max_sources=max_sources,
        )
    else:
        result["research"] = {
            "enabled": False,
            "reason": (
                "Research was not requested or "
                "external access was disabled."
            ),
        }

    result["answer"] = {
        "objective": objective,
        "interpretation": (
            "AI Infinity received the objective and "
            "completed the available runtime pipeline."
        ),
        "next_action": (
            "Use the returned task data as the machine-readable "
            "execution record."
        ),
    }

    if verify_enabled:
        result["verification"] = verify_result(
            objective,
            result,
        )
    else:
        result["verification"] = {
            "verified": False,
            "enabled": False,
        }

    if remember_enabled:
        key = f"objective:{sha256_text(objective)[:16]}"

        memory_record = remember(
            key,
            {
                "objective": objective,
                "created_at": utc_now(),
            },
        )

        result["memory"] = {
            "saved": True,
            "key": key,
            "record": memory_record,
        }
    else:
        result["memory"] = {
            "saved": False,
        }

    return result


# ============================================================
# BACKGROUND TASK ENGINE
# ============================================================

async def execute_task(
    task_id: str,
    objective: str,
    research: bool,
    verify: bool,
    remember_flag: bool,
    external_access: bool,
    max_sources: int,
) -> None:

    task = load_task(task_id)

    if not task:
        return

    try:
        task["status"] = "running"
        task["started_at"] = utc_now()
        save_task(task)

        await asyncio.sleep(0)

        result = await asyncio.to_thread(
            build_analysis,
            objective,
            research,
            verify,
            remember_flag,
            external_access,
            max_sources,
        )

        task["status"] = "completed"
        task["completed_at"] = utc_now()
        task["result"] = result

        save_task(task)

    except Exception as exc:
        task["status"] = "failed"
        task["completed_at"] = utc_now()
        task["error"] = str(exc)

        save_task(task)


# ============================================================
# ROOT / UI
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body {{
    margin: 0;
    background: #0b1020;
    color: #ffffff;
    font-family: Arial, sans-serif;
}}
.container {{
    max-width: 900px;
    margin: auto;
    padding: 28px;
}}
.card {{
    background: #151c31;
    border-radius: 18px;
    padding: 24px;
    margin-top: 20px;
}}
textarea {{
    width: 100%;
    min-height: 150px;
    box-sizing: border-box;
    border-radius: 12px;
    padding: 14px;
    background: #0b1020;
    color: white;
    border: 1px solid #35405e;
}}
button {{
    margin-top: 12px;
    padding: 13px 20px;
    border: 0;
    border-radius: 10px;
    cursor: pointer;
}}
pre {{
    white-space: pre-wrap;
    word-break: break-word;
}}
.status {{
    color: #8ee6a5;
}}
</style>
</head>
<body>
<div class="container">
<h1>AI Infinity</h1>
<p class="status">● Runtime online</p>
<p>Version: {VERSION}</p>

<div class="card">
<h2>Run Objective</h2>
<textarea id="objective"
placeholder="Enter an objective..."></textarea>
<br>
<button onclick="runTask()">Run AI Infinity</button>
</div>

<div class="card">
<h2>Result</h2>
<pre id="result">Waiting...</pre>
</div>
</div>

<script>
async function runTask() {{
    const objective =
        document.getElementById("objective").value;

    if (!objective.trim()) {{
        alert("Enter an objective first.");
        return;
    }}

    document.getElementById("result").textContent =
        "Starting...";

    try {{
        const response = await fetch("/run", {{
            method: "POST",
            headers: {{
                "Content-Type": "application/json"
            }},
            body: JSON.stringify({{
                objective: objective,
                research: true,
                verify: true,
                remember: true,
                external_access: true
            }})
        }});

        const data = await response.json();

        document.getElementById("result").textContent =
            JSON.stringify(data, null, 2);

    }} catch (error) {{
        document.getElementById("result").textContent =
            String(error);
    }}
}}
</script>
</body>
</html>
"""


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():
    uptime = round(time.time() - START_TIME, 2)

    return {
        "status": "ok",
        "online": True,
        "service": NAME,
        "version": VERSION,
        "uptime_seconds": uptime,
        "timestamp": utc_now(),
    }


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():
    return {
        "name": NAME,
        "version": VERSION,
        "builtin_tools": BUILTIN_TOOLS,
        "features": {
            "task_engine": True,
            "background_tasks": True,
            "research": True,
            "verification": True,
            "memory": True,
            "external_http": True,
            "web_ui": True,
            "json_api": True,
        },
        "limitations": {
            "internet": (
                "External HTTP requests depend on the "
                "deployment network and target website."
            ),
            "autonomous_actions": (
                "The runtime does not silently perform "
                "irreversible actions."
            ),
        },
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
async def status():
    task_files = list(TASK_DIR.glob("*.json"))

    completed = 0
    running = 0
    failed = 0

    for path in task_files:
        task = load_json(path)

        if not task:
            continue

        state = task.get("status")

        if state == "completed":
            completed += 1
        elif state == "running":
            running += 1
        elif state == "failed":
            failed += 1

    return {
        "service": NAME,
        "version": VERSION,
        "status": "operational",
        "uptime_seconds": round(
            time.time() - START_TIME,
            2,
        ),
        "tasks": {
            "total": len(task_files),
            "completed": completed,
            "running": running,
            "failed": failed,
        },
        "memory": {
            "records": len(load_memory()),
        },
        "timestamp": utc_now(),
    }


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory")
async def get_memory():
    data = load_memory()

    return {
        "count": len(data),
        "memory": data,
    }


@app.get("/memory/count")
async def memory_count():
    return {
        "count": len(load_memory()),
    }


@app.post("/memory")
async def add_memory(request: MemoryRequest):
    record = remember(
        request.key,
        request.value,
    )

    return {
        "status": "saved",
        "key": request.key,
        "record": record,
    }


# ============================================================
# RUN
# ============================================================

@app.post("/run")
async def run(request: RunRequest):
    data = request.model_dump()

    objective = normalize_objective(data)

    task_id = new_id("task")

    task = {
        "task_id": task_id,
        "status": "queued",
        "objective": objective,
        "created_at": utc_now(),
        "options": {
            "research": request.research,
            "verify": request.verify,
            "remember": request.remember,
            "external_access": request.external_access,
            "max_sources": request.max_sources,
        },
    }

    save_task(task)

    asyncio.create_task(
        execute_task(
            task_id=task_id,
            objective=objective,
            research=request.research,
            verify=request.verify,
            remember_flag=request.remember,
            external_access=request.external_access,
            max_sources=request.max_sources,
        )
    )

    return {
        "task_id": task_id,
        "status": "queued",
        "version": VERSION,
        "objective": objective,
        "poll": f"/task/{task_id}",
    }


# ============================================================
# TASK CREATION
# ============================================================

@app.post("/tasks")
async def create_task(
    request: TaskRequest,
):
    objective = normalize_objective(
        request.model_dump()
    )

    task_id = new_id("task")

    task = {
        "task_id": task_id,
        "status": "queued",
        "objective": objective,
        "created_at": utc_now(),
        "options": {
            "research": request.research,
            "verify": request.verify,
            "remember": request.remember,
            "external_access": request.external_access,
        },
    }

    save_task(task)

    asyncio.create_task(
        execute_task(
            task_id=task_id,
            objective=objective,
            research=request.research,
            verify=request.verify,
            remember_flag=request.remember,
            external_access=request.external_access,
            max_sources=5,
        )
    )

    return task


# ============================================================
# TASK STATUS
# ============================================================

@app.get("/task/{task_id}")
async def get_task(task_id: str):
    task = load_task(task_id)

    if not task:
        raise HTTPException(
            status_code=404,
            detail="Task not found.",
        )

    return task


@app.get("/tasks/{task_id}")
async def get_task_alias(task_id: str):
    return await get_task(task_id)


# ============================================================
# TASK LIST
# ============================================================

@app.get("/tasks")
async def list_tasks():
    tasks = []

    for path in sorted(
        TASK_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        task = load_json(path)

        if task:
            tasks.append(task)

    return {
        "count": len(tasks),
        "tasks": tasks[:100],
    }


# ============================================================
# DIRECT RESEARCH
# ============================================================

@app.post("/research")
async def research(request: RunRequest):
    objective = normalize_objective(
        request.model_dump()
    )

    result = await asyncio.to_thread(
        research_objective,
        objective,
        request.max_sources,
    )

    return {
        "status": "completed",
        "version": VERSION,
        "research": result,
    }


# ============================================================
# EXTERNAL URL
# ============================================================

class URLRequest(BaseModel):
    url: str


@app.post("/external/fetch")
async def external_fetch(request: URLRequest):
    result = await asyncio.to_thread(
        fetch_url,
        request.url,
    )

    return result


@app.post("/fetch")
async def fetch_alias(request: URLRequest):
    return await external_fetch(request)


# ============================================================
# VERIFY
# ============================================================

@app.post("/verify")
async def verify(request: RunRequest):
    objective = normalize_objective(
        request.model_dump()
    )

    result = verify_result(
        objective,
        {
            "objective": objective,
            "timestamp": utc_now(),
        },
    )

    return {
        "status": "completed",
        "verification": result,
    }


# ============================================================
# VERSION
# ============================================================

@app.get("/version")
async def version():
    return {
        "name": NAME,
        "version": VERSION,
        "target": VERSION,
    }


# ============================================================
# VIDEO COMPATIBILITY
# ============================================================

@app.get("/video/{video_id}")
async def get_video(video_id: str):
    safe_id = re.sub(
        r"[^a-zA-Z0-9._-]",
        "",
        video_id,
    )

    candidates = [
        VIDEO_DIR / safe_id,
        VIDEO_DIR / f"{safe_id}.mp4",
        BASE_DIR / safe_id,
        BASE_DIR / f"{safe_id}.mp4",
    ]

    for path in candidates:
        if path.exists() and path.is_file():
            return FileResponse(
                path,
                media_type="video/mp4",
                filename=path.name,
            )

    raise HTTPException(
        status_code=404,
        detail="Video not found.",
    )


# ============================================================
# GENERIC API ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request,
    exc: Exception,
):
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "error": str(exc),
            "path": str(request.url.path),
            "version": VERSION,
            "timestamp": utc_now(),
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# LOCAL START
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
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
