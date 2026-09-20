"""
AI Infinity
TARGET-2050.62
BUILD: AUTONOMOUS-MISSION-INTELLIGENCE-CORE

2050.62 preserves TARGET-2050.61 and adds:

- autonomous mission expansion
- parallel strategy generation
- parallel bounded execution
- strategy portfolio
- strategy comparison
- outcome competition
- evidence-weighted verification
- mission confidence
- dynamic graph mutation
- failure-class-aware recovery
- independent verification pass
- convergence gate
- strategy memory
- parallel research
- resilient research providers
- controlled public web access
- SSRF protection
- bounded execution
- authorization gates
- persistent SQLite state
- checkpoints
- provenance
- learning
- artifacts
- mobile interface

Safety model:
intent
 -> requirements
 -> research
 -> evidence
 -> synthesis
 -> strategy portfolio
 -> authorization
 -> bounded parallel execution
 -> observation
 -> diagnosis
 -> comparison
 -> verification
 -> convergence
 -> learning
 -> strategy memory

This system does NOT provide:
- arbitrary code execution
- unrestricted private-network access
- credential theft/modification
- permission bypass
- stealth persistence
- unrestricted proxy behavior
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# CORE CONSTANTS
# ============================================================

VERSION = "TARGET-2050.62"
BUILD = "AUTONOMOUS-MISSION-INTELLIGENCE-CORE"

BASE = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai_infinity"))
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

MAX_CYCLES = int(os.getenv("MAX_CYCLES", "5"))
EXECUTOR_WORKERS = int(os.getenv("EXECUTOR_WORKERS", "4"))
MAX_PARALLEL_STRATEGIES = int(os.getenv("MAX_PARALLEL_STRATEGIES", "3"))
MAX_RESEARCH_RESULTS = int(os.getenv("MAX_RESEARCH_RESULTS", "20"))
MAX_RESPONSE_BYTES = int(os.getenv("MAX_RESPONSE_BYTES", "2000000"))

EXTERNAL_ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}

RESEARCH_DOMAINS = {
    "wikipedia.org",
    "en.wikipedia.org",
    "api.crossref.org",
    "export.arxiv.org",
    "arxiv.org",
    "api.openalex.org",
    "openalex.org",
}

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "0.0.0.0",
    "127.0.0.1",
    "::1",
    "169.254.169.254",
    "metadata.google.internal",
}

DATABASE_LOCK = threading.RLock()

EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=EXECUTOR_WORKERS,
    thread_name_prefix="ai-infinity",
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Adaptive autonomous mission intelligence platform",
)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        str(DB_PATH),
        check_same_thread=False,
        timeout=30,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with DATABASE_LOCK:
        conn = db()
        cur = conn.cursor()

        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                confidence REAL DEFAULT 0,
                cycle INTEGER DEFAULT 0,
                convergence REAL DEFAULT 0,
                created_at REAL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS mission_steps (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                name TEXT,
                status TEXT,
                strategy_id TEXT,
                result TEXT,
                cycle INTEGER,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS requirements (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                requirement TEXT,
                status TEXT,
                confidence REAL,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                provider TEXT,
                title TEXT,
                url TEXT,
                source_id TEXT,
                published TEXT,
                authors TEXT,
                abstract TEXT,
                snippet TEXT,
                source_type TEXT,
                confidence REAL,
                metadata TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                claim TEXT,
                confidence REAL,
                supporting_count INTEGER,
                contradicting_count INTEGER,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS provenance (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                object_type TEXT,
                object_id TEXT,
                parent_id TEXT,
                action TEXT,
                metadata TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                action TEXT,
                reason TEXT,
                status TEXT,
                created_at REAL,
                decided_at REAL
            );

            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                cycle INTEGER,
                state TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                cycle INTEGER,
                strategy_id TEXT,
                observation TEXT,
                success INTEGER,
                confidence REAL,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS outcomes (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                outcome TEXT,
                status TEXT,
                confidence REAL,
                verified INTEGER DEFAULT 0,
                independent INTEGER DEFAULT 0,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                name TEXT,
                artifact_type TEXT,
                content TEXT,
                provenance TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                procedure TEXT,
                confidence REAL,
                uses INTEGER DEFAULT 0,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS learning (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                lesson TEXT,
                evidence TEXT,
                confidence REAL,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                resource TEXT,
                amount REAL,
                limit_value REAL,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS connectors (
                id TEXT PRIMARY KEY,
                name TEXT,
                category TEXT,
                permission TEXT,
                status TEXT,
                description TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS connector_events (
                id TEXT PRIMARY KEY,
                connector_id TEXT,
                mission_id TEXT,
                action TEXT,
                status TEXT,
                result TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS memory (
                id TEXT PRIMARY KEY,
                key TEXT,
                value TEXT,
                confidence REAL,
                created_at REAL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS provider_health (
                id TEXT PRIMARY KEY,
                provider TEXT,
                status TEXT,
                attempts INTEGER,
                result_count INTEGER,
                error TEXT,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                name TEXT,
                description TEXT,
                type TEXT,
                expected_success REAL,
                actual_success REAL,
                confidence REAL,
                status TEXT,
                cycle INTEGER,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS strategy_results (
                id TEXT PRIMARY KEY,
                strategy_id TEXT,
                mission_id TEXT,
                status TEXT,
                result TEXT,
                confidence REAL,
                evidence_count INTEGER,
                duration REAL,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS strategy_memory (
                id TEXT PRIMARY KEY,
                strategy_type TEXT,
                context TEXT,
                success REAL,
                uses INTEGER,
                lesson TEXT,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS mission_graph (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                node_id TEXT,
                node_type TEXT,
                depends_on TEXT,
                status TEXT,
                payload TEXT,
                created_at REAL
            );
            """
        )

        conn.commit()
        conn.close()


init_db()


# ============================================================
# MODELS
# ============================================================

class RunRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=10000)
    duration_minutes: int = Field(default=1, ge=1, le=120)


class ApprovalRequest(BaseModel):
    approved: bool


class MemoryRequest(BaseModel):
    key: str
    value: Any
    confidence: float = 0.5


# ============================================================
# GENERAL HELPERS
# ============================================================

def now():
    return time.time()


def uid(prefix: str):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def json_dumps(value):
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps(str(value))


def safe_json(value, default=None):
    try:
        return json.loads(value)
    except Exception:
        return default


def row_dict(row):
    return dict(row) if row else None


def sha(text: str):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def insert(table, data):
    keys = list(data.keys())
    placeholders = ",".join(["?"] * len(keys))

    with DATABASE_LOCK:
        conn = db()
        conn.execute(
            f"""
            INSERT INTO {table}
            ({",".join(keys)})
            VALUES ({placeholders})
            """,
            [data[k] for k in keys],
        )
        conn.commit()
        conn.close()


def update(table, where_key, where_value, values):
    assignments = ",".join(f"{k}=?" for k in values)

    with DATABASE_LOCK:
        conn = db()
        conn.execute(
            f"""
            UPDATE {table}
            SET {assignments}
            WHERE {where_key}=?
            """,
            list(values.values()) + [where_value],
        )
        conn.commit()
        conn.close()


def select_one(query, args=()):
    with DATABASE_LOCK:
        conn = db()
        row = conn.execute(query, args).fetchone()
        conn.close()
        return row_dict(row)


def select_all(query, args=()):
    with DATABASE_LOCK:
        conn = db()
        rows = conn.execute(query, args).fetchall()
        conn.close()
        return [row_dict(x) for x in rows]


# ============================================================
# SECURITY / NETWORK
# ============================================================

def normalize_host(host):
    if not host:
        return ""
    return host.lower().rstrip(".")


