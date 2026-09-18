"""
AI Infinity — TARGET-3.3.0
Action & Tool Orchestration Core

Mission flow:
Intent
  ↓
Plan
  ↓
Research
  ↓
Reason
  ↓
Verify
  ↓
Execute
  ↓
Observe
  ↓
Recover
  ↓
Learn
  ↓
Deliver

Free-first / zero-new-dependencies architecture.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
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

VERSION = "TARGET-3.3.0"
PROJECT = "AI Infinity"

BASE = Path("/tmp/ai_infinity")
MEMORY_FILE = BASE / "memory.json"
SKILLS_FILE = BASE / "skills.json"
ACTIONS_FILE = BASE / "actions.json"

BASE.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Free-first Action & Tool Orchestration Engine",
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

MEMORY: List[Dict[str, Any]] = []
SKILLS: List[Dict[str, Any]] = []
ACTIONS: List[Dict[str, Any]] = []


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
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


MEMORY = load_json(MEMORY_FILE, [])
SKILLS = load_json(SKILLS_FILE, [])
ACTIONS = load_json(ACTIONS_FILE, [])


# ============================================================
# MODELS
# ============================================================

class MissionRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=20000)
    research: bool = True
    verify: bool = True
    remember: bool = True
    execute: bool = True
    max_sources: int = Field(default=6, ge=1, le=12)
    max_actions: int = Field(default=5, ge=1, le=10)


class TaskRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=20000)
    duration_minutes: int = Field(default=1, ge=1, le=120)


class MemoryRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)
    tags: List[str] = Field(default_factory=list)


class ActionRequest(BaseModel):
    action: str = Field(..., min_length=2, max_length=10000)
    target: Optional[str] = Field(default=None, max_length=2000)


# ============================================================
# INTENT
# ============================================================

def classify_intent(text: str) -> Dict[str, Any]:
    t = text.lower()

    groups = {
        "research": [
            "research",
            "analyze",
            "analysis",
            "investigate",
            "study",
            "compare",
            "find",
            "latest",
            "evidence",
            "sources",
        ],
        "build": [
            "build",
            "create",
            "make",
            "develop",
            "implement",
            "code",
            "design",
            "launch",
        ],
        "planning": [
            "plan",
            "strategy",
            "roadmap",
            "steps",
            "prepare",
        ],
        "verification": [
            "verify",
            "check",
            "validate",
            "confirm",
            "test",
        ],
        "execution": [
            "execute",
            "run",
            "do",
            "perform",
            "automate",
            "send",
            "generate",
            "deploy",
        ],
    }

    scores = {
        name: sum(1 for word in words if word in t)
        for name, words in groups.items()
    }

    category = max(
        scores,
        key=scores.get,
    ) if any(scores.values()) else "general"

    highest = scores.get(category, 0)

    confidence = (
        min(0.95, 0.55 + highest * 0.08)
        if category != "general"
        else 0.50
    )

    return {
        "category": category,
        "scores": scores,
        "confidence": round(confidence, 2),
    }


# ============================================================
# MISSION PLANNER
# ============================================================

def build_plan(
    objective: str,
    research: bool,
    verify: bool,
    execute: bool,
) -> List[Dict[str, Any]]:

    stages = [
        {
            "id": "understand",
            "name": "Understand",
            "purpose": "Interpret the requested outcome.",
        },
        {
            "id": "plan",
            "name": "Plan",
            "purpose": "Construct an ordered mission strategy.",
        },
    ]

    if research:
        stages.append(
            {
                "id": "research",
                "name": "Research",
                "purpose": "Collect external evidence.",
            }
        )

    stages.append(
        {
            "id": "reason",
            "name": "Reason",
            "purpose": "Synthesize available evidence.",
        }
    )

    if verify:
        stages.append(
            {
                "id": "verify",
                "name": "Verify",
                "purpose": "Check important claims.",
            }
        )

    if execute:
        stages.extend(
            [
                {
                    "id": "execute",
                    "name": "Execute",
                    "purpose": "Perform safe supported actions.",
                },
                {
                    "id": "observe",
                    "name": "Observe",
                    "purpose": "Measure action results.",
                },
                {
                    "id": "recover",
                    "name": "Recover",
                    "purpose": "Retry or safely degrade failed actions.",
                },
            ]
        )

    stages.extend(
        [
            {
                "id": "learn",
                "name": "Learn",
                "purpose": "Store reusable mission intelligence.",
            },
            {
                "id": "deliver",
                "name": "Deliver",
                "purpose": "Return structured results.",
            },
        ]
    )

    return stages


# ============================================================
# WEB RESEARCH
# ============================================================

def search_web(
    query: str,
    limit: int = 6,
) -> List[Dict[str, str]]:

    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 AI-Infinity/3.3"
            },
            timeout=20,
        )

        if response.status_code != 200:
            return []

        pattern = re.compile(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.I | re.S,
        )

        results = []

        for match in pattern.finditer(response.text):
            url = match.group(1)

            title = re.sub(
                r"<.*?>",
                "",
                match.group(2),
            )

            title = re.sub(
                r"\s+",
                " ",
                title,
            ).strip()

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


def read_source(
    item: Dict[str, str],
) -> Dict[str, Any]:

    url = item.get("url", "")

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 AI-Infinity/3.3"
            },
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

        text = re.sub(
            r"<[^>]+>",
            " ",
            text,
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

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

    discovered = search_web(
        objective,
        max_sources,
    )

    if not discovered:
        return []

    sources = []

    with ThreadPoolExecutor(
        max_workers=min(6, len(discovered))
    ) as executor:

        futures = [
            executor.submit(
                read_source,
                item,
            )
            for item in discovered
        ]

        for future in as_completed(futures):
            try:
                sources.append(
                    future.result()
                )
            except Exception:
                pass

    return sources


# ============================================================
# AI PROVIDERS
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
        response = requests.post(
            base_url.rstrip("/")
            + "/chat/completions",

            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },

            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are AI Infinity's reasoning "
                            "and execution-planning engine. "
                            "Be factual and structured. "
                            "Never invent completed actions."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "temperature": 0.2,
            },

            timeout=60,
        )

        if response.status_code >= 400:
            return None

        data = response.json()

        choices = data.get(
            "choices",
            [],
        )

        if not choices:
            return None

        content = choices[0].get(
            "message",
            {},
        ).get("content")

        return (
            str(content).strip()
            if content
            else None
        )

    except Exception:
        return None


def call_pollinations(
    prompt: str,
) -> Optional[str]:

    try:
        response = requests.get(
            "https://text.pollinations.ai/",
            params={
                "prompt": prompt,
            },
            headers={
                "User-Agent": "AI-Infinity/3.3",
            },
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
# REASONING
# ============================================================

def deterministic_reasoning(
    objective: str,
    sources: List[Dict[str, Any]],
) -> str:

    source_lines = []

    for source in sources[:8]:
        source_lines.append(
            "- "
            + source.get("title", "Untitled")
            + " — "
            + source.get("url", "")
        )

    if not source_lines:
        source_lines = [
            "- No external sources successfully retrieved."
        ]

    return (
        "AI Infinity Mission Analysis\n\n"
        f"Objective:\n{objective}\n\n"
        "Execution strategy:\n"
        "1. Understand the objective.\n"
        "2. Gather evidence when requested.\n"
        "3. Synthesize findings.\n"
        "4. Verify important claims.\n"
        "5. Execute only supported/safe local actions.\n"
        "6. Observe the result.\n"
        "7. Recover from recoverable failures.\n\n"
        "Sources:\n"
        + "\n".join(source_lines)
    )


def synthesize(
    objective: str,
    sources: List[Dict[str, Any]],
    intent: Dict[str, Any],
) -> Dict[str, Any]:

    evidence = []

    for source in sources[:8]:
        evidence.append(
            "\n".join(
                [
                    f"TITLE: {source.get('title', '')}",
                    f"URL: {source.get('url', '')}",
                    f"CONTENT: {source.get('content', '')[:5000]}",
                ]
            )
        )

    prompt = f"""
