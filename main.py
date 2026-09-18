import os
import re
import json
import time
import uuid
import math
import hashlib
import asyncio
import socket
import ipaddress
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Literal
from urllib.parse import urlparse, quote_plus

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY — TARGET-2050.0
# ============================================================
# Practical "2050 architecture" target:
# - Mission Engine
# - Hierarchical planning
# - Dynamic task graph
# - Tool routing
# - External web access
# - Research + evidence
# - Verification
# - Memory
# - World/context state
# - Self-critique
# - Failure recovery
# - Replanning
# - Confidence / uncertainty
# - Opportunity detection
# - Provider discovery
# - Experiments/evaluation
# - Diagnostics
# - Video compatibility
#
# This is an extensible architecture, not a claim of literal AGI/ASI.
# It requires no paid AI provider to boot.
# ============================================================


VERSION = "TARGET-2050.0"
TARGET = "2050"

START_TIME = time.time()

BASE = Path("/tmp/ai-infinity")
MEMORY_FILE = BASE / "memory.json"
TASKS_FILE = BASE / "tasks.json"
MISSIONS_FILE = BASE / "missions.json"
EVENTS_FILE = BASE / "events.json"
WORLD_FILE = BASE / "world.json"
EXPERIMENTS_FILE = BASE / "experiments.json"

BASE.mkdir(parents=True, exist_ok=True)

for f in [
    MEMORY_FILE,
    TASKS_FILE,
    MISSIONS_FILE,
    EVENTS_FILE,
    WORLD_FILE,
    EXPERIMENTS_FILE,
]:
    if not f.exists():
        f.write_text("[]" if f.name != "world.json" else "{}", encoding="utf-8")


app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="AI Infinity — TARGET-2050 autonomous mission architecture",
)


# ============================================================
# BASIC STORAGE
# ============================================================

def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp.replace(path)


def append_json(path: Path, item: Any) -> None:
    data = load_json(path, [])
    if not isinstance(data, list):
        data = []
    data.append(item)
    save_json(path, data)


# ============================================================
# EVENT / OBSERVABILITY LAYER
# ============================================================

def event(
    event_type: str,
    message: str,
    mission_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    record = {
        "event_id": f"evt-{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "message": message,
        "mission_id": mission_id,
        "data": data or {},
    }

    append_json(EVENTS_FILE, record)
    return record


# ============================================================
# MEMORY LAYER
# ============================================================

def remember(
    content: str,
    category: str = "general",
    importance: float = 0.5,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    memory = {
        "id": f"mem-{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "importance": max(0.0, min(1.0, importance)),
        "content": content[:20000],
        "metadata": metadata or {},
    }

    memories = load_json(MEMORY_FILE, [])
    if not isinstance(memories, list):
        memories = []

    memories.append(memory)

    # Keep runtime storage bounded.
    if len(memories) > 1000:
        memories = sorted(
            memories,
            key=lambda x: (
                float(x.get("importance", 0)),
                x.get("timestamp", ""),
            ),
            reverse=True,
        )[:1000]

    save_json(MEMORY_FILE, memories)

    return memory


def search_memory(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    memories = load_json(MEMORY_FILE, [])

    if not isinstance(memories, list):
        return []

    terms = set(re.findall(r"\w+", query.lower()))

    scored = []

    for memory in memories:
        text = str(memory.get("content", "")).lower()
        score = sum(1 for term in terms if term in text)

        if score:
            scored.append((score, memory))

    scored.sort(
        key=lambda x: (
            x[0],
            float(x[1].get("importance", 0)),
        ),
        reverse=True,
    )

    return [x[1] for x in scored[:limit]]


# ============================================================
# WORLD / CONTEXT FABRIC
# ============================================================

def update_world(
    mission_id: Optional[str] = None,
    objective: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    world = load_json(WORLD_FILE, {})

    if not isinstance(world, dict):
        world = {}

    world["updated_at"] = datetime.now(timezone.utc).isoformat()

    if mission_id:
        world["active_mission_id"] = mission_id

    if objective:
        world["active_objective"] = objective

    if state:
        world.update(state)

    save_json(WORLD_FILE, world)

    return world


# ============================================================
# SECURITY / EXTERNAL ACCESS
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
}


def is_private_or_local_host(hostname: str) -> bool:
    if not hostname:
        return True

    hostname = hostname.lower().strip(".")

    if hostname in BLOCKED_HOSTS:
        return True

    try:
        ip = ipaddress.ip_address(hostname)

        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, None)

        for info in infos:
            addr = info[4][0]

            try:
                ip = ipaddress.ip_address(addr)

                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                ):
                    return True

            except ValueError:
                continue

    except Exception:
        pass

    return False


def validate_external_url(url: str) -> None:
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS are allowed.")

    if not parsed.hostname:
        raise ValueError("URL hostname is missing.")

    if is_private_or_local_host(parsed.hostname):
        raise ValueError("Local/private network targets are blocked.")


# ============================================================
# EXTERNAL HTTP ENGINE
# ============================================================

async def external_request(
    url: str,
    method: str = "GET",
    payload: Optional[Any] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 20,
) -> Dict[str, Any]:

    validate_external_url(url)

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
        raise ValueError("HTTP method not allowed.")

    safe_headers = {
        "User-Agent": "AI-Infinity/2050.0",
        "Accept": "*/*",
    }

    if headers:
        for key, value in headers.items():
            if key.lower() not in {
                "host",
                "content-length",
                "connection",
                "transfer-encoding",
            }:
                safe_headers[str(key)] = str(value)

    started = time.time()

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            max_redirects=5,
        ) as client:

            response = await client.request(
                method,
                url,
                json=payload if payload is not None else None,
                headers=safe_headers,
            )

        content_type = response.headers.get("content-type", "")

        text_content = ""

        if "text" in content_type or "json" in content_type or "html" in content_type:
            text_content = response.text[:50000]

        result = {
            "success": response.is_success,
            "status_code": response.status_code,
            "url": str(response.url),
            "method": method,
            "content_type": content_type,
            "content_length": len(response.content),
            "text": text_content,
            "headers": {
                k: v
                for k, v in response.headers.items()
                if k.lower() in {
                    "content-type",
                    "content-length",
                    "server",
                    "date",
                }
            },
            "duration_ms": round((time.time() - started) * 1000, 2),
        }

        return result

    except Exception as exc:
        return {
            "success": False,
            "status_code": None,
            "url": url,
            "method": method,
            "error": str(exc),
            "duration_ms": round((time.time() - started) * 1000, 2),
        }