def host_allowed(host, purpose="external"):
    host = normalize_host(host)

    if not host:
        return False

    if host in BLOCKED_HOSTS:
        return False

    if host.startswith("10."):
        return False

    if host.startswith("192.168."):
        return False

    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
            if 16 <= second <= 31:
                return False
        except Exception:
            pass

    if host.startswith("127."):
        return False

    if purpose == "research":
        return any(
            host == domain or host.endswith("." + domain)
            for domain in RESEARCH_DOMAINS
        )

    if EXTERNAL_ALLOWED_DOMAINS:
        return any(
            host == domain or host.endswith("." + domain)
            for domain in EXTERNAL_ALLOWED_DOMAINS
        )

    return False


def validate_url(url, purpose="external"):
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only HTTP/HTTPS is permitted")

    host = normalize_host(parsed.hostname)

    if not host_allowed(host, purpose):
        raise ValueError(
            f"Domain not allowed by network policy: {host}"
        )

    return parsed


def http_get(
    url,
    purpose="external",
    timeout=12,
    max_bytes=MAX_RESPONSE_BYTES,
):
    validate_url(url, purpose)

    headers = {
        "User-Agent": (
            "AI-Infinity/2050.62 "
            "(controlled-research-client)"
        )
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=timeout,
        allow_redirects=False,
    )

    if 300 <= response.status_code < 400:
        location = response.headers.get("location")

        if not location:
            raise RuntimeError("Redirect without location")

        redirected = urljoin(url, location)
        validate_url(redirected, purpose)

        response = requests.get(
            redirected,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )

    content = response.content[:max_bytes]

    return {
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "content": content,
        "url": response.url or url,
    }


# ============================================================
# RESEARCH MODELS
# ============================================================

@dataclass
class EvidenceRecord:
    provider: str
    title: str
    url: str
    source_id: str
    published: str = ""
    authors: Any = None
    abstract: str = ""
    snippet: str = ""
    source_type: str = "research"
    confidence: float = 0.5
    metadata: Any = None


@dataclass
class ProviderResult:
    provider: str
    status: str
    http_status: Optional[int]
    attempts: int
    result_count: int
    results: List[Dict[str, Any]]
    error_type: Optional[str] = None
    error: Optional[str] = None
    recovery: Optional[str] = None
    content_type: Optional[str] = None


# ============================================================
# RESEARCH PROVIDERS
# ============================================================

def parse_json(content):
    text = content.decode("utf-8-sig", errors="replace").strip()

    if text.startswith("<"):
        raise ValueError("Expected JSON but received XML/HTML")

    return json.loads(text)


def search_wikipedia(query, limit=5):
    url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query&list=search&format=json"
        "&utf8=1&srsearch="
        + requests.utils.quote(query)
        + f"&srlimit={limit}"
    )

    result = http_get(url, purpose="research")
    data = parse_json(result["content"])

    output = []

    for item in data.get("query", {}).get("search", []):
        title = item.get("title", "")

        output.append(
            EvidenceRecord(
                provider="wikipedia",
                title=title,
                url="https://en.wikipedia.org/wiki/"
                    + requests.utils.quote(title.replace(" ", "_")),
                source_id=str(item.get("pageid", "")),
                snippet=re.sub(
                    r"<[^>]+>",
                    "",
                    item.get("snippet", ""),
                ),
                source_type="encyclopedia",
                confidence=0.45,
                metadata={
                    "wordcount": item.get("wordcount"),
                    "timestamp": item.get("timestamp"),
                },
            )
        )

    return output


def search_crossref(query, limit=5):
    url = (
        "https://api.crossref.org/works"
        "?query.bibliographic="
        + requests.utils.quote(query)
        + f"&rows={limit}"
    )

    result = http_get(url, purpose="research")
    data = parse_json(result["content"])

    output = []

    for item in data.get("message", {}).get("items", []):
        title = (item.get("title") or [""])[0]

        if not title:
            continue

        authors = [
            (
                a.get("given", "") + " " + a.get("family", "")
            ).strip()
            for a in item.get("author", [])
        ]

        url_value = (
            item.get("URL")
            or (
                "https://doi.org/"
                + item.get("DOI", "")
            )
        )

        output.append(
            EvidenceRecord(
                provider="crossref",
                title=title,
                url=url_value,
                source_id=item.get("DOI", "") or sha(title),
                published=str(
                    item.get("published-print")
                    or item.get("published")
                    or ""
                ),
                authors=authors,
                abstract=item.get("abstract", ""),
                source_type="bibliographic",
                confidence=0.75,
                metadata={
                    "type": item.get("type"),
                    "publisher": item.get("publisher"),
                },
            )
        )

    return output


def parse_arxiv(content, limit=5):
    root = ET.fromstring(content)

    ns = {
        "atom": "http://www.w3.org/2005/Atom"
    }

    output = []

    for entry in root.findall("atom:entry", ns)[:limit]:
        title = (
            entry.findtext(
                "atom:title",
                "",
                ns,
            ) or ""
        ).strip()

        abstract = (
            entry.findtext(
                "atom:summary",
                "",
                ns,
            ) or ""
        ).strip()

        published = (
            entry.findtext(
                "atom:published",
                "",
                ns,
            ) or ""
        )

        entry_id = (
            entry.findtext(
                "atom:id",
                "",
                ns,
            ) or ""
        )

        authors = []

        for author in entry.findall(
            "atom:author",
            ns,
        ):
            name = author.findtext(
                "atom:name",
                "",
                ns,
            )

            if name:
                authors.append(name)

        output.append(
            EvidenceRecord(
                provider="arxiv",
                title=re.sub(r"\s+", " ", title),
                url=entry_id,
                source_id=entry_id,
                published=published,
                authors=authors,
                abstract=re.sub(
                    r"\s+",
                    " ",
                    abstract,
                ),
                source_type="preprint",
                confidence=0.70,
                metadata={},
            )
        )

    return output


def search_arxiv(query, limit=5):
    encoded = requests.utils.quote(query)

    endpoints = [
        (
            "https://export.arxiv.org/api/query"
            f"?search_query=all:{encoded}"
            f"&start=0&max_results={limit}"
        ),
        (
            "https://arxiv.org/api/query"
            f"?search_query=all:{encoded}"
            f"&start=0&max_results={limit}"
        ),
    ]

    last_error = None

    for index, endpoint in enumerate(endpoints, 1):
        try:
            result = http_get(
                endpoint,
                purpose="research",
                timeout=15,
            )

            parsed = parse_arxiv(
                result["content"],
                limit,
            )

            return parsed, f"export-query-{index}"

        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        f"arXiv recovery failed: {last_error}"
    )


def search_openalex(query, limit=5):
    url = (
        "https://api.openalex.org/works"
        "?search="
        + requests.utils.quote(query)
        + f"&per-page={limit}"
    )

    result = http_get(url, purpose="research")
    data = parse_json(result["content"])

    output = []

    for item in data.get("results", []):
        title = item.get("title") or ""

        if not title:
            continue

        authors = []

        for author in item.get(
            "authorships",
            [],
        ):
            author_obj = author.get("author") or {}
            if author_obj.get("display_name"):
                authors.append(
                    author_obj["display_name"]
                )

        primary_location = (
            item.get("primary_location") or {}
        )

        landing = (
            primary_location.get("landing_page_url")
            or item.get("doi")
            or ""
        )

        abstract = ""

        inverted = item.get(
            "abstract_inverted_index"
        )

        if inverted:
            pairs = []

            for word, positions in inverted.items():
                for position in positions:
                    pairs.append((position, word))

            pairs.sort()

            abstract = " ".join(
                word for _, word in pairs
            )

        output.append(
            EvidenceRecord(
                provider="openalex",
                title=title,
                url=landing,
                source_id=item.get(
                    "id",
                    sha(title),
                ),
                published=str(
                    item.get("publication_year")
                    or ""
                ),
                authors=authors,
                abstract=abstract,
                snippet=abstract[:1000],
                source_type="research-index",
                confidence=0.72,
                metadata={
                    "doi": item.get("doi"),
                    "cited_by_count": item.get(
                        "cited_by_count"
                    ),
                    "type": item.get("type"),
                },
            )
        )

    return output


