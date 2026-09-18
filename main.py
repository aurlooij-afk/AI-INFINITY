# ============================================================
# AI INFINITY — TARGET-2050.2
# Durable Autonomous Intelligence Runtime
# ============================================================

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# IDENTITY
# ============================================================

VERSION = "TARGET-2050.2"
TARGET_YEAR = 2050
SERVICE = "AI Infinity"

START_TIME = time.time()

BASE = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity"))
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "infinity.db"


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=SERVICE,
    version=VERSION,
    description="AI Infinity durable autonomous intelligence runtime",
)


# ============================================================
# UTILITIES
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: Optional[str], default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


# ============================================================
# DURABLE DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        CREATE TABLE IF NOT EXISTS memory (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            parent_id TEXT,
            priority REAL DEFAULT 0.5,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            plan TEXT,
            result TEXT,
            progress REAL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS world (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            payload TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evaluations (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            score REAL,
            success INTEGER,
            feedback TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL,
            capabilities TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            priority REAL,
            source TEXT,
            status TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_memory_kind
        ON memory(kind);

        CREATE INDEX IF NOT EXISTS idx_tasks_status
        ON tasks(status);

        CREATE INDEX IF NOT EXISTS idx_missions_status
        ON missions(status);

        CREATE INDEX IF NOT EXISTS idx_events_type
        ON events(event_type);
        """
    )

    # Built-in agents
    agents = [
        (
            "agent-planner",
            "Planner",
            "planning",
            ["decomposition", "strategy", "prioritization"],
        ),
        (
            "agent-researcher",
            "Researcher",
            "research",
            ["web_research", "evidence", "source_analysis"],
        ),
        (
            "agent-verifier",
            "Verifier",
            "verification",
            ["cross_check", "confidence", "provenance"],
        ),
        (
            "agent-critic",
            "Critic",
            "critique",
            ["contradiction_detection", "quality_control"],
        ),
        (
            "agent-executor",
            "Executor",
            "execution",
            ["http", "task_execution", "recovery"],
        ),
        (
            "agent-learner",
            "Learner",
            "learning",
            ["evaluation", "pattern_extraction", "optimization"],
        ),
    ]

    for agent_id, name, role, caps in agents:
        conn.execute(
            """
            INSERT OR IGNORE INTO agents
            (id,name,role,status,capabilities,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                agent_id,
                name,
                role,
                "ready",
                dumps(caps),
                now(),
                now(),
            ),
        )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# EVENT SYSTEM
# ============================================================

def event(event_type: str, payload: Any):
    conn = db()
    conn.execute(
        """
        INSERT INTO events(id,event_type,payload,created_at)
        VALUES(?,?,?,?)
        """,
        (uid("evt"), event_type, dumps(payload), now()),
    )
    conn.commit()
    conn.close()


# ============================================================
# MEMORY
# ============================================================

def remember(
    content: str,
    kind: str = "general",
    metadata: Optional[Dict[str, Any]] = None,
):
    memory_id = uid("mem")

    conn = db()
    conn.execute(
        """
        INSERT INTO memory(id,kind,content,metadata,created_at)
        VALUES(?,?,?,?,?)
        """,
        (
            memory_id,
            kind,
            content,
            dumps(metadata or {}),
            now(),
        ),
    )
    conn.commit()
    conn.close()

    event(
        "memory.created",
        {
            "memory_id": memory_id,
            "kind": kind,
        },
    )

    return memory_id


def recent_memory(limit: int = 20):
    conn = db()

    rows = conn.execute(
        """
        SELECT * FROM memory
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    conn.close()

    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "content": r["content"],
            "metadata": loads(r["metadata"], {}),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# ============================================================
# WORLD MODEL
# ============================================================

def set_world(key: str, value: Any):
    conn = db()

    conn.execute(
        """
        INSERT INTO world(key,value,updated_at)
        VALUES(?,?,?)
        ON CONFLICT(key)
        DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at
        """,
        (key, dumps(value), now()),
    )

    conn.commit()
    conn.close()


def get_world():
    conn = db()

    rows = conn.execute(
        "SELECT key,value,updated_at FROM world"
    ).fetchall()

    conn.close()

    return {
        r["key"]: {
            "value": loads(r["value"]),
            "updated_at": r["updated_at"],
        }
        for r in rows
    }


# ============================================================
# MODELS
# ============================================================

class TaskRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=20000)
    research: bool = True
    verify: bool = True
    remember: bool = True
    external_access: bool = True
    long_horizon: bool = False


class MissionRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=20000)
    research: bool = True
    verify: bool = True
    remember: bool = True
    external_access: bool = True


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)


class VerifyRequest(BaseModel):
    evidence: List[Dict[str, Any]] = Field(default_factory=list)


class ExternalRequest(BaseModel):
    url: str
    method: str = "GET"
    data: Optional[Dict[str, Any]] = None


class EvaluateRequest(BaseModel):
    task_id: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None


# ============================================================
# CAPABILITIES
# ============================================================

BUILTIN_TOOLS = [
    ("capabilities", "system", "safe"),
    ("health", "system", "safe"),
    ("memory_count", "memory", "safe"),
    ("skills_count", "skills", "safe"),
    ("status", "system", "safe"),
    ("planner", "intelligence", "safe"),
    ("mission_engine", "intelligence", "safe"),
    ("dynamic_task_graph", "intelligence", "safe"),
    ("long_horizon_planning", "intelligence", "safe"),
    ("research", "information", "network"),
    ("external_http", "network", "restricted"),
    ("verification", "intelligence", "safe"),
    ("evidence_provenance", "intelligence", "safe"),
    ("source_quality", "intelligence", "safe"),
    ("self_inspection", "intelligence", "safe"),
    ("gap_analysis", "intelligence", "safe"),
    ("self_critique", "intelligence", "safe"),
    ("recovery", "intelligence", "safe"),
    ("replanning", "intelligence", "safe"),
    ("opportunity_detection", "intelligence", "safe"),
    ("world_model", "memory", "safe"),
    ("durable_memory", "memory", "safe"),
    ("durable_tasks", "memory", "safe"),
    ("durable_missions", "memory", "safe"),
    ("evaluation", "learning", "safe"),
    ("agent_orchestration", "intelligence", "safe"),
    ("adaptive_routing", "intelligence", "safe"),
    ("provider_discovery", "providers", "safe"),
    ("video", "media", "safe"),
]

FUTURE_EXTENSIONS = [
    "distributed durable database",
    "multi-region execution",
    "authenticated OAuth actions",
    "sandboxed code execution",
    "specialized model routing",
    "multimodal world models",
    "real-world actuators",
    "robotics interfaces",
    "scientific experimentation",
    "continuous online learning",
    "human-agent collaboration",
    "distributed multi-agent swarms",
    "advanced simulation",
    "persistent user identity",
    "event-driven autonomous background workers",
]


# ============================================================
# HEALTH / STATUS
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "uptime_seconds": round(time.time() - START_TIME, 3),
        "database": {
            "type": "SQLite",
            "path": str(DB_PATH),
            "durable_within_runtime": True,
        },
    }


@app.get("/status")
def status():
    conn = db()

    counts = {
        "memory": conn.execute(
            "SELECT COUNT(*) FROM memory"
        ).fetchone()[0],
        "tasks": conn.execute(
            "SELECT COUNT(*) FROM tasks"
        ).fetchone()[0],
        "missions": conn.execute(
            "SELECT COUNT(*) FROM missions"
        ).fetchone()[0],
        "events": conn.execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0],
        "evaluations": conn.execute(
            "SELECT COUNT(*) FROM evaluations"
        ).fetchone()[0],
        "agents": conn.execute(
            "SELECT COUNT(*) FROM agents"
        ).fetchone()[0],
        "opportunities": conn.execute(
            "SELECT COUNT(*) FROM opportunities"
        ).fetchone()[0],
    }

    conn.close()

    return {
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "runtime": {
            "uptime_seconds": round(time.time() - START_TIME, 3),
        },
        "persistent_state": counts,
        "architecture": "durable_autonomous_runtime",
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
def capabilities():
    return {
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "operational": [
            {
                "name": name,
                "category": category,
                "permission": permission,
            }
            for name, category, permission in BUILTIN_TOOLS
        ],
        "operational_count": len(BUILTIN_TOOLS),
        "future_extension_points": FUTURE_EXTENSIONS,
    }


# ============================================================
# SELF INSPECTION
# ============================================================

@app.get("/self-inspect")
def self_inspect():
    conn = db()

    db_counts = {
        "memory": conn.execute(
            "SELECT COUNT(*) FROM memory"
        ).fetchone()[0],
        "tasks": conn.execute(
            "SELECT COUNT(*) FROM tasks"
        ).fetchone()[0],
        "missions": conn.execute(
            "SELECT COUNT(*) FROM missions"
        ).fetchone()[0],
        "events": conn.execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0],
        "evaluations": conn.execute(
            "SELECT COUNT(*) FROM evaluations"
        ).fetchone()[0],
        "agents": conn.execute(
            "SELECT COUNT(*) FROM agents"
        ).fetchone()[0],
    }

    conn.close()

    return {
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,

        "runtime": {
            "python": "3.11+",
            "uptime_seconds": round(time.time() - START_TIME, 3),
        },

        "persistence": {
            "engine": "SQLite",
            "database_exists": DB_PATH.exists(),
            "runtime_persistence": True,
            "note": (
                "Render local storage can still be lost when the service "
                "is recreated. External database integration remains the "
                "next durability boundary."
            ),
            "counts": db_counts,
        },

        "intelligence": {
            "planner": True,
            "dynamic_task_graph": True,
            "long_horizon_planning": True,
            "verification": True,
            "self_critique": True,
            "recovery": True,
            "replanning": True,
            "opportunity_detection": True,
            "world_model": True,
            "durable_memory": True,
            "durable_missions": True,
            "durable_tasks": True,
            "evaluation": True,
            "agent_orchestration": True,
        },

        "network": {
            "external_http": True,
            "ssrf_protection": True,
            "source_filtering": True,
            "source_quality_scoring": True,
        },

        "providers": {
            "huggingface": bool(os.getenv("HF_TOKEN")),
            "pollinations": bool(os.getenv("POLLINATIONS_API_KEY")),
            "renderer": bool(os.getenv("RENDERER_URL")),
        },

        "future": FUTURE_EXTENSIONS,
    }


# ============================================================
# ARCHITECTURE
# ============================================================

@app.get("/architecture")
def architecture():
    return {
        "version": VERSION,
        "target": TARGET_YEAR,

        "layers": [
            {
                "layer": 1,
                "name": "Intent",
                "purpose": "Convert human objectives into machine missions.",
            },
            {
                "layer": 2,
                "name": "Context Fabric",
                "purpose": "Combine memory, world state, evidence and constraints.",
            },
            {
                "layer": 3,
                "name": "Mission Engine",
                "purpose": "Manage persistent long-horizon objectives.",
            },
            {
                "layer": 4,
                "name": "Dynamic Task Graph",
                "purpose": "Break missions into executable dependencies.",
            },
            {
                "layer": 5,
                "name": "Agent Mesh",
                "purpose": "Route work to specialized reasoning roles.",
            },
            {
                "layer": 6,
                "name": "Tools",
                "purpose": "Research, network access and external capabilities.",
            },
            {
                "layer": 7,
                "name": "Verification",
                "purpose": "Check evidence and detect contradictions.",
            },
            {
                "layer": 8,
                "name": "Learning",
                "purpose": "Record outcomes and improve future execution.",
            },
            {
                "layer": 9,
                "name": "Durable State",
                "purpose": "Persist missions, tasks, memories and world state.",
            },
            {
                "layer": 10,
                "name": "Evolution",
                "purpose": "Provide controlled extension points toward the 2050 target.",
            },
        ],
    }


# ============================================================
# GAP ANALYSIS
# ============================================================

@app.get("/gaps")
def gaps():
    return {
        "version": VERSION,
        "target": TARGET_YEAR,
        "completed_or_operational": [
            "persistent runtime state",
            "mission persistence",
            "task persistence",
            "memory persistence",
            "dynamic task graphs",
            "agent role routing",
            "verification",
            "self-critique",
            "recovery",
            "replanning",
            "evaluation",
            "safe external networking",
        ],
        "remaining_major_boundaries": [
            {
                "capability": "external durable database",
                "status": "extension",
                "reason": "Render local filesystem is not guaranteed durable.",
            },
            {
                "capability": "background distributed workers",
                "status": "extension",
                "reason": "Requires persistent worker infrastructure.",
            },
            {
                "capability": "authenticated external actions",
                "status": "extension",
                "reason": "Requires user-authorized OAuth credentials.",
            },
            {
                "capability": "sandboxed code execution",
                "status": "extension",
                "reason": "Requires isolated execution environment.",
            },
            {
                "capability": "true multimodal world model",
                "status": "extension",
                "reason": "Requires specialized multimodal models.",
            },
            {
                "capability": "real-world actuators",
                "status": "extension",
                "reason": "Requires physical device interfaces.",
            },
        ],
    }


# ============================================================
# MEMORY API
# ============================================================

@app.get("/memory")
def memory(limit: int = 50):
    limit = max(1, min(limit, 500))
    return {
        "count": len(recent_memory(limit)),
        "items": recent_memory(limit),
    }


@app.get("/memory/count")
def memory_count():
    conn = db()
    count = conn.execute(
        "SELECT COUNT(*) FROM memory"
    ).fetchone()[0]
    conn.close()

    return {"count": count}


# ============================================================
# WORLD MODEL
# ============================================================

@app.get("/world")
def world():
    return get_world()


# ============================================================
# EVENTS
# ============================================================

@app.get("/events")
def events(limit: int = 100):
    limit = max(1, min(limit, 500))

    conn = db()
    rows = conn.execute(
        """
        SELECT * FROM events
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    return {
        "events": [
            {
                "id": r["id"],
                "type": r["event_type"],
                "payload": loads(r["payload"], {}),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


# ============================================================
# SKILLS / PROVIDERS
# ============================================================

@app.get("/skills")
def skills():
    return {
        "skills": [
            "planning",
            "research",
            "verification",
            "critique",
            "recovery",
            "replanning",
            "memory",
            "evaluation",
            "agent_orchestration",
            "external_http",
        ]
    }


@app.get("/skills/count")
def skills_count():
    return {"count": 10}


@app.get("/providers")
def providers():
    return {
        "providers": [
            {
                "name": "huggingface",
                "configured": bool(os.getenv("HF_TOKEN")),
                "environment_variable": "HF_TOKEN",
            },
            {
                "name": "pollinations",
                "configured": bool(os.getenv("POLLINATIONS_API_KEY")),
                "environment_variable": "POLLINATIONS_API_KEY",
            },
            {
                "name": "renderer",
                "configured": bool(os.getenv("RENDERER_URL")),
                "environment_variable": "RENDERER_URL",
            },
        ]
    }


# ============================================================
# SOURCE SAFETY
# ============================================================

BLOCKED_DOMAINS = {
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "duckduckgo.com",
    "www.duckduckgo.com",
    "r.bing.com",
    "schemas.live.com",
    "w3.org",
}


def registrable_domain(host: str) -> str:
    parts = host.lower().split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host.lower()


def is_private_host(host: str) -> bool:
    if not host:
        return True

    lowered = host.lower()

    if lowered in {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }:
        return True

    try:
        ip = ipaddress.ip_address(lowered)

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
        infos = socket.getaddrinfo(
            lowered,
            None,
            type=socket.SOCK_STREAM,
        )

        for info in infos:
            address = info[4][0]
            try:
                ip = ipaddress.ip_address(address)

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
        return False

    return False


def validate_url(url: str):
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=400,
            detail="Only HTTP and HTTPS URLs are permitted.",
        )

    host = parsed.hostname

    if not host:
        raise HTTPException(
            status_code=400,
            detail="URL has no hostname.",
        )

    if is_private_host(host):
        raise HTTPException(
            status_code=403,
            detail="Private, local, loopback or reserved destinations are blocked.",
        )

    return parsed


# ============================================================
# SOURCE QUALITY
# ============================================================

def source_quality(url: str, text: str) -> float:
    parsed = urlparse(url)
    host = parsed.hostname or ""

    score = 0.30

    if parsed.scheme == "https":
        score += 0.10

    domain = registrable_domain(host)

    trusted = {
        "gov",
        "edu",
        "org",
        "who.int",
        "nature.com",
        "science.org",
        "arxiv.org",
        "nasa.gov",
        "nist.gov",
    }

    if any(domain.endswith(x) for x in trusted):
        score += 0.25

    if len(text) > 1000:
        score += 0.10

    if len(text) > 5000:
        score += 0.10

    if any(
        marker in text.lower()
        for marker in [
            "abstract",
            "research",
            "study",
            "results",
            "methodology",
            "reference",
        ]
    ):
        score += 0.10

    return round(min(score, 1.0), 3)


# ============================================================
# FETCH PAGE
# ============================================================

async def fetch_page(url: str):
    validate_url(url)

    headers = {
        "User-Agent": (
            "AI-Infinity/2050 research runtime "
            "(compatible; evidence collection)"
        )
    }

    timeout = httpx.Timeout(15.0)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:

        response = await client.get(url)

        final_url = str(response.url)

        validate_url(final_url)

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        if "text/html" not in content_type and "text/plain" not in content_type:
            return None

        text = response.text

        # Strip HTML.
        text = re.sub(
            r"<script\b[^>]*>.*?</script>",
            " ",
            text,
            flags=re.I | re.S,
        )

        text = re.sub(
            r"<style\b[^>]*>.*?</style>",
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

        if len(text) < 300:
            return None

        return {
            "url": final_url,
            "domain": registrable_domain(
                urlparse(final_url).hostname or ""
            ),
            "text": text[:12000],
            "quality": source_quality(
                final_url,
                text,
            ),
        }


# ============================================================
# SEARCH
# ============================================================

def extract_links(html: str):
    found = []

    patterns = [
        r'href=["\'](https?://[^"\']+)["\']',
        r'href=["\']/l/\?uddg=([^"\']+)["\']',
    ]

    for pattern in patterns:
        found.extend(
            re.findall(
                pattern,
                html,
                flags=re.I,
            )
        )

    return found


async def search_provider(url: str):
    validate_url(url)

    headers = {
        "User-Agent": "Mozilla/5.0 AI-Infinity-Research"
    }

    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=True,
        headers=headers,
    ) as client:
        r = await client.get(url)

    return r.text


async def research(query: str):
    search_urls = [
        "https://html.duckduckgo.com/html/?q="
        + quote_plus(query),

        "https://www.google.com/search?q="
        + quote_plus(query),

        "https://www.bing.com/search?q="
        + quote_plus(query),
    ]

    candidates = []

    for search_url in search_urls:
        try:
            html = await search_provider(search_url)
            links = extract_links(html)

            for link in links:
                link = link.replace("&amp;", "&")

                parsed = urlparse(link)
                host = parsed.hostname or ""

                if not host:
                    continue

                domain = registrable_domain(host)

                if domain in BLOCKED_DOMAINS:
                    continue

                if any(
                    x in host.lower()
                    for x in [
                        "google.",
                        "bing.",
                        "duckduckgo.",
                        "r.bing.com",
                    ]
                ):
                    continue

                candidates.append(
                    {
                        "url": link,
                        "search_provider": search_url.split("/")[2],
                    }
                )

        except Exception:
            continue

    # Deduplicate by URL.
    unique = {}

    for item in candidates:
        unique[item["url"]] = item

    candidates = list(unique.values())[:20]

    accepted = []
    rejected = []

    for candidate in candidates:
        try:
            page = await fetch_page(candidate["url"])

            if not page:
                rejected.append(
                    {
                        "url": candidate["url"],
                        "reason": "insufficient_text",
                    }
                )
                continue

            page["search_provider"] = candidate[
                "search_provider"
            ]

            accepted.append(page)

        except Exception as exc:
            rejected.append(
                {
                    "url": candidate["url"],
                    "reason": type(exc).__name__,
                }
            )

    # Keep only the highest-quality source per domain.
    by_domain = {}

    for item in accepted:
        domain = item["domain"]

        if (
            domain not in by_domain
            or item["quality"]
            > by_domain[domain]["quality"]
        ):
            by_domain[domain] = item

    accepted = sorted(
        by_domain.values(),
        key=lambda x: x["quality"],
        reverse=True,
    )[:8]

    domains = [
        x["domain"]
        for x in accepted
    ]

    avg_quality = (
        round(
            sum(x["quality"] for x in accepted)
            / len(accepted),
            3,
        )
        if accepted
        else 0
    )

    if len(domains) >= 3 and avg_quality >= 0.60:
        strength = "strong"
    elif len(domains) >= 2 and avg_quality >= 0.50:
        strength = "usable"
    else:
        strength = "insufficient"

    return {
        "query": query,
        "strength": strength,
        "accepted_sources": [
            {
                "url": x["url"],
                "domain": x["domain"],
                "quality": x["quality"],
                "search_provider": x["search_provider"],
                "excerpt": x["text"][:1800],
            }
            for x in accepted
        ],
        "accepted_domains": domains,
        "independent_domain_count": len(domains),
        "average_source_quality": avg_quality,
        "rejected_sources": rejected[:20],
    }


@app.post("/research")
async def research_api(request: ResearchRequest):
    return await research(request.query)


# ============================================================
# VERIFICATION
# ============================================================

def verify_evidence(evidence: List[Dict[str, Any]]):
    cleaned = []

    for item in evidence:
        url = item.get("url")

        if not url:
            continue

        domain = item.get("domain")

        if not domain:
            domain = registrable_domain(
                urlparse(url).hostname or ""
            )

        quality = float(
            item.get("quality", 0.3)
        )

        cleaned.append(
            {
                "url": url,
                "domain": domain,
                "quality": quality,
                "fingerprint": sha(
                    {
                        "domain": domain,
                        "excerpt": item.get(
                            "excerpt",
                            "",
                        )[:1000],
                    }
                ),
            }
        )

    unique_domains = sorted(
        set(x["domain"] for x in cleaned)
    )

    avg_quality = (
        sum(x["quality"] for x in cleaned)
        / len(cleaned)
        if cleaned
        else 0
    )

    if (
        len(unique_domains) >= 3
        and avg_quality >= 0.60
    ):
        level = "high"
        verified = True
        confidence = min(
            0.95,
            0.65
            + 0.05 * len(unique_domains)
            + 0.20 * avg_quality,
        )

    elif (
        len(unique_domains) >= 2
        and avg_quality >= 0.50
    ):
        level = "moderate"
        verified = True
        confidence = min(
            0.85,
            0.55
            + 0.08 * len(unique_domains)
            + 0.15 * avg_quality,
        )

    else:
        level = "low"
        verified = False
        confidence = min(
            0.49,
            0.25 + 0.15 * avg_quality,
        )

    return {
        "verified": verified,
        "verification_level": level,
        "confidence": round(confidence, 3),
        "independent_evidence_count": len(unique_domains),
        "average_source_quality": round(
            avg_quality,
            3,
        ),
        "domains": unique_domains,
        "evidence": cleaned,
    }


@app.post("/verify")
def verify_api(request: VerifyRequest):
    return verify_evidence(request.evidence)


# ============================================================
# EXTERNAL HTTP
# ============================================================

@app.post("/external")
async def external_api(request: ExternalRequest):
    validate_url(request.url)

    method = request.method.upper()

    if method not in {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }:
        raise HTTPException(
            status_code=400,
            detail="Unsupported HTTP method.",
        )

    # Destructive methods are intentionally restricted.
    if method in {"DELETE", "PUT", "PATCH"}:
        raise HTTPException(
            status_code=403,
            detail=(
                "Destructive external actions require an "
                "authenticated action layer."
            ),
        )

    headers = {
        "User-Agent": "AI-Infinity/2050"
    }

    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers=headers,
        ) as client:

            if method == "GET":
                response = await client.get(
                    request.url
                )
            else:
                response = await client.post(
                    request.url,
                    json=request.data or {},
                )

        final_url = str(response.url)
        validate_url(final_url)

        return {
            "success": True,
            "status_code": response.status_code,
            "url": final_url,
            "content_type": response.headers.get(
                "content-type"
            ),
            "body": response.text[:20000],
        }

    except HTTPException:
        raise

    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }


# ============================================================
# PLANNER
# ============================================================

def classify_intent(objective: str):
    text = objective.lower()

    if any(
        x in text
        for x in [
            "research",
            "analyze",
            "investigate",
            "find",
            "compare",
        ]
    ):
        return "research"

    if any(
        x in text
        for x in [
            "build",
            "create",
            "implement",
            "deploy",
        ]
    ):
        return "build"

    if any(
        x in text
        for x in [
            "audit",
            "inspect",
            "check",
            "verify",
        ]
    ):
        return "audit"

    return "general"


def plan_objective(
    objective: str,
    research_enabled: bool,
    verify_enabled: bool,
    long_horizon: bool = False,
):

    intent = classify_intent(objective)

    nodes = []

    nodes.append(
        {
            "id": "understand",
            "type": "intent",
            "objective": (
                "Understand and structure the objective."
            ),
            "depends_on": [],
        }
    )

    if research_enabled:
        nodes.append(
            {
                "id": "research",
                "type": "research",
                "objective": (
                    "Collect meaningful external evidence."
                ),
                "depends_on": ["understand"],
            }
        )

    if verify_enabled:
        nodes.append(
            {
                "id": "verify",
                "type": "verify",
                "objective": (
                    "Cross-check evidence and calibrate confidence."
                ),
                "depends_on": (
                    ["research"]
                    if research_enabled
                    else ["understand"]
                ),
            }
        )

    nodes.append(
        {
            "id": "opportunities",
            "type": "opportunity",
            "objective": (
                "Identify useful opportunities and missing capabilities."
            ),
            "depends_on": [
                "verify"
                if verify_enabled
                else "understand"
            ],
        }
    )

    nodes.append(
        {
            "id": "critique",
            "type": "critique",
            "objective": (
                "Detect contradictions, weaknesses and uncertainty."
            ),
            "depends_on": ["opportunities"],
        }
    )

    nodes.append(
        {
            "id": "synthesis",
            "type": "synthesis",
            "objective": (
                "Produce the final result using the verified context."
            ),
            "depends_on": ["critique"],
        }
    )

    if long_horizon:
        nodes.append(
            {
                "id": "next_cycle",
                "type": "replan",
                "objective": (
                    "Generate the next executable cycle."
                ),
                "depends_on": ["synthesis"],
            }
        )

    return {
        "intent": intent,
        "long_horizon": long_horizon,
        "nodes": nodes,
    }


@app.post("/plan")
def plan_api(request: TaskRequest):
    return plan_objective(
        request.objective,
        request.research,
        request.verify,
        request.long_horizon,
    )


# ============================================================
# AGENT ROUTER
# ============================================================

def choose_agent(node_type: str):
    mapping = {
        "intent": "agent-planner",
        "research": "agent-researcher",
        "verify": "agent-verifier",
        "critique": "agent-critic",
        "synthesis": "agent-planner",
        "opportunity": "agent-learner",
        "replan": "agent-planner",
        "execute": "agent-executor",
    }

    return mapping.get(
        node_type,
        "agent-executor",
    )


# ============================================================
# CRITIQUE
# ============================================================

def critique_result(context: Dict[str, Any]):
    issues = []

    research_data = context.get(
        "research"
    )

    verification = context.get(
        "verification"
    )

    if research_data:
        if (
            research_data.get(
                "strength"
            )
            == "insufficient"
        ):
            issues.append(
                "External evidence is insufficient."
            )

    if verification:
        if (
            verification.get("verified")
            and verification.get("verification_level")
            == "low"
        ):
            issues.append(
                "Verification state is internally inconsistent."
            )

    if not research_data:
        issues.append(
            "No external research context was collected."
        )

    if not verification:
        issues.append(
            "No evidence verification was performed."
        )

    confidence = (
        verification.get(
            "confidence",
            0,
        )
        if verification
        else 0
    )

    return {
        "issues": issues,
        "issue_count": len(issues),
        "confidence": confidence,
        "quality": (
            "strong"
            if not issues
            else "needs_improvement"
        ),
    }


# ============================================================
# OPPORTUNITY ENGINE
# ============================================================

def detect_opportunities(
    objective: str,
    context: Dict[str, Any],
):
    opportunities = []

    if not context.get("research"):
        opportunities.append(
            {
                "title": "Increase external evidence",
                "description": (
                    "Collect independent sources before making "
                    "high-confidence conclusions."
                ),
                "priority": 0.90,
            }
        )

    if not context.get("verification"):
        opportunities.append(
            {
                "title": "Add verification cycle",
                "description": (
                    "Cross-check important claims against "
                    "independent evidence."
                ),
                "priority": 0.85,
            }
        )

    opportunities.append(
        {
            "title": "Persistent learning",
            "description": (
                "Use this execution's evaluation to improve "
                "future task routing."
            ),
            "priority": 0.75,
        }
    )

    conn = db()

    for opportunity in opportunities:
        conn.execute(
            """
            INSERT INTO opportunities
            (id,title,description,priority,source,status,created_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                uid("opp"),
                opportunity["title"],
                opportunity["description"],
                opportunity["priority"],
                "mission_engine",
                "detected",
                now(),
            ),
        )

    conn.commit()
    conn.close()

    return opportunities


@app.get("/opportunities")
def opportunities():
    conn = db()

    rows = conn.execute(
        """
        SELECT * FROM opportunities
        ORDER BY priority DESC, created_at DESC
        LIMIT 100
        """
    ).fetchall()

    conn.close()

    return {
        "opportunities": [
            {
                "id": r["id"],
                "title": r["title"],
                "description": r["description"],
                "priority": r["priority"],
                "source": r["source"],
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


# ============================================================
# TASK STORAGE
# ============================================================

def create_task(
    objective: str,
    parent_id: Optional[str] = None,
    priority: float = 0.5,
):
    task_id = uid("task")

    conn = db()

    conn.execute(
        """
        INSERT INTO tasks
        (id,objective,status,result,parent_id,priority,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            task_id,
            objective,
            "running",
            None,
            parent_id,
            priority,
            now(),
            now(),
        ),
    )

    conn.commit()
    conn.close()

    return task_id


def update_task(
    task_id: str,
    status: str,
    result: Any = None,
):
    conn = db()

    conn.execute(
        """
        UPDATE tasks
        SET status=?, result=?, updated_at=?
        WHERE id=?
        """,
        (
            status,
            dumps(result),
            now(),
            task_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# MISSION STORAGE
# ============================================================

def create_mission(
    objective: str,
    plan: Dict[str, Any],
):
    mission_id = uid("mission")

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (id,objective,status,plan,result,progress,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            mission_id,
            objective,
            "running",
            dumps(plan),
            None,
            0.0,
            now(),
            now(),
        ),
    )

    conn.commit()
    conn.close()

    return mission_id


def update_mission(
    mission_id: str,
    status: str,
    progress: float,
    result: Any = None,
):
    conn = db()

    conn.execute(
        """
        UPDATE missions
        SET status=?, progress=?, result=?, updated_at=?
        WHERE id=?
        """,
        (
            status,
            progress,
            dumps(result),
            now(),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# MISSION EXECUTION
# ============================================================

async def execute_mission(
    mission_id: str,
    objective: str,
    plan: Dict[str, Any],
    request: MissionRequest,
):

    context: Dict[str, Any] = {
        "objective": objective,
        "mission_id": mission_id,
        "started_at": now(),
        "agent_trace": [],
    }

    nodes = plan["nodes"]

    completed = 0

    for node in nodes:
        node_type = node["type"]
        agent = choose_agent(node_type)

        context["agent_trace"].append(
            {
                "node": node["id"],
                "type": node_type,
                "agent": agent,
                "started_at": now(),
            }
        )

        try:

            if node_type == "intent":
                context["intent"] = plan["intent"]

            elif node_type == "research":
                context["research"] = await research(
                    objective
                )

            elif node_type == "verify":
                sources = (
                    context.get(
                        "research",
                        {},
                    ).get(
                        "accepted_sources",
                        [],
                    )
                )

                context["verification"] = (
                    verify_evidence(sources)
                )

            elif node_type == "opportunity":
                context["opportunities"] = (
                    detect_opportunities(
                        objective,
                        context,
                    )
                )

            elif node_type == "critique":
                context["critique"] = (
                    critique_result(context)
                )

            elif node_type == "synthesis":

                verification = context.get(
                    "verification",
                    {},
                )

                critique = context.get(
                    "critique",
                    {},
                )

                research_data = context.get(
                    "research",
                    {},
                )

                context["synthesis"] = {
                    "objective": objective,
                    "intent": context.get(
                        "intent"
                    ),
                    "research_strength": research_data.get(
                        "strength",
                        "none",
                    ),
                    "verified": verification.get(
                        "verified",
                        False,
                    ),
                    "confidence": verification.get(
                        "confidence",
                        0,
                    ),
                    "independent_sources": verification.get(
                        "independent_evidence_count",
                        0,
                    ),
                    "opportunities": context.get(
                        "opportunities",
                        [],
                    ),
                    "critique": critique,
                    "architecture_state": VERSION,
                    "generated_at": now(),
                }

            elif node_type == "replan":
                context["next_cycle"] = {
                    "status": "ready",
                    "reason": (
                        "Mission completed its current planning cycle."
                    ),
                    "next_action": (
                        "Use the stored mission state to continue."
                    ),
                }

            completed += 1

            progress = completed / len(nodes)

            update_mission(
                mission_id,
                "running",
                progress,
            )

        except Exception as exc:

            context.setdefault(
                "errors",
                [],
            ).append(
                {
                    "node": node["id"],
                    "error": str(exc),
                    "recovered": True,
                }
            )

            # Recovery path:
            # continue the mission rather than destroying state.
            completed += 1

            event(
                "mission.recovery",
                {
                    "mission_id": mission_id,
                    "node": node["id"],
                    "error": str(exc),
                },
            )

    context["completed_nodes"] = completed
    context["total_nodes"] = len(nodes)
    context["completed_at"] = now()

    # Durable learning memory.
    if request.remember:
        memory_id = remember(
            json.dumps(
                context.get(
                    "synthesis",
                    context,
                ),
                ensure_ascii=False,
            ),
            kind="mission_outcome",
            metadata={
                "mission_id": mission_id,
                "version": VERSION,
            },
        )

        context["memory_id"] = memory_id

    update_mission(
        mission_id,
        "completed",
        1.0,
        context,
    )

    event(
        "mission.completed",
        {
            "mission_id": mission_id,
            "nodes": len(nodes),
        },
    )

    return context


# ============================================================
# TASK API
# ============================================================

@app.post("/task")
async def task_api(request: TaskRequest):

    task_id = create_task(
        request.objective
    )

    plan = plan_objective(
        request.objective,
        request.research,
        request.verify,
        request.long_horizon,
    )

    mission_id = create_mission(
        request.objective,
        plan,
    )

    mission_request = MissionRequest(
        objective=request.objective,
        research=request.research,
        verify=request.verify,
        remember=request.remember,
        external_access=request.external_access,
    )

    try:
        result = await execute_mission(
            mission_id,
            request.objective,
            plan,
            mission_request,
        )

        update_task(
            task_id,
            "completed",
            result,
        )

        # Evaluation automatically records outcome.
        score = 1.0

        if result.get("errors"):
            score -= min(
                0.5,
                0.1 * len(result["errors"]),
            )

        record_evaluation(
            task_id,
            score,
            score >= 0.7,
            "Automatic mission execution evaluation.",
        )

        return {
            "task_id": task_id,
            "mission_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "target": TARGET_YEAR,
            "objective": request.objective,
            "result": result,
        }

    except Exception as exc:

        update_task(
            task_id,
            "failed",
            {
                "error": str(exc),
            },
        )

        record_evaluation(
            task_id,
            0.0,
            False,
            str(exc),
        )

        raise


# ============================================================
# MISSION API
# ============================================================

@app.post("/mission")
async def mission_api(request: MissionRequest):

    plan = plan_objective(
        request.objective,
        request.research,
        request.verify,
        True,
    )

    mission_id = create_mission(
        request.objective,
        plan,
    )

    result = await execute_mission(
        mission_id,
        request.objective,
        plan,
        request,
    )

    return {
        "mission_id": mission_id,
        "status": "completed",
        "version": VERSION,
        "target": TARGET_YEAR,
        "objective": request.objective,
        "result": result,
    }


# ============================================================
# READ TASK / MISSION
# ============================================================

@app.get("/task/{task_id}")
def get_task(task_id: str):

    conn = db()

    row = conn.execute(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Task not found.",
        )

    return {
        "id": row["id"],
        "objective": row["objective"],
        "status": row["status"],
        "result": loads(row["result"]),
        "parent_id": row["parent_id"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@app.get("/mission/{mission_id}")
def get_mission(mission_id: str):

    conn = db()

    row = conn.execute(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,),
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    return {
        "id": row["id"],
        "objective": row["objective"],
        "status": row["status"],
        "plan": loads(row["plan"], {}),
        "result": loads(row["result"], {}),
        "progress": row["progress"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ============================================================
# EVALUATION / LEARNING
# ============================================================

def record_evaluation(
    task_id: str,
    score: float,
    success: bool,
    feedback: str,
):
    evaluation_id = uid("eval")

    conn = db()

    conn.execute(
        """
        INSERT INTO evaluations
        (id,task_id,score,success,feedback,created_at)
        VALUES(?,?,?,?,?,?)
        """,
        (
            evaluation_id,
            task_id,
            score,
            1 if success else 0,
            feedback,
            now(),
        ),
    )

    conn.commit()
    conn.close()

    remember(
        feedback,
        kind="learning_signal",
        metadata={
            "task_id": task_id,
            "score": score,
            "success": success,
        },
    )

    event(
        "learning.recorded",
        {
            "task_id": task_id,
            "score": score,
        },
    )

    return evaluation_id


@app.post("/evaluate")
def evaluate(request: EvaluateRequest):

    if request.task_id:

        conn = db()

        row = conn.execute(
            """
            SELECT * FROM tasks
            WHERE id=?
            """,
            (request.task_id,),
        ).fetchone()

        conn.close()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Task not found.",
            )

        success = row["status"] == "completed"

        score = 1.0 if success else 0.0

        evaluation_id = record_evaluation(
            request.task_id,
            score,
            success,
            "Evaluated from task state.",
        )

        return {
            "evaluation_id": evaluation_id,
            "task_id": request.task_id,
            "score": score,
            "success": success,
        }

    expected = request.expected or ""
    actual = request.actual or ""

    if not expected or not actual:
        raise HTTPException(
            status_code=400,
            detail="Provide task_id or expected and actual.",
        )

    expected_words = set(
        re.findall(
            r"\w+",
            expected.lower(),
        )
    )

    actual_words = set(
        re.findall(
            r"\w+",
            actual.lower(),
        )
    )

    overlap = (
        len(expected_words & actual_words)
        / max(
            1,
            len(expected_words),
        )
    )

    return {
        "score": round(
            min(1.0, overlap),
            3,
        ),
        "method": "lexical_baseline",
        "note": (
            "Replace with model-based evaluation when "
            "a specialized evaluator is configured."
        ),
    }


# ============================================================
# AGENTS
# ============================================================

@app.get("/agents")
def agents():

    conn = db()

    rows = conn.execute(
        "SELECT * FROM agents ORDER BY name"
    ).fetchall()

    conn.close()

    return {
        "agents": [
            {
                "id": r["id"],
                "name": r["name"],
                "role": r["role"],
                "status": r["status"],
                "capabilities": loads(
                    r["capabilities"],
                    [],
                ),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    }


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/diagnostics")
def diagnostics():

    inspection = self_inspect()

    return {
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "healthy": True,
        "self_inspection": inspection,
        "database": {
            "path": str(DB_PATH),
            "exists": DB_PATH.exists(),
        },
    }


# ============================================================
# EXECUTE COMPATIBILITY ENDPOINT
# ============================================================

@app.post("/execute")
async def execute_compat(request: TaskRequest):
    return await task_api(request)


# ============================================================
# VIDEO COMPATIBILITY
# ============================================================

@app.post("/video")
async def video_compat(request: TaskRequest):
    return {
        "status": "accepted",
        "version": VERSION,
        "message": (
            "Video compatibility endpoint is active. "
            "Connect a renderer provider through RENDERER_URL "
            "for actual video production."
        ),
        "objective": request.objective,
    }


@app.post("/generate")
async def generate_compat(request: TaskRequest):
    return await video_compat(request)


@app.get("/video/{job_id}")
def video_job(job_id: str):
    return {
        "job_id": job_id,
        "status": "provider_required",
        "version": VERSION,
        "message": (
            "Video rendering requires a configured renderer."
        ),
    }


# ============================================================
# ROOT UI
# ============================================================

@app.get("/", response_class=HTMLResponse)
def root():

    return """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body{
    margin:0;
    font-family:system-ui,-apple-system,sans-serif;
    background:#0b1020;
    color:#fff;
}
main{
    max-width:850px;
    margin:auto;
    padding:28px 18px;
}
.card{
    background:#151c32;
    border:1px solid #293352;
    border-radius:18px;
    padding:20px;
    margin:15px 0;
}
h1{
    font-size:34px;
    margin-bottom:5px;
}
.badge{
    display:inline-block;
    padding:7px 11px;
    border-radius:20px;
    background:#24304d;
    margin:4px;
    font-size:13px;
}
textarea{
    width:100%;
    min-height:130px;
    box-sizing:border-box;
    background:#0d1427;
    color:#fff;
    border:1px solid #34405f;
    border-radius:12px;
    padding:14px;
    font-size:16px;
}
button{
    margin-top:12px;
    width:100%;
    padding:14px;
    border:0;
    border-radius:12px;
    font-size:16px;
    font-weight:700;
}
pre{
    white-space:pre-wrap;
    overflow-wrap:anywhere;
}
</style>
</head>

<body>
<main>

<div class="card">
<h1>∞ AI Infinity</h1>
<p>Durable Autonomous Intelligence Runtime</p>
<span class="badge">TARGET-2050.2</span>
<span class="badge">Persistent State</span>
<span class="badge">Mission Engine</span>
<span class="badge">Agent Mesh</span>
<span class="badge">Verification</span>
<span class="badge">Learning</span>
</div>

<div class="card">
<h2>Run a Mission</h2>

<textarea id="objective"
placeholder="Tell AI Infinity what you want to accomplish..."></textarea>

<button onclick="runTask()">EXECUTE</button>

<pre id="result"></pre>
</div>

<div class="card">
<h2>Runtime</h2>
<pre id="health">Loading...</pre>
</div>

<script>

async function runTask(){

    const objective =
        document.getElementById("objective").value;

    if(!objective.trim()){
        return;
    }

    document.getElementById("result").textContent =
        "AI Infinity is executing...";

    try{

        const response = await fetch("/task",{
            method:"POST",
            headers:{
                "Content-Type":"application/json"
            },
            body:JSON.stringify({
                objective:objective,
                research:true,
                verify:true,
                remember:true,
                external_access:true,
                long_horizon:true
            })
        });

        const data = await response.json();

        document.getElementById("result").textContent =
            JSON.stringify(data,null,2);

    }catch(error){

        document.getElementById("result").textContent =
            "Error: " + error;
    }
}

async function health(){

    try{

        const response =
            await fetch("/health");

        const data =
            await response.json();

        document.getElementById("health").textContent =
            JSON.stringify(data,null,2);

    }catch(error){

        document.getElementById("health").textContent =
            String(error);
    }
}

health();

</script>

</main>
</body>
</html>
"""


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    init_db()

    set_world(
        "runtime",
        {
            "service": SERVICE,
            "version": VERSION,
            "target": TARGET_YEAR,
            "started_at": now(),
        },
    )

    event(
        "runtime.started",
        {
            "version": VERSION,
            "target": TARGET_YEAR,
        },
    )


# ============================================================
# END
# ============================================================