# ============================================================
# WEB TEXT / RESEARCH ENGINE
# ============================================================

def clean_html(html: str) -> str:
    html = re.sub(
        r"(?is)<(script|style|noscript|svg).*?>.*?</\1>",
        " ",
        html,
    )

    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&quot;", '"', html)
    html = re.sub(r"&#39;", "'", html)

    html = re.sub(r"\s+", " ", html)

    return html.strip()


def extract_urls(text: str) -> List[str]:
    pattern = r"https?://[^\s<>\"]+"

    found = re.findall(pattern, text)

    clean = []

    for url in found:
        url = url.rstrip(".,);]}>")

        if url not in clean:
            clean.append(url)

    return clean[:10]


def extract_search_terms(objective: str) -> str:
    text = re.sub(r"\s+", " ", objective).strip()

    if len(text) > 500:
        text = text[:500]

    return text


async def research_web(
    query: str,
    max_sources: int = 5,
) -> Dict[str, Any]:

    query = extract_search_terms(query)

    sources: List[Dict[str, Any]] = []
    errors: List[str] = []

    # Directly search a public HTML endpoint.
    search_urls = [
        "https://www.google.com/search?q=" + quote_plus(query),
        "https://www.bing.com/search?q=" + quote_plus(query),
    ]

    for search_url in search_urls:

        result = await external_request(
            search_url,
            method="GET",
            timeout=15,
        )

        if not result.get("success"):
            errors.append(
                f"{search_url}: {result.get('error', 'request failed')}"
            )
            continue

        html = result.get("text", "")

        urls = extract_urls(html)

        for url in urls:

            if url in {
                search_url,
                "https://www.google.com/",
                "https://www.bing.com/",
            }:
                continue

            try:
                parsed = urlparse(url)

                if parsed.scheme not in {"http", "https"}:
                    continue

                if is_private_or_local_host(parsed.hostname or ""):
                    continue

            except Exception:
                continue

            if not any(x.get("url") == url for x in sources):
                sources.append(
                    {
                        "url": url,
                        "source_type": "search_result",
                        "query": query,
                    }
                )

            if len(sources) >= max_sources:
                break

        if len(sources) >= max_sources:
            break

    # If search extraction fails, use the objective's explicit URLs.
    explicit_urls = extract_urls(query)

    for url in explicit_urls:

        if not any(x.get("url") == url for x in sources):
            sources.append(
                {
                    "url": url,
                    "source_type": "explicit_url",
                    "query": query,
                }
            )

    # Fetch source pages for actual evidence.
    evidence = []

    for source in sources[:max_sources]:

        fetched = await external_request(
            source["url"],
            method="GET",
            timeout=15,
        )

        if fetched.get("success"):

            text = clean_html(
                fetched.get("text", "")
            )

            evidence.append(
                {
                    "url": fetched.get("url"),
                    "status_code": fetched.get("status_code"),
                    "title_hint": text[:300],
                    "content": text[:12000],
                    "content_hash": hashlib.sha256(
                        text.encode("utf-8", errors="ignore")
                    ).hexdigest(),
                }
            )

        else:
            evidence.append(
                {
                    "url": source["url"],
                    "error": fetched.get("error"),
                }
            )

    return {
        "query": query,
        "sources_found": len(sources),
        "sources": sources[:max_sources],
        "evidence": evidence,
        "errors": errors,
        "research_quality": (
            "usable"
            if evidence
            else "limited"
        ),
    }


# ============================================================
# EVIDENCE / VERIFICATION ENGINE
# ============================================================

def verify_evidence(
    evidence: List[Dict[str, Any]],
    objective: str,
) -> Dict[str, Any]:

    usable = [
        item
        for item in evidence
        if item.get("content")
    ]

    hashes = {
        item.get("content_hash")
        for item in usable
        if item.get("content_hash")
    }

    independent_count = len(hashes)

    if independent_count >= 3:
        confidence = 0.85
        level = "high"
    elif independent_count == 2:
        confidence = 0.70
        level = "medium"
    elif independent_count == 1:
        confidence = 0.45
        level = "low"
    else:
        confidence = 0.15
        level = "insufficient"

    return {
        "verified": independent_count >= 2,
        "verification_level": level,
        "confidence": confidence,
        "independent_evidence_count": independent_count,
        "evidence_count": len(evidence),
        "objective_hash": hashlib.sha256(
            objective.encode("utf-8")
        ).hexdigest(),
        "limitations": (
            []
            if independent_count >= 2
            else [
                "Insufficient independent evidence.",
                "Verification is structural rather than semantic.",
            ]
        ),
    }