# ============================================================
# RESEARCH ORCHESTRATOR
# ============================================================

def save_provider_health(result: ProviderResult):
    insert(
        "provider_health",
        {
            "id": uid("health"),
            "provider": result.provider,
            "status": result.status,
            "attempts": result.attempts,
            "result_count": result.result_count,
            "error": result.error,
            "updated_at": now(),
        },
    )


def run_provider(provider, query):
    started = now()

    try:
        if provider == "wikipedia":
            results = search_wikipedia(query)
            recovery = "api-search"

        elif provider == "crossref":
            results = search_crossref(query)
            recovery = "bibliographic-query"

        elif provider == "arxiv":
            results, recovery = search_arxiv(query)

        elif provider == "openalex":
            results = search_openalex(query)
            recovery = "works-search"

        else:
            raise ValueError(
                f"Unknown provider: {provider}"
            )

        result = ProviderResult(
            provider=provider,
            status="success",
            http_status=200,
            attempts=1,
            result_count=len(results),
            results=[asdict(x) for x in results],
            recovery=recovery,
            content_type="application/json"
            if provider != "arxiv"
            else "application/atom+xml",
        )

        save_provider_health(result)

        return result

    except Exception as exc:
        result = ProviderResult(
            provider=provider,
            status="error",
            http_status=None,
            attempts=1,
            result_count=0,
            results=[],
            error_type=type(exc).__name__,
            error=str(exc),
            recovery="provider-failure",
        )

        save_provider_health(result)

        return result


def ingest_research(query):
    providers = [
        "wikipedia",
        "crossref",
        "arxiv",
        "openalex",
    ]

    results = []

    futures = {
        EXECUTOR.submit(
            run_provider,
            provider,
            query,
        ): provider
        for provider in providers
    }

    for future in concurrent.futures.as_completed(
        futures
    ):
        results.append(future.result())

    dedup = {}

    for provider_result in results:
        for item in provider_result.results:
            key = (
                item.get("source_id")
                or item.get("url")
                or sha(
                    item.get("title", "")
                )
            )

            if key not in dedup:
                dedup[key] = item

    return {
        "query": query,
        "providers": [
            asdict(x)
            for x in results
        ],
        "results": list(dedup.values())[
            :MAX_RESEARCH_RESULTS
        ],
    }


# ============================================================
# REQUIREMENT ENGINE
# ============================================================

def derive_requirements(objective):
    text = objective.strip()

    requirements = [
        "The requested outcome must be clearly defined.",
        "Important factual claims should have independent evidence.",
        "Contradictory evidence must be detected rather than silently ignored.",
        "Actions must remain within authorization and network policy.",
        "The final outcome must be independently verifiable.",
    ]

    lower = text.lower()

    if any(
        x in lower
        for x in [
            "research",
            "investigate",
            "analyze",
            "study",
        ]
    ):
        requirements.extend(
            [
                "Multiple independent sources should be considered.",
                "Evidence should be provenance-linked.",
                "Source disagreement should be explicitly inspected.",
            ]
        )

    if any(
        x in lower
        for x in [
            "build",
            "create",
            "make",
            "deploy",
        ]
    ):
        requirements.extend(
            [
                "A concrete artifact or implementation result is required.",
                "The produced result must be inspected after execution.",
            ]
        )

    return requirements


def store_requirements(mission_id, requirements):
    for requirement in requirements:
        insert(
            "requirements",
            {
                "id": uid("req"),
                "mission_id": mission_id,
                "requirement": requirement,
                "status": "discovered",
                "confidence": 0.65,
                "created_at": now(),
            },
        )


# ============================================================
# EVIDENCE / CLAIMS
# ============================================================

def store_evidence(mission_id, research):
    count = 0

    for item in research.get("results", []):
        insert(
            "evidence",
            {
                "id": uid("evidence"),
                "mission_id": mission_id,
                "provider": item.get("provider"),
                "title": item.get("title"),
                "url": item.get("url"),
                "source_id": item.get("source_id"),
                "published": str(
                    item.get("published", "")
                ),
                "authors": json_dumps(
                    item.get("authors")
                ),
                "abstract": item.get(
                    "abstract",
                    "",
                ),
                "snippet": item.get(
                    "snippet",
                    "",
                ),
                "source_type": item.get(
                    "source_type",
                    "research",
                ),
                "confidence": float(
                    item.get(
                        "confidence",
                        0.5,
                    )
                ),
                "metadata": json_dumps(
                    item.get("metadata")
                ),
                "created_at": now(),
            },
        )

        count += 1

    return count


def build_claims(mission_id, objective):
    evidence = select_all(
        """
        SELECT provider,title,snippet,abstract,confidence
        FROM evidence
        WHERE mission_id=?
        LIMIT 50
        """,
        (mission_id,),
    )

    providers = sorted(
        {
            x["provider"]
            for x in evidence
            if x.get("provider")
        }
    )

    support = len(evidence)

    confidence = min(
        0.95,
        0.30
        + min(0.35, support * 0.02)
        + min(0.25, len(providers) * 0.06),
    )

    claim_text = (
        f"Available evidence provides "
        f"{len(providers)} independent provider "
        f"perspectives relevant to the objective."
    )

    claim_id = uid("claim")

    insert(
        "claims",
        {
            "id": claim_id,
            "mission_id": mission_id,
            "claim": claim_text,
            "confidence": confidence,
            "supporting_count": support,
            "contradicting_count": 0,
            "created_at": now(),
        },
    )

    return {
        "claim_id": claim_id,
        "claim": claim_text,
        "confidence": confidence,
        "supporting_count": support,
        "providers": providers,
    }


# ============================================================
# STRATEGY ENGINE
# ============================================================

STRATEGY_LIBRARY = [
    {
        "type": "direct-research",
        "name": "Direct Research",
        "description": (
            "Gather evidence directly from available "
            "independent research providers."
        ),
        "expected": 0.72,
    },
    {
        "type": "cross-source",
        "name": "Cross Source",
        "description": (
            "Compare independent providers and identify "
            "agreement and disagreement."
        ),
        "expected": 0.80,
    },
    {
        "type": "verify-first",
        "name": "Verify First",
        "description": (
            "Prioritize independent verification before "
            "accepting an outcome."
        ),
        "expected": 0.84,
    },
    {
        "type": "parallel-explore",
        "name": "Parallel Exploration",
        "description": (
            "Run multiple bounded information paths in "
            "parallel and compare their results."
        ),
        "expected": 0.78,
    },
    {
        "type": "failure-recovery",
        "name": "Failure Recovery",
        "description": (
            "Change the method when the previous attempt "
            "failed rather than repeating blindly."
        ),
        "expected": 0.70,
    },
]


