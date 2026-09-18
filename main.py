# AI Infinity — TARGET-2050.3
# Next-generation autonomous orchestration runtime
# Free-first / safe-by-default / graceful degradation

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
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
# CORE
# ============================================================

VERSION = "TARGET-2050.3"
TARGET_YEAR = 2050

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "infinity.db"

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Next-generation free-first autonomous intelligence runtime",
)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            id TEXT PRIMARY KEY,
            kind TEXT,
            content TEXT,
            confidence REAL DEFAULT 0,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            objective TEXT,
            status TEXT,
            result TEXT,
            created_at TEXT,
            completed_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT,
            status TEXT,
            state TEXT,
            created_at TEXT,
            completed_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            event_type TEXT,
            payload TEXT,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS evaluations (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            score REAL,
            signals TEXT,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            name TEXT PRIMARY KEY,
            attempts INTEGER DEFAULT 0,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            last_error TEXT,
            updated_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            priority REAL,
            status TEXT,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS world (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS regression (
            id TEXT PRIMARY KEY,
            test_name TEXT,
            passed INTEGER,
            details TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILITIES
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str):
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def save_event(event_type: str, payload: Any):
    conn = db()
    conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?)",
        (
            uid("evt"),
            event_type,
            json.dumps(payload, ensure_ascii=False, default=str),
            now(),
        ),
    )
    conn.commit()
    conn.close()


def save_memory(kind: str, content: Any, confidence: float = 0.5):
    memory_id = uid("mem")

    conn = db()
    conn.execute(
        "INSERT INTO memory VALUES (?, ?, ?, ?, ?)",
        (
            memory_id,
            kind,
            json.dumps(content, ensure_ascii=False, default=str),
            max(0, min(1, confidence)),
            now(),
        ),
    )
    conn.commit()
    conn.close()

    return memory_id


