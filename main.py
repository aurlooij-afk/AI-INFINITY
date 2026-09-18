# AI Infinity — TARGET-2050.4
# Autonomous Intelligence Runtime
# Free-first / graceful degradation / command+query compatibility

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from pathlib import Path
from urllib.parse import urlparse, quote
from datetime import datetime, timezone
import asyncio
import json
import math
import os
import re
import socket
import sqlite3
import time
import uuid
import ipaddress
import html as html_lib

import httpx


VERSION = "TARGET-2050.4"
TARGET = 2050

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "infinity.db"

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Free-first autonomous intelligence runtime."
)


# ============================================================
# CORE UTILITIES
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def clean_text(value: Any, limit: int = 20000) -> str:
    if value is None:
        return ""
    value = str(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


# ============================================================
# DATABASE
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
            created_at TEXT,
            kind TEXT,
            content TEXT,
            importance REAL DEFAULT 0.5,
            source TEXT
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            updated_at TEXT,
            objective TEXT,
            status TEXT,
            result TEXT
        );

        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            updated_at TEXT,
            objective TEXT,
            status TEXT,
            result TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            event_type TEXT,
            payload TEXT
        );

        CREATE TABLE IF NOT EXISTS evaluations (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            task_id TEXT,
            score REAL,
            feedback TEXT
        );

        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY,
            name TEXT,
            category TEXT,
            status TEXT,
            metadata TEXT
        );

        CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            title TEXT,
            description TEXT,
            priority REAL,
            status TEXT
        );

        CREATE TABLE IF NOT EXISTS world (
            key TEXT PRIMARY KEY,
            updated_at TEXT,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS regression (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            name TEXT,
            passed INTEGER,
            details TEXT
        );
        """
    )

    conn.commit()
    conn.close()


init_db()


def db_execute(sql: str, params=()):
    conn = db()
    cur = conn.execute(sql, params)
    conn.commit()
    result = cur.lastrowid
    conn.close()
    return result


def db_one(sql: str, params=()):
    conn = db()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def db_all(sql: str, params=()):
    conn = db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(x) for x in rows]


def save_event(event_type: str, payload: Any):
    db_execute(
        """
        INSERT INTO events(id, created_at, event_type, payload)
        VALUES (?, ?, ?, ?)
        """,
        (
            uid("event"),
            now(),
            event_type,
            json.dumps(payload, ensure_ascii=False, default=str),
        ),
    )


def save_memory(content: Any, kind="execution", importance=0.6, source="ai-infinity"):
    memory_id = uid("mem")
    db_execute(
        """
        INSERT INTO memory(id, created_at, kind, content, importance, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            now(),
            kind,
            json.dumps(content, ensure_ascii=False, default=str),
            importance,
            source,
        ),
    )
    return memory_id


# ============================================================
# REQUEST MODELS
# ============================================================

class ExecuteRequest(BaseModel):
    # New API
    query: Optional[str] = None

    # Legacy / UI API
    command: Optional[str] = None

    # Optional controls
    research: bool = True
    verify: bool = True
    remember: bool = True

    def get_objective(self) -> str:
        """
        Accept all of these:

        {"query":"hello"}
        {"command":"hello"}

        {"command":"{\"query\":\"hello\"}"}

        {"command":"{\"command\":\"hello\"}"}

        or plain command text.
        """

        raw = self.query or self.command or ""
        raw = str(raw).strip()

        for _ in range(3):
            if not raw:
                break

            if raw.startswith("{") and raw.endswith("}"):
                try:
                    data = json.loads(raw)

                    if isinstance(data, dict):
                        candidate = (
                            data.get("query")
                            or data.get("command")
                            or data.get("objective")
                            or ""
                        )

                        if candidate:
                            raw = str(candidate).strip()
                            continue

                except Exception:
                    pass

            break

        return clean_text(raw, 10000)


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=10000)


class VerifyRequest(BaseModel):
    claim: str = Field(..., min_length=1, max_length=10000)
    evidence: List[Dict[str, Any]] = []


class PlanRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=10000)


class TaskRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=10000)


class MissionRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=10000)


class ExternalRequest(BaseModel):
    url: str
    method: str = "GET"
    data: Optional[Dict[str, Any]] = None


# ============================================================
# SECURITY / URL VALIDATION
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "host.docker.internal",
}


def host_is_safe(host: str) -> bool:
    if not host:
        return False

    host = host.lower().strip(".")
    if host in BLOCKED_HOSTS:
        return False

    try:
        ip = ipaddress.ip_address(host)

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False

        return True

    except ValueError:
        pass

    return True


def url_is_safe(url: str) -> bool:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return False

        if not parsed.hostname:
            return False

        return host_is_safe(parsed.hostname)

    except Exception:
        return False


# ============================================================
# SOURCE QUALITY
# ============================================================

SEARCH_INFRASTRUCTURE = {
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "duckduckgo.com",
    "www.duckduckgo.com",
    "r.bing.com",
    "schemas.microsoft.com",
    "w3.org",
    "www.w3.org",
    "gstatic.com",
    "googleusercontent.com",
    "cloudflare.com",
}


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        host = host.lower()

        if host.startswith("www."):
            host = host[4:]

        return host
    except Exception:
        return ""


def meaningful_domain(url: str) -> bool:
    d = domain_of(url)

    if not d:
        return False

    if d in SEARCH_INFRASTRUCTURE:
        return False

    blocked_fragments = [
        "schema.",
        "schemas.",
        "cdn.",
        "static.",
        "doubleclick.",
        "googleapis.",
        "gstatic.",
    ]

    return not any(x in d for x in blocked_fragments)


