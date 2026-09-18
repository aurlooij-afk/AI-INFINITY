from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio
import json
import math
import os
import re
import time
import uuid
import requests
from urllib.parse import quote_plus


# ============================================================
# AI INFINITY — TARGET-3.4.0
# Universal Tool Gateway
# ============================================================

VERSION = "TARGET-3.4.0"
PROJECT = "AI Infinity"

BASE = Path("/tmp/ai_infinity")
BASE.mkdir(parents=True, exist_ok=True)

MEMORY_FILE = BASE / "memory.json"
SKILLS_FILE = BASE / "skills.json"
ACTIONS_FILE = BASE / "actions.json"
TOOLS_FILE = BASE / "tools.json"

TIMEOUT = 20

app = FastAPI(
    title=PROJECT,
    version=VERSION,
    description="AI Infinity — Universal Tool Gateway"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# BASIC UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


memory: List[Dict[str, Any]] = load_json(MEMORY_FILE, [])
skills: List[Dict[str, Any]] = load_json(SKILLS_FILE, [])
actions: List[Dict[str, Any]] = load_json(ACTIONS_FILE, [])
tool_logs: List[Dict[str, Any]] = load_json(ACTIONS_FILE, [])
registered_tools: List[Dict[str, Any]] = load_json(TOOLS_FILE, [])


# ============================================================
# MODELS
# ============================================================

class MissionRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=10000)
    research: bool = True
    verify: bool = True
    remember: bool = True
    execute: bool = True
    max_sources: int = Field(default=5, ge=1, le=10)
    max_actions: int = Field(default=5, ge=1, le=10)


class TaskRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=10000)
    duration_minutes: int = Field(default=1, ge=1, le=120)


class MemoryRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    category: str = "general"


class ActionRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=200)
    arguments: Dict[str, Any] = {}


class ToolRequest(BaseModel):
    tool: str = Field(..., min_length=1, max_length=100)
    arguments: Dict[str, Any] = {}
    permission: str = "safe"


class RegisterToolRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=500)
    category: str = "general"
    permission: str = "safe"


# ============================================================
# INTELLIGENCE / INTENT
# ============================================================

def classify_intent(text: str) -> Dict[str, Any]:
    t = text.lower().strip()

    research_words = [
        "research", "search", "find", "investigate",
        "analyze", "study", "look up", "sources"
    ]

    build_words = [
        "build", "create", "make", "develop",
        "implement", "code", "upgrade"
    ]

    planning_words = [
        "plan", "strategy", "roadmap", "steps",
        "how do i", "how to"
    ]

    verification_words = [
        "verify", "check", "validate",
        "confirm", "test", "proof"
    ]

    execution_words = [
        "run", "execute", "perform", "do",
        "launch", "start"
    ]

    scores = {
        "research": sum(x in t for x in research_words),
        "build": sum(x in t for x in build_words),
        "planning": sum(x in t for x in planning_words),
        "verification": sum(x in t for x in verification_words),
        "execution": sum(x in t for x in execution_words),
    }

    intent = max(scores, key=scores.get) if max(scores.values()) > 0 else "general"

    return {
        "intent": intent,
        "scores": scores,
        "confidence": round(
            min(0.99, 0.50 + (scores[intent] * 0.10)),
            2
        )
    }


# ============================================================
# PLANNER
# ============================================================

def build_plan(objective: str) -> Dict[str, Any]:
    intent = classify_intent(objective)

    steps = [
        "understand",
        "plan",
    ]

    if intent["intent"] in ["research", "general", "verification"]:
        steps.append("research")

    steps.extend([
        "reason",
        "verify",
        "tool_route",
        "observe",
        "recover",
        "learn",
        "deliver"
    ])

    return {
        "objective": objective,
        "intent": intent,
        "steps": steps,
        "architecture": "intent → plan → research → reason → verify → tool_route → observe → recover → learn → deliver"
    }


# ============================================================
# WEB RESEARCH
# ============================================================

def search_web(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)

    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=TIMEOUT
        )

        if r.status_code != 200:
            return []

        html = r.text

        results = []

        pattern = re.compile(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.I | re.S
        )

        for match in pattern.finditer(html):
            link = match.group(1)
            title = re.sub("<.*?>", "", match.group(2))
            title = title.strip()

            if title and link:
                results.append({
                    "title": title,
                    "url": link
                })

            if len(results) >= max_results:
                break

        return results

    except Exception as e:
        return [{
            "error": str(e)
        }]