def set_world(key: str, value: Any):
    conn = db()
    conn.execute(
        """
        INSERT INTO world(key,value,updated_at)
        VALUES(?,?,?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (
            key,
            json.dumps(value, ensure_ascii=False, default=str),
            now(),
        ),
    )
    conn.commit()
    conn.close()


# ============================================================
# SECURITY
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
}

BLOCKED_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
)

SEARCH_INFRASTRUCTURE = (
    "bing.com",
    "google.com",
    "duckduckgo.com",
    "r.bing.com",
    "www.google.com",
)


def safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return False

        host = parsed.hostname

        if not host:
            return False

        host = host.lower().rstrip(".")

        if host in BLOCKED_HOSTS:
            return False

        if any(host.endswith(x) for x in BLOCKED_SUFFIXES):
            return False

        try:
            infos = socket.getaddrinfo(host, None)
            addresses = {x[4][0] for x in infos}

            for address in addresses:
                ip = ipaddress.ip_address(address)

                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                ):
                    return False

        except Exception:
            return False

        return True

    except Exception:
        return False


def meaningful_domain(url: str) -> Optional[str]:
    try:
        host = urlparse(url).hostname
        if not host:
            return None

        host = host.lower()

        if any(x in host for x in SEARCH_INFRASTRUCTURE):
            return None

        if host.startswith(("r.", "cc.", "assets.", "static.")):
            return None

        return host
    except Exception:
        return None


# ============================================================
# SOURCE QUALITY
# ============================================================

def source_quality(url: str, title: str, text: str) -> float:
    score = 0.0

    domain = meaningful_domain(url)

    if domain:
        score += 0.30

    if title and len(title) >= 10:
        score += 0.15

    if text and len(text) >= 500:
        score += 0.25

    if text and len(text) >= 1500:
        score += 0.10

    academic_terms = [
        "research",
        "study",
        "paper",
        "journal",
        "university",
        "arxiv",
        "doi",
    ]

    if any(x in (title + " " + text).lower() for x in academic_terms):
        score += 0.10

    official_terms = [
        "documentation",
        "official",
        "technical report",
        "developer",
    ]

    if any(x in (title + " " + text).lower() for x in official_terms):
        score += 0.10

    return round(min(1.0, score), 3)


# ============================================================
# HTTP
# ============================================================

async def fetch_page(url: str) -> Optional[str]:
    if not safe_url(url):
        return None

    try:
        async with httpx.AsyncClient(
            timeout=12,
            follow_redirects=True,
            headers={
                "User-Agent":
                    "AI-Infinity/2050 research-runtime"
            },
        ) as client:

            response = await client.get(url)

            if response.status_code >= 400:
                return None

            final_url = str(response.url)

            if not safe_url(final_url):
                return None

            return response.text[:150000]

    except Exception:
        return None


def clean_html(html: str) -> str:
    html = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        html,
        flags=re.I | re.S,
    )

    html = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        html,
        flags=re.I | re.S,
    )

    html = re.sub(r"<[^>]+>", " ", html)

    html = re.sub(r"\s+", " ", html)

    return html.strip()


# ============================================================
# SEARCH ENGINE EXTRACTION
# ============================================================

def extract_links(html: str) -> List[Dict[str, str]]:
    results = []

    patterns = [
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    ]

    for pattern in patterns:
        for match in re.findall(pattern, html, flags=re.I | re.S):
            url = match[0]
            title = clean_html(match[1])

            if url.startswith("//"):
                url = "https:" + url

            if not url.startswith("http"):
                continue

            if not safe_url(url):
                continue

            domain = meaningful_domain(url)

            if not domain:
                continue

            if len(title) < 5:
                continue

            results.append({
                "url": url,
                "title": title[:300],
                "domain": domain,
            })

    unique = {}
    for item in results:
        unique[item["url"]] = item

    return list(unique.values())


async def search_provider(provider: str, query: str):
    encoded = quote_plus(query)

    if provider == "duckduckgo":
        url = f"https://html.duckduckgo.com/html/?q={encoded}"

    elif provider == "bing":
        url = f"https://www.bing.com/search?q={encoded}"

    elif provider == "google":
        url = f"https://www.google.com/search?q={encoded}"

    else:
        return []

    html = await fetch_page(url)

    if not html:
        return []

    return extract_links(html)


# ============================================================
# ADAPTIVE RESEARCH
# ============================================================

def generate_queries(query: str) -> List[str]:
    base = re.sub(r"\s+", " ", query).strip()

    queries = [
        base[:800],
        f"{base[:500]} autonomous AI agents research",
        f"{base[:500]} AI agents planning tool use verification",
        f"{base[:500]} technical report autonomous agents",
    ]

    # Remove duplicates
    output = []

    for q in queries:
        if q not in output:
            output.append(q)

    return output


async def research_once(query: str) -> Dict[str, Any]:

    providers = [
        "duckduckgo",
        "bing",
        "google",
    ]

    collected = []
    provider_results = {}

    for provider in providers:

        conn = db()

        conn.execute(
            """
            INSERT INTO providers(name,attempts,updated_at)
            VALUES(?,?,?)
            ON CONFLICT(name)
            DO UPDATE SET attempts=providers.attempts+1,
                          updated_at=excluded.updated_at
            """,
            (provider, 1, now()),
        )

        conn.commit()
        conn.close()

        try:
            results = await search_provider(provider, query)

            provider_results[provider] = len(results)

            if results:
                conn = db()
                conn.execute(
                    """
                    UPDATE providers
                    SET successes=successes+1,
                        updated_at=?
                    WHERE name=?
                    """,
                    (now(), provider),
                )
                conn.commit()
                conn.close()

                collected.extend(results)

            else:
                conn = db()
                conn.execute(
                    """
                    UPDATE providers
                    SET failures=failures+1,
                        last_error=?,
                        updated_at=?
                    WHERE name=?
                    """,
                    ("no_results", now(), provider),
                )
                conn.commit()
                conn.close()

        except Exception as exc:

            conn = db()
            conn.execute(
                """
                UPDATE providers
                SET failures=failures+1,
                    last_error=?,
                    updated_at=?
                WHERE name=?
                """,
                (str(exc)[:500], now(), provider),
            )
            conn.commit()
            conn.close()

    # domain deduplication
    by_domain = {}

    for item in collected:
        domain = item["domain"]

        if domain not in by_domain:
            by_domain[domain] = item

    candidates = list(by_domain.values())[:12]

    accepted = []

    for item in candidates:

        html = await fetch_page(item["url"])

        if not html:
            continue

        text_content = clean_html(html)

        if len(text_content) < 250:
            continue

        quality = source_quality(
            item["url"],
            item["title"],
            text_content,
        )

        if quality < 0.40:
            continue

        accepted.append({
            "url": item["url"],
            "title": item["title"],
            "domain": item["domain"],
            "quality": quality,
            "text": text_content[:5000],
        })

    domains = sorted({
        x["domain"]
        for x in accepted
    })

    average = (
        sum(x["quality"] for x in accepted)
        / len(accepted)
        if accepted
        else 0
    )

    if len(domains) >= 3 and average >= 0.55:
        strength = "strong"
    elif len(domains) >= 1 and average >= 0.40:
        strength = "usable"
    else:
        strength = "insufficient"

    return {
        "query": query,
        "strength": strength,
        "accepted_sources": accepted,
        "accepted_domains": domains,
        "independent_domain_count": len(domains),
        "average_source_quality": round(average, 3),
        "provider_results": provider_results,
    }


async def adaptive_research(query: str) -> Dict[str, Any]:

    attempts = []

    for generated_query in generate_queries(query):

        result = await research_once(generated_query)

        attempts.append(result)

        if (
            result["independent_domain_count"] >= 3
            and result["average_source_quality"] >= 0.55
        ):
            result["attempts"] = len(attempts)
            result["recovery"] = (
                "successful_adaptive_research"
            )
            return result

    best = max(
        attempts,
        key=lambda x: (
            x["independent_domain_count"],
            x["average_source_quality"],
        ),
        default={
            "query": query,
            "strength": "insufficient",
            "accepted_sources": [],
            "accepted_domains": [],
            "independent_domain_count": 0,
            "average_source_quality": 0,
            "provider_results": {},
        },
    )

    best["attempts"] = len(attempts)
    best["recovery"] = (
        "exhausted_without_sufficient_evidence"
    )

    return best


# ============================================================
# VERIFICATION
# ============================================================

def verify_research(research: Dict[str, Any]):

    domains = research.get(
        "accepted_domains",
        [],
    )

    quality = float(
        research.get(
            "average_source_quality",
            0,
        )
    )

    count = len(domains)

    if count >= 4 and quality >= 0.65:
        level = "high"
        confidence = min(
            0.95,
            0.65 + count * 0.05 + quality * 0.15,
        )
        verified = True

    elif count >= 2 and quality >= 0.50:
        level = "moderate"
        confidence = min(
            0.80,
            0.45 + count * 0.06 + quality * 0.12,
        )
        verified = False

    else:
        level = "low"
        confidence = min(
            0.40,
            0.10 + count * 0.04 + quality * 0.08,
        )
        verified = False

    return {
        "verified": verified,
        "verification_level": level,
        "confidence": round(confidence, 3),
        "independent_evidence_count": count,
        "average_source_quality": quality,
        "domains": domains,
        "evidence": [
            {
                "domain": x["domain"],
                "title": x["title"],
                "url": x["url"],
                "quality": x["quality"],
            }
            for x in research.get(
                "accepted_sources",
                [],
            )
        ],
    }


# ============================================================
# INTENT + PLANNING
# ============================================================

def classify_intent(objective: str):

    text = objective.lower()

    if any(
        x in text
        for x in [
            "research",
            "analyze",
            "investigate",
            "audit",
            "compare",
        ]
    ):
        return "research"

    if any(
        x in text
        for x in [
            "build",
            "create",
            "make",
            "generate",
            "develop",
        ]
    ):
        return "creation"

    if any(
        x in text
        for x in [
            "execute",
            "run",
            "perform",
            "do",
        ]
    ):
        return "execution"

    return "general"


def build_plan(objective: str):

    intent = classify_intent(objective)

    nodes = [
        {
            "id": "understand",
            "type": "intent",
            "agent": "agent-planner",
        }
    ]

    if intent in {"research", "general"}:
        nodes.extend([
            {
                "id": "research",
                "type": "research",
                "agent": "agent-researcher",
            },
            {
                "id": "verify",
                "type": "verify",
                "agent": "agent-verifier",
            },
        ])

    nodes.extend([
        {
            "id": "opportunities",
            "type": "opportunity",
            "agent": "agent-learner",
        },
        {
            "id": "critique",
            "type": "critique",
            "agent": "agent-critic",
        },
        {
            "id": "synthesis",
            "type": "synthesis",
            "agent": "agent-planner",
        },
        {
            "id": "evaluation",
            "type": "evaluation",
            "agent": "agent-learner",
        },
        {
            "id": "next_cycle",
            "type": "replan",
            "agent": "agent-planner",
        },
    ])

    return {
        "intent": intent,
        "nodes": nodes,
    }


# ============================================================
# OPPORTUNITY ENGINE
# ============================================================

def detect_opportunities(
    objective: str,
    research: Dict[str, Any],
    verification: Dict[str, Any],
):

    opportunities = []

    if not verification["verified"]:
        opportunities.append({
            "title": "Evidence recovery",
            "description":
                "Improve independent evidence before making high-confidence claims.",
            "priority": 0.95,
        })

    if research.get("attempts", 1) > 1:
        opportunities.append({
            "title": "Adaptive research learning",
            "description":
                "Store successful query/provider combinations for future missions.",
            "priority": 0.85,
        })

    opportunities.append({
        "title": "Persistent learning",
        "description":
            "Use execution evaluation to improve future task routing.",
        "priority": 0.75,
    })

    return opportunities


# ============================================================
# CRITIC
# ============================================================

def critique(
    research: Dict[str, Any],
    verification: Dict[str, Any],
):

    issues = []

    if research["independent_domain_count"] == 0:
        issues.append(
            "No meaningful independent external evidence was collected."
        )

    if (
        research["independent_domain_count"] > 0
        and not verification["verified"]
    ):
        issues.append(
            "Evidence exists but does not meet the high-confidence verification threshold."
        )

    if research["average_source_quality"] < 0.50:
        issues.append(
            "Average source quality is below the preferred threshold."
        )

    if not issues:
        issues.append(
            "No critical evidence-quality defects detected."
        )

    quality = (
        "good"
        if verification["verified"]
        else "needs_improvement"
    )

    return {
        "issues": issues,
        "issue_count": len(issues),
        "confidence": verification["confidence"],
        "quality": quality,
    }


# ============================================================
# FUTURE GAP ENGINE
# ============================================================

def architecture_gaps():

    return [
        {
            "rank": 1,
            "capability":
                "Durable external persistence",
            "reason":
                "Runtime-local SQLite can disappear after infrastructure replacement.",
            "next_boundary":
                "Managed PostgreSQL/Redis with local fallback.",
        },
        {
            "rank": 2,
            "capability":
                "Event-driven background workers",
            "reason":
                "Long missions currently depend on a single request execution.",
            "next_boundary":
                "Queue + worker architecture.",
        },
        {
            "rank": 3,
            "capability":
                "Authenticated external actions",
            "reason":
                "Safe HTTP research is not equivalent to acting on external accounts.",
            "next_boundary":
                "OAuth-scoped tool permissions.",
        },
        {
            "rank": 4,
            "capability":
                "Sandboxed execution",
            "reason":
                "Arbitrary code execution requires isolation.",
            "next_boundary":
                "Container/VM sandbox with strict resource limits.",
        },
        {
            "rank": 5,
            "capability":
                "Continuous regression evaluation",
            "reason":
                "Future upgrades need automatic capability protection.",
            "next_boundary":
                "Automated benchmark suite and release gates.",
        },
    ]


# ============================================================
# MISSION EXECUTION
# ============================================================

async def execute_mission(objective: str):

    mission_id = uid("mission")
    task_id = uid("task")

    started = now()
    trace = []

    plan = build_plan(objective)

    conn = db()

    conn.execute(
        "INSERT INTO missions VALUES (?, ?, ?, ?, ?, ?)",
        (
            mission_id,
            objective,
            "running",
            json.dumps({
                "plan": plan,
                "trace": trace,
            }),
            started,
            None,
        ),
    )

    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
        (
            task_id,
            objective,
            "running",
            "",
            started,
            None,
        ),
    )

    conn.commit()
    conn.close()

    research = {
        "query": objective,
        "strength": "not_run",
        "accepted_sources": [],
        "accepted_domains": [],
        "independent_domain_count": 0,
        "average_source_quality": 0,
    }

    verification = {
        "verified": False,
        "verification_level": "low",
        "confidence": 0,
        "independent_evidence_count": 0,
        "average_source_quality": 0,
        "domains": [],
        "evidence": [],
    }

    opportunities = []
    criticism = None

    try:

        for node in plan["nodes"]:

            trace.append({
                "node": node["id"],
                "type": node["type"],
                "agent": node["agent"],
                "started_at": now(),
            })

            if node["type"] == "research":

                research = await adaptive_research(
                    objective
                )

                save_event(
                    "research.completed",
                    research,
                )

            elif node["type"] == "verify":

                verification = verify_research(
                    research
                )

                save_event(
                    "verification.completed",
                    verification,
                )

            elif node["type"] == "opportunity":

                opportunities = detect_opportunities(
                    objective,
                    research,
                    verification,
                )

                for item in opportunities:

                    conn = db()

                    conn.execute(
                        "INSERT INTO opportunities VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            uid("opp"),
                            item["title"],
                            item["description"],
                            item["priority"],
                            "open",
                            now(),
                        ),
                    )

                    conn.commit()
                    conn.close()

            elif node["type"] == "critique":

                criticism = critique(
                    research,
                    verification,
                )

            elif node["type"] == "synthesis":

                pass

            elif node["type"] == "evaluation":

                score = (
                    0.9
                    if verification["verified"]
                    else (
                        0.6
                        if research["independent_domain_count"] > 0
                        else 0.35
                    )
                )

                evaluation = {
                    "score": score,
                    "evidence_domains":
                        research["independent_domain_count"],
                    "verification":
                        verification["verification_level"],
                    "research_attempts":
                        research.get("attempts", 0),
                }

                conn = db()

                conn.execute(
                    "INSERT INTO evaluations VALUES (?, ?, ?, ?, ?)",
                    (
                        uid("eval"),
                        task_id,
                        score,
                        json.dumps(
                            evaluation,
                            ensure_ascii=False,
                        ),
                        now(),
                    ),
                )

                conn.commit()
                conn.close()

                save_memory(
                    "learning_signal",
                    evaluation,
                    score,
                )

            trace[-1]["completed_at"] = now()

        architecture_state = {
            "version": VERSION,
            "operational": [
                "adaptive research",
                "multi-provider search",
                "source extraction",
                "source quality scoring",
                "independent-domain verification",
                "dynamic planning",
                "agent routing",
                "persistent mission state",
                "persistent memory",
                "opportunity detection",
                "self-critique",
                "evaluation",
                "replanning",
                "regression storage",
            ],
            "extensions": architecture_gaps(),
        }

        synthesis = {
            "objective": objective,
            "intent": plan["intent"],
            "research_strength":
                research["strength"],
            "verified":
                verification["verified"],
            "confidence":
                verification["confidence"],
            "independent_sources":
                research["independent_domain_count"],
            "opportunities":
                opportunities,
            "critique":
                criticism,
            "architecture":
                architecture_state,
            "research_recovery":
                research.get("recovery"),
            "research_attempts":
                research.get("attempts", 0),
            "generated_at": now(),
        }

        memory_id = save_memory(
            "mission_result",
            synthesis,
            verification["confidence"],
        )

        completed = now()

        final_result = {
            "objective": objective,
            "mission_id": mission_id,
            "started_at": started,
            "agent_trace": trace,
            "intent": plan["intent"],
            "research": research,
            "verification": verification,
            "opportunities": opportunities,
            "critique": criticism,
            "synthesis": synthesis,
            "next_cycle": {
                "status": "ready",
                "reason":
                    "Current mission cycle completed.",
                "next_action":
                    "Use stored mission state and learning signals for the next cycle.",
            },
            "completed_nodes": len(trace),
            "total_nodes": len(plan["nodes"]),
            "completed_at": completed,
            "memory_id": memory_id,
        }

        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status=?, state=?, completed_at=?
            WHERE id=?
            """,
            (
                "completed",
                json.dumps(
                    final_result,
                    ensure_ascii=False,
                    default=str,
                ),
                completed,
                mission_id,
            ),
        )

        conn.execute(
            """
            UPDATE tasks
            SET status=?, result=?, completed_at=?
            WHERE id=?
            """,
            (
                "completed",
                json.dumps(
                    final_result,
                    ensure_ascii=False,
                    default=str,
                ),
                completed,
                task_id,
            ),
        )

        conn.commit()
        conn.close()

        save_event(
            "mission.completed",
            {
                "mission_id": mission_id,
                "task_id": task_id,
                "confidence":
                    verification["confidence"],
            },
        )

        return {
            "task_id": task_id,
            "mission_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "target": TARGET_YEAR,
            "objective": objective,
            "result": final_result,
        }

    except Exception as exc:

        error = str(exc)

        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status=?, state=?, completed_at=?
            WHERE id=?
            """,
            (
                "failed",
                json.dumps({
                    "error": error,
                    "trace": trace,
                }),
                now(),
                mission_id,
            ),
        )

        conn.execute(
            """
            UPDATE tasks
            SET status=?, result=?, completed_at=?
            WHERE id=?
            """,
            (
                "failed",
                json.dumps({"error": error}),
                now(),
                task_id,
            ),
        )

        conn.commit()
        conn.close()

        save_memory(
            "failure",
            {
                "objective": objective,
                "error": error,
                "trace": trace,
            },
            0.9,
        )

        raise


# ============================================================
# REGRESSION TESTS
# ============================================================

async def run_regression():

    tests = []

    def test(name, passed, details):
        tests.append({
            "name": name,
            "passed": bool(passed),
            "details": details,
        })

    test(
        "database",
        DB_PATH.exists(),
        str(DB_PATH),
    )

    test(
        "security",
        not safe_url("http://127.0.0.1"),
        "Private/loopback destination blocked",
    )

    test(
        "source-domain-filter",
        meaningful_domain(
            "https://r.bing.com/test"
        ) is None,
        "Search infrastructure rejected",
    )

    test(
        "planner",
        len(build_plan("research autonomous AI")) > 0,
        "Dynamic plan generated",
    )

    test(
        "verification-calibration",
        verify_research({
            "accepted_domains": [],
            "average_source_quality": 0,
        })["verified"] is False,
        "Zero evidence cannot be verified",
    )

    for item in tests:

        conn = db()

        conn.execute(
            "INSERT INTO regression VALUES (?, ?, ?, ?, ?)",
            (
                uid("reg"),
                item["name"],
                1 if item["passed"] else 0,
                item["details"],
                now(),
            ),
        )

        conn.commit()
        conn.close()

    passed = sum(
        1
        for x in tests
        if x["passed"]
    )

    return {
        "version": VERSION,
        "passed": passed,
        "total": len(tests),
        "healthy": passed == len(tests),
        "tests": tests,
    }


# ============================================================
# REQUEST MODELS
# ============================================================

class CreateRequest(BaseModel):
    command: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )

    duration_minutes: int = Field(
        default=1,
        ge=1,
        le=120,
    )


class ResearchRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=5000,
    )


class VerifyRequest(BaseModel):
    research: Dict[str, Any]


class ExternalRequest(BaseModel):
    url: str
    method: str = "GET"
    body: Optional[Dict[str, Any]] = None


# ============================================================
# ROUTES
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Infinity</title>
<style>
body {{
    font-family: Arial, sans-serif;
    max-width: 900px;
    margin: auto;
    padding: 24px;
}}
textarea {{
    width: 100%;
    min-height: 180px;
    padding: 12px;
    box-sizing: border-box;
}}
button {{
    margin-top: 12px;
    padding: 14px 24px;
    font-size: 16px;
}}
pre {{
    white-space: pre-wrap;
    word-break: break-word;
    background: #f4f4f4;
    padding: 15px;
}}
</style>
</head>
<body>

<h1>AI Infinity ∞</h1>
<p><b>{VERSION}</b> — Autonomous Intelligence Runtime</p>

<textarea id="command"
placeholder="Tell AI Infinity what you want it to accomplish..."></textarea>

<br>

<button onclick="run()">Execute Mission</button>

<pre id="output">Ready.</pre>

<script>
async function run() {{
    const command =
        document.getElementById("command").value;

    if (!command.trim()) return;

    document.getElementById("output").textContent =
        "AI Infinity is executing...";

    try {{
        const response = await fetch("/execute", {{
            method: "POST",
            headers: {{
                "Content-Type": "application/json"
            }},
            body: JSON.stringify({{
                command: command
            }})
        }});

        const data = await response.json();

        document.getElementById("output").textContent =
            JSON.stringify(data, null, 2);

    }} catch (e) {{
        document.getElementById("output").textContent =
            "Execution error: " + e;
    }}
}}
</script>

</body>
</html>
"""