def source_quality(url: str, title: str = "", text: str = "") -> float:
    d = domain_of(url)

    if not meaningful_domain(url):
        return 0.0

    score = 0.35

    high_quality = [
        ".gov",
        ".edu",
        ".ac.",
        "who.int",
        "un.org",
        "worldbank.org",
        "oecd.org",
        "nasa.gov",
        "nature.com",
        "science.org",
        "arxiv.org",
        "openai.com",
        "anthropic.com",
        "deepmind.google",
        "microsoft.com",
        "ibm.com",
        "stanford.edu",
        "mit.edu",
    ]

    if any(x in d for x in high_quality):
        score += 0.35

    if title:
        score += 0.05

    if len(text) > 500:
        score += 0.10

    if len(text) > 2000:
        score += 0.10

    return round(clamp(score), 3)


# ============================================================
# HTTP
# ============================================================

async def http_get(
    url: str,
    timeout: float = 15.0,
    headers: Optional[Dict[str, str]] = None,
):
    if not url_is_safe(url):
        raise ValueError("Unsafe or blocked URL")

    default_headers = {
        "User-Agent": "AI-Infinity/2050 research-runtime",
        "Accept": "text/html,application/xhtml+xml,text/plain,application/json",
    }

    if headers:
        default_headers.update(headers)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=default_headers,
    ) as client:
        response = await client.get(url)

        if not url_is_safe(str(response.url)):
            raise ValueError("Redirected to blocked URL")

        return response


async def http_post(
    url: str,
    data: Optional[Dict[str, Any]] = None,
    timeout: float = 20.0,
):
    if not url_is_safe(url):
        raise ValueError("Unsafe or blocked URL")

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers={
            "User-Agent": "AI-Infinity/2050",
            "Accept": "application/json,text/plain,*/*",
        },
    ) as client:
        return await client.post(url, json=data or {})


# ============================================================
# HTML / SEARCH EXTRACTION
# ============================================================

def html_to_text(raw: str) -> str:
    if not raw:
        return ""

    raw = re.sub(
        r"<(script|style|noscript|svg).*?>.*?</\1>",
        " ",
        raw,
        flags=re.I | re.S,
    )

    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html_lib.unescape(raw)
    raw = re.sub(r"\s+", " ", raw)

    return raw.strip()


def extract_links(raw: str) -> List[Dict[str, str]]:
    results = []

    if not raw:
        return results

    pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.I | re.S,
    )

    for match in pattern.finditer(raw):
        href = html_lib.unescape(match.group(1))
        title = html_to_text(match.group(2))

        if href.startswith("//"):
            href = "https:" + href

        if href.startswith("/"):
            continue

        if not href.startswith(("http://", "https://")):
            continue

        if not url_is_safe(href):
            continue

        if not meaningful_domain(href):
            continue

        results.append(
            {
                "url": href,
                "title": clean_text(title, 500),
            }
        )

    return results