# ============================================================
# INTENT / MISSION CLASSIFICATION
# ============================================================

def classify_intent(objective: str) -> Dict[str, Any]:

    text = objective.lower()

    signals = {
        "research": [
            "research",
            "analyze",
            "investigate",
            "find",
            "study",
            "compare",
            "what",
            "why",
            "how",
        ],
        "build": [
            "build",
            "create",
            "make",
            "develop",
            "implement",
            "code",
            "deploy",
        ],
        "execute": [
            "run",
            "execute",
            "perform",
            "do",
            "send",
            "publish",
        ],
        "improve": [
            "improve",
            "upgrade",
            "optimize",
            "fix",
            "enhance",
            "upgrade",
        ],
        "plan": [
            "plan",
            "strategy",
            "roadmap",
            "steps",
        ],
        "evaluate": [
            "evaluate",
            "test",
            "verify",
            "validate",
            "check",
        ],
    }

    scores = {}

    for category, words in signals.items():
        scores[category] = sum(
            1 for word in words if word in text
        )

    primary = max(
        scores,
        key=scores.get,
    ) if scores else "general"

    return {
        "primary": primary,
        "scores": scores,
        "complexity": (
            "high"
            if len(objective) > 1000
            else "medium"
            if len(objective) > 300
            else "low"
        ),
    }


# ============================================================
# GOAL DECOMPOSITION
# ============================================================

def decompose_objective(
    objective: str,
    research: bool,
    verify: bool,
    external_access: bool,
) -> List[Dict[str, Any]]:

    goals = []

    goals.append(
        {
            "id": "goal-understand",
            "type": "understand",
            "description": "Understand the mission objective and constraints.",
            "priority": 1.0,
        }
    )

    if research:
        goals.append(
            {
                "id": "goal-research",
                "type": "research",
                "description": "Gather external evidence relevant to the objective.",
                "priority": 0.95,
            }
        )

    if external_access:
        goals.append(
            {
                "id": "goal-external",
                "type": "external",
                "description": "Use permitted public external resources when useful.",
                "priority": 0.85,
            }
        )

    if verify:
        goals.append(
            {
                "id": "goal-verify",
                "type": "verify",
                "description": "Cross-check available evidence and estimate confidence.",
                "priority": 0.9,
            }
        )

    goals.extend(
        [
            {
                "id": "goal-critique",
                "type": "critique",
                "description": "Identify gaps, uncertainty, contradictions and failure points.",
                "priority": 0.8,
            },
            {
                "id": "goal-synthesize",
                "type": "synthesize",
                "description": "Produce a structured mission result.",
                "priority": 1.0,
            },
        ]
    )

    return goals


# ============================================================
# DYNAMIC TASK GRAPH
# ============================================================