AI INFINITY MISSION

OBJECTIVE:
{objective}

INTENT:
{json.dumps(intent, ensure_ascii=False)}

EVIDENCE:
{
    chr(10).join(evidence)
    if evidence
    else "No external evidence."
}

Return:

1. Mission interpretation
2. Evidence-supported findings
3. Uncertainty
4. Proposed actions
5. Expected observations
6. Recovery strategy

Never claim an external action happened unless AI Infinity
actually performed it.
"""

    providers = [
        (
            "openai-compatible",
            call_openai_compatible,
        ),
        (
            "pollinations",
            call_pollinations,
        ),
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
            pass

    return {
        "provider": "deterministic",
        "text": deterministic_reasoning(
            objective,
            sources,
        ),
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_result(
    result_text: str,
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:

    claims = [
        line.strip("- • \t")
        for line in result_text.splitlines()
        if len(line.strip()) >= 35
    ][:10]

    combined = " ".join(
        source.get(
            "content",
            "",
        ).lower()
        for source in sources
    )

    checks = []
    supported = 0

    for claim in claims:

        words = re.findall(
            r"[a-zA-Z0-9]{5,}",
            claim.lower(),
        )

        words = [
            w
            for w in words
            if w not in {
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
            for word in words[:12]
            if word in combined
        )

        ok = (
            matches >= 2
            if words
            else False
        )

        if ok:
            supported += 1

        checks.append(
            {
                "claim": claim,
                "supported": ok,
                "match_count": matches,
            }
        )

    if claims:
        confidence = round(
            supported / len(claims),
            2,
        )
    elif sources:
        confidence = 0.55
    else:
        confidence = 0.35

    return {
        "checked_claims": len(claims),
        "supported_claims": supported,
        "confidence": confidence,
        "checks": checks,
    }


# ============================================================
# ACTION ENGINE
# ============================================================

SAFE_ACTIONS = {
    "status",
    "health",
    "version",
    "capabilities",
    "time",
    "memory_count",
    "skills_count",
}


def normalize_action(
    action: str,
) -> str:

    action = action.lower().strip()

    aliases = {
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
    }

    return aliases.get(
        action,
        action,
    )


def execute_safe_action(
    action: str,
    target: Optional[str] = None,
) -> Dict[str, Any]:

    normalized = normalize_action(action)

    action_id = make_id("action")

    started = now()

    result: Dict[str, Any] = {
        "action_id": action_id,
        "requested": action,
        "normalized": normalized,
        "target": target,
        "status": "started",
        "started_at": started,
    }

    try:

        if normalized == "status":
            value = {
                "project": PROJECT,
                "version": VERSION,
                "status": "online",
                "missions": len(MISSIONS),
            }

        elif normalized == "health":
            value = {
                "status": "healthy",
                "version": VERSION,
            }

        elif normalized == "version":
            value = {
                "project": PROJECT,
                "version": VERSION,
            }

        elif normalized == "capabilities":
            value = {
                "research": True,
                "reasoning": True,
                "verification": True,
                "execution": True,
                "observation": True,
                "recovery": True,
                "learning": True,
            }

        elif normalized == "time":
            value = {
                "utc": now(),
            }

        elif normalized == "memory_count":
            value = {
                "count": len(MEMORY),
            }

        elif normalized == "skills_count":
            value = {
                "count": len(SKILLS),
            }

        else:
            result.update(
                {
                    "status": "blocked",
                    "reason": (
                        "Action is not in the safe built-in "
                        "action registry."
                    ),
                }
            )

            result["completed_at"] = now()

            return result

        result.update(
            {
                "status": "completed",
                "result": value,
                "completed_at": now(),
            }
        )

    except Exception as exc:

        result.update(
            {
                "status": "failed",
                "error": str(exc)[:500],
                "completed_at": now(),
            }
        )

    return result


def observe_action(
    action_result: Dict[str, Any],
) -> Dict[str, Any]:

    status = action_result.get(
        "status",
        "unknown",
    )

    return {
        "observed_at": now(),
        "action_id": action_result.get(
            "action_id"
        ),
        "status": status,
        "success": status == "completed",
        "has_result": "result" in action_result,
    }


def recover_action(
    action: str,
    target: Optional[str],
    previous: Dict[str, Any],
) -> Dict[str, Any]:

    if previous.get("status") != "failed":
        return {
            "attempted": False,
            "reason": "No recovery required.",
        }

    retry = execute_safe_action(
        action,
        target,
    )

    return {
        "attempted": True,
        "retry": retry,
        "recovered": retry.get("status") == "completed",
    }


def record_action(
    action_result: Dict[str, Any],
) -> None:

    ACTIONS.append(action_result)

    if len(ACTIONS) > 500:
        del ACTIONS[:-500]

    save_json(
        ACTIONS_FILE,
        ACTIONS,
    )


# ============================================================
# ACTION EXTRACTION
# ============================================================

def propose_actions(
    objective: str,
    max_actions: int,
) -> List[str]:

    text = objective.lower()

    proposed: List[str] = []

    if "status" in text:
        proposed.append("status")

    if "health" in text:
        proposed.append("health")

    if "version" in text:
        proposed.append("version")

    if "capabilities" in text:
        proposed.append("capabilities")

    if "time" in text:
        proposed.append("time")

    if "memory" in text:
        proposed.append("memory_count")

    if "skills" in text:
        proposed.append("skills_count")

    return proposed[:max_actions]


# ============================================================
# EVIDENCE
# ============================================================

def make_evidence_ledger(
    sources: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    ledger = []

    for source in sources:

        content = source.get(
            "content",
            "",
        )

        digest = hashlib.sha256(
            content.encode(
                "utf-8",
                errors="ignore",
            )
        ).hexdigest()[:16]

        ledger.append(
            {
                "evidence_id": f"ev-{digest}",
                "title": source.get("title", ""),
                "url": source.get("url", ""),
                "status": source.get("status"),
                "content_hash": digest,
            }
        )

    return ledger


# ============================================================
# LEARNING
# ============================================================

def objective_signature(
    objective: str,
) -> str:

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
    confidence: float,
    provider: str,
) -> Dict[str, Any]:

    skill = {
        "skill_id": make_id("skill"),
        "signature": objective_signature(
            objective
        ),
        "intent": intent.get(
            "category"
        ),
        "provider": provider,
        "confidence": confidence,
        "learned_at": now(),
    }

    SKILLS.append(skill)

    if len(SKILLS) > 200:
        del SKILLS[:-200]

    save_json(
        SKILLS_FILE,
        SKILLS,
    )

    return skill


def remember_mission(
    mission: Dict[str, Any],
) -> None:

    MEMORY.append(
        {
            "mission_id": mission.get(
                "mission_id"
            ),
            "objective": mission.get(
                "objective"
            ),
            "intent": mission.get(
                "intent"
            ),
            "confidence": mission.get(
                "confidence"
            ),
            "provider": mission.get(
                "provider"
            ),
            "actions": mission.get(
                "actions"
            ),
            "created_at": mission.get(
                "created_at"
            ),
            "completed_at": mission.get(
                "completed_at"
            ),
        }
    )

    if len(MEMORY) > 500:
        del MEMORY[:-500]

    save_json(
        MEMORY_FILE,
        MEMORY,
    )


# ============================================================
# MISSION EXECUTION
# ============================================================

def execute_mission(
    mission_id: str,
) -> None:

    with LOCK:
        mission = MISSIONS.get(
            mission_id
        )

    if not mission:
        return

    try:

        mission["status"] = "running"
        mission["stage"] = "understand"
        mission["started_at"] = now()

        objective = mission[
            "objective"
        ]

        # ------------------------------------
        # UNDERSTAND
        # ------------------------------------

        intent = classify_intent(
            objective
        )

        mission["intent"] = intent

        # ------------------------------------
        # PLAN
        # ------------------------------------

        mission["stage"] = "plan"

        mission["plan"] = build_plan(
            objective,
            mission["research"],
            mission["verify"],
            mission["execute"],
        )

        # ------------------------------------
        # RESEARCH
        # ------------------------------------

        sources = []

        if mission["research"]:

            mission["stage"] = "research"

            sources = research_objective(
                objective,
                mission["max_sources"],
            )

        mission["sources"] = [
            {
                "title": source.get(
                    "title"
                ),
                "url": source.get(
                    "url"
                ),
                "status": source.get(
                    "status"
                ),
            }
            for source in sources
        ]

        mission[
            "evidence_ledger"
        ] = make_evidence_ledger(
            sources
        )

        # ------------------------------------
        # REASON
        # ------------------------------------

        mission["stage"] = "reason"

        synthesis = synthesize(
            objective,
            sources,
            intent,
        )

        mission["provider"] = (
            synthesis["provider"]
        )

        mission["result"] = (
            synthesis["text"]
        )

        # ------------------------------------
        # VERIFY
        # ------------------------------------

        mission["stage"] = "verify"

        if mission["verify"]:

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

        mission[
            "verification"
        ] = verification

        mission[
            "confidence"
        ] = verification[
            "confidence"
        ]

        # ------------------------------------
        # EXECUTE
        # ------------------------------------

        actions = []

        if mission["execute"]:

            mission["stage"] = "execute"

            proposed = propose_actions(
                objective,
                mission["max_actions"],
            )

            for action in proposed:

                action_result = (
                    execute_safe_action(
                        action
                    )
                )

                record_action(
                    action_result
                )

                actions.append(
                    action_result
                )

        mission["actions"] = actions

        # ------------------------------------
        # OBSERVE
        # ------------------------------------

        mission["stage"] = "observe"

        observations = [
            observe_action(action)
            for action in actions
        ]

        mission[
            "observations"
        ] = observations

        # ------------------------------------
        # RECOVER
        # ------------------------------------

        mission["stage"] = "recover"

        recoveries = []

        for action in actions:

            if action.get(
                "status"
            ) == "failed":

                recovery = recover_action(
                    action.get(
                        "requested",
                        "",
                    ),
                    action.get(
                        "target"
                    ),
                    action,
                )

                recoveries.append(
                    recovery
                )

        mission[
            "recoveries"
        ] = recoveries

        # ------------------------------------
        # LEARN
        # ------------------------------------

        mission["stage"] = "learn"

        skill = learn_skill(
            objective,
            intent,
            mission["confidence"],
            mission["provider"],
        )

        mission[
            "learned_skill"
        ] = skill

        if mission["remember"]:
            remember_mission(
                mission
            )

        # ------------------------------------
        # DELIVER
        # ------------------------------------

        mission["stage"] = "deliver"
        mission["status"] = "completed"
        mission["completed_at"] = now()

        started = mission.get(
            "started_at"
        )

        try:
            start_dt = datetime.fromisoformat(
                started
            )

            end_dt = datetime.fromisoformat(
                mission["completed_at"]
            )

            mission[
                "duration_seconds"
            ] = round(
                (
                    end_dt - start_dt
                ).total_seconds(),
                2,
            )

        except Exception:
            mission[
                "duration_seconds"
            ] = None

    except Exception as exc:

        mission["status"] = "failed"
        mission["stage"] = "error"
        mission["error"] = str(exc)[
            :1000
        ]
        mission["completed_at"] = now()


async def run_background_mission(
    mission_id: str,
) -> None:

    await asyncio.to_thread(
        execute_mission,
        mission_id,
    )


# ============================================================
# BASIC ENDPOINTS
# ============================================================

@app.get("/")
def root():

    return {
        "project": PROJECT,
        "version": VERSION,
        "status": "online",
        "message": (
            "AI Infinity Action & Tool "
            "Orchestration Core is online."
        ),
        "mode": "one-command",
        "architecture": (
            "intent → plan → research → reason → "
            "verify → execute → observe → recover "
            "→ learn → deliver"
        ),
        "capabilities": [
            "intent-routing",
            "adaptive-planning",
            "web-research",
            "parallel-source-reading",
            "evidence-ledger",
            "ai-provider-routing",
            "reasoning",
            "verification",
            "confidence-estimation",
            "safe-action-execution",
            "observation",
            "failure-recovery",
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


# ============================================================
# MISSIONS
# ============================================================

@app.post(
    "/mission",
    status_code=202,
)
async def create_mission(
    request: MissionRequest,
):

    mission_id = make_id(
        "mission"
    )

    mission = {
        "mission_id": mission_id,
        "status": "queued",
        "stage": "queued",
        "objective": request.objective,
        "research": request.research,
        "verify": request.verify,
        "remember": request.remember,
        "execute": request.execute,
        "max_sources": request.max_sources,
        "max_actions": request.max_actions,
        "created_at": now(),
    }

    with LOCK:
        MISSIONS[
            mission_id
        ] = mission

    asyncio.create_task(
        run_background_mission(
            mission_id
        )
    )

    return {
        "mission_id": mission_id,
        "status": "queued",
        "version": VERSION,
        "poll": (
            f"/mission/{mission_id}"
        ),
    }


@app.post(
    "/missions",
    status_code=202,
)
async def create_mission_alias(
    request: MissionRequest,
):

    return await create_mission(
        request
    )


@app.get(
    "/mission/{mission_id}"
)
def get_mission(
    mission_id: str,
):

    with LOCK:
        mission = MISSIONS.get(
            mission_id
        )

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return mission


@app.get(
    "/missions/{mission_id}"
)
def get_mission_alias(
    mission_id: str,
):

    return get_mission(
        mission_id
    )


@app.get(
    "/mission-status/{mission_id}"
)
def mission_status(
    mission_id: str,
):

    with LOCK:
        mission = MISSIONS.get(
            mission_id
        )

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return {
        "mission_id": mission_id,
        "status": mission.get(
            "status"
        ),
        "stage": mission.get(
            "stage"
        ),
        "confidence": mission.get(
            "confidence"
        ),
        "provider": mission.get(
            "provider"
        ),
        "action_count": len(
            mission.get(
                "actions",
                []
            )
        ),
        "version": VERSION,
    }


# ============================================================
# DIRECT ACTION API
# ============================================================

@app.post(
    "/action",
    status_code=200,
)
def action_endpoint(
    request: ActionRequest,
):

    result = execute_safe_action(
        request.action,
        request.target,
    )

    record_action(
        result
    )

    return result


@app.get("/actions")
def list_actions():

    return {
        "count": len(ACTIONS),
        "actions": ACTIONS[-100:],
    }


@app.get("/actions/{action_id}")
def get_action(
    action_id: str,
):

    for action in reversed(ACTIONS):

        if action.get(
            "action_id"
        ) == action_id:

            return action

    raise HTTPException(
        status_code=404,
        detail="Action not found",
    )


# ============================================================
# TASK COMPATIBILITY
# ============================================================

@app.post(
    "/task",
    status_code=202,
)
async def create_task(
    request: TaskRequest,
):

    return await create_mission(
        MissionRequest(
            objective=request.command,
            research=True,
            verify=True,
            remember=True,
            execute=True,
        )
    )


@app.post(
    "/tasks",
    status_code=202,
)
async def create_task_alias(
    request: TaskRequest,
):

    return await create_task(
        request
    )


@app.get(
    "/task/{task_id}"
)
def get_task(
    task_id: str,
):

    return get_mission(
        task_id
    )


@app.get(
    "/tasks/{task_id}"
)
def get_task_alias(
    task_id: str,
):

    return get_mission(
        task_id
    )


@app.get("/tasks")
def list_tasks():

    with LOCK:
        missions = list(
            MISSIONS.values()
        )

    return {
        "count": len(missions),
        "tasks": missions[-50:],
    }


# ============================================================
# MEMORY
# ============================================================

@app.post("/memory")
def add_memory(
    request: MemoryRequest,
):

    item = {
        "memory_id": make_id(
            "memory"
        ),
        "content": request.content,
        "tags": request.tags,
        "created_at": now(),
    }

    MEMORY.append(item)

    if len(MEMORY) > 500:
        del MEMORY[:-500]

    save_json(
        MEMORY_FILE,
        MEMORY,
    )

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
def search_memory(
    q: str = "",
):

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
def search_skills(
    q: str = "",
):

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
def intent_endpoint(
    request: MissionRequest,
):

    return classify_intent(
        request.objective
    )


@app.post("/plan")
def plan_endpoint(
    request: MissionRequest,
):

    intent = classify_intent(
        request.objective
    )

    return {
        "objective": request.objective,
        "intent": intent,
        "plan": build_plan(
            request.objective,
            request.research,
            request.verify,
            request.execute,
        ),
        "version": VERSION,
    }


# ============================================================
# CAPABILITIES
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
            "safe-action-execution",
            "observation",
            "failure-recovery",
            "mission-learning",
            "reusable-skills",
            "memory",
            "background-execution",
            "structured-outcomes",
            "free-first",
        ],
        "safe_actions": sorted(
            SAFE_ACTIONS
        ),
        "providers": {
            "openai_compatible": bool(
                os.getenv(
                    "AI_BASE_URL"
                )
                and os.getenv(
                    "AI_API_KEY"
                )
                and os.getenv(
                    "AI_MODEL"
                )
            ),
            "pollinations": True,
            "deterministic": True,
        },
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
def status():

    with LOCK:
        missions = list(
            MISSIONS.values()
        )

    completed = sum(
        1
        for mission in missions
        if mission.get(
            "status"
        ) == "completed"
    )

    running = sum(
        1
        for mission in missions
        if mission.get(
            "status"
        ) in {
            "queued",
            "running",
        }
    )

    failed = sum(
        1
        for mission in missions
        if mission.get(
            "status"
        ) == "failed"
    )

    action_success = sum(
        1
        for action in ACTIONS
        if action.get(
            "status"
        ) == "completed"
    )

    return {
        "project": PROJECT,
        "version": VERSION,
        "status": "online",
        "missions": {
            "total": len(missions),
            "completed": completed,
            "running": running,
            "failed": failed,
        },
        "actions": {
            "total": len(ACTIONS),
            "successful": action_success,
        },
        "memory_items": len(MEMORY),
        "learned_skills": len(SKILLS),
        "free_first": True,
        "time": now(),
    }


# ============================================================
# ERROR HANDLER
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

    BASE.mkdir(
        parents=True,
        exist_ok=True,
    )


# ============================================================
# LOCAL RUNNER
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
    )