def read_source(item: Dict[str, Any]) -> Dict[str, Any]:
    url = item.get("url")

    if not url:
        return item

    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=TIMEOUT
        )

        text = re.sub(r"<script.*?</script>", " ", r.text, flags=re.I | re.S)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return {
            **item,
            "status": r.status_code,
            "content": text[:6000]
        }

    except Exception as e:
        return {
            **item,
            "error": str(e)
        }


def research_objective(
    objective: str,
    max_sources: int = 5
) -> List[Dict[str, Any]]:

    results = search_web(objective, max_sources)

    valid = [
        x for x in results
        if x.get("url")
    ]

    if not valid:
        return results

    output = []

    with ThreadPoolExecutor(
        max_workers=min(5, len(valid))
    ) as executor:

        futures = [
            executor.submit(read_source, x)
            for x in valid
        ]

        for future in as_completed(futures):
            try:
                output.append(future.result())
            except Exception as e:
                output.append({"error": str(e)})

    return output


# ============================================================
# AI PROVIDERS
# ============================================================

def call_openai_compatible(prompt: str) -> Optional[str]:

    base_url = os.getenv("AI_BASE_URL")
    api_key = os.getenv("AI_API_KEY")
    model = os.getenv("AI_MODEL")

    if not base_url or not api_key or not model:
        return None

    try:
        url = base_url.rstrip("/") + "/chat/completions"

        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are the reasoning engine of AI Infinity."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.2
            },
            timeout=TIMEOUT
        )

        if response.status_code != 200:
            return None

        data = response.json()

        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content")
        )

    except Exception:
        return None


def call_pollinations(prompt: str) -> Optional[str]:

    try:
        url = (
            "https://text.pollinations.ai/"
            + quote_plus(prompt)
        )

        response = requests.get(
            url,
            timeout=TIMEOUT
        )

        if response.status_code == 200:
            return response.text[:12000]

    except Exception:
        pass

    return None


def deterministic_reasoning(
    objective: str,
    research: List[Dict[str, Any]]
) -> str:

    evidence = []

    for item in research[:5]:
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")

        evidence.append(
            f"SOURCE: {title}\nURL: {url}\n"
            f"CONTENT: {content[:1200]}"
        )

    prompt = f"""
AI Infinity reasoning task.

Objective:
{objective}

Evidence:
{chr(10).join(evidence)}

Produce:
1. Direct answer
2. Important evidence
3. Uncertainty
4. Recommended next actions
"""

    answer = call_openai_compatible(prompt)

    if answer:
        return answer

    answer = call_pollinations(prompt)

    if answer:
        return answer

    return (
        f"AI Infinity analyzed the objective:\n\n"
        f"{objective}\n\n"
        f"Evidence items collected: {len(research)}.\n"
        f"The available local reasoning engine produced a "
        f"structured result, but no external AI provider was available."
    )


# ============================================================
# VERIFICATION
# ============================================================

def verify_result(
    objective: str,
    reasoning: str,
    research: List[Dict[str, Any]]
) -> Dict[str, Any]:

    source_count = len([
        x for x in research
        if x.get("url")
    ])

    confidence = 0.50

    if source_count >= 3:
        confidence += 0.20

    if source_count >= 5:
        confidence += 0.10

    if len(reasoning) > 500:
        confidence += 0.10

    confidence = min(0.95, confidence)

    return {
        "verified": confidence >= 0.70,
        "confidence": round(confidence, 2),
        "sources_checked": source_count,
        "verification_method": "evidence_count + reasoning_integrity"
    }


# ============================================================
# UNIVERSAL TOOL GATEWAY
# ============================================================

SAFE_ACTIONS = {
    "status",
    "health",
    "version",
    "capabilities",
    "time",
    "memory_count",
    "skills_count",
    "tools",
    "tool_logs"
}