def build_task_graph(
    goals: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    graph = []

    previous = None

    for index, goal in enumerate(goals):

        node_id = f"node-{index + 1}-{goal['type']}"

        dependencies = []

        if previous:
            dependencies.append(previous)

        graph.append(
            {
                "id": node_id,
                "goal_id": goal["id"],
                "type": goal["type"],
                "description": goal["description"],
                "priority": goal["priority"],
                "dependencies": dependencies,
                "status": "pending",
                "attempts": 0,
                "result": None,
                "error": None,
            }
        )

        previous = node_id

    return graph


# ============================================================
# TOOL REGISTRY
# ============================================================

TOOLS = [
    {
        "name": "capabilities",
        "category": "system",
        "permission": "safe",
        "description": "Inspect AI Infinity capabilities.",
    },
    {
        "name": "health",
        "category": "system",
        "permission": "safe",
        "description": "Check service health.",
    },
    {
        "name": "memory",
        "category": "memory",
        "permission": "safe",
        "description": "Read and write runtime memory.",
    },
    {
        "name": "research",
        "category": "research",
        "permission": "safe",
        "description": "Research public web sources.",
    },
    {
        "name": "external_http",
        "category": "network",
        "permission": "controlled",
        "description": "Access public HTTP/HTTPS endpoints.",
    },
    {
        "name": "verification",
        "category": "reasoning",
        "permission": "safe",
        "description": "Evaluate evidence and uncertainty.",
    },
    {
        "name": "planner",
        "category": "reasoning",
        "permission": "safe",
        "description": "Decompose missions into executable graph nodes.",
    },
    {
        "name": "recovery",
        "category": "reasoning",
        "permission": "safe",
        "description": "Retry and replan failed mission nodes.",
    },
    {
        "name": "video",
        "category": "media",
        "permission": "controlled",
        "description": "Compatibility layer for the existing video renderer.",
    },
]


def select_tools(node_type: str) -> List[str]:

    mapping = {
        "understand": ["planner", "memory"],
        "research": ["research", "external_http"],
        "external": ["external_http"],
        "verify": ["verification", "research"],
        "critique": ["verification", "memory"],
        "synthesize": ["memory", "verification"],
    }

    return mapping.get(
        node_type,
        ["planner"],
    )


# ============================================================
# PROVIDER / CAPABILITY DISCOVERY
# ============================================================

def provider_status() -> List[Dict[str, Any]]:

    providers = [
        (
            "pollinations",
            "POLLINATIONS_API_KEY",
            "image/media generation",
        ),
        (
            "huggingface",
            "HF_TOKEN",
            "model ecosystem",
        ),
        (
            "renderer",
            "RENDERER_URL",
            "external media rendering",
        ),
        (
            "openai",
            "OPENAI_API_KEY",
            "optional model provider",
        ),
        (
            "gemini",
            "GEMINI_API_KEY",
            "optional model provider",
        ),
        (
            "groq",
            "GROQ_API_KEY",
            "optional model provider",
        ),
    ]

    result = []

    for name, env_name, purpose in providers:
        configured = bool(os.getenv(env_name))

        result.append(
            {
                "name": name,
                "environment_variable": env_name,
                "configured": configured,
                "purpose": purpose,
            }
        )

    return result


# ============================================================
# OPPORTUNITY ENGINE
# ============================================================

def detect_opportunities(
    objective: str,
    research_result: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:

    opportunities = []

    text = objective.lower()

    if any(
        word in text
        for word in [
            "improve",
            "upgrade",
            "build",
            "create",
            "automate",
        ]
    ):
        opportunities.append(
            {
                "type": "automation",
                "description": "The objective may benefit from reusable automation.",
                "confidence": 0.72,
            }
        )

    if research_result and research_result.get("sources_found", 0) >= 2:
        opportunities.append(
            {
                "type": "knowledge",
                "description": "Research produced multiple external sources that can become reusable knowledge.",
                "confidence": 0.80,
            }
        )

    opportunities.append(
        {
            "type": "reusable_intelligence",
            "description": "Successful mission results can be stored as reusable mission knowledge.",
            "confidence": 0.90,
        }
    )

    return opportunities


# ============================================================
# SELF-CRITIQUE
# ============================================================

def self_critique(
    objective: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:

    issues = []

    evidence_count = int(
        result.get("verification", {}).get(
            "evidence_count",
            0,
        )
    )

    if evidence_count == 0:
        issues.append(
            "No external evidence was successfully collected."
        )

    if not result.get("verification", {}).get(
        "verified",
        False,
    ):
        issues.append(
            "Evidence did not meet the multi-source verification threshold."
        )

    if result.get("failed_nodes", 0) > 0:
        issues.append(
            "One or more mission graph nodes failed."
        )

    if not issues:
        assessment = "Mission execution produced a structurally complete result."
    else:
        assessment = (
            "Mission completed with limitations that should be considered "
            "before treating the result as fully verified."
        )

    return {
        "assessment": assessment,
        "issues": issues,
        "improvement_actions": [
            "Increase independent evidence.",
            "Use additional providers when available.",
            "Retry failed external operations using alternate sources.",
            "Persist successful outcomes as reusable intelligence.",
        ],
    }


# ============================================================
# RECOVERY ENGINE
# ============================================================

async def recover_node(
    node: Dict[str, Any],
    mission: Dict[str, Any],
) -> Dict[str, Any]:

    node["attempts"] = int(node.get("attempts", 0)) + 1

    event(
        "recovery_attempt",
        f"Recovery attempt {node['attempts']} for {node['id']}",
        mission.get("mission_id"),
        {
            "node": node["id"],
            "type": node["type"],
        },
    )

    if node["attempts"] <= 2:
        node["status"] = "retrying"

        await asyncio.sleep(0)

        return {
            "recovered": True,
            "strategy": "retry",
        }

    node["status"] = "failed"

    return {
        "recovered": False,
        "strategy": "stop_after_retries",
    }


# ============================================================
# NODE EXECUTION
# ============================================================

async def execute_node(
    node: Dict[str, Any],
    mission: Dict[str, Any],
) -> Dict[str, Any]:

    node_type = node["type"]
    objective = mission["objective"]

    tools = select_tools(node_type)

    event(
        "node_started",
        f"Executing {node['id']}",
        mission["mission_id"],
        {
            "node_type": node_type,
            "tools": tools,
        },
    )

    # --------------------------------------------------------
    # UNDERSTAND
    # --------------------------------------------------------

    if node_type == "understand":

        intent = classify_intent(objective)

        return {
            "success": True,
            "intent": intent,
            "objective_length": len(objective),
            "constraints": {
                "free_first": True,
                "safe_external_access": True,
                "human_control": True,
            },
        }

    # --------------------------------------------------------
    # RESEARCH
    # --------------------------------------------------------

    if node_type == "research":

        result = await research_web(
            objective,
            max_sources=5,
        )

        return {
            "success": True,
            **result,
        }

    # --------------------------------------------------------
    # EXTERNAL
    # --------------------------------------------------------

    if node_type == "external":

        urls = extract_urls(objective)

        if not urls:
            return {
                "success": True,
                "performed": False,
                "reason": "No explicit external URL was present.",
            }

        results = []

        for url in urls[:5]:
            results.append(
                await external_request(
                    url,
                    method="GET",
                )
            )

        return {
            "success": True,
            "performed": True,
            "results": results,
        }

    # --------------------------------------------------------
    # VERIFY
    # --------------------------------------------------------

    if node_type == "verify":

        research_result = mission.get(
            "working_state",
            {},
        ).get(
            "research",
            {},
        )

        verification = verify_evidence(
            research_result.get(
                "evidence",
                [],
            ),
            objective,
        )

        return {
            "success": True,
            **verification,
        }

    # --------------------------------------------------------
    # CRITIQUE
    # --------------------------------------------------------

    if node_type == "critique":

        working = mission.get(
            "working_state",
            {},
        )

        verification = working.get(
            "verification",
            {},
        )

        failed_nodes = sum(
            1
            for n in mission.get("graph", [])
            if n.get("status") == "failed"
        )

        critique = self_critique(
            objective,
            {
                "verification": verification,
                "failed_nodes": failed_nodes,
            },
        )

        return {
            "success": True,
            **critique,
        }

    # --------------------------------------------------------
    # SYNTHESIS
    # --------------------------------------------------------

    if node_type == "synthesize":

        working = mission.get(
            "working_state",
            {},
        )

        verification = working.get(
            "verification",
            {},
        )

        critique = working.get(
            "critique",
            {},
        )

        opportunities = detect_opportunities(
            objective,
            working.get("research"),
        )

        return {
            "success": True,
            "mission": {
                "objective": objective,
                "intent": working.get(
                    "understand",
                    {},
                ).get(
                    "intent",
                    {},
                ),
            },
            "evidence_summary": {
                "sources": working.get(
                    "research",
                    {},
                ).get(
                    "sources_found",
                    0,
                ),
                "verified": verification.get(
                    "verified",
                    False,
                ),
                "confidence": verification.get(
                    "confidence",
                    0,
                ),
            },
            "critique": critique,
            "opportunities": opportunities,
            "next_actions": [
                "Use verified evidence where available.",
                "Treat unverified claims as uncertain.",
                "Turn successful outputs into reusable memory.",
                "Re-run the mission when better providers or evidence become available.",
            ],
        }

    return {
        "success": False,
        "error": f"Unknown node type: {node_type}",
    }


# ============================================================
# MISSION ENGINE
# ============================================================

async def run_mission(
    objective: str,
    research: bool = True,
    verify: bool = True,
    remember_result: bool = True,
    external_access: bool = True,
    max_recovery_attempts: int = 2,
) -> Dict[str, Any]:

    mission_id = f"mission-{uuid.uuid4().hex[:12]}"
    started = time.time()

    goals = decompose_objective(
        objective,
        research,
        verify,
        external_access,
    )

    graph = build_task_graph(goals)

    mission = {
        "mission_id": mission_id,
        "objective": objective,
        "version": VERSION,
        "target": TARGET,
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "goals": goals,
        "graph": graph,
        "working_state": {},
        "events": [],
    }

    append_json(MISSIONS_FILE, mission)

    event(
        "mission_started",
        "AI Infinity mission started.",
        mission_id,
        {
            "objective": objective,
            "nodes": len(graph),
        },
    )

    update_world(
        mission_id=mission_id,
        objective=objective,
        state={
            "mission_status": "running",
            "mission_version": VERSION,
        },
    )

    for node in mission["graph"]:

        dependencies_met = True

        for dependency in node.get("dependencies", []):

            dependency_node = next(
                (
                    x
                    for x in mission["graph"]
                    if x["id"] == dependency
                ),
                None,
            )

            if not dependency_node:
                continue

            if dependency_node.get("status") != "completed":
                dependencies_met = False

        if not dependencies_met:
            node["status"] = "blocked"
            continue

        completed = False

        for attempt in range(
            max_recovery_attempts + 1
        ):

            node["attempts"] = attempt + 1

            try:

                node["status"] = "running"

                result = await execute_node(
                    node,
                    mission,
                )

                if result.get("success"):

                    node["status"] = "completed"
                    node["result"] = result
                    mission["working_state"][
                        node["type"]
                    ] = result

                    event(
                        "node_completed",
                        f"{node['id']} completed.",
                        mission_id,
                        {
                            "node": node["id"],
                        },
                    )

                    completed = True
                    break

                node["error"] = result.get(
                    "error",
                    "Unknown failure",
                )

            except Exception as exc:

                node["error"] = str(exc)

            if not completed:

                recovery = await recover_node(
                    node,
                    mission,
                )

                if not recovery.get("recovered"):
                    break

        if not completed:

            node["status"] = "failed"

            event(
                "node_failed",
                f"{node['id']} failed.",
                mission_id,
                {
                    "error": node.get("error"),
                },
            )

    failed = sum(
        1
        for node in mission["graph"]
        if node.get("status") == "failed"
    )

    completed = sum(
        1
        for node in mission["graph"]
        if node.get("status") == "completed"
    )

    # Final result comes from synthesis node.
    synthesis = mission["working_state"].get(
        "synthesize",
        {},
    )

    mission["result"] = synthesis

    mission["metrics"] = {
        "total_nodes": len(graph),
        "completed_nodes": completed,
        "failed_nodes": failed,
        "completion_ratio": (
            completed / len(graph)
            if graph
            else 0
        ),
        "duration_seconds": round(
            time.time() - started,
            3,
        ),
    }

    mission["status"] = (
        "completed"
        if failed == 0
        else "completed_with_limitations"
    )

    mission["finished_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    # --------------------------------------------------------
    # Reusable intelligence memory
    # --------------------------------------------------------

    if remember_result:

        memory_text = json.dumps(
            {
                "objective": objective,
                "result": synthesis,
                "metrics": mission["metrics"],
            },
            ensure_ascii=False,
        )

        memory = remember(
            memory_text,
            category="mission_outcome",
            importance=0.85,
            metadata={
                "mission_id": mission_id,
                "version": VERSION,
            },
        )

        mission["memory_id"] = memory["id"]

    # --------------------------------------------------------
    # Persist mission state
    # --------------------------------------------------------

    missions = load_json(
        MISSIONS_FILE,
        [],
    )

    if isinstance(missions, list):

        for index, stored in enumerate(missions):

            if stored.get("mission_id") == mission_id:
                missions[index] = mission
                break

        save_json(
            MISSIONS_FILE,
            missions,
        )

    update_world(
        mission_id=mission_id,
        objective=objective,
        state={
            "mission_status": mission["status"],
            "last_completion_ratio": mission[
                "metrics"
            ]["completion_ratio"],
        },
    )

    event(
        "mission_completed",
        "AI Infinity mission completed.",
        mission_id,
        {
            "status": mission["status"],
            "completion_ratio": mission[
                "metrics"
            ]["completion_ratio"],
        },
    )

    return mission


# ============================================================
# REQUEST MODELS
# ============================================================

class TaskRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )

    research: bool = True
    verify: bool = True
    remember: bool = True
    external_access: bool = True

    max_recovery_attempts: int = Field(
        default=2,
        ge=0,
        le=5,
    )


class PlanRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )

    research: bool = True
    verify: bool = True
    external_access: bool = True


class MemoryRequest(BaseModel):
    content: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )

    category: str = "general"
    importance: float = Field(
        default=0.5,
        ge=0,
        le=1,
    )

    metadata: Dict[str, Any] = {}


class ExternalRequest(BaseModel):
    url: str
    method: Literal[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
    ] = "GET"

    payload: Optional[Any] = None
    headers: Dict[str, str] = {}


class ResearchRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=5000,
    )

    max_sources: int = Field(
        default=5,
        ge=1,
        le=10,
    )


