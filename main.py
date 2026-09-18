"""
AI Infinity — TARGET-3.2.0
Mission Intelligence Core

Flow:
Intent → Plan → Research → Synthesize → Verify → Learn → Deliver

Free-first:
OpenAI-compatible provider → Pollinations → deterministic fallback

No additional Python packages required.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# CORE
# ============================================================

VERSION = "TARGET-3.2.0"
PROJECT = "AI Infinity"

BASE = Path("/tmp/ai_infinity")
MEMORY_FILE = BASE / "memory.json"
SKILLS_FILE = BASE / "skills.json"

BASE.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Free-first Mission Intelligence Engine",
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

MISSIONS: Dict[str, Dict[str, Any]] = {}
LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(prefix: str = "mission") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path: Path, data: Any) -> None:
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


MEMORY: List[Dict[str, Any]] = load_json(MEMORY_FILE, [])
SKILLS: List[Dict[str, Any]] = load_json(SKILLS_FILE, [])


# ============================================================
# MODELS
# ============================================================

class MissionRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=20000)
    research: bool = True
    verify: bool = True
    remember: bool = True
    max_sources: int = Field(default=6, ge=1, le=12)


class TaskRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=20000)
    duration_minutes: int = Field(default=1, ge=1, le=120)


class MemoryRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)
    tags: List[str] = Field(default_factory=list)


# ============================================================
# INTENT
# ============================================================

def classify_intent(text: str) -> Dict[str, Any]:
    t = text.lower()

    research_words = [
        "research", "analyze", "analysis", "investigate",
        "study", "compare", "find", "latest", "evidence",
        "sources", "what is", "how does",
    ]

    build_words = [
        "build", "create", "make", "develop", "implement",
        "code", "design", "launch",
    ]

    planning_words = [
        "plan", "strategy", "roadmap", "steps", "prepare",
    ]

    verify_words = [
        "verify", "check", "validate", "fact check",
        "confirm", "test",
    ]

    if any(w in t for w in build_words):
        category = "build"
    elif any(w in t for w in verify_words):
        category = "verification"
    elif any(w in t for w in planning_words):
        category = "planning"
    elif any(w in t for w in research_words):
        category = "research"
    else:
        category = "general"

    signals = {
        "research": any(w in t for w in research_words),
        "build": any(w in t for w in build_words),
        "planning": any(w in t for w in planning_words),
        "verification": any(w in t for w in verify_words),
    }

    return {
        "category": category,
        "signals": signals,
        "confidence": 0.82 if category != "general" else 0.58,
    }


# ============================================================
# PLANNING
# ============================================================

def build_plan(
    objective: str,
    research: bool,
    verify: bool,
) -> List[Dict[str, Any]]:
    stages = [
        {
            "id": "understand",
            "name": "Understand",
            "purpose": "Convert the objective into a precise mission.",
        },
    ]

    if research:
        stages.append(
            {
                "id": "research",
                "name": "Research",
                "purpose": "Collect relevant external evidence.",
            }
        )

    stages.append(
        {
            "id": "synthesize",
            "name": "Synthesize",
            "purpose": "Combine evidence into a useful answer.",
        }
    )

    if verify:
        stages.append(
            {
                "id": "verify",
                "name": "Verify",
                "purpose": "Check important claims against available evidence.",
            }
        )

    stages.extend(
        [
            {
                "id": "learn",
                "name": "Learn",
                "purpose": "Extract reusable mission knowledge.",
            },
            {
                "id": "deliver",
                "name": "Deliver",
                "purpose": "Return a structured mission outcome.",
            },
        ]
    )

    return stages


# ============================================================
# WEB RESEARCH
# ============================================================

def search_web(query: str, limit: int = 6) -> List[Dict[str, str]]:
    """
    Lightweight free web discovery through DuckDuckGo HTML.
    """
    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "AI-Infinity/3.2"
                )
            },
            timeout=20,
        )

        if response.status_code != 200:
            return []

        html = response.text

        results: List[Dict[str, str]] = []

        pattern = re.compile(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.I | re.S,
        )

        for match in pattern.finditer(html):
            url = match.group(1)
            title = re.sub("<.*?>", "", match.group(2))
            title = re.sub(r"\s+", " ", title).strip()

            if url.startswith("//"):
                url = "https:" + url

            if title and url:
                results.append(
                    {
                        "title": title[:300],
                        "url": url[:1000],
                    }
                )

            if len(results) >= limit:
                break

        return results

    except Exception:
        return []


def read_source(item: Dict[str, str]) -> Dict[str, Any]:
    url = item.get("url", "")

    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 AI-Infinity/3.2"},
            timeout=15,
        )

        text = response.text

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

        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return {
            "title": item.get("title", ""),
            "url": url,
            "content": text[:10000],
            "status": "read",
        }

    except Exception as exc:
        return {
            "title": item.get("title", ""),
            "url": url,
            "content": "",
            "status": "failed",
            "error": str(exc)[:300],
        }


def research_objective(
    objective: str,
    max_sources: int,
) -> List[Dict[str, Any]]:
    results = search_web(objective, max_sources)

    if not results:
        return []

    sources: List[Dict[str, Any]] = []

    workers = min(6, max(1, len(results)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(read_source, item)
            for item in results
        ]

        for future in as_completed(futures):
            try:
                sources.append(future.result())
            except Exception:
                pass

    return sources


# ============================================================
# PROVIDERS
# ============================================================

def call_openai_compatible(prompt: str) -> Optional[str]:
    base_url = os.getenv("AI_BASE_URL", "").strip()
    api_key = os.getenv("AI_API_KEY", "").strip()
    model = os.getenv("AI_MODEL", "").strip()

    if not base_url or not api_key or not model:
        return None

    url = base_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the reasoning engine inside AI Infinity. "
                    "Produce factual, structured, concise results. "
                    "Separate evidence from inference."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.2,
    }

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )

        if response.status_code >= 400:
            return None

        data = response.json()

        choices = data.get("choices", [])

        if not choices:
            return None

        content = choices[0].get("message", {}).get("content")

        if content:
            return str(content).strip()

    except Exception:
        return None

    return None


def call_pollinations(prompt: str) -> Optional[str]:
    try:
        response = requests.get(
            "https://text.pollinations.ai/",
            params={"prompt": prompt},
            headers={"User-Agent": "AI-Infinity/3.2"},
            timeout=60,
        )

        if response.status_code == 200:
            text = response.text.strip()

            if text:
                return text[:30000]

    except Exception:
        pass

    return None


# ============================================================
# DETERMINISTIC FALLBACK
# ============================================================

def deterministic_reasoning(
    objective: str,
    sources: List[Dict[str, Any]],
) -> str:
    source_lines = []

    for source in sources[:8]:
        source_lines.append(
            f"- {source.get('title', 'Untitled')} — "
            f"{source.get('url', '')}"
        )

    source_text = "\n".join(source_lines)

    if not source_text:
        source_text = "- No external sources were successfully retrieved."

    return (
        "Mission analysis\n\n"
        f"Objective:\n{objective}\n\n"
        "Approach:\n"
        "1. Clarify the requested outcome.\n"
        "2. Gather available evidence.\n"
        "3. Separate evidence from inference.\n"
        "4. Identify practical next actions.\n"
        "5. Verify important claims where possible.\n\n"
        "Available sources:\n"
        f"{source_text}\n\n"
        "Result:\n"
        "The mission was processed by the AI Infinity "
        "free-first reasoning pipeline."
    )


# ============================================================
# SYNTHESIS
# ============================================================

def create_prompt(
    objective: str,
    sources: List[Dict[str, Any]],
    intent: Dict[str, Any],
) -> str:
    evidence_blocks = []

    for source in sources[:8]:
        content = source.get("content", "")

        evidence_blocks.append(
            "\n".join(
                [
                    f"TITLE: {source.get('title', '')}",
                    f"URL: {source.get('url', '')}",
                    f"CONTENT: {content[:5000]}",
                ]
            )
        )

    evidence = "\n\n--- SOURCE ---\n\n".join(evidence_blocks)

    return f"""