TOOL_ALIASES = {
    "check status": "status",
    "system status": "status",
    "check health": "health",
    "health check": "health",
    "get version": "version",
    "what time is it": "time",
    "list capabilities": "capabilities",
    "show capabilities": "capabilities",
    "memory": "memory_count",
    "skills": "skills_count",
    "list tools": "tools",
    "show tools": "tools",
    "tool logs": "tool_logs"
}


def builtin_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:

    name = TOOL_ALIASES.get(
        name.lower().strip(),
        name.lower().strip()
    )

    if name == "status":
        return {
            "status": "online",
            "project": PROJECT,
            "version": VERSION,
            "missions": len(actions),
            "memory": len(memory),
            "skills": len(skills),
            "tools": len(registered_tools)
        }

    if name == "health":
        return {
            "healthy": True,
            "project": PROJECT,
            "version": VERSION
        }

    if name == "version":
        return {
            "project": PROJECT,
            "version": VERSION,
            "target": VERSION,
            "status": "production"
        }

    if name == "time":
        return {
            "utc": now_iso()
        }

    if name == "memory_count":
        return {
            "count": len(memory)
        }

    if name == "skills_count":
        return {
            "count": len(skills)
        }

    if name == "tools":
        return {
            "count": len(registered_tools),
            "tools": registered_tools
        }

    if name == "tool_logs":
        return {
            "count": len(tool_logs),
            "logs": tool_logs[-50:]
        }

    if name == "capabilities":
        return {
            "project": PROJECT,
            "version": VERSION,
            "capabilities": [
                "intent classification",
                "mission planning",
                "web research",
                "parallel source reading",
                "AI provider routing",
                "reasoning",
                "verification",
                "universal tool gateway",
                "tool permissions",
                "tool logging",
                "tool retry",
                "tool fallback",
                "tool-result verification",
                "memory",
                "skills",
                "recovery",
                "background missions"
            ],
            "safe_tools": sorted(SAFE_ACTIONS)
        }

    raise ValueError("Unknown built-in tool")


def tool_permission_allowed(
    permission: str,
    requested: str
) -> bool:

    levels = {
        "safe": 0,
        "standard": 1,
        "external": 2,
        "dangerous": 3
    }

    return levels.get(requested, 99) <= levels.get(permission, 0)


def route_tool(
    name: str,
    arguments: Dict[str, Any],
    requested_permission: str = "safe",
    retry: int = 1
) -> Dict[str, Any]:

    tool_id = make_id("tool")
    started = time.time()

    canonical = TOOL_ALIASES.get(
        name.lower().strip(),
        name.lower().strip()
    )

    log = {
        "tool_id": tool_id,
        "tool": canonical,
        "arguments": arguments,
        "permission_requested": requested_permission,
        "started_at": now_iso(),
        "attempts": 0
    }

    if canonical not in SAFE_ACTIONS:
        log["status"] = "blocked"
        log["reason"] = "tool_not_allowlisted"
        log["duration_ms"] = round(
            (time.time() - started) * 1000,
            2
        )

        tool_logs.append(log)
        save_json(TOOLS_FILE, registered_tools)
        save_json(ACTIONS_FILE, tool_logs)

        return {
            "success": False,
            "status": "blocked",
            "tool_id": tool_id,
            "tool": canonical,
            "reason": "Tool is not in the safe allowlist."
        }

    if requested_permission != "safe":
        log["status"] = "blocked"
        log["reason"] = "permission_boundary"

        tool_logs.append(log)
        save_json(ACTIONS_FILE, tool_logs)

        return {
            "success": False,
            "status": "blocked",
            "tool_id": tool_id,
            "reason": "Permission boundary blocked this tool."
        }

    last_error = None

    for attempt in range(retry + 1):

        log["attempts"] = attempt + 1

        try:
            result = builtin_tool(
                canonical,
                arguments
            )

            verification = {
                "verified": isinstance(result, dict),
                "type": type(result).__name__,
                "non_empty": bool(result)
            }

            log["status"] = "completed"
            log["result_verified"] = verification
            log["duration_ms"] = round(
                (time.time() - started) * 1000,
                2
            )

            tool_logs.append(log)
            save_json(ACTIONS_FILE, tool_logs)

            return {
                "success": True,
                "status": "completed",
                "tool_id": tool_id,
                "tool": canonical,
                "attempts": attempt + 1,
                "result": result,
                "verification": verification
            }

        except Exception as e:
            last_error = str(e)

    log["status"] = "failed"
    log["error"] = last_error
    log["duration_ms"] = round(
        (time.time() - started) * 1000,
        2
    )

    tool_logs.append(log)
    save_json(ACTIONS_FILE, tool_logs)

    return {
        "success": False,
        "status": "failed",
        "tool_id": tool_id,
        "tool": canonical,
        "error": last_error
    }