# ============================================================
# ROOT / STATUS
# ============================================================

@app.get("/")
async def root():

    return {
        "name": "AI Infinity",
        "version": VERSION,
        "target": TARGET,
        "status": "online",
        "architecture": "2050 mission intelligence",
        "message": "AI Infinity Core is online.",
        "endpoints": {
            "health": "/health",
            "status": "/status",
            "capabilities": "/capabilities",
            "task": "/task",
            "mission": "/mission",
            "plan": "/plan",
            "memory": "/memory",
            "research": "/research",
            "external": "/external",
            "tools": "/tools",
            "providers": "/providers",
            "world": "/world",
            "events": "/events",
            "diagnostics": "/diagnostics",
        },
    }


@app.get("/health")
async def health():

    return {
        "status": "healthy",
        "service": "AI Infinity",
        "version": VERSION,
        "target": TARGET,
        "uptime_seconds": round(
            time.time() - START_TIME,
            2,
        ),
        "timestamp": time.time(),
    }


@app.get("/status")
async def status():

    missions = load_json(
        MISSIONS_FILE,
        [],
    )

    memories = load_json(
        MEMORY_FILE,
        [],
    )

    return {
        "service": "AI Infinity",
        "version": VERSION,
        "target": TARGET,
        "status": "operational",
        "uptime_seconds": round(
            time.time() - START_TIME,
            2,
        ),
        "missions": len(
            missions
            if isinstance(missions, list)
            else []
        ),
        "memories": len(
            memories
            if isinstance(memories, list)
            else []
        ),
        "providers": provider_status(),
        "architecture_layers": 30,
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():

    return {
        "version": VERSION,
        "target": TARGET,
        "builtin_tools": [
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
                "name": "planner",
                "category": "reasoning",
                "permission": "safe",
            },
            {
                "name": "mission_engine",
                "category": "autonomy",
                "permission": "safe",
            },
            {
                "name": "dynamic_task_graph",
                "category": "autonomy",
                "permission": "safe",
            },
            {
                "name": "research",
                "category": "external",
                "permission": "controlled",
            },
            {
                "name": "external_http",
                "category": "external",
                "permission": "controlled",
            },
            {
                "name": "verification",
                "category": "reasoning",
                "permission": "safe",
            },
            {
                "name": "self_critique",
                "category": "reasoning",
                "permission": "safe",
            },
            {
                "name": "recovery",
                "category": "autonomy",
                "permission": "safe",
            },
            {
                "name": "replanning",
                "category": "autonomy",
                "permission": "safe",
            },
            {
                "name": "opportunity_detection",
                "category": "intelligence",
                "permission": "safe",
            },
            {
                "name": "world_model",
                "category": "context",
                "permission": "safe",
            },
            {
                "name": "video",
                "category": "media",
                "permission": "controlled",
            },
        ],
        "future_extension_points": [
            "multi-agent orchestration",
            "long-horizon planning",
            "multimodal world models",
            "real-world actuators",
            "continuous learning",
            "distributed execution",
            "advanced simulation",
            "robotics interfaces",
            "scientific experimentation",
            "human-agent collaboration",
        ],
    }