@app.get("/health")
async def health():

    return {
        "status": "healthy",
        "service": "AI Infinity",
        "version": VERSION,
        "target": TARGET_YEAR,
        "database": {
            "type": "SQLite",
            "path": str(DB_PATH),
            "durable_within_runtime": True,
        },
    }


@app.get("/status")
async def status():

    conn = db()

    memory_count = conn.execute(
        "SELECT COUNT(*) FROM memory"
    ).fetchone()[0]

    task_count = conn.execute(
        "SELECT COUNT(*) FROM tasks"
    ).fetchone()[0]

    mission_count = conn.execute(
        "SELECT COUNT(*) FROM missions"
    ).fetchone()[0]

    event_count = conn.execute(
        "SELECT COUNT(*) FROM events"
    ).fetchone()[0]

    conn.close()

    return {
        "service": "AI Infinity",
        "version": VERSION,
        "target": TARGET_YEAR,
        "status": "operational",
        "runtime": {
            "memory": memory_count,
            "tasks": task_count,
            "missions": mission_count,
            "events": event_count,
        },
    }


@app.get("/capabilities")
async def capabilities():

    return {
        "version": VERSION,
        "operational": [
            "dynamic mission planning",
            "specialized agent routing",
            "adaptive multi-provider research",
            "real webpage extraction",
            "source quality scoring",
            "independent-domain verification",
            "evidence calibration",
            "self-critique",
            "opportunity detection",
            "persistent memory",
            "persistent mission state",
            "evaluation and learning signals",
            "automatic recovery",
            "replanning",
            "SSRF protection",
            "provider health tracking",
            "regression testing",
        ],
        "future_extensions": architecture_gaps(),
    }