AI INFINITY MISSION

OBJECTIVE:
{objective}

INTENT:
{json.dumps(intent, ensure_ascii=False)}

EVIDENCE:
{evidence if evidence else "No external evidence available."}

Produce a structured result with:

1. Mission interpretation
2. Evidence-supported findings
3. Important uncertainty
4. Practical conclusions
5. Recommended next actions

Do not invent sources.
Do not present unsupported assumptions as facts.
"""


def synthesize(
    objective: str,
    sources: List[Dict[str, Any]],
    intent: Dict[str, Any],
) -> Dict[str, Any]:
    prompt = create_prompt(objective, sources, intent)

    providers = [
        ("openai-compatible", call_openai_compatible),
        ("pollinations", call_pollinations),
    ]

    for name, provider in providers:
        try:
            result = provider(prompt)

            if result:
                return {
                    "provider": name,
                    "text": result,
                }
        except Exception:
            continue

    return {
        "provider": "deterministic",
        "text": deterministic_reasoning(objective, sources),
    }


# ============================================================
# VERIFICATION
# ============================================================

def extract_claims(text: str) -> List[str]:
    lines = [
        line.strip("- • \t")
        for line in text.splitlines()
    ]

    claims = []

    for line in lines:
        if len(line) >= 35:
            claims.append(line[:500])

        if len(claims) >= 8:
            break

    return claims


def verify_result(
    result_text: str,
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:
    claims = extract_claims(result_text)

    combined_source_text = " ".join(
        source.get("content", "").lower()
        for source in sources
    )

    supported = 0
    checks = []

    for claim in claims:
        words = re.findall(r"[a-zA-Z0-9]{5,}", claim.lower())

        meaningful = [
            word for word in words
            if word not in {
                "about",
                "which",
                "there",
                "their",
                "these",
                "those",
                "should",
                "would",
                "could",
                "result",
                "mission",
            }
        ]

        matches = sum(
            1
            for word in meaningful[:12]
            if word in combined_source_text
        )

        is_supported = matches >= 2 if meaningful else False

        if is_supported:
            supported += 1

        checks.append(
            {
                "claim": claim,
                "supported": is_supported,
                "match_count": matches,
            }
        )

    if claims:
        score = round(supported / len(claims), 2)
    elif sources:
        score = 0.55
    else:
        score = 0.35

    return {
        "checked_claims": len(claims),
        "supported_claims": supported,
        "confidence": score,
        "checks": checks,
    }


# ============================================================
# EVIDENCE LEDGER
# ============================================================

def make_evidence_ledger(
    sources: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    ledger = []

    for source in sources:
        url = source.get("url", "")
        content = source.get("content", "")

        digest = hashlib.sha256(
            content.encode("utf-8", errors="ignore")
        ).hexdigest()[:16]

        ledger.append(
            {
                "evidence_id": f"ev-{digest}",
                "title": source.get("title", ""),
                "url": url,
                "status": source.get("status", "unknown"),
                "content_hash": digest,
            }
        )

    return ledger


# ============================================================
# LEARNING / SKILLS
# ============================================================

def objective_signature(objective: str) -> str:
    normalized = re.sub(
        r"\s+",
        " ",
        objective.lower().strip(),
    )

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()[:16]


def learn_skill(
    objective: str,
    intent: Dict[str, Any],
    verification: Dict[str, Any],
    provider: str,
) -> Dict[str, Any]:
    skill = {
        "skill_id": f"skill-{uuid.uuid4().hex[:10]}",
        "signature": objective_signature(objective),
        "intent": intent.get("category"),
        "provider": provider,
        "confidence": verification.get("confidence", 0),
        "learned_at": now(),
    }

    SKILLS.append(skill)

    # Keep local memory bounded.
    if len(SKILLS) > 200:
        del SKILLS[:-200]

    save_json(SKILLS_FILE, SKILLS)

    return skill


def remember_mission(
    mission: Dict[str, Any],
) -> None:
    MEMORY.append(
        {
            "mission_id": mission.get("mission_id"),
            "objective": mission.get("objective"),
            "intent": mission.get("intent"),
            "confidence": mission.get("confidence"),
            "provider": mission.get("provider"),
            "created_at": mission.get("created_at"),
            "completed_at": mission.get("completed_at"),
        }
    )

    if len(MEMORY) > 500:
        del MEMORY[:-500]

    save_json(MEMORY_FILE, MEMORY)


# ============================================================
# MISSION EXECUTION
# ============================================================

def execute_mission(mission_id: str) -> None:
    with LOCK:
        mission = MISSIONS.get(mission_id)

    if not mission:
        return

    try:
        mission["status"] = "running"
        mission["stage"] = "understand"
        mission["started_at"] = now()

        objective = mission["objective"]
        research = mission["research"]
        verify = mission["verify"]

        # ----------------------------------------
        # UNDERSTAND
        # ----------------------------------------

        intent = classify_intent(objective)
        mission["intent"] = intent

        mission["stage"] = "planning"

        plan = build_plan(
            objective,
            research,
            verify,
        )

        mission["plan"] = plan

        # ----------------------------------------
        # RESEARCH
        # ----------------------------------------

        sources: List[Dict[str, Any]] = []

        if research:
            mission["stage"] = "research"

            sources = research_objective(
                objective,
                mission["max_sources"],
            )

        mission["sources"] = [
            {
                "title": s.get("title"),
                "url": s.get("url"),
                "status": s.get("status"),
            }
            for s in sources
        ]

        mission["evidence_ledger"] = make_evidence_ledger(
            sources
        )

        # ----------------------------------------
        # SYNTHESIZE
        # ----------------------------------------

        mission["stage"] = "synthesize"

        synthesis = synthesize(
            objective,
            sources,
            intent,
        )

        mission["provider"] = synthesis["provider"]
        mission["result"] = synthesis["text"]

        # ----------------------------------------
        # VERIFY
        # ----------------------------------------

        mission["stage"] = "verify"

        if verify:
            verification = verify_result(
                synthesis["text"],
                sources,
            )
        else:
            verification = {
                "checked_claims": 0,
                "supported_claims": 0,
                "confidence": 0.50,
                "checks": [],
            }

        mission["verification"] = verification
        mission["confidence"] = verification["confidence"]

        # ----------------------------------------
        # LEARN
        # ----------------------------------------

        mission["stage"] = "learn"

        skill = learn_skill(
            objective,
            intent,
            verification,
            synthesis["provider"],
        )

        mission["learned_skill"] = skill

        if mission["remember"]:
            remember_mission(mission)

        # ----------------------------------------
        # DELIVER
        # ----------------------------------------

        mission["stage"] = "deliver"
        mission["status"] = "completed"
        mission["completed_at"] = now()

        started = mission.get("started_at")
        if started:
            try:
                start_dt = datetime.fromisoformat(started)
                end_dt = datetime.fromisoformat(
                    mission["completed_at"]
                )
                mission["duration_seconds"] = round(
                    (end_dt - start_dt).total_seconds(),
                    2,
                )
            except Exception:
                mission["duration_seconds"] = None

    except Exception as exc:
        mission["status"] = "failed"
        mission["stage"] = "error"
        mission["error"] = str(exc)[:1000]
        mission["completed_at"] = now()


async def start_background_mission(
    mission_id: str,
) -> None:
    await asyncio.to_thread(
        execute_mission,
        mission_id,
    )


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
def root():
    return {
        "project": PROJECT,
        "version": VERSION,
        "status": "online",
        "message": "AI Infinity Mission Intelligence Core is online.",
        "mode": "one-command",
        "architecture": (
            "intent → plan → research → synthesize → "
            "verify → learn → deliver"
        ),
        "capabilities": [
            "intent-routing",
            "adaptive-mission-planning",
            "web-research",
            "parallel-source-reading",
            "evidence-ledger",
            "ai-provider-routing",
            "reasoning",
            "verification",
            "confidence-estimation",
            "mission-learning",
            "reusable-skills",
            "memory",
            "background-execution",
            "structured-outcomes",
            "free-first",
        ],
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "project": PROJECT,
        "version": VERSION,
        "time": now(),
    }


@app.get("/version")
def version():
    return {
        "project": PROJECT,
        "version": VERSION,
        "target": VERSION,
        "status": "production",
    }


@app.post("/mission", status_code=202)
async def create_mission(request: MissionRequest):
    mission_id = safe_id("mission")

    mission = {
        "mission_id": mission_id,
        "status": "queued",
        "stage": "queued",
        "objective": request.objective,
        "research": request.research,
        "verify": request.verify,
        "remember": request.remember,
        "max_sources": request.max_sources,
        "created_at": now(),
    }

    with LOCK:
        MISSIONS[mission_id] = mission

    asyncio.create_task(
        start_background_mission(mission_id)
    )

    return {
        "mission_id": mission_id,
        "status": "queued",
        "version": VERSION,
        "poll": f"/mission/{mission_id}",
    }


@app.post("/missions", status_code=202)
async def create_mission_alias(request: MissionRequest):
    return await create_mission(request)


@app.get("/mission/{mission_id}")
def get_mission(mission_id: str):
    with LOCK:
        mission = MISSIONS.get(mission_id)

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return mission


@app.get("/missions/{mission_id}")
def get_mission_alias(mission_id: str):
    return get_mission(mission_id)


@app.get("/mission-status/{mission_id}")
def mission_status(mission_id: str):
    with LOCK:
        mission = MISSIONS.get(mission_id)

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return {
        "mission_id": mission_id,
        "status": mission.get("status"),
        "stage": mission.get("stage"),
        "confidence": mission.get("confidence"),
        "provider": mission.get("provider"),
        "version": VERSION,
    }


# ============================================================
# TASK COMPATIBILITY
# ============================================================

@app.post("/task", status_code=202)
async def create_task(request: TaskRequest):
    mission_request = MissionRequest(
        objective=request.command,
        research=True,
        verify=True,
        remember=True,
    )

    return await create_mission(mission_request)


@app.post("/tasks", status_code=202)
async def create_task_alias(request: TaskRequest):
    return await create_task(request)


@app.get("/task/{task_id}")
def get_task(task_id: str):
    return get_mission(task_id)


@app.get("/tasks/{task_id}")
def get_task_alias(task_id: str):
    return get_mission(task_id)


@app.get("/tasks")
def list_tasks():
    with LOCK:
        items = list(MISSIONS.values())

    return {
        "count": len(items),
        "tasks": items[-50:],
    }


# ============================================================
# MEMORY
# ============================================================

@app.post("/memory")
def add_memory(request: MemoryRequest):
    item = {
        "memory_id": f"mem-{uuid.uuid4().hex[:10]}",
        "content": request.content,
        "tags": request.tags,
        "created_at": now(),
    }

    MEMORY.append(item)

    if len(MEMORY) > 500:
        del MEMORY[:-500]

    save_json(MEMORY_FILE, MEMORY)

    return {
        "status": "stored",
        "memory": item,
    }


@app.get("/memory")
def get_memory():
    return {
        "count": len(MEMORY),
        "memory": MEMORY[-100:],
    }


@app.get("/memory/search")
def search_memory(q: str = ""):
    query = q.lower().strip()

    if not query:
        return {
            "count": len(MEMORY),
            "results": MEMORY[-50:],
        }

    results = [
        item
        for item in MEMORY
        if query in json.dumps(
            item,
            ensure_ascii=False,
        ).lower()
    ]

    return {
        "count": len(results),
        "results": results[-50:],
    }


# ============================================================
# SKILLS
# ============================================================

@app.get("/skills")
def get_skills():
    return {
        "count": len(SKILLS),
        "skills": SKILLS[-100:],
    }


@app.get("/skills/search")
def search_skills(q: str = ""):
    query = q.lower().strip()

    if not query:
        return {
            "count": len(SKILLS),
            "results": SKILLS[-50:],
        }

    results = [
        skill
        for skill in SKILLS
        if query in json.dumps(
            skill,
            ensure_ascii=False,
        ).lower()
    ]

    return {
        "count": len(results),
        "results": results[-50:],
    }


# ============================================================
# INTENT / PLAN
# ============================================================

@app.post("/intent")
def intent_endpoint(request: MissionRequest):
    return classify_intent(request.objective)


@app.post("/plan")
def plan_endpoint(request: MissionRequest):
    intent = classify_intent(request.objective)

    return {
        "objective": request.objective,
        "intent": intent,
        "plan": build_plan(
            request.objective,
            request.research,
            request.verify,
        ),
        "version": VERSION,
    }


# ============================================================
# CAPABILITIES / STATUS
# ============================================================

@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "capabilities": [
            "one-command-missions",
            "intent-routing",
            "adaptive-planning",
            "web-research",
            "parallel-source-reading",
            "evidence-ledger",
            "provider-routing",
            "reasoning",
            "verification",
            "confidence-estimation",
            "mission-learning",
            "reusable-skills",
            "memory",
            "background-execution",
            "structured-outcomes",
            "free-first",
        ],
        "providers": {
            "openai_compatible": bool(
                os.getenv("AI_BASE_URL")
                and os.getenv("AI_API_KEY")
                and os.getenv("AI_MODEL")
            ),
            "pollinations": True,
            "deterministic": True,
        },
    }


@app.get("/status")
def status():
    with LOCK:
        mission_count = len(MISSIONS)

    completed = sum(
        1
        for mission in MISSIONS.values()
        if mission.get("status") == "completed"
    )

    running = sum(
        1
        for mission in MISSIONS.values()
        if mission.get("status") in {
            "queued",
            "running",
        }
    )

    failed = sum(
        1
        for mission in MISSIONS.values()
        if mission.get("status") == "failed"
    )

    return {
        "project": PROJECT,
        "version": VERSION,
        "status": "online",
        "missions": {
            "total": mission_count,
            "completed": completed,
            "running": running,
            "failed": failed,
        },
        "memory_items": len(MEMORY),
        "learned_skills": len(SKILLS),
        "free_first": True,
        "time": now(),
    }


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request,
    exc: Exception,
):
    return JSONResponse(
        status_code=500,
        content={
            "error": "AI Infinity internal error",
            "detail": str(exc)[:1000],
            "version": VERSION,
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():
    BASE.mkdir(parents=True, exist_ok=True)


# ============================================================
# LOCAL RUNNER
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