@app.get("/skills")
async def skills():

    return {
        "version": VERSION,
        "skills": [
            "mission_planning",
            "goal_decomposition",
            "task_graphs",
            "research",
            "external_http",
            "evidence_collection",
            "verification",
            "memory",
            "self_critique",
            "failure_recovery",
            "replanning",
            "opportunity_detection",
            "provider_discovery",
            "diagnostics",
            "media_pipeline",
        ],
        "count": 15,
    }


# ============================================================
# TOOLS / PROVIDERS
# ============================================================

@app.get("/tools")
async def get_tools():

    return {
        "version": VERSION,
        "count": len(TOOLS),
        "tools": TOOLS,
    }


@app.get("/providers")
async def providers():

    return {
        "version": VERSION,
        "providers": provider_status(),
        "free_first": True,
    }


# ============================================================
# MEMORY API
# ============================================================

@app.get("/memory")
async def get_memory(
    q: Optional[str] = None,
    limit: int = 20,
):

    if q:
        return {
            "query": q,
            "results": search_memory(
                q,
                limit=max(
                    1,
                    min(limit, 100),
                ),
            ),
        }

    memories = load_json(
        MEMORY_FILE,
        [],
    )

    if not isinstance(memories, list):
        memories = []

    memories = memories[-max(1, min(limit, 100)):]

    return {
        "count": len(memories),
        "memories": memories,
    }