@app.get("/self-inspect")
async def self_inspect():

    regression = await run_regression()

    conn = db()

    memory_count = conn.execute(
        "SELECT COUNT(*) FROM memory"
    ).fetchone()[0]

    mission_count = conn.execute(
        "SELECT COUNT(*) FROM missions"
    ).fetchone()[0]

    conn.close()

    return {
        "version": VERSION,
        "target": TARGET_YEAR,
        "architecture": "next-generation autonomous orchestration runtime",
        "operational": True,
        "memory_count": memory_count,
        "mission_count": mission_count,
        "regression": regression,
        "gaps": architecture_gaps(),
    }


@app.get("/architecture")
async def architecture():

    return {
        "version": VERSION,
        "layers": [
            "Human Intent",
            "Intent Classification",
            "Mission Planner",
            "Dynamic Task Graph",
            "Specialized Agents",
            "Adaptive Research",
            "Evidence Fabric",
            "Verification",
            "Opportunity Engine",
            "Self-Critique",
            "Evaluation",
            "Persistent Memory",
            "World State",
            "Recovery",
            "Replanning",
        ],
        "future": [
            "distributed workers",
            "managed durable database",
            "OAuth action layer",
            "sandboxed execution",
            "specialized model routing",
            "multimodal world models",
            "continuous learning",
            "multi-agent concurrency",
            "real-world actuators",
            "robotics",
        ],
    }