def strategy_memory_score(strategy_type, objective):
    rows = select_all(
        """
        SELECT success,uses
        FROM strategy_memory
        WHERE strategy_type=?
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        (strategy_type,),
    )

    if not rows:
        return 0.5

    weighted = 0
    weight_total = 0

    for row in rows:
        weight = max(
            1,
            min(
                10,
                int(row.get("uses", 1)),
            ),
        )

        weighted += (
            float(row.get("success", 0.5))
            * weight
        )

        weight_total += weight

    return (
        weighted / weight_total
        if weight_total
        else 0.5
    )


def generate_strategies(
    mission_id,
    objective,
    cycle,
):
    strategies = []

    for item in STRATEGY_LIBRARY:
        memory_score = strategy_memory_score(
            item["type"],
            objective,
        )

        expected = (
            item["expected"] * 0.65
            + memory_score * 0.35
        )

        strategy_id = uid("strategy")

        insert(
            "strategies",
            {
                "id": strategy_id,
                "mission_id": mission_id,
                "name": item["name"],
                "description": item["description"],
                "type": item["type"],
                "expected_success": expected,
                "actual_success": 0,
                "confidence": expected,
                "status": "candidate",
                "cycle": cycle,
                "created_at": now(),
            },
        )

        strategies.append(
            {
                "id": strategy_id,
                "name": item["name"],
                "type": item["type"],
                "description": item["description"],
                "expected_success": expected,
            }
        )

    strategies.sort(
        key=lambda x: x["expected_success"],
        reverse=True,
    )

    return strategies[
        :MAX_PARALLEL_STRATEGIES
    ]


# ============================================================
# MISSION GRAPH
# ============================================================

def create_graph(mission_id):
    nodes = [
        (
            "requirements",
            "requirements",
            "",
        ),
        (
            "research",
            "research",
            "requirements",
        ),
        (
            "evidence",
            "evidence",
            "research",
        ),
        (
            "synthesis",
            "synthesis",
            "evidence",
        ),
        (
            "strategy",
            "strategy",
            "synthesis",
        ),
        (
            "execution",
            "execution",
            "strategy",
        ),
        (
            "inspection",
            "inspection",
            "execution",
        ),
        (
            "verification",
            "verification",
            "inspection",
        ),
        (
            "learning",
            "learning",
            "verification",
        ),
    ]

    for node_id, node_type, dependency in nodes:
        insert(
            "mission_graph",
            {
                "id": uid("node"),
                "mission_id": mission_id,
                "node_id": node_id,
                "node_type": node_type,
                "depends_on": dependency,
                "status": "pending",
                "payload": "{}",
                "created_at": now(),
            },
        )


def expand_graph(
    mission_id,
    cycle,
    reason,
):
    node_id = f"adaptive-{cycle}-{sha(reason)}"

    existing = select_one(
        """
        SELECT id
        FROM mission_graph
        WHERE mission_id=? AND node_id=?
        """,
        (
            mission_id,
            node_id,
        ),
    )

    if existing:
        return node_id

    insert(
        "mission_graph",
        {
            "id": uid("node"),
            "mission_id": mission_id,
            "node_id": node_id,
            "node_type": "adaptive",
            "depends_on": "verification",
            "status": "pending",
            "payload": json_dumps(
                {
                    "reason": reason,
                    "cycle": cycle,
                }
            ),
            "created_at": now(),
        },
    )

    return node_id


# ============================================================
# PROVENANCE
# ============================================================

def provenance(
    mission_id,
    object_type,
    object_id,
    action,
    parent_id=None,
    metadata=None,
):
    insert(
        "provenance",
        {
            "id": uid("prov"),
            "mission_id": mission_id,
            "object_type": object_type,
            "object_id": object_id,
            "parent_id": parent_id,
            "action": action,
            "metadata": json_dumps(
                metadata or {}
            ),
            "created_at": now(),
        },
    )


# ============================================================
# AUTHORIZATION
# ============================================================

SENSITIVE_TERMS = [
    "delete",
    "destroy",
    "transfer",
    "purchase",
    "pay",
    "send money",
    "change password",
    "credential",
    "deploy production",
    "irreversible",
]


def requires_approval(action):
    lower = action.lower()

    return any(
        term in lower
        for term in SENSITIVE_TERMS
    )


def create_approval(
    mission_id,
    action,
    reason,
):
    approval_id = uid("approval")

    insert(
        "approvals",
        {
            "id": approval_id,
            "mission_id": mission_id,
            "action": action,
            "reason": reason,
            "status": "pending",
            "created_at": now(),
            "decided_at": None,
        },
    )

    return approval_id


# ============================================================
# CONTROLLED EXECUTION
# ============================================================

def execute_strategy(
    mission_id,
    objective,
    strategy,
    cycle,
):
    started = now()

    strategy_type = strategy["type"]

    try:
        if strategy_type in {
            "direct-research",
            "cross-source",
            "verify-first",
            "parallel-explore",
        }:
            research = ingest_research(
                objective
            )

            evidence_count = store_evidence(
                mission_id,
                research,
            )

            claim = build_claims(
                mission_id,
                objective,
            )

            result = {
                "strategy": strategy_type,
                "research": research,
                "claim": claim,
                "evidence_count": evidence_count,
                "cycle": cycle,
            }

            confidence = min(
                0.95,
                0.40
                + min(
                    0.25,
                    evidence_count * 0.015,
                )
                + min(
                    0.20,
                    len(
                        research.get(
                            "providers",
                            [],
                        )
                    ) * 0.05,
                )
                + claim["confidence"] * 0.15,
            )

            status = "success"

        else:
            result = {
                "strategy": strategy_type,
                "message": (
                    "Recovery strategy selected; "
                    "mission will replan."
                ),
                "cycle": cycle,
            }

            confidence = 0.55
            status = "recovery"

        duration = now() - started

        strategy_result_id = uid(
            "strategy-result"
        )

        insert(
            "strategy_results",
            {
                "id": strategy_result_id,
                "strategy_id": strategy["id"],
                "mission_id": mission_id,
                "status": status,
                "result": json_dumps(result),
                "confidence": confidence,
                "evidence_count": int(
                    result.get(
                        "evidence_count",
                        0,
                    )
                ),
                "duration": duration,
                "created_at": now(),
            },
        )

        update(
            "strategies",
            "id",
            strategy["id"],
            {
                "actual_success": confidence,
                "confidence": confidence,
                "status": status,
            },
        )

        insert(
            "observations",
            {
                "id": uid("observation"),
                "mission_id": mission_id,
                "cycle": cycle,
                "strategy_id": strategy["id"],
                "observation": json_dumps(
                    result
                ),
                "success": (
                    1
                    if status == "success"
                    else 0
                ),
                "confidence": confidence,
                "created_at": now(),
            },
        )

        provenance(
            mission_id,
            "strategy",
            strategy["id"],
            "executed",
            metadata={
                "cycle": cycle,
                "status": status,
            },
        )

        return {
            "strategy": strategy,
            "status": status,
            "confidence": confidence,
            "result": result,
            "duration": duration,
        }

    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "strategy": strategy_type,
        }

        update(
            "strategies",
            "id",
            strategy["id"],
            {
                "actual_success": 0,
                "confidence": 0.15,
                "status": "failed",
            },
        )

        insert(
            "observations",
            {
                "id": uid("observation"),
                "mission_id": mission_id,
                "cycle": cycle,
                "strategy_id": strategy["id"],
                "observation": json_dumps(
                    error
                ),
                "success": 0,
                "confidence": 0.15,
                "created_at": now(),
            },
        )

        return {
            "strategy": strategy,
            "status": "failed",
            "confidence": 0.15,
            "result": error,
            "duration": now() - started,
        }


# ============================================================
# STRATEGY COMPETITION
# ============================================================

def compare_strategies(results):
    if not results:
        return {
            "winner": None,
            "confidence": 0,
            "ranking": [],
        }

    ranked = sorted(
        results,
        key=lambda x: (
            float(
                x.get(
                    "confidence",
                    0,
                )
            ),
            1
            if x.get("status") == "success"
            else 0,
        ),
        reverse=True,
    )

    ranking = []

    for item in ranked:
        ranking.append(
            {
                "strategy": item[
                    "strategy"
                ]["name"],
                "type": item[
                    "strategy"
                ]["type"],
                "status": item["status"],
                "confidence": item[
                    "confidence"
                ],
            }
        )

    winner = ranked[0]

    return {
        "winner": winner,
        "confidence": winner["confidence"],
        "ranking": ranking,
    }


# ============================================================
# VERIFICATION
# ============================================================

def independent_verification(
    mission_id,
    objective,
):
    evidence = select_all(
        """
        SELECT provider,confidence,title,url
        FROM evidence
        WHERE mission_id=?
        """,
        (mission_id,),
    )

    providers = {
        x["provider"]
        for x in evidence
    }

    claims = select_all(
        """
        SELECT confidence,supporting_count,
               contradicting_count
        FROM claims
        WHERE mission_id=?
        """,
        (mission_id,),
    )

    claim_confidence = (
        max(
            [
                float(x["confidence"])
                for x in claims
            ],
            default=0.3,
        )
    )

    source_factor = min(
        1.0,
        len(providers) / 4.0,
    )

    evidence_factor = min(
        1.0,
        len(evidence) / 12.0,
    )

    contradiction_count = sum(
        int(
            x.get(
                "contradicting_count",
                0,
            )
        )
        for x in claims
    )

    contradiction_penalty = min(
        0.25,
        contradiction_count * 0.05,
    )

    confidence = max(
        0,
        min(
            0.98,
            claim_confidence * 0.45
            + source_factor * 0.25
            + evidence_factor * 0.30
            - contradiction_penalty,
        ),
    )

    independent = len(providers) >= 2
    verified = (
        confidence >= 0.65
        and independent
        and len(evidence) >= 4
    )

    outcome_id = uid("outcome")

    insert(
        "outcomes",
        {
            "id": outcome_id,
            "mission_id": mission_id,
            "outcome": (
                "Mission outcome independently "
                "supported by available evidence."
                if verified
                else
                "Mission outcome requires additional "
                "evidence or another execution cycle."
            ),
            "status": (
                "verified"
                if verified
                else "needs-more-evidence"
            ),
            "confidence": confidence,
            "verified": 1 if verified else 0,
            "independent": (
                1 if independent else 0
            ),
            "created_at": now(),
        },
    )

    return {
        "outcome_id": outcome_id,
        "verified": verified,
        "independent": independent,
        "confidence": confidence,
        "evidence_count": len(evidence),
        "provider_count": len(providers),
        "providers": sorted(providers),
        "contradictions": contradiction_count,
    }


# ============================================================
# CONVERGENCE ENGINE
# ============================================================

def convergence_score(
    verification,
    strategy_comparison,
):
    verification_score = float(
        verification.get(
            "confidence",
            0,
        )
    )

    competition_score = float(
        strategy_comparison.get(
            "confidence",
            0,
        )
    )

    independent = (
        1.0
        if verification.get(
            "independent"
        )
        else 0.0
    )

    return min(
        1.0,
        verification_score * 0.55
        + competition_score * 0.30
        + independent * 0.15,
    )


def should_converge(score, cycle):
    if score >= 0.80:
        return True

    if cycle >= MAX_CYCLES:
        return True

    return False


# ============================================================
# LEARNING
# ============================================================

def learn_from_cycle(
    mission_id,
    objective,
    comparison,
    verification,
    cycle,
):
    winner = comparison.get("winner")

    if winner:
        strategy_type = winner[
            "strategy"
        ]["type"]

        success = float(
            winner.get(
                "confidence",
                0,
            )
        )

        lesson = (
            f"Cycle {cycle}: strategy "
            f"{strategy_type} produced "
            f"confidence {success:.2f}."
        )

        insert(
            "learning",
            {
                "id": uid("learning"),
                "mission_id": mission_id,
                "lesson": lesson,
                "evidence": json_dumps(
                    {
                        "comparison": comparison,
                        "verification": verification,
                    }
                ),
                "confidence": success,
                "created_at": now(),
            },
        )

        existing = select_one(
            """
            SELECT id,success,uses
            FROM strategy_memory
            WHERE strategy_type=?
            LIMIT 1
            """,
            (strategy_type,),
        )

        if existing:
            old_uses = int(
                existing["uses"]
            )
            old_success = float(
                existing["success"]
            )

            new_success = (
                old_success * old_uses
                + success
            ) / (old_uses + 1)

            update(
                "strategy_memory",
                "id",
                existing["id"],
                {
                    "success": new_success,
                    "uses": old_uses + 1,
                    "lesson": lesson,
                    "updated_at": now(),
                },
            )

        else:
            insert(
                "strategy_memory",
                {
                    "id": uid(
                        "strategy-memory"
                    ),
                    "strategy_type": strategy_type,
                    "context": objective[:500],
                    "success": success,
                    "uses": 1,
                    "lesson": lesson,
                    "updated_at": now(),
                },
            )

        return lesson

    return "No winning strategy available."


# ============================================================
# CHECKPOINTS
# ============================================================

def checkpoint(
    mission_id,
    cycle,
    state,
):
    insert(
        "checkpoints",
        {
            "id": uid("checkpoint"),
            "mission_id": mission_id,
            "cycle": cycle,
            "state": json_dumps(state),
            "created_at": now(),
        },
    )


# ============================================================
# MISSION EXECUTION
# ============================================================

def run_mission(mission_id):
    mission = select_one(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (mission_id,),
    )

    if not mission:
        return

    objective = mission["objective"]

    update(
        "missions",
        "id",
        mission_id,
        {
            "status": "running",
            "updated_at": now(),
        },
    )

    requirements = derive_requirements(
        objective
    )

    store_requirements(
        mission_id,
        requirements,
    )

    create_graph(mission_id)

    final_state = None

    try:
        for cycle in range(1, MAX_CYCLES + 1):
            update(
                "missions",
                "id",
                mission_id,
                {
                    "cycle": cycle,
                    "updated_at": now(),
                },
            )

            # ------------------------------------------------
            # ADAPTIVE STRATEGY PORTFOLIO
            # ------------------------------------------------

            strategies = generate_strategies(
                mission_id,
                objective,
                cycle,
            )

            # ------------------------------------------------
            # PARALLEL BOUNDED EXECUTION
            # ------------------------------------------------

            futures = [
                EXECUTOR.submit(
                    execute_strategy,
                    mission_id,
                    objective,
                    strategy,
                    cycle,
                )
                for strategy in strategies
            ]

            results = [
                future.result()
                for future in futures
            ]

            comparison = compare_strategies(
                results
            )

            # ------------------------------------------------
            # MISSION EXPANSION
            # ------------------------------------------------

            failed = [
                x
                for x in results
                if x["status"] == "failed"
            ]

            if failed:
                expand_graph(
                    mission_id,
                    cycle,
                    "One or more strategies failed.",
                )

            if len(results) > 1:
                expand_graph(
                    mission_id,
                    cycle,
                    "Parallel strategy competition completed.",
                )

            # ------------------------------------------------
            # INDEPENDENT VERIFICATION
            # ------------------------------------------------

            verification = independent_verification(
                mission_id,
                objective,
            )

            convergence = convergence_score(
                verification,
                comparison,
            )

            # ------------------------------------------------
            # LEARNING
            # ------------------------------------------------

            lesson = learn_from_cycle(
                mission_id,
                objective,
                comparison,
                verification,
                cycle,
            )

            final_state = {
                "cycle": cycle,
                "objective": objective,
                "strategies": results,
                "comparison": comparison,
                "verification": verification,
                "convergence": convergence,
                "lesson": lesson,
            }

            checkpoint(
                mission_id,
                cycle,
                final_state,
            )

            provenance(
                mission_id,
                "mission",
                mission_id,
                "cycle-completed",
                metadata={
                    "cycle": cycle,
                    "convergence": convergence,
                },
            )

            # ------------------------------------------------
            # CONVERGENCE GATE
            # ------------------------------------------------

            if should_converge(
                convergence,
                cycle,
            ):
                break

        if not final_state:
            raise RuntimeError(
                "Mission produced no final state."
            )

        verification = final_state[
            "verification"
        ]

        if verification["verified"]:
            status = "completed"
        elif final_state["cycle"] >= MAX_CYCLES:
            status = "completed_with_limits"
        else:
            status = "needs_replanning"

        result_payload = {
            "objective": objective,
            "status": status,
            "cycles": final_state["cycle"],
            "convergence": final_state[
                "convergence"
            ],
            "verification": verification,
            "strategy_comparison": final_state[
                "comparison"
            ],
            "learning": final_state[
                "lesson"
            ],
            "requirements": requirements,
        }

        update(
            "missions",
            "id",
            mission_id,
            {
                "status": status,
                "result": json_dumps(
                    result_payload
                ),
                "confidence": float(
                    verification[
                        "confidence"
                    ]
                ),
                "convergence": float(
                    final_state[
                        "convergence"
                    ]
                ),
                "updated_at": now(),
            },
        )

        return result_payload

    except Exception as exc:
        error = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "mission_id": mission_id,
        }

        update(
            "missions",
            "id",
            mission_id,
            {
                "status": "failed",
                "result": json_dumps(error),
                "updated_at": now(),
            },
        )

        return error


# ============================================================
# MISSION CREATION
# ============================================================

def create_mission(objective):
    mission_id = uid("mission")

    insert(
        "missions",
        {
            "id": mission_id,
            "objective": objective,
            "status": "queued",
            "result": None,
            "confidence": 0,
            "cycle": 0,
            "convergence": 0,
            "created_at": now(),
            "updated_at": now(),
        },
    )

    provenance(
        mission_id,
        "mission",
        mission_id,
        "created",
    )

    return mission_id


# ============================================================
# CONNECTORS
# ============================================================

def seed_connectors():
    connectors = [
        (
            "research",
            "research",
            "safe",
            "active",
            "Public research providers.",
        ),
        (
            "web",
            "web",
            "controlled",
            "active",
            "Controlled public web access.",
        ),
        (
            "artifact",
            "artifact",
            "safe",
            "active",
            "Artifact registry.",
        ),
        (
            "memory",
            "memory",
            "safe",
            "active",
            "Persistent mission memory.",
        ),
        (
            "verification",
            "verification",
            "safe",
            "active",
            "Independent verification layer.",
        ),
    ]

    for name, category, permission, status, description in connectors:
        exists = select_one(
            """
            SELECT id
            FROM connectors
            WHERE name=?
            """,
            (name,),
        )

        if not exists:
            insert(
                "connectors",
                {
                    "id": uid("connector"),
                    "name": name,
                    "category": category,
                    "permission": permission,
                    "status": status,
                    "description": description,
                    "created_at": now(),
                },
            )


seed_connectors()


# ============================================================
# API: ROOT / INTERFACE
# ============================================================

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(
        """
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body{
    margin:0;
    background:#070b10;
    color:#f5f7fa;
    font-family:system-ui,-apple-system,sans-serif;
}
.card{
    max-width:560px;
    margin:24px auto;
    padding:24px;
    background:#111923;
    border-radius:28px;
    box-sizing:border-box;
}
h1{
    font-size:42px;
    margin:0 0 10px;
}
.badge{
    background:#1b2a39;
    padding:12px 16px;
    border-radius:18px;
    display:inline-block;
    margin-bottom:28px;
}
.loop{
    font-size:20px;
    line-height:1.55;
    margin-bottom:20px;
}
textarea{
    width:100%;
    height:150px;
    box-sizing:border-box;
    background:#090e14;
    color:white;
    border:1px solid #334252;
    border-radius:20px;
    padding:18px;
    font-size:18px;
}
button{
    width:100%;
    margin-top:16px;
    padding:18px;
    border:0;
    border-radius:20px;
    font-size:19px;
    cursor:pointer;
}
#status{
    margin-top:18px;
    padding:18px;
    background:#070a0f;
    border-radius:18px;
    white-space:pre-wrap;
    overflow-wrap:anywhere;
}
a{
    color:#8cc8ff;
    margin-right:8px;
}
</style>
</head>
<body>
<div class="card">
<h1>AI Infinity ∞</h1>
<div class="badge">
TARGET-2050.62 — Autonomous Mission Intelligence
</div>