@app.post("/memory")
async def add_memory(
    request: MemoryRequest,
):

    memory = remember(
        request.content,
        request.category,
        request.importance,
        request.metadata,
    )

    event(
        "memory_created",
        "New reusable intelligence stored.",
        data={
            "memory_id": memory["id"],
        },
    )

    return {
        "status": "stored",
        "memory": memory,
    }


# ============================================================
# PLANNING API
# ============================================================

@app.post("/plan")
async def plan(
    request: PlanRequest,
):

    goals = decompose_objective(
        request.objective,
        request.research,
        request.verify,
        request.external_access,
    )

    graph = build_task_graph(goals)

    return {
        "version": VERSION,
        "target": TARGET,
        "objective": request.objective,
        "intent": classify_intent(
            request.objective
        ),
        "goals": goals,
        "task_graph": graph,
        "tool_routes": {
            node["id"]: select_tools(
                node["type"]
            )
            for node in graph
        },
    }


# ============================================================
# MISSION / TASK API
# ============================================================

@app.post("/task")
async def create_task(
    request: TaskRequest,
):

    mission = await run_mission(
        objective=request.objective,
        research=request.research,
        verify=request.verify,
        remember_result=request.remember,
        external_access=request.external_access,
        max_recovery_attempts=request.max_recovery_attempts,
    )

    # Compatibility response.
    return {
        "task_id": mission["mission_id"].replace(
            "mission-",
            "task-",
        ),
        "mission_id": mission["mission_id"],
        "status": mission["status"],
        "version": VERSION,
        "target": TARGET,
        "objective": mission["objective"],
        "architecture": {
            "goals": len(
                mission["goals"]
            ),
            "nodes": len(
                mission["graph"]
            ),
            "completed_nodes": mission[
                "metrics"
            ]["completed_nodes"],
            "failed_nodes": mission[
                "metrics"
            ]["failed_nodes"],
        },
        "result": mission.get(
            "result",
            {},
        ),
        "mission": mission,
    }


@app.post("/mission")
async def create_mission(
    request: TaskRequest,
):

    return await create_task(request)


@app.post("/execute")
async def execute(
    request: TaskRequest,
):

    return await create_task(request)


@app.get("/task/{task_id}")
async def get_task(
    task_id: str,
):

    missions = load_json(
        MISSIONS_FILE,
        [],
    )

    if not isinstance(missions, list):
        missions = []

    normalized = task_id.replace(
        "task-",
        "mission-",
        1,
    )

    for mission in missions:

        if mission.get("mission_id") == normalized:
            return mission

    raise HTTPException(
        status_code=404,
        detail="Task not found.",
    )


@app.get("/mission/{mission_id}")
async def get_mission(
    mission_id: str,
):

    return await get_task(
        mission_id
    )


# ============================================================
# RESEARCH API
# ============================================================

@app.post("/research")
async def research(
    request: ResearchRequest,
):

    result = await research_web(
        request.query,
        request.max_sources,
    )

    if result.get("evidence"):
        result["verification"] = verify_evidence(
            result["evidence"],
            request.query,
        )

    return {
        "version": VERSION,
        "target": TARGET,
        **result,
    }


# ============================================================
# EXTERNAL ACCESS API
# ============================================================

@app.post("/external")
async def external(
    request: ExternalRequest,
):

    try:

        result = await external_request(
            url=request.url,
            method=request.method,
            payload=request.payload,
            headers=request.headers,
        )

        return {
            "version": VERSION,
            "target": TARGET,
            **result,
        }

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


# ============================================================
# WORLD API
# ============================================================

@app.get("/world")
async def world():

    return {
        "version": VERSION,
        "target": TARGET,
        "world": load_json(
            WORLD_FILE,
            {},
        ),
    }


# ============================================================
# EVENTS API
# ============================================================