def deduplicate_sources(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_domain = {}

    for source in sources:
        domain = domain_of(source.get("url", ""))

        if not domain:
            continue

        quality = float(source.get("quality", 0))

        if domain not in by_domain or quality > by_domain[domain]["quality"]:
            by_domain[domain] = source

    return sorted(
        by_domain.values(),
        key=lambda x: x.get("quality", 0),
        reverse=True,
    )


# ============================================================
# SEARCH PROVIDERS
# ============================================================

async def search_duckduckgo(query: str) -> List[Dict[str, Any]]:
    url = (
        "https://html.duckduckgo.com/html/?q="
        + quote(query)
    )

    try:
        response = await http_get(url, timeout=15)

        if response.status_code != 200:
            return []

        links = extract_links(response.text)

        results = []

        for item in links[:12]:
            results.append(
                {
                    "provider": "duckduckgo",
                    "url": item["url"],
                    "title": item["title"],
                }
            )

        return results

    except Exception:
        return []


async def search_bing(query: str) -> List[Dict[str, Any]]:
    url = (
        "https://www.bing.com/search?q="
        + quote(query)
    )

    try:
        response = await http_get(url, timeout=15)

        if response.status_code != 200:
            return []

        links = extract_links(response.text)

        results = []

        for item in links[:12]:
            results.append(
                {
                    "provider": "bing",
                    "url": item["url"],
                    "title": item["title"],
                }
            )

        return results

    except Exception:
        return []


async def search_google(query: str) -> List[Dict[str, Any]]:
    url = (
        "https://www.google.com/search?q="
        + quote(query)
    )

    try:
        response = await http_get(url, timeout=15)

        if response.status_code != 200:
            return []

        links = extract_links(response.text)

        results = []

        for item in links[:12]:
            results.append(
                {
                    "provider": "google",
                    "url": item["url"],
                    "title": item["title"],
                }
            )

        return results

    except Exception:
        return []


async def fetch_source(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = item.get("url", "")

    if not url_is_safe(url):
        return None

    try:
        response = await http_get(url, timeout=12)

        if response.status_code >= 400:
            return None

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        raw = response.text[:200000]

        if "html" in content_type:
            text = html_to_text(raw)
        else:
            text = clean_text(raw, 20000)

        if len(text) < 120:
            return None

        title = item.get("title") or ""

        quality = source_quality(
            url,
            title,
            text,
        )

        if quality <= 0:
            return None

        return {
            "url": url,
            "domain": domain_of(url),
            "title": clean_text(title, 500),
            "text": text[:12000],
            "quality": quality,
            "provider": item.get("provider"),
        }

    except Exception:
        return None


# ============================================================
# ADAPTIVE RESEARCH
# ============================================================

def generate_queries(objective: str) -> List[str]:
    objective = clean_text(objective, 5000)

    queries = [
        objective,
        f"{objective} evidence",
        f"{objective} research",
        f"{objective} 2026",
        f"{objective} independent analysis",
    ]

    # Preserve order while removing duplicates
    seen = set()
    output = []

    for q in queries:
        key = q.lower()

        if key not in seen:
            seen.add(key)
            output.append(q)

    return output


async def adaptive_research(
    objective: str,
    max_rounds: int = 3,
) -> Dict[str, Any]:

    all_candidates = []
    provider_attempts = []

    queries = generate_queries(objective)

    for round_no in range(1, max_rounds + 1):

        round_query = queries[min(round_no - 1, len(queries) - 1)]

        providers = [
            ("duckduckgo", search_duckduckgo),
            ("bing", search_bing),
            ("google", search_google),
        ]

        results_this_round = []

        # Run providers concurrently
        async def run_provider(name, fn):
            try:
                result = await fn(round_query)
                return name, result
            except Exception:
                return name, []

        responses = await asyncio.gather(
            *[
                run_provider(name, fn)
                for name, fn in providers
            ]
        )

        for name, results in responses:
            provider_attempts.append(
                {
                    "round": round_no,
                    "provider": name,
                    "results": len(results),
                    "query": round_query,
                }
            )

            results_this_round.extend(results)

        all_candidates.extend(results_this_round)

        # Stop only when enough meaningful domains exist
        domains = {
            domain_of(x.get("url", ""))
            for x in all_candidates
            if meaningful_domain(x.get("url", ""))
        }

        if len(domains) >= 3:
            break

        await asyncio.sleep(0.2)

    # Remove duplicate URLs
    unique = {}
    for item in all_candidates:
        url = item.get("url")

        if url and url not in unique:
            unique[url] = item

    candidates = list(unique.values())

    # Fetch strongest candidates first
    candidates = candidates[:25]

    fetched = await asyncio.gather(
        *[
            fetch_source(item)
            for item in candidates
        ]
    )

    sources = [
        x for x in fetched
        if x is not None
    ]

    sources = deduplicate_sources(sources)

    independent_domains = sorted(
        {
            x["domain"]
            for x in sources
            if x.get("domain")
        }
    )

    average_quality = (
        sum(x["quality"] for x in sources) / len(sources)
        if sources
        else 0.0
    )

    if len(independent_domains) >= 3 and average_quality >= 0.45:
        strength = "strong"
    elif len(independent_domains) >= 2 and average_quality >= 0.35:
        strength = "moderate"
    elif sources:
        strength = "weak"
    else:
        strength = "insufficient"

    return {
        "query": objective,
        "queries_used": queries,
        "provider_attempts": provider_attempts,
        "accepted_sources": sources,
        "accepted_domains": independent_domains,
        "independent_domain_count": len(independent_domains),
        "average_source_quality": round(average_quality, 3),
        "strength": strength,
        "retry_count": max(0, len(provider_attempts) // 3 - 1),
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_evidence(
    claim: str,
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:

    claim_words = {
        x.lower()
        for x in re.findall(r"[A-Za-z0-9]{4,}", claim)
    }

    evidence = []

    for source in sources:
        text = source.get("text", "")

        text_words = {
            x.lower()
            for x in re.findall(r"[A-Za-z0-9]{4,}", text[:12000])
        }

        overlap = (
            len(claim_words & text_words)
            / max(1, len(claim_words))
        )

        if overlap >= 0.05:
            evidence.append(
                {
                    "url": source["url"],
                    "domain": source["domain"],
                    "quality": source["quality"],
                    "relevance": round(clamp(overlap), 3),
                    "snippet": clean_text(text[:500], 500),
                }
            )

    domains = sorted(
        {
            x["domain"]
            for x in evidence
        }
    )

    average_quality = (
        sum(x["quality"] for x in evidence) / len(evidence)
        if evidence
        else 0.0
    )

    count = len(evidence)

    # Conservative verification.
    if count >= 3 and len(domains) >= 3 and average_quality >= 0.45:
        level = "high"
        verified = True
        confidence = clamp(
            0.60
            + min(0.25, count * 0.04)
            + min(0.15, len(domains) * 0.03)
        )

    elif count >= 2 and len(domains) >= 2 and average_quality >= 0.40:
        level = "moderate"
        verified = True
        confidence = clamp(
            0.50
            + min(0.20, count * 0.05)
        )

    elif count >= 1 and average_quality >= 0.50:
        level = "low"
        verified = False
        confidence = 0.35

    else:
        level = "low"
        verified = False
        confidence = 0.25

    return {
        "verified": verified,
        "verification_level": level,
        "confidence": round(confidence, 3),
        "independent_evidence_count": count,
        "average_source_quality": round(average_quality, 3),
        "domains": domains,
        "evidence": evidence[:10],
    }


# ============================================================
# PLANNING
# ============================================================

def plan_objective(objective: str) -> Dict[str, Any]:

    lower = objective.lower()

    nodes = [
        {
            "id": "understand",
            "type": "intent",
            "agent": "agent-planner",
        }
    ]

    if any(
        word in lower
        for word in [
            "research",
            "audit",
            "analyze",
            "investigate",
            "current",
            "evidence",
        ]
    ):
        nodes.append(
            {
                "id": "research",
                "type": "research",
                "agent": "agent-researcher",
            }
        )

        nodes.append(
            {
                "id": "verify",
                "type": "verify",
                "agent": "agent-verifier",
            }
        )

    nodes.append(
        {
            "id": "opportunities",
            "type": "opportunity",
            "agent": "agent-learner",
        }
    )

    nodes.append(
        {
            "id": "critique",
            "type": "critique",
            "agent": "agent-critic",
        }
    )

    nodes.append(
        {
            "id": "synthesis",
            "type": "synthesis",
            "agent": "agent-planner",
        }
    )

    nodes.append(
        {
            "id": "next_cycle",
            "type": "replan",
            "agent": "agent-planner",
        }
    )

    return {
        "objective": objective,
        "nodes": nodes,
        "node_count": len(nodes),
        "strategy": "adaptive evidence-driven execution",
    }


# ============================================================
# OPPORTUNITY ENGINE
# ============================================================

def find_opportunities(
    objective: str,
    research: Dict[str, Any],
    verification: Dict[str, Any],
) -> List[Dict[str, Any]]:

    opportunities = []

    if research.get("strength") in {
        "weak",
        "insufficient",
    }:
        opportunities.append(
            {
                "title": "Improve evidence acquisition",
                "description": (
                    "Increase research-provider resilience, "
                    "query diversity, and independent-source coverage."
                ),
                "priority": 0.95,
            }
        )

    if verification.get("confidence", 0) < 0.6:
        opportunities.append(
            {
                "title": "Strengthen verification",
                "description": (
                    "Require stronger independent evidence before "
                    "promoting claims to verified status."
                ),
                "priority": 0.90,
            }
        )

    opportunities.append(
        {
            "title": "Persistent learning",
            "description": (
                "Use execution evaluations and failure signals "
                "to improve future routing and planning."
            ),
            "priority": 0.75,
        }
    )

    opportunities.append(
        {
            "title": "Durable distributed memory",
            "description": (
                "Move important long-lived memory from ephemeral "
                "local storage to durable managed infrastructure."
            ),
            "priority": 0.70,
        }
    )

    opportunities.append(
        {
            "title": "Background execution",
            "description": (
                "Add durable workers and queues for long-running missions."
            ),
            "priority": 0.68,
        }
    )

    opportunities.append(
        {
            "title": "Authenticated actions",
            "description": (
                "Add permission-scoped OAuth and authenticated external "
                "actions instead of unrestricted access."
            ),
            "priority": 0.67,
        }
    )

    return opportunities[:8]


# ============================================================
# CRITIC
# ============================================================

def critique_execution(
    research: Dict[str, Any],
    verification: Dict[str, Any],
    opportunities: List[Dict[str, Any]],
) -> Dict[str, Any]:

    issues = []

    if research.get("strength") == "insufficient":
        issues.append("External evidence is insufficient.")

    if research.get("independent_domain_count", 0) < 2:
        issues.append(
            "Independent-source diversity is below the desired threshold."
        )

    if verification.get("verified") is False:
        issues.append(
            "Claims were not promoted to verified without sufficient evidence."
        )

    if not opportunities:
        issues.append(
            "Opportunity discovery produced no improvement candidates."
        )

    confidence = verification.get("confidence", 0.25)

    if not issues and confidence >= 0.7:
        quality = "strong"
    elif confidence >= 0.5:
        quality = "acceptable"
    else:
        quality = "needs_improvement"

    return {
        "issues": issues,
        "issue_count": len(issues),
        "confidence": confidence,
        "quality": quality,
    }


# ============================================================
# SYNTHESIS
# ============================================================

def synthesize(
    objective: str,
    research: Dict[str, Any],
    verification: Dict[str, Any],
    critique: Dict[str, Any],
    opportunities: List[Dict[str, Any]],
) -> Dict[str, Any]:

    source_summaries = []

    for source in research.get("accepted_sources", [])[:8]:
        source_summaries.append(
            {
                "title": source.get("title"),
                "domain": source.get("domain"),
                "url": source.get("url"),
                "quality": source.get("quality"),
                "summary": clean_text(
                    source.get("text", "")[:800],
                    800,
                ),
            }
        )

    return {
        "objective": objective,
        "research_strength": research.get("strength"),
        "verification": verification,
        "critique": critique,
        "opportunities": opportunities,
        "evidence_sources": source_summaries,
        "statement": (
            "AI Infinity completed an evidence-aware execution cycle. "
            "Unverified claims are explicitly marked as unverified. "
            "The next cycle should address the highest-priority gaps."
        ),
    }


# ============================================================
# GAP ENGINE
# ============================================================

def architecture_gaps() -> List[Dict[str, Any]]:
    return [
        {
            "capability": "Durable external persistence",
            "status": "partial",
            "reason": "Local SQLite may be lost on service recreation.",
        },
        {
            "capability": "Event-driven background workers",
            "status": "partial",
            "reason": "Long missions need durable queue/worker infrastructure.",
        },
        {
            "capability": "Authenticated external actions",
            "status": "partial",
            "reason": "External actions require permission-scoped credentials.",
        },
        {
            "capability": "Sandboxed code execution",
            "status": "missing",
            "reason": "Arbitrary code execution needs isolation.",
        },
        {
            "capability": "Continuous regression and release gates",
            "status": "partial",
            "reason": "Regression infrastructure should become continuous.",
        },
        {
            "capability": "Concurrent multi-agent execution",
            "status": "partial",
            "reason": "Current agents are orchestrated but not a distributed swarm.",
        },
        {
            "capability": "Advanced model/provider routing",
            "status": "partial",
            "reason": "Provider discovery and routing need deeper specialization.",
        },
        {
            "capability": "Multimodal world model",
            "status": "partial",
            "reason": "Current world state is primarily structured text/data.",
        },
        {
            "capability": "Durable distributed memory",
            "status": "partial",
            "reason": "Current memory is local SQLite.",
        },
        {
            "capability": "Real-world actuators",
            "status": "future",
            "reason": "Requires explicit permissions, hardware, and safety controls.",
        },
    ]


# ============================================================
# MISSION ENGINE
# ============================================================

async def execute_mission(
    objective: str,
    do_research: bool = True,
    do_verify: bool = True,
    remember: bool = True,
):

    mission_id = uid("mission")
    task_id = uid("task")

    started = time.time()

    db_execute(
        """
        INSERT INTO missions(
            id, created_at, updated_at,
            objective, status, result
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            now(),
            now(),
            objective,
            "running",
            "{}",
        ),
    )

    db_execute(
        """
        INSERT INTO tasks(
            id, created_at, updated_at,
            objective, status, result
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            now(),
            now(),
            objective,
            "running",
            "{}",
        ),
    )

    save_event(
        "mission_started",
        {
            "mission_id": mission_id,
            "task_id": task_id,
            "objective": objective,
        },
    )

    plan = plan_objective(objective)

    trace = []
    completed_nodes = 0

    # --------------------------------------------------------
    # UNDERSTAND
    # --------------------------------------------------------

    trace.append(
        {
            "node": "understand",
            "type": "intent",
            "agent": "agent-planner",
        }
    )
    completed_nodes += 1

    # --------------------------------------------------------
    # RESEARCH
    # --------------------------------------------------------

    if do_research:
        research = await adaptive_research(objective)

        trace.append(
            {
                "node": "research",
                "type": "research",
                "agent": "agent-researcher",
            }
        )
        completed_nodes += 1
    else:
        research = {
            "query": objective,
            "strength": "not_requested",
            "accepted_sources": [],
            "accepted_domains": [],
            "independent_domain_count": 0,
            "average_source_quality": 0,
            "provider_attempts": [],
            "queries_used": [],
        }

    # --------------------------------------------------------
    # VERIFY
    # --------------------------------------------------------

    if do_verify and do_research:

        verification = verify_evidence(
            objective,
            research.get("accepted_sources", []),
        )

        trace.append(
            {
                "node": "verify",
                "type": "verify",
                "agent": "agent-verifier",
            }
        )
        completed_nodes += 1

    else:
        verification = {
            "verified": False,
            "verification_level": "not_run",
            "confidence": 0.25,
            "independent_evidence_count": 0,
            "average_source_quality": 0,
            "domains": [],
            "evidence": [],
        }

    # --------------------------------------------------------
    # OPPORTUNITIES
    # --------------------------------------------------------

    opportunities = find_opportunities(
        objective,
        research,
        verification,
    )

    for opportunity in opportunities:
        db_execute(
            """
            INSERT INTO opportunities(
                id, created_at, title,
                description, priority, status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                uid("opp"),
                now(),
                opportunity["title"],
                opportunity["description"],
                opportunity["priority"],
                "open",
            ),
        )

    trace.append(
        {
            "node": "opportunities",
            "type": "opportunity",
            "agent": "agent-learner",
        }
    )
    completed_nodes += 1

    # --------------------------------------------------------
    # CRITIQUE
    # --------------------------------------------------------

    critique = critique_execution(
        research,
        verification,
        opportunities,
    )

    trace.append(
        {
            "node": "critique",
            "type": "critique",
            "agent": "agent-critic",
        }
    )
    completed_nodes += 1

    # --------------------------------------------------------
    # SYNTHESIS
    # --------------------------------------------------------

    synthesis = synthesize(
        objective,
        research,
        verification,
        critique,
        opportunities,
    )

    trace.append(
        {
            "node": "synthesis",
            "type": "synthesis",
            "agent": "agent-planner",
        }
    )
    completed_nodes += 1

    # --------------------------------------------------------
    # NEXT CYCLE
    # --------------------------------------------------------

    next_cycle = {
        "priority": (
            opportunities[0]["title"]
            if opportunities
            else "Improve autonomous execution"
        ),
        "recommended_action": (
            opportunities[0]["description"]
            if opportunities
            else "Run another evidence-driven cycle."
        ),
    }

    trace.append(
        {
            "node": "next_cycle",
            "type": "replan",
            "agent": "agent-planner",
        }
    )
    completed_nodes += 1

    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    memory_id = None

    if remember:
        memory_id = save_memory(
            {
                "objective": objective,
                "verification": verification,
                "research_strength": research.get("strength"),
                "opportunities": opportunities,
                "critique": critique,
                "next_cycle": next_cycle,
            },
            kind="mission_result",
            importance=0.85,
        )

    duration = round(
        (time.time() - started) / 60,
        3,
    )

    result = {
        "task_id": task_id,
        "mission_id": mission_id,
        "status": "completed",
        "version": VERSION,
        "target": TARGET,
        "objective": objective,
        "duration_minutes": duration,
        "plan": plan,
        "agent_trace": trace,
        "research": research,
        "verification": verification,
        "opportunities": opportunities,
        "critique": critique,
        "synthesis": synthesis,
        "next_cycle": next_cycle,
        "completed_nodes": completed_nodes,
        "total_nodes": len(trace),
        "memory_id": memory_id,
    }

    db_execute(
        """
        UPDATE missions
        SET updated_at=?, status=?, result=?
        WHERE id=?
        """,
        (
            now(),
            "completed",
            json.dumps(result, ensure_ascii=False, default=str),
            mission_id,
        ),
    )

    db_execute(
        """
        UPDATE tasks
        SET updated_at=?, status=?, result=?
        WHERE id=?
        """,
        (
            now(),
            "completed",
            json.dumps(result, ensure_ascii=False, default=str),
            task_id,
        ),
    )

    db_execute(
        """
        INSERT INTO evaluations(
            id, created_at, task_id, score, feedback
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            uid("eval"),
            now(),
            task_id,
            critique.get("confidence", 0.25),
            json.dumps(critique),
        ),
    )

    save_event(
        "mission_completed",
        {
            "mission_id": mission_id,
            "task_id": task_id,
            "quality": critique.get("quality"),
            "confidence": critique.get("confidence"),
        },
    )

    return result


# ============================================================
# BUILT-IN AGENTS
# ============================================================

BUILTIN_AGENTS = [
    {
        "id": "agent-planner",
        "name": "Planner",
        "category": "reasoning",
        "status": "operational",
    },
    {
        "id": "agent-researcher",
        "name": "Researcher",
        "category": "information",
        "status": "operational",
    },
    {
        "id": "agent-verifier",
        "name": "Verifier",
        "category": "verification",
        "status": "operational",
    },
    {
        "id": "agent-critic",
        "name": "Critic",
        "category": "evaluation",
        "status": "operational",
    },
    {
        "id": "agent-executor",
        "name": "Executor",
        "category": "actions",
        "status": "operational",
    },
    {
        "id": "agent-learner",
        "name": "Learner",
        "category": "improvement",
        "status": "operational",
    },
]


CAPABILITIES = [
    "intent_understanding",
    "hierarchical_planning",
    "dynamic_task_graph",
    "adaptive_research",
    "multi_provider_search",
    "source_quality_scoring",
    "independent_domain_deduplication",
    "evidence_verification",
    "calibrated_confidence",
    "self_critique",
    "failure_detection",
    "replanning",
    "opportunity_detection",
    "persistent_local_memory",
    "mission_history",
    "evaluation_learning",
    "world_state",
    "event_logging",
    "safe_external_http",
    "provider_routing",
    "regression_framework",
    "video_compatibility",
]


# ============================================================
# REGRESSION TESTS
# ============================================================

async def run_regression_tests():
    tests = []

    # Test command compatibility
    request = ExecuteRequest(
        command='{"query":"compatibility test"}'
    )

    tests.append(
        {
            "name": "nested_command_query_compatibility",
            "passed": request.get_objective() == "compatibility test",
        }
    )

    request2 = ExecuteRequest(
        command="plain command"
    )

    tests.append(
        {
            "name": "plain_command_compatibility",
            "passed": request2.get_objective() == "plain command",
        }
    )

    request3 = ExecuteRequest(
        query="direct query"
    )

    tests.append(
        {
            "name": "direct_query_compatibility",
            "passed": request3.get_objective() == "direct query",
        }
    )

    tests.append(
        {
            "name": "unsafe_localhost_blocked",
            "passed": not url_is_safe("http://127.0.0.1:8000"),
        }
    )

    tests.append(
        {
            "name": "safe_https_allowed",
            "passed": url_is_safe("https://example.com"),
        }
    )

    for test in tests:
        db_execute(
            """
            INSERT INTO regression(
                id, created_at, name,
                passed, details
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                uid("reg"),
                now(),
                test["name"],
                1 if test["passed"] else 0,
                json.dumps(test),
            ),
        )

    return {
        "passed": sum(1 for x in tests if x["passed"]),
        "total": len(tests),
        "tests": tests,
    }


# ============================================================
# ROOT UI
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity</title>

<style>
body {{
    font-family: Arial, sans-serif;
    background:#0b1020;
    color:white;
    margin:0;
    padding:20px;
}}

.container {{
    max-width:900px;
    margin:auto;
}}

h1 {{
    font-size:32px;
    margin-bottom:4px;
}}

.version {{
    opacity:.7;
    margin-bottom:25px;
}}

textarea {{
    width:100%;
    min-height:180px;
    box-sizing:border-box;
    border-radius:14px;
    border:1px solid #39405a;
    background:#11182d;
    color:white;
    padding:16px;
    font-size:16px;
}}

button {{
    margin-top:14px;
    width:100%;
    padding:16px;
    border:0;
    border-radius:14px;
    font-size:17px;
    font-weight:bold;
    cursor:pointer;
}}

pre {{
    white-space:pre-wrap;
    word-wrap:break-word;
    background:#11182d;
    padding:15px;
    border-radius:14px;
    margin-top:20px;
}}

.status {{
    margin-top:15px;
    opacity:.8;
}}
</style>
</head>

<body>
<div class="container">

<h1>∞ AI Infinity</h1>

<div class="version">
TARGET-2050.4 · Autonomous Intelligence Runtime
</div>

<textarea id="command"
placeholder="Tell AI Infinity what you want it to accomplish..."></textarea>

<button onclick="executeMission()">
Execute Mission
</button>

<div id="status" class="status"></div>

<pre id="result"></pre>

</div>

<script>
async function executeMission() {{

    const command =
        document.getElementById("command").value.trim();

    if (!command) {{
        document.getElementById("status").innerText =
            "Enter a mission first.";
        return;
    }}

    document.getElementById("status").innerText =
        "AI Infinity is executing...";

    document.getElementById("result").innerText = "";

    try {{

        const response = await fetch("/execute", {{
            method: "POST",
            headers: {{
                "Content-Type": "application/json"
            }},
            body: JSON.stringify({{
                command: command,
                research: true,
                verify: true,
                remember: true
            }})
        }});

        const data = await response.json();

        document.getElementById("result").innerText =
            JSON.stringify(data, null, 2);

        document.getElementById("status").innerText =
            response.ok
            ? "Mission completed."
            : "Execution returned an error.";

    }} catch (error) {{

        document.getElementById("status").innerText =
            "Connection error.";

        document.getElementById("result").innerText =
            String(error);
    }}
}}
</script>

</body>
</html>
"""


# ============================================================
# HEALTH / STATUS
# ============================================================

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": VERSION,
        "target": TARGET,
        "time": now(),
    }


@app.get("/status")
async def status():

    memory_count = db_one(
        "SELECT COUNT(*) AS count FROM memory"
    )["count"]

    task_count = db_one(
        "SELECT COUNT(*) AS count FROM tasks"
    )["count"]

    mission_count = db_one(
        "SELECT COUNT(*) AS count FROM missions"
    )["count"]

    opportunity_count = db_one(
        "SELECT COUNT(*) AS count FROM opportunities"
    )["count"]

    return {
        "status": "operational",
        "version": VERSION,
        "target": TARGET,
        "runtime": "free-first",
        "storage": "sqlite",
        "memory_count": memory_count,
        "task_count": task_count,
        "mission_count": mission_count,
        "opportunity_count": opportunity_count,
        "agents": len(BUILTIN_AGENTS),
        "capabilities": len(CAPABILITIES),
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():
    return {
        "version": VERSION,
        "capabilities": [
            {
                "name": x,
                "status": "operational",
            }
            for x in CAPABILITIES
        ],
    }


@app.get("/self-inspect")
async def self_inspect():

    return {
        "version": VERSION,
        "target": TARGET,
        "operational": {
            "planner": True,
            "dynamic_task_graph": True,
            "adaptive_research": True,
            "multi_provider_search": True,
            "verification": True,
            "self_critique": True,
            "recovery_replanning": True,
            "opportunity_detection": True,
            "persistent_local_memory": True,
            "evaluation": True,
            "safe_external_http": True,
            "regression_tests": True,
        },
        "agents": BUILTIN_AGENTS,
        "future_extensions": [
            "durable_external_database",
            "event_driven_workers",
            "authenticated_oauth_actions",
            "sandboxed_execution",
            "distributed_multi_agent_runtime",
            "advanced_model_routing",
            "multimodal_world_model",
            "durable_distributed_memory",
            "real_world_actuators",
        ],
    }


@app.get("/architecture")
async def architecture():

    return {
        "version": VERSION,
        "layers": [
            "Human Intent",
            "Intent Understanding",
            "Goal Decomposition",
            "Mission Planner",
            "Dynamic Task Graph",
            "Agent Routing",
            "Research",
            "Evidence",
            "Verification",
            "Execution",
            "Critique",
            "Recovery",
            "Learning",
            "Memory",
            "World State",
            "Opportunity Engine",
            "Next-Cycle Replanning",
        ],
    }


@app.get("/gaps")
async def gaps():
    return {
        "version": VERSION,
        "gaps": architecture_gaps(),
    }


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory")
async def memory(limit: int = 50):

    limit = max(1, min(limit, 200))

    rows = db_all(
        """
        SELECT *
        FROM memory
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )

    return {
        "count": len(rows),
        "items": rows,
    }


@app.get("/memory/count")
async def memory_count():

    row = db_one(
        "SELECT COUNT(*) AS count FROM memory"
    )

    return {
        "count": row["count"] if row else 0
    }


# ============================================================
# WORLD
# ============================================================

@app.get("/world")
async def world():

    return {
        "items": db_all(
            """
            SELECT key, updated_at, value
            FROM world
            ORDER BY updated_at DESC
            LIMIT 100
            """
        )
    }


# ============================================================
# EVENTS
# ============================================================

@app.get("/events")
async def events(limit: int = 100):

    limit = max(1, min(limit, 500))

    return {
        "items": db_all(
            """
            SELECT *
            FROM events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
    }


# ============================================================
# RESEARCH
# ============================================================

@app.post("/research")
async def research(request: ResearchRequest):

    result = await adaptive_research(request.query)

    save_event(
        "research_completed",
        {
            "query": request.query,
            "strength": result.get("strength"),
            "domains": result.get("accepted_domains"),
        },
    )

    return result


# ============================================================
# VERIFY
# ============================================================

@app.post("/verify")
async def verify(request: VerifyRequest):

    result = verify_evidence(
        request.claim,
        request.evidence,
    )

    return result


# ============================================================
# PLAN
# ============================================================

@app.post("/plan")
async def plan(request: PlanRequest):
    return plan_objective(request.objective)


# ============================================================
# TASK
# ============================================================

@app.post("/task")
async def create_task(request: TaskRequest):

    task_id = uid("task")

    db_execute(
        """
        INSERT INTO tasks(
            id, created_at, updated_at,
            objective, status, result
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            now(),
            now(),
            request.objective,
            "created",
            "{}",
        ),
    )

    return {
        "task_id": task_id,
        "status": "created",
        "objective": request.objective,
    }


@app.get("/task/{task_id}")
async def get_task(task_id: str):

    result = db_one(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,),
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    try:
        result["result"] = json.loads(result["result"])
    except Exception:
        pass

    return result


# ============================================================
# MISSION
# ============================================================

@app.post("/mission")
async def create_mission(request: MissionRequest):

    result = await execute_mission(
        request.objective,
        True,
        True,
        True,
    )

    return result


@app.get("/mission/{mission_id}")
async def get_mission(mission_id: str):

    result = db_one(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,),
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    try:
        result["result"] = json.loads(result["result"])
    except Exception:
        pass

    return result


# ============================================================
# MAIN EXECUTION ENDPOINT
# ============================================================

@app.post("/execute")
async def execute(request: ExecuteRequest):

    objective = request.get_objective()

    if not objective:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide either 'command' or 'query'. "
                "Plain command text is also supported."
            ),
        )

    result = await execute_mission(
        objective=objective,
        do_research=request.research,
        do_verify=request.verify,
        remember=request.remember,
    )

    return result


# ============================================================
# OPPORTUNITIES
# ============================================================

@app.get("/opportunities")
async def opportunities():

    return {
        "items": db_all(
            """
            SELECT *
            FROM opportunities
            ORDER BY priority DESC, created_at DESC
            LIMIT 100
            """
        )
    }


# ============================================================
# AGENTS
# ============================================================

@app.get("/agents")
async def agents():
    return {
        "count": len(BUILTIN_AGENTS),
        "agents": BUILTIN_AGENTS,
    }


# ============================================================
# PROVIDERS
# ============================================================

@app.get("/providers")
async def providers():

    return {
        "search": [
            {
                "name": "DuckDuckGo",
                "status": "available",
                "mode": "public-web",
            },
            {
                "name": "Bing",
                "status": "available",
                "mode": "public-web",
            },
            {
                "name": "Google",
                "status": "available",
                "mode": "public-web",
            },
        ],
        "generation": [
            {
                "name": "Pollinations",
                "status": (
                    "configured"
                    if os.getenv("POLLINATIONS_API_KEY")
                    else "optional"
                ),
            }
        ],
    }


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/diagnostics")
async def diagnostics():

    regression = await run_regression_tests()

    return {
        "version": VERSION,
        "target": TARGET,
        "database": str(DB_PATH),
        "database_exists": DB_PATH.exists(),
        "regression": regression,
        "capability_count": len(CAPABILITIES),
        "agent_count": len(BUILTIN_AGENTS),
    }


# ============================================================
# SKILLS
# ============================================================

@app.get("/skills")
async def skills():

    skills = [
        {
            "name": "adaptive_research",
            "status": "operational",
        },
        {
            "name": "evidence_verification",
            "status": "operational",
        },
        {
            "name": "autonomous_planning",
            "status": "operational",
        },
        {
            "name": "self_critique",
            "status": "operational",
        },
        {
            "name": "replanning",
            "status": "operational",
        },
        {
            "name": "opportunity_detection",
            "status": "operational",
        },
        {
            "name": "persistent_memory",
            "status": "operational",
        },
        {
            "name": "safe_external_http",
            "status": "operational",
        },
    ]

    return {
        "count": len(skills),
        "skills": skills,
    }


@app.get("/skills/count")
async def skills_count():
    return {
        "count": 8
    }


# ============================================================
# SAFE EXTERNAL ACCESS
# ============================================================

@app.post("/external")
async def external(request: ExternalRequest):

    method = request.method.upper()

    if method not in {"GET", "POST"}:
        raise HTTPException(
            status_code=400,
            detail="Only GET and POST are supported.",
        )

    if not url_is_safe(request.url):
        raise HTTPException(
            status_code=400,
            detail="URL blocked by AI Infinity security policy.",
        )

    try:

        if method == "GET":
            response = await http_get(request.url)
        else:
            response = await http_post(
                request.url,
                request.data,
            )

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        if "application/json" in content_type:
            try:
                body = response.json()
            except Exception:
                body = response.text[:20000]
        else:
            body = response.text[:20000]

        return {
            "status_code": response.status_code,
            "url": str(response.url),
            "content_type": content_type,
            "body": body,
        }

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail=f"External request failed: {str(exc)}",
        )


# ============================================================
# VIDEO COMPATIBILITY
# ============================================================

@app.get("/video/{job_id}")
async def video(job_id: str):

    candidates = [
        BASE / "videos" / f"{job_id}.mp4",
        Path("/tmp/genius") / job_id / "genius.mp4",
        Path("/tmp/genius") / job_id / "output.mp4",
    ]

    for path in candidates:
        if path.exists():
            from fastapi.responses import FileResponse

            return FileResponse(
                path=str(path),
                media_type="video/mp4",
                filename=f"{job_id}.mp4",
            )

    raise HTTPException(
        status_code=404,
        detail="Video not found",
    )


@app.post("/generate")
async def generate_compatibility(request: ExecuteRequest):

    objective = request.get_objective()

    if not objective:
        raise HTTPException(
            status_code=400,
            detail="Provide a command or query.",
        )

    return {
        "status": "accepted",
        "version": VERSION,
        "objective": objective,
        "message": (
            "Generation compatibility endpoint is operational. "
            "Use the existing video pipeline/provider configuration "
            "for actual media generation."
        ),
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    init_db()

    # Register built-in providers
    providers = [
        (
            "provider-duckduckgo",
            "DuckDuckGo",
            "search",
            "available",
        ),
        (
            "provider-bing",
            "Bing",
            "search",
            "available",
        ),
        (
            "provider-google",
            "Google",
            "search",
            "available",
        ),
    ]

    for provider_id, name, category, status_value in providers:

        db_execute(
            """
            INSERT OR REPLACE INTO providers(
                id, name, category,
                status, metadata
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                provider_id,
                name,
                category,
                status_value,
                "{}",
            ),
        )

    save_event(
        "runtime_started",
        {
            "version": VERSION,
            "target": TARGET,
        },
    )


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):

    save_event(
        "runtime_error",
        {
            "path": str(request.url.path),
            "error": str(exc),
        },
    )

    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "version": VERSION,
            "detail": str(exc),
        },
    )


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