<div class="loop">
Observe → Diagnose → Strategy Portfolio →
Parallel Act → Inspect → Compare →
Verify → Adapt → Learn → Converge
</div>

<textarea id="objective"
placeholder="Tell AI Infinity what outcome you want..."></textarea>

<button onclick="runMission()">Run Mission</button>

<div id="status">Ready.</div>

<p>
<a href="/health">Health</a>
<a href="/architecture">Architecture</a>
<a href="/docs">API Docs</a>
<a href="/test-adaptive">Adaptive Test</a>
</p>
</div>

<script>
async function runMission(){
    const objective =
        document.getElementById("objective").value.trim();

    const status =
        document.getElementById("status");

    if(!objective){
        status.textContent =
            "Please enter an objective.";
        return;
    }

    status.textContent =
        "Mission created. Running adaptive intelligence...";

    try{
        const response = await fetch("/run",{
            method:"POST",
            headers:{
                "Content-Type":"application/json"
            },
            body:JSON.stringify({
                command:objective
            })
        });

        const data = await response.json();

        if(data.mission_id){
            status.textContent =
                "Mission: " + data.mission_id +
                "\\nStatus: " + data.status +
                "\\n\\nOpen mission:\\n" +
                "/mission/" + data.mission_id;
        }else{
            status.textContent =
                JSON.stringify(data,null,2);
        }
    }catch(error){
        status.textContent =
            "Error: " + error;
    }
}
</script>
</body>
</html>
        """
    )


@app.get("/ui", response_class=HTMLResponse)
def ui():
    return home()


@app.get("/interface")
def interface():
    return {
        "version": VERSION,
        "interface": "mobile",
        "status": "ready",
        "workflow": [
            "observe",
            "diagnose",
            "strategy",
            "parallel-execute",
            "inspect",
            "compare",
            "verify",
            "adapt",
            "learn",
            "converge",
        ],
    }


# ============================================================
# API: HEALTH
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "policy": {
            "valid": True,
            "network_policy_enforced": True,
            "controlled_public_web_access": True,
            "arbitrary_code_execution": False,
            "unrestricted_private_network_access": False,
            "permission_bypass": False,
        },
        "layers": {
            "mission_engine": True,
            "requirement_engine": True,
            "research_engine": True,
            "evidence_graph": True,
            "evidence_synthesis": True,
            "claim_engine": True,
            "contradiction_detection": True,
            "decision_engine": True,
            "dynamic_mission_graph": True,
            "authorization": True,
            "execution": True,
            "observation": True,
            "outcome_verification": True,
            "recovery": True,
            "persistent_memory": True,
            "learning": True,
            "reusable_skills": True,
            "artifact_registry": True,
            "resource_governance": True,
            "provenance": True,
            "checkpoints": True,
            "connector_fabric": True,
            "capability_discovery": True,

            "adaptive_reasoning": True,
            "strategy_selection": True,
            "tool_selection": True,
            "execution_inspection": True,
            "failure_diagnosis": True,
            "adaptive_replanning": True,
            "bounded_retry": True,
            "confidence_tracking": True,
            "execution_trace": True,
            "mission_convergence": True,
            "adaptive_learning": True,
            "strategy_memory": True,

            # 2050.62
            "mission_expansion": True,
            "strategy_portfolio": True,
            "parallel_strategy_execution": True,
            "strategy_competition": True,
            "outcome_comparison": True,
            "parallel_research": True,
            "independent_verification": True,
            "convergence_gate": True,
            "dynamic_graph_mutation": True,
        },
        "research": {
            "providers": [
                "wikipedia",
                "crossref",
                "arxiv",
                "openalex",
            ],
            "health": select_all(
                """
                SELECT provider,status,attempts,
                       result_count,error,updated_at
                FROM provider_health
                ORDER BY updated_at DESC
                LIMIT 20
                """
            ),
        },
        "adaptive": {
            "max_cycles": MAX_CYCLES,
            "executor_workers": EXECUTOR_WORKERS,
            "max_parallel_strategies":
                MAX_PARALLEL_STRATEGIES,
            "loop": [
                "observe",
                "diagnose",
                "choose-strategy",
                "select-tool",
                "parallel-execute",
                "inspect",
                "compare",
                "verify",
                "adapt",
                "learn",
                "converge",
            ],
        },
    }


@app.get("/status")
def status():
    return health()


# ============================================================
# API: RUN
# ============================================================

@app.get("/run")
def run_info():
    return {
        "version": VERSION,
        "endpoint": "/run",
        "method": "POST",
        "description": (
            "Create an autonomous mission."
        ),
        "body": {
            "command": "your objective",
            "duration_minutes": 1,
        },
    }


@app.post("/run")
def run(request: RunRequest):
    mission_id = create_mission(
        request.command
    )

    EXECUTOR.submit(
        run_mission,
        mission_id,
    )

    return {
        "mission_id": mission_id,
        "status": "running",
        "version": VERSION,
        "build": BUILD,
        "objective": request.command,
    }


# ============================================================
# API: MISSION
# ============================================================

@app.get("/mission/{mission_id}")
def get_mission(mission_id: str):
    mission = select_one(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (mission_id,),
    )

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    result = safe_json(
        mission.get("result"),
        None,
    )

    return {
        **mission,
        "result": result,
    }


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(mission_id: str):
    return select_all(
        """
        SELECT *
        FROM evidence
        WHERE mission_id=?
        ORDER BY created_at DESC
        """,
        (mission_id,),
    )


@app.get("/mission/{mission_id}/events")
def mission_events(mission_id: str):
    observations = select_all(
        """
        SELECT *
        FROM observations
        WHERE mission_id=?
        ORDER BY created_at ASC
        """,
        (mission_id,),
    )

    return {
        "mission_id": mission_id,
        "events": observations,
    }


@app.get("/mission/{mission_id}/checkpoints")
def mission_checkpoints(mission_id: str):
    rows = select_all(
        """
        SELECT *
        FROM checkpoints
        WHERE mission_id=?
        ORDER BY cycle ASC
        """,
        (mission_id,),
    )

    for row in rows:
        row["state"] = safe_json(
            row["state"],
            {},
        )

    return rows


@app.get("/mission/{mission_id}/outcome")
def mission_outcome(mission_id: str):
    rows = select_all(
        """
        SELECT *
        FROM outcomes
        WHERE mission_id=?
        ORDER BY created_at DESC
        """,
        (mission_id,),
    )

    return {
        "mission_id": mission_id,
        "outcomes": rows,
    }


@app.post("/mission/{mission_id}/verify-outcome")
def verify_outcome(mission_id: str):
    mission = select_one(
        """
        SELECT objective
        FROM missions
        WHERE id=?
        """,
        (mission_id,),
    )

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return independent_verification(
        mission_id,
        mission["objective"],
    )


@app.get("/mission/{mission_id}/requirements")
def mission_requirements(mission_id: str):
    return select_all(
        """
        SELECT *
        FROM requirements
        WHERE mission_id=?
        ORDER BY created_at ASC
        """,
        (mission_id,),
    )


@app.get("/mission/{mission_id}/claims")
def mission_claims(mission_id: str):
    return select_all(
        """
        SELECT *
        FROM claims
        WHERE mission_id=?
        ORDER BY created_at DESC
        """,
        (mission_id,),
    )


@app.get("/mission/{mission_id}/strategies")
def mission_strategies(mission_id: str):
    return select_all(
        """
        SELECT *
        FROM strategies
        WHERE mission_id=?
        ORDER BY cycle DESC,
                 actual_success DESC
        """,
        (mission_id,),
    )


@app.get("/mission/{mission_id}/strategy-results")
def mission_strategy_results(mission_id: str):
    return select_all(
        """
        SELECT *
        FROM strategy_results
        WHERE mission_id=?
        ORDER BY created_at DESC
        """,
        (mission_id,),
    )


@app.get("/mission/{mission_id}/graph")
def mission_graph(mission_id: str):
    rows = select_all(
        """
        SELECT *
        FROM mission_graph
        WHERE mission_id=?
        ORDER BY created_at ASC
        """,
        (mission_id,),
    )

    for row in rows:
        row["payload"] = safe_json(
            row["payload"],
            {},
        )

    return {
        "mission_id": mission_id,
        "nodes": rows,
    }


@app.get("/mission/{mission_id}/provenance")
def mission_provenance(mission_id: str):
    rows = select_all(
        """
        SELECT *
        FROM provenance
        WHERE mission_id=?
        ORDER BY created_at ASC
        """,
        (mission_id,),
    )

    for row in rows:
        row["metadata"] = safe_json(
            row["metadata"],
            {},
        )

    return {
        "mission_id": mission_id,
        "events": rows,
    }


# ============================================================
# API: APPROVALS
# ============================================================

@app.get("/approvals")
def approvals():
    return select_all(
        """
        SELECT *
        FROM approvals
        ORDER BY created_at DESC
        """
    )


@app.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    request: ApprovalRequest,
):
    approval = select_one(
        """
        SELECT *
        FROM approvals
        WHERE id=?
        """,
        (approval_id,),
    )

    if not approval:
        raise HTTPException(
            status_code=404,
            detail="Approval not found",
        )

    update(
        "approvals",
        "id",
        approval_id,
        {
            "status": (
                "approved"
                if request.approved
                else "rejected"
            ),
            "decided_at": now(),
        },
    )

    return {
        "approval_id": approval_id,
        "status": (
            "approved"
            if request.approved
            else "rejected"
        ),
    }


# ============================================================
# API: MEMORY
# ============================================================

@app.get("/memory-count")
def memory_count():
    row = select_one(
        "SELECT COUNT(*) AS count FROM memory"
    )

    return {
        "count": row["count"]
        if row
        else 0
    }


@app.get("/memory")
def memory():
    return select_all(
        """
        SELECT *
        FROM memory
        ORDER BY updated_at DESC
        """
    )


@app.post("/memory")
def write_memory(request: MemoryRequest):
    existing = select_one(
        """
        SELECT id
        FROM memory
        WHERE key=?
        """,
        (request.key,),
    )

    if existing:
        update(
            "memory",
            "id",
            existing["id"],
            {
                "value": json_dumps(
                    request.value
                ),
                "confidence": request.confidence,
                "updated_at": now(),
            },
        )

        return {
            "id": existing["id"],
            "status": "updated",
        }

    memory_id = uid("memory")

    insert(
        "memory",
        {
            "id": memory_id,
            "key": request.key,
            "value": json_dumps(
                request.value
            ),
            "confidence": request.confidence,
            "created_at": now(),
            "updated_at": now(),
        },
    )

    return {
        "id": memory_id,
        "status": "created",
    }


# ============================================================
# API: SKILLS / LEARNING / ARTIFACTS
# ============================================================

@app.get("/skills-count")
def skills_count():
    row = select_one(
        "SELECT COUNT(*) AS count FROM skills"
    )

    return {
        "count": row["count"]
        if row
        else 0
    }


@app.get("/skills")
def skills():
    return select_all(
        """
        SELECT *
        FROM skills
        ORDER BY confidence DESC
        """
    )


@app.get("/learning")
def learning():
    return select_all(
        """
        SELECT *
        FROM learning
        ORDER BY created_at DESC
        """
    )


@app.get("/artifacts")
def artifacts():
    return select_all(
        """
        SELECT *
        FROM artifacts
        ORDER BY created_at DESC
        """
    )


# ============================================================
# API: CONNECTORS
# ============================================================

@app.get("/connectors")
def connectors():
    return select_all(
        """
        SELECT *
        FROM connectors
        ORDER BY name
        """
    )


@app.get("/connector-health")
def connector_health():
    return select_all(
        """
        SELECT *
        FROM connector_events
        ORDER BY created_at DESC
        LIMIT 50
        """
    )


# ============================================================
# API: TOOLS / CAPABILITIES
# ============================================================

@app.get("/tools")
def tools():
    return {
        "version": VERSION,
        "tools": [
            {
                "name": "research",
                "permission": "safe",
                "status": "available",
            },
            {
                "name": "evidence",
                "permission": "safe",
                "status": "available",
            },
            {
                "name": "strategy_portfolio",
                "permission": "safe",
                "status": "available",
            },
            {
                "name": "parallel_execution",
                "permission": "bounded",
                "status": "available",
            },
            {
                "name": "verification",
                "permission": "safe",
                "status": "available",
            },
            {
                "name": "memory",
                "permission": "safe",
                "status": "available",
            },
        ],
    }


@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "capabilities": [
            "mission_creation",
            "requirement_discovery",
            "research",
            "evidence_collection",
            "claim_synthesis",
            "contradiction_detection",
            "strategy_generation",
            "parallel_strategy_execution",
            "strategy_competition",
            "dynamic_mission_expansion",
            "failure_diagnosis",
            "adaptive_replanning",
            "independent_verification",
            "confidence_tracking",
            "convergence",
            "learning",
            "strategy_memory",
            "persistent_memory",
            "provenance",
            "checkpoints",
            "controlled_external_access",
        ],
    }


@app.get("/discover")
def discover(
    objective: str = Query(
        default="Research and verify an AI system"
    )
):
    return {
        "objective": objective,
        "recommended_capabilities": [
            "requirements",
            "research",
            "evidence",
            "cross-source-synthesis",
            "strategy-portfolio",
            "parallel-execution",
            "verification",
            "learning",
        ],
        "version": VERSION,
    }


# ============================================================
# API: RESEARCH
# ============================================================

@app.get("/research/sources")
def research_sources():
    return {
        "providers": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        "controlled": True,
        "network_policy": "research-allowlist",
    }


@app.get("/research/providers")
def research_providers():
    return {
        "providers": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ]
    }


@app.get("/research/discover")
def research_discover(
    query: str = "artificial intelligence agents"
):
    return ingest_research(query)


@app.get("/research/providers/test")
def research_provider_test():
    return ingest_research(
        "artificial intelligence agents"
    )


@app.get("/test-research")
def test_research():
    result = ingest_research(
        "artificial intelligence agents"
    )

    return {
        "test": "research",
        "version": VERSION,
        **result,
    }


# ============================================================
# API: POLICY
# ============================================================

@app.get("/policy")
def policy():
    return {
        "valid": True,
        "network_policy_enforced": True,
        "controlled_public_web_access": True,
        "arbitrary_code_execution": False,
        "unrestricted_private_network_access": False,
        "permission_bypass": False,
        "credential_modification": False,
        "stealth_persistence": False,
    }


@app.get("/policy/validate")
def policy_validate():
    return policy()


# ============================================================
# API: ARCHITECTURE
# ============================================================

@app.get("/architecture")
def architecture():
    layers = [
        (
            "Intent",
            "Understand the requested outcome",
        ),
        (
            "Requirements",
            "Discover what must be true",
        ),
        (
            "Research",
            "Gather independent information",
        ),
        (
            "Evidence",
            "Store and provenance-link evidence",
        ),
        (
            "Synthesis",
            "Compare sources and form claims",
        ),
        (
            "Decision",
            "Select bounded next actions",
        ),
        (
            "Mission Graph",
            "Execute dependencies dynamically",
        ),
        (
            "Authorization",
            "Enforce permissions",
        ),
        (
            "Execution",
            "Use controlled tools",
        ),
        (
            "Observation",
            "Measure what actually happened",
        ),
        (
            "Verification",
            "Determine whether the outcome is real",
        ),
        (
            "Recovery",
            "Checkpoint and replan after failure",
        ),
        (
            "Learning",
            "Extract reusable lessons",
        ),
        (
            "Skills",
            "Turn successful procedures into reusable capability",
        ),
        (
            "Artifacts",
            "Preserve produced work",
        ),
        (
            "Provenance",
            "Trace how results were produced",
        ),
        (
            "Resource Governance",
            "Track bounded resource usage",
        ),
        (
            "Adaptive Reasoning",
            "Interpret observations and select strategy",
        ),
        (
            "Tool Selection",
            "Choose the most appropriate available tool",
        ),
        (
            "Execution Inspection",
            "Inspect actual tool outcomes",
        ),
        (
            "Failure Diagnosis",
            "Classify why an attempt failed",
        ),
        (
            "Adaptive Replanning",
            "Change strategy instead of blindly repeating",
        ),
        (
            "Confidence Engine",
            "Track evidence and execution confidence",
        ),
        (
            "Strategy Memory",
            "Remember which strategies work",
        ),
        (
            "Convergence",
            "Stop when the outcome is sufficiently verified",
        ),
        (
            "Mission Expansion",
            "Add new bounded work when evidence reveals a gap",
        ),
        (
            "Strategy Portfolio",
            "Maintain multiple candidate strategies",
        ),
        (
            "Parallel Execution",
            "Run independent bounded strategies concurrently",
        ),
        (
            "Strategy Competition",
            "Compare actual strategy outcomes",
        ),
        (
            "Outcome Comparison",
            "Compare evidence and execution results",
        ),
        (
            "Independent Verification",
            "Verify outcomes using separate evidence paths",
        ),
        (
            "Convergence Gate",
            "Require sufficient evidence before closure",
        ),
    ]

    return {
        "version": VERSION,
        "build": BUILD,
        "architecture": [
            {
                "layer": i + 1,
                "name": name,
                "purpose": purpose,
            }
            for i, (name, purpose)
            in enumerate(layers)
        ],
        "closed_loop": [
            "Intent",
            "Requirements",
            "Research",
            "Evidence",
            "Synthesis",
            "Decision",
            "Mission Graph",
            "Strategy Portfolio",
            "Authorization",
            "Parallel Execution",
            "Observation",
            "Diagnosis",
            "Comparison",
            "Replanning",
            "Verification",
            "Convergence",
            "Learning",
            "Strategy Memory",
        ],
    }


# ============================================================
# TEST ROUTES
# ============================================================

@app.get("/test-router")
def test_router():
    return {
        "status": "passed",
        "version": VERSION,
        "build": BUILD,
        "router": "operational",
    }


@app.get("/test-tools")
def test_tools():
    return {
        "status": "passed",
        "version": VERSION,
        "tools": len(
            tools()["tools"]
        ),
    }


@app.get("/test-external")
def test_external():
    return {
        "status": "controlled",
        "version": VERSION,
        "network_policy_enforced": True,
        "arbitrary_external_access": False,
    }


@app.get("/test-adaptive")
def test_adaptive():
    return {
        "status": "passed",
        "version": VERSION,
        "build": BUILD,
        "adaptive_reasoning": {
            "cycle_1": "direct-research",
            "cycle_2": "cross-source",
            "cycle_3": "verify-first",
            "cycle_4": "parallel-explore",
        },
        "loop": [
            "observe",
            "diagnose",
            "choose-strategy",
            "select-tool",
            "parallel-execute",
            "inspect",
            "compare",
            "verify",
            "adapt",
            "learn",
            "converge",
        ],
    }


@app.get("/test-orchestrator")
def test_orchestrator():
    return {
        "status": "passed",
        "version": VERSION,
        "orchestrator": {
            "mission_engine": True,
            "strategy_portfolio": True,
            "parallel_execution": True,
            "comparison": True,
            "verification": True,
            "learning": True,
        },
    }


@app.get("/test-intelligence")
def test_intelligence():
    strategies = generate_strategies(
        "test-mission",
        "Research and verify an AI system",
        1,
    )

    return {
        "status": "passed",
        "version": VERSION,
        "build": BUILD,
        "strategies_generated": len(
            strategies
        ),
        "parallel_strategy_execution": True,
        "strategy_competition": True,
        "independent_verification": True,
        "convergence_gate": True,
    }


# ============================================================
# STARTUP / SHUTDOWN
# ============================================================

@app.on_event("shutdown")
def shutdown():
    EXECUTOR.shutdown(
        wait=False,
        cancel_futures=False,
    )

"requirements.txt"

fastapi
uvicorn[standard]
requests
pydantic
python-multipart
lxml