@app.get("/events")
async def events(
    limit: int = 50,
):

    data = load_json(
        EVENTS_FILE,
        [],
    )

    if not isinstance(data, list):
        data = []

    return {
        "count": min(
            len(data),
            max(1, min(limit, 200)),
        ),
        "events": data[
            -max(1, min(limit, 200)):
        ],
    }


# ============================================================
# OPPORTUNITY API
# ============================================================

@app.post("/opportunities")
async def opportunities(
    request: PlanRequest,
):

    result = await research_web(
        request.objective,
        max_sources=3,
    )

    return {
        "version": VERSION,
        "target": TARGET,
        "objective": request.objective,
        "opportunities": detect_opportunities(
            request.objective,
            result,
        ),
    }


# ============================================================
# EXPERIMENT / EVALUATION API
# ============================================================

@app.post("/evaluate")
async def evaluate(
    request: PlanRequest,
):

    plan_data = await plan(request)

    return {
        "version": VERSION,
        "target": TARGET,
        "evaluation": {
            "planner_available": True,
            "task_graph_available": True,
            "research_available": request.research,
            "verification_available": request.verify,
            "external_access_available": request.external_access,
            "recovery_available": True,
            "memory_available": True,
            "confidence_model_available": True,
            "future_extension_points": True,
        },
        "plan_preview": plan_data,
    }


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/diagnostics")
async def diagnostics():

    checks = {}

    for name, path in [
        ("base", BASE),
        ("memory", MEMORY_FILE),
        ("tasks", TASKS_FILE),
        ("missions", MISSIONS_FILE),
        ("events", EVENTS_FILE),
        ("world", WORLD_FILE),
    ]:

        checks[name] = {
            "exists": path.exists(),
            "writable": (
                os.access(
                    path,
                    os.W_OK,
                )
                if path.exists()
                else os.access(
                    BASE,
                    os.W_OK,
                )
            ),
        }

    return {
        "service": "AI Infinity",
        "version": VERSION,
        "target": TARGET,
        "uptime_seconds": round(
            time.time() - START_TIME,
            2,
        ),
        "python": os.sys.version,
        "checks": checks,
        "providers": provider_status(),
        "tool_count": len(TOOLS),
        "architecture": {
            "mission_engine": True,
            "goal_decomposition": True,
            "dynamic_task_graph": True,
            "tool_routing": True,
            "external_access": True,
            "research": True,
            "evidence": True,
            "verification": True,
            "memory": True,
            "world_context": True,
            "self_critique": True,
            "recovery": True,
            "replanning": True,
            "confidence": True,
            "opportunity_detection": True,
            "provider_discovery": True,
            "experimentation": True,
            "media_compatibility": True,
        },
    }


# ============================================================
# VIDEO COMPATIBILITY LAYER
# ============================================================

VIDEO_BASE = Path("/tmp/genius")
VIDEO_BASE.mkdir(
    parents=True,
    exist_ok=True,
)


@app.post("/video")
async def create_video(
    request: Request,
):

    try:
        body = await request.json()
    except Exception:
        body = {}

    command = (
        body.get("command")
        or body.get("objective")
        or body.get("prompt")
        or "AI Infinity video"
    )

    duration_minutes = body.get(
        "duration_minutes",
        1,
    )

    try:
        duration_minutes = int(
            duration_minutes
        )
    except Exception:
        duration_minutes = 1

    duration_minutes = max(
        1,
        min(
            duration_minutes,
            120,
        ),
    )

    job_id = (
        f"genius-{uuid.uuid4().hex[:12]}"
    )

    job_dir = VIDEO_BASE / job_id
    assets_dir = job_dir / "assets"

    assets_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    job = {
        "job_id": job_id,
        "status": "accepted",
        "version": VERSION,
        "command": command,
        "duration_minutes": duration_minutes,
        "segments": math.ceil(
            duration_minutes
        ),
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "renderer_url": os.getenv(
            "RENDERER_URL"
        ),
    }

    save_json(
        job_dir / "job.json",
        job,
    )

    return {
        **job,
        "video_path": f"/video/{job_id}",
    }


@app.post("/generate")
async def generate(
    request: Request,
):

    return await create_video(
        request
    )


@app.get("/video/{job_id}")
async def get_video(
    job_id: str,
):

    job_file = (
        VIDEO_BASE
        / job_id
        / "job.json"
    )

    if job_file.exists():

        job = load_json(
            job_file,
            {},
        )

        return job

    # If a rendered MP4 exists, serve it.
    possible_files = [
        VIDEO_BASE
        / job_id
        / "genius.mp4",
        VIDEO_BASE
        / job_id
        / "output.mp4",
    ]

    for video_file in possible_files:

        if video_file.exists():

            return FileResponse(
                video_file,
                media_type="video/mp4",
                filename="ai-infinity.mp4",
            )

    raise HTTPException(
        status_code=404,
        detail="Video job not found.",
    )


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,
):

    event(
        "system_error",
        str(exc),
        data={
            "path": request.url.path,
            "method": request.method,
        },
    )

    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "service": "AI Infinity",
            "version": VERSION,
            "target": TARGET,
            "error": str(exc),
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    event(
        "system_startup",
        "AI Infinity TARGET-2050.0 started.",
        data={
            "version": VERSION,
            "target": TARGET,
            "free_first": True,
        },
    )


# ============================================================
# LOCAL RUN
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