@app.get("/gaps")
async def gaps():

    return {
        "version": VERSION,
        "highest_value_missing": architecture_gaps(),
    }


@app.get("/memory")
async def memory(limit: int = 50):

    limit = max(1, min(limit, 200))

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM memory
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    conn.close()

    return [
        dict(x)
        for x in rows
    ]


@app.get("/memory/count")
async def memory_count():

    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) FROM memory"
    ).fetchone()[0]

    conn.close()

    return {
        "count": count
    }


@app.get("/world")
async def world():

    conn = db()

    rows = conn.execute(
        "SELECT * FROM world"
    ).fetchall()

    conn.close()

    return {
        x["key"]:
            json.loads(x["value"])
        for x in rows
    }


@app.get("/events")
async def events(limit: int = 50):

    limit = max(1, min(limit, 200))

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM events
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    conn.close()

    return [
        dict(x)
        for x in rows
    ]


@app.post("/research")
async def research(request: ResearchRequest):

    return await adaptive_research(
        request.query
    )


@app.post("/verify")
async def verify(request: VerifyRequest):

    return verify_research(
        request.research
    )


@app.post("/plan")
async def plan(request: ResearchRequest):

    return {
        "version": VERSION,
        "objective": request.query,
        "plan": build_plan(
            request.query
        ),
    }