# ============================================================
# TOOL SELECTION
# ============================================================

def select_tools(objective: str) -> List[str]:

    t = objective.lower()

    selected = []

    if "status" in t:
        selected.append("status")

    if "health" in t:
        selected.append("health")

    if "version" in t:
        selected.append("version")

    if "time" in t:
        selected.append("time")

    if "capabilit" in t:
        selected.append("capabilities")

    if "memory" in t:
        selected.append("memory_count")

    if "skill" in t:
        selected.append("skills_count")

    if "tool" in t:
        selected.append("tools")

    return list(dict.fromkeys(selected))


# ============================================================
# ACTION COMPATIBILITY LAYER
# ============================================================

def propose_actions(
    objective: str,
    max_actions: int = 5
) -> List[str]:

    selected = select_tools(objective)

    return selected[:max_actions]


def execute_action(
    action: str,
    arguments: Dict[str, Any] = None
) -> Dict[str, Any]:

    arguments = arguments or {}

    result = route_tool(
        action,
        arguments,
        requested_permission="safe",
        retry=1
    )

    actions.append({
        "action_id": make_id("action"),
        "action": action,
        "arguments": arguments,
        "result": result,
        "timestamp": now_iso()
    })

    save_json(ACTIONS_FILE, actions)

    return result


# ============================================================
# MEMORY
# ============================================================

def remember_mission(
    objective: str,
    result: Dict[str, Any]
):
    item = {
        "id": make_id("mem"),
        "timestamp": now_iso(),
        "objective": objective,
        "summary": result.get("summary", ""),
        "confidence": result.get("verification", {}).get(
            "confidence", 0
        )
    }

    memory.append(item)

    # Keep memory bounded
    if len(memory) > 500:
        del memory[:-500]

    save_json(MEMORY_FILE, memory)


def learn_skill(
    objective: str,
    result: Dict[str, Any]
):

    skill = {
        "id": make_id("skill"),
        "timestamp": now_iso(),
        "name": classify_intent(objective)["intent"],
        "objective_pattern": objective[:500],
        "confidence": result.get(
            "verification", {}
        ).get("confidence", 0)
    }

    skills.append(skill)

    if len(skills) > 500:
        del skills[:-500]

    save_json(SKILLS_FILE, skills)


# ============================================================
# MISSION ENGINE
# ============================================================

def execute_mission_sync(
    mission_id: str,
    request: MissionRequest
) -> Dict[str, Any]:

    started = time.time()

    result: Dict[str, Any] = {
        "mission_id": mission_id,
        "project": PROJECT,
        "version": VERSION,
        "status": "running",
        "objective": request.objective,
        "started_at": now_iso()
    }

    try:

        # 1. UNDERSTAND
        result["intent"] = classify_intent(
            request.objective
        )

        # 2. PLAN
        result["plan"] = build_plan(
            request.objective
        )

        # 3. RESEARCH
        research = []

        if request.research:
            research = research_objective(
                request.objective,
                request.max_sources
            )

        result["research"] = research

        # 4. REASON
        reasoning = deterministic_reasoning(
            request.objective,
            research
        )

        result["reasoning"] = reasoning

        # 5. VERIFY
        if request.verify:
            verification = verify_result(
                request.objective,
                reasoning,
                research
            )
        else:
            verification = {
                "verified": False,
                "confidence": 0,
                "verification_skipped": True
            }

        result["verification"] = verification

        # 6. TOOL ROUTING
        routed_tools = []

        if request.execute:
            tool_names = propose_actions(
                request.objective,
                request.max_actions
            )

            for tool_name in tool_names:
                routed_tools.append(
                    route_tool(
                        tool_name,
                        {},
                        requested_permission="safe",
                        retry=1
                    )
                )

        result["tools"] = routed_tools

        # 7. OBSERVE
        result["observation"] = {
            "research_items": len(research),
            "tools_attempted": len(routed_tools),
            "tools_successful": sum(
                1 for x in routed_tools
                if x.get("success")
            )
        }

        # 8. RECOVER
        failed_tools = [
            x for x in routed_tools
            if not x.get("success")
        ]

        result["recovery"] = {
            "attempted": bool(failed_tools),
            "failed_tools": len(failed_tools),
            "strategy": "retry-safe-tools-once"
        }

        # 9. LEARN
        result["summary"] = reasoning[:3000]

        if request.remember:
            remember_mission(
                request.objective,
                result
            )

            learn_skill(
                request.objective,
                result
            )

        # 10. DELIVER
        result["status"] = "completed"
        result["duration_seconds"] = round(
            time.time() - started,
            3
        )
        result["completed_at"] = now_iso()

        return result

    except Exception as e:

        result["status"] = "failed"
        result["error"] = str(e)
        result["duration_seconds"] = round(
            time.time() - started,
            3
        )

        return result


# ============================================================
# BACKGROUND MISSIONS
# ============================================================

MISSIONS: Dict[str, Dict[str, Any]] = {}


async def background_mission(
    mission_id: str,
    request: MissionRequest
):

    MISSIONS[mission_id]["status"] = "running"

    result = await asyncio.to_thread(
        execute_mission_sync,
        mission_id,
        request
    )

    MISSIONS[mission_id] = result


# ============================================================
# API
# ============================================================

@app.get("/")
def root():

    return {
        "project": PROJECT,
        "version": VERSION,
        "status": "production",
        "message": "AI Infinity Universal Tool Gateway is online.",
        "architecture": (
            "intent → plan → research → reason → verify "
            "→ tool_route → observe → recover → learn → deliver"
        )
    }


@app.get("/health")
def health():

    return {
        "status": "healthy",
        "project": PROJECT,
        "version": VERSION
    }


@app.get("/version")
def version():

    return {
        "project": PROJECT,
        "version": VERSION,
        "target": VERSION,
        "status": "production"
    }


@app.get("/status")
def status():

    return {
        "project": PROJECT,
        "version": VERSION,
        "status": "online",
        "missions": len(MISSIONS),
        "actions": len(actions),
        "memory": len(memory),
        "skills": len(skills),
        "tools": len(registered_tools),
        "tool_logs": len(tool_logs)
    }


@app.get("/capabilities")
def capabilities():

    return builtin_tool(
        "capabilities",
        {}
    )


# ============================================================
# MISSION ENDPOINTS
# ============================================================

@app.post("/mission")
async def create_mission(
    request: MissionRequest
):

    mission_id = make_id("mission")

    MISSIONS[mission_id] = {
        "mission_id": mission_id,
        "project": PROJECT,
        "version": VERSION,
        "status": "queued",
        "objective": request.objective,
        "created_at": now_iso()
    }

    asyncio.create_task(
        background_mission(
            mission_id,
            request
        )
    )

    return {
        "mission_id": mission_id,
        "task_id": mission_id,
        "status": "queued",
        "version": VERSION
    }


@app.post("/missions")
async def create_mission_alias(
    request: MissionRequest
):

    return await create_mission(request)


@app.get("/mission/{mission_id}")
def get_mission(
    mission_id: str
):

    if mission_id not in MISSIONS:
        raise HTTPException(
            status_code=404,
            detail="Mission not found"
        )

    return MISSIONS[mission_id]


@app.get("/missions/{mission_id}")
def get_mission_alias(
    mission_id: str
):

    return get_mission(mission_id)


@app.get("/mission-status/{mission_id}")
def mission_status(
    mission_id: str
):

    return get_mission(mission_id)


# ============================================================
# TOOL ENDPOINTS
# ============================================================

@app.post("/tool")
def run_tool(
    request: ToolRequest
):

    return route_tool(
        request.tool,
        request.arguments,
        request.permission,
        retry=1
    )


@app.post("/tools/run")
def run_tool_alias(
    request: ToolRequest
):

    return run_tool(request)


@app.get("/tools")
def list_tools():

    builtin = [
        {
            "name": x,
            "category": "builtin",
            "permission": "safe"
        }
        for x in sorted(SAFE_ACTIONS)
    ]

    return {
        "version": VERSION,
        "builtin_tools": builtin,
        "registered_tools": registered_tools
    }