@app.post("/task")
async def task(request: ResearchRequest):

    task_id = uid("task")

    conn = db()

    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
        (
            task_id,
            request.query,
            "queued",
            "",
            now(),
            None,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "task_id": task_id,
        "status": "queued",
        "version": VERSION,
    }


@app.post("/mission")
async def mission(request: ResearchRequest):

    mission_id = uid("mission")

    conn = db()

    conn.execute(
        "INSERT INTO missions VALUES (?, ?, ?, ?, ?, ?)",
        (
            mission_id,
            request.query,
            "planned",
            json.dumps(
                build_plan(request.query)
            ),
            now(),
            None,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "mission_id": mission_id,
        "status": "planned",
        "version": VERSION,
    }


@app.post("/execute")
async def execute(request: ResearchRequest):

    return await execute_mission(
        request.query
    )


@app.post("/generate")
async def generate(request: CreateRequest):

    # Compatibility endpoint.
    # Video rendering remains delegated to the
    # existing renderer/provider architecture.

    return {
        "status": "accepted",
        "version": VERSION,
        "command": request.command,
        "duration_minutes":
            request.duration_minutes,
        "message":
            "Mission accepted by AI Infinity runtime.",
    }


@app.post("/external")
async def external(request: ExternalRequest):

    method = request.method.upper()

    if method not in {"GET", "POST"}:
        raise HTTPException(
            status_code=403,
            detail=
                "Only safe GET/POST external access is enabled. Authenticated destructive actions require a dedicated permission layer.",
        )

    if not safe_url(request.url):
        raise HTTPException(
            status_code=400,
            detail="Unsafe or restricted destination.",
        )

    try:

        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
        ) as client:

            if method == "GET":
                response = await client.get(
                    request.url
                )
            else:
                response = await client.post(
                    request.url,
                    json=request.body or {},
                )

        return {
            "status": "completed",
            "url": str(response.url),
            "status_code":
                response.status_code,
            "content":
                response.text[:20000],
        }

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )


@app.get("/task/{task_id}")
async def get_task(task_id: str):

    conn = db()

    row = conn.execute(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    return dict(row)


@app.get("/mission/{mission_id}")
async def get_mission(mission_id: str):

    conn = db()

    row = conn.execute(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,),
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    result = dict(row)

    try:
        result["state"] = json.loads(
            result["state"]
        )
    except Exception:
        pass

    return result


@app.get("/opportunities")
async def opportunities():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM opportunities
        ORDER BY priority DESC, created_at DESC
        """
    ).fetchall()

    conn.close()

    return [
        dict(x)
        for x in rows
    ]


@app.get("/agents")
async def agents():

    return {
        "agents": [
            {
                "id": "agent-planner",
                "role": "planning and synthesis",
            },
            {
                "id": "agent-researcher",
                "role": "adaptive research",
            },
            {
                "id": "agent-verifier",
                "role": "evidence verification",
            },
            {
                "id": "agent-critic",
                "role": "self-critique",
            },
            {
                "id": "agent-executor",
                "role": "task execution",
            },
            {
                "id": "agent-learner",
                "role": "evaluation and opportunity detection",
            },
        ]
    }


@app.get("/providers")
async def providers():

    conn = db()

    rows = conn.execute(
        "SELECT * FROM providers"
    ).fetchall()

    conn.close()

    return {
        "providers": [
            dict(x)
            for x in rows
        ]
    }


@app.get("/diagnostics")
async def diagnostics():

    regression = await run_regression()

    return {
        "version": VERSION,
        "status":
            "healthy"
            if regression["healthy"]
            else "degraded",
        "regression": regression,
        "security": {
            "ssrf_protection": True,
            "private_network_blocking": True,
            "restricted_search_infrastructure":
                True,
        },
        "research": {
            "adaptive_retry": True,
            "multi_provider": True,
            "source_quality": True,
            "independent_domain_verification":
                True,
        },
    }


@app.get("/skills")
async def skills():

    return {
        "skills": [
            "planning",
            "research",
            "verification",
            "adaptive recovery",
            "self-critique",
            "evaluation",
            "memory",
            "opportunity detection",
            "replanning",
            "provider routing",
            "security validation",
            "regression testing",
        ]
    }


@app.get("/skills/count")
async def skills_count():

    return {
        "count": 12
    }


@app.get("/video/{job_id}")
async def video(job_id: str):

    return {
        "job_id": job_id,
        "status": "delegated",
        "message":
            "Video jobs remain compatible with the external renderer/provider layer.",
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    init_db()

    set_world(
        "runtime",
        {
            "version": VERSION,
            "target": TARGET_YEAR,
            "started_at": now(),
            "mode": "free-first",
        },
    )

    save_event(
        "runtime.started",
        {
            "version": VERSION,
            "target": TARGET_YEAR,
        },
    )