@app.post("/tools/register")
def register_tool(
    request: RegisterToolRequest
):

    # Registration does NOT automatically grant execution rights.
    tool = {
        "id": make_id("registered"),
        "name": request.name,
        "description": request.description,
        "category": request.category,
        "permission": request.permission,
        "execution": "disabled-until-explicitly-implemented",
        "created_at": now_iso()
    }

    registered_tools.append(tool)

    if len(registered_tools) > 200:
        del registered_tools[:-200]

    save_json(TOOLS_FILE, registered_tools)

    return {
        "success": True,
        "tool": tool,
        "message": (
            "Tool registered safely. "
            "Registration alone does not grant arbitrary execution."
        )
    }


@app.get("/tools/logs")
def get_tool_logs():

    return {
        "count": len(tool_logs),
        "logs": tool_logs[-100:]
    }


# ============================================================
# ACTION ENDPOINTS
# ============================================================

@app.post("/action")
def action(
    request: ActionRequest
):

    return execute_action(
        request.action,
        request.arguments
    )


@app.get("/actions")
def list_actions():

    return {
        "count": len(actions),
        "actions": actions[-100:]
    }


@app.get("/actions/{action_id}")
def get_action(
    action_id: str
):

    for item in actions:
        if item.get("action_id") == action_id:
            return item

    raise HTTPException(
        status_code=404,
        detail="Action not found"
    )


# ============================================================
# INTENT / PLAN
# ============================================================

@app.get("/intent")
def intent(
    text: str
):

    return classify_intent(text)


@app.get("/plan")
def plan(
    objective: str
):

    return build_plan(objective)


# ============================================================
# MEMORY
# ============================================================

@app.post("/memory")
def add_memory(
    request: MemoryRequest
):

    item = {
        "id": make_id("mem"),
        "timestamp": now_iso(),
        "category": request.category,
        "content": request.content
    }

    memory.append(item)

    if len(memory) > 500:
        del memory[:-500]

    save_json(MEMORY_FILE, memory)

    return {
        "success": True,
        "memory": item
    }


@app.get("/memory")
def get_memory():

    return {
        "count": len(memory),
        "memory": memory
    }


@app.get("/memory/search")
def search_memory(
    q: str
):

    q = q.lower()

    results = [
        x for x in memory
        if q in json.dumps(
            x,
            ensure_ascii=False
        ).lower()
    ]

    return {
        "query": q,
        "count": len(results),
        "results": results
    }


# ============================================================
# SKILLS
# ============================================================

@app.get("/skills")
def get_skills():

    return {
        "count": len(skills),
        "skills": skills
    }


@app.get("/skills/search")
def search_skills(
    q: str
):

    q = q.lower()

    results = [
        x for x in skills
        if q in json.dumps(
            x,
            ensure_ascii=False
        ).lower()
    ]

    return {
        "query": q,
        "count": len(results),
        "results": results
    }


# ============================================================
# COMPATIBILITY / TASK API
# ============================================================

@app.post("/task")
async def task(
    request: TaskRequest
):

    mission = MissionRequest(
        objective=request.command,
        research=True,
        verify=True,
        remember=True,
        execute=True
    )

    return await create_mission(mission)


@app.post("/tasks")
async def tasks(
    request: TaskRequest
):

    return await task(request)


@app.get("/task/{task_id}")
def get_task(
    task_id: str
):

    return get_mission(task_id)


@app.get("/tasks/{task_id}")
def get_task_alias(
    task_id: str
):

    return get_mission(task_id)


# ============================================================
# ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception
):

    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "project": PROJECT,
            "version": VERSION,
            "detail": str(exc),
            "path": str(request.url.path)
        }
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    global memory
    global skills
    global actions
    global registered_tools

    memory = load_json(
        MEMORY_FILE,
        memory
    )

    skills = load_json(
        SKILLS_FILE,
        skills
    )

    actions = load_json(
        ACTIONS_FILE,
        actions
    )

    registered_tools = load_json(
        TOOLS_FILE,
        registered_tools
    )


# ============================================================
# LOCAL RUNNER
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000"))
    )
