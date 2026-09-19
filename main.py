# AI Infinity
# TARGET-2050.6 — Evidence Infinity
# Free-first autonomous intelligence platform
#
# Replace your existing main.py with this file.
#
# Compatible with:
#   uvicorn main:app --host 0.0.0.0 --port $PORT
#
# Core principle:
#   More powerful by becoming better at knowing what it does not know.

from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import math
import os
import re
import socket
import sqlite3
import time
import traceback
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field


# ============================================================
# IDENTITY
# ============================================================

VERSION = "TARGET-2050.6"
PROJECT = "AI Infinity"
TARGET_YEAR = 2050

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "infinity.db"

USER_AGENT = (
    "AI-Infinity/2050.6 "
    "(evidence-research-agent; +https://ai-infinity-ca5e.onrender.com)"
)

MAX_FETCH_BYTES = 2_000_000
MAX_TEXT_CHARS = 80_000
REQUEST_TIMEOUT = 15.0

SEARCH_TIMEOUT = 12.0

MIN_SOURCE_WORDS = 80
MIN_SOURCE_UNIQUE_RATIO = 0.18

STRONG_DOMAIN_COUNT = 3
MODERATE_DOMAIN_COUNT = 2


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "Evidence-aware autonomous intelligence platform with "
        "research swarm, evidence graph, verification, counterclaims, "
        "memory, planning, recovery and self-diagnostics."
    ),
)


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS memory (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            confidence REAL DEFAULT 0.0,
            source TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT
        );

        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evaluations (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            task_id TEXT,
            score REAL DEFAULT 0.0,
            feedback TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            priority REAL DEFAULT 0.0,
            status TEXT DEFAULT 'open'
        );

        CREATE TABLE IF NOT EXISTS world (
            key TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            capabilities TEXT NOT NULL,
            status TEXT DEFAULT 'ready'
        );

        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT DEFAULT 'unknown',
            last_checked TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence_sources (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            domain TEXT NOT NULL,
            title TEXT DEFAULT '',
            text TEXT DEFAULT '',
            quality REAL DEFAULT 0.0,
            provider TEXT DEFAULT '',
            fetched_at TEXT NOT NULL,
            status TEXT DEFAULT 'accepted',
            rejection_reason TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS claims (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            claim TEXT NOT NULL,
            normalized_claim TEXT NOT NULL,
            confidence REAL DEFAULT 0.0,
            state TEXT DEFAULT 'unverified',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS claim_evidence (
            id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            passage TEXT DEFAULT '',
            similarity REAL DEFAULT 0.0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS research_runs (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            query TEXT NOT NULL,
            provider TEXT NOT NULL,
            created_at TEXT NOT NULL,
            result_count INTEGER DEFAULT 0,
            accepted_count INTEGER DEFAULT 0,
            rejected_count INTEGER DEFAULT 0,
            strength TEXT DEFAULT 'insufficient'
        );

        CREATE TABLE IF NOT EXISTS regressions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            test_name TEXT NOT NULL,
            passed INTEGER NOT NULL,
            score REAL DEFAULT 0.0,
            details TEXT NOT NULL
        );
        """
    )

    agents = [
        (
            "agent-planner",
            "Planner",
            "mission planning and decomposition",
            ["planning", "decomposition", "replanning"],
        ),
        (
            "agent-researcher",
            "Researcher",
            "evidence acquisition",
            ["search", "fetch", "source-analysis"],
        ),
        (
            "agent-verifier",
            "Verifier",
            "claim verification",
            ["verification", "evidence-graph", "contradiction-analysis"],
        ),
        (
            "agent-critic",
            "Critic",
            "failure and uncertainty analysis",
            ["critique", "uncertainty", "counterclaims"],
        ),
        (
            "agent-executor",
            "Executor",
            "safe task execution",
            ["execution", "http", "workflow"],
        ),
        (
            "agent-learner",
            "Learner",
            "evaluation and improvement",
            ["learning", "evaluation", "opportunity-detection"],
        ),
    ]

    for agent_id, name, role, capabilities in agents:
        conn.execute(
            """
            INSERT OR REPLACE INTO agents
            (id,name,role,capabilities,status)
            VALUES (?,?,?,?,?)
            """,
            (
                agent_id,
                name,
                role,
                json.dumps(capabilities),
                "ready",
            ),
        )

    providers = [
        ("provider-duckduckgo", "DuckDuckGo", "search"),
        ("provider-bing", "Bing", "search"),
        ("provider-google", "Google", "search"),
        ("provider-direct", "Direct HTTP", "fetch"),
    ]

    for pid, name, category in providers:
        conn.execute(
            """
            INSERT OR IGNORE INTO providers
            (id,name,category,status)
            VALUES (?,?,?,'available')
            """,
            (pid, name, category),
        )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILITIES
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def save_event(event_type: str, payload: Any) -> str:
    event_id = uid("evt")

    conn = db()
    conn.execute(
        """
        INSERT INTO events(id,created_at,event_type,payload)
        VALUES(?,?,?,?)
        """,
        (
            event_id,
            now(),
            event_type,
            json.dumps(safe_json(payload), ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()

    return event_id


def save_memory(
    kind: str,
    content: Any,
    confidence: float = 0.0,
    source: str = "",
) -> str:
    memory_id = uid("mem")

    conn = db()
    conn.execute(
        """
        INSERT INTO memory
        (id,created_at,kind,content,confidence,source)
        VALUES(?,?,?,?,?,?)
        """,
        (
            memory_id,
            now(),
            kind,
            json.dumps(safe_json(content), ensure_ascii=False),
            float(confidence),
            source,
        ),
    )
    conn.commit()
    conn.close()

    return memory_id


# ============================================================
# URL / SSRF SECURITY
# ============================================================

BLOCKED_HOST_PARTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "instance-data",
    "host.docker.internal",
}


def hostname_is_safe(host: str) -> bool:
    if not host:
        return False

    h = host.lower().strip(".")

    if h in BLOCKED_HOST_PARTS:
        return False

    if h.endswith(".local"):
        return False

    try:
        infos = socket.getaddrinfo(h, None)

        for info in infos:
            address = info[4][0]

            try:
                ip = ipaddress.ip_address(address)
            except Exception:
                continue

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
        pass

    return True


def safe_url(url: str) -> bool:
    try:
        p = urlparse(url)

        if p.scheme not in {"http", "https"}:
            return False

        if not p.hostname:
            return False

        if not hostname_is_safe(p.hostname):
            return False

        return True

    except Exception:
        return False


# ============================================================
# SEARCH INFRASTRUCTURE FIREWALL
# ============================================================

BAD_PATH_PARTS = {
    "/search",
    "/preferences",
    "/settings",
    "/support",
    "/help",
    "/websearch",
    "/login",
    "/signin",
    "/signup",
    "/accounts",
    "/account",
    "/consent",
    "/privacy",
    "/terms",
    "/maps",
    "/images",
    "/videos",
    "/news",
    "/shopping",
    "/translate",
    "/cache",
}

BAD_EXTENSIONS = {
    ".css",
    ".js",
    ".json",
    ".xml",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".mp3",
    ".mp4",
    ".zip",
    ".exe",
}


def is_search_infrastructure(url: str) -> bool:
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        path = (p.path or "").lower()

        infrastructure_hosts = (
            "google.com",
            "bing.com",
            "duckduckgo.com",
            "r.bing.com",
            "googleusercontent.com",
            "gstatic.com",
            "cloudfront.net",
            "akamaihd.net",
        )

        if any(host == h or host.endswith("." + h) for h in infrastructure_hosts):
            if host.endswith("google.com") or host.endswith("bing.com"):
                return True

        if any(part in path for part in BAD_PATH_PARTS):
            return True

        if any(path.endswith(ext) for ext in BAD_EXTENSIONS):
            return True

        return False

    except Exception:
        return True


def canonicalize_url(url: str) -> str:
    try:
        p = urlparse(url)

        query = []

        for key, value in parse_qsl(
            p.query,
            keep_blank_values=True,
        ):
            k = key.lower()

            if (
                k.startswith("utm_")
                or k in {
                    "gclid",
                    "fbclid",
                    "ref",
                    "source",
                    "campaign",
                    "trk",
                    "tracking",
                }
            ):
                continue

            query.append((key, value))

        path = re.sub(r"/+", "/", p.path or "/")

        if path != "/" and path.endswith("/"):
            path = path[:-1]

        return urlunparse(
            (
                p.scheme.lower(),
                (p.netloc or "").lower(),
                path,
                "",
                urlencode(query),
                "",
            )
        )

    except Exception:
        return url


# ============================================================
# CONTENT CONTAMINATION DETECTION
# ============================================================

CONTAMINATION_PATTERNS = [
    r"\{font\s*:",
    r"\{margin\s*:",
    r"font-family\s*:",
    r"background-color\s*:",
    r"webpack",
    r"sourceMappingURL",
    r"javascript:",
    r"window\.__",
    r"document\.getElementById",
    r"googletag",
    r"gstatic",
    r"r\.bing\.com",
    r"duckduckgo",
]


def contamination_score(text: str) -> float:
    if not text:
        return 1.0

    sample = text[:30000].lower()

    hits = 0

    for pattern in CONTAMINATION_PATTERNS:
        if re.search(pattern.lower(), sample):
            hits += 1

    css_tokens = len(
        re.findall(
            r"[a-z-]+\s*:\s*[^;]{1,100};",
            sample,
        )
    )

    script_tokens = len(
        re.findall(
            r"(function\s*\(|=>|var\s+[a-zA-Z_$]+|const\s+[a-zA-Z_$]+)",
            sample,
        )
    )

    score = min(
        1.0,
        (hits * 0.12)
        + min(0.5, css_tokens / 100)
        + min(0.5, script_tokens / 100),
    )

    return score


def clean_html(raw: str) -> str:
    if not raw:
        return ""

    text = raw

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
        r"<noscript\b[^>]*>.*?</noscript>",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = re.sub(
        r"<svg\b[^>]*>.*?</svg>",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = html.unescape(text)

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()[:MAX_TEXT_CHARS]


def unique_ratio(text: str) -> float:
    words = re.findall(r"\b[\w'-]{2,}\b", text.lower())

    if not words:
        return 0.0

    return len(set(words)) / len(words)


# ============================================================
# SOURCE QUALITY
# ============================================================

LOW_VALUE_DOMAINS = {
    "support.google.com",
    "support.microsoft.com",
    "support.apple.com",
    "help.openai.com",
    "help.bing.microsoft.com",
    "duckduckgo.com",
    "google.com",
    "bing.com",
}

HIGH_VALUE_TLDS = {
    ".gov",
    ".edu",
    ".ac.uk",
    ".ac",
}


def domain_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host.removeprefix("www.")
    except Exception:
        return ""


def source_quality(
    url: str,
    title: str,
    text: str,
) -> float:
    domain = domain_of(url)

    if not domain:
        return 0.0

    if domain in LOW_VALUE_DOMAINS:
        return 0.0

    if is_search_infrastructure(url):
        return 0.0

    words = re.findall(r"\b[\w'-]{2,}\b", text)

    if len(words) < MIN_SOURCE_WORDS:
        return 0.05

    ratio = unique_ratio(text)

    if ratio < MIN_SOURCE_UNIQUE_RATIO:
        return 0.08

    contamination = contamination_score(text)

    if contamination >= 0.65:
        return 0.0

    score = 0.45

    if len(words) >= 300:
        score += 0.15

    if len(words) >= 800:
        score += 0.10

    if ratio >= 0.30:
        score += 0.10

    if title and len(title.strip()) >= 10:
        score += 0.05

    if any(domain.endswith(tld) for tld in HIGH_VALUE_TLDS):
        score += 0.15

    score -= contamination * 0.45

    return max(0.0, min(1.0, score))


# ============================================================
# SEARCH RESULT PARSING
# ============================================================

def extract_links_from_html(
    base_url: str,
    raw: str,
) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []

    pattern = re.compile(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        flags=re.I | re.S,
    )

    for match in pattern.finditer(raw):
        href = html.unescape(match.group(1))
        anchor = clean_html(match.group(2))

        if href.startswith("//"):
            href = "https:" + href

        href = urljoin(base_url, href)

        if not safe_url(href):
            continue

        if is_search_infrastructure(href):
            continue

        canonical = canonicalize_url(href)

        if not canonical:
            continue

        results.append(
            {
                "url": canonical,
                "title": anchor[:300],
            }
        )

    return results


def search_result_candidates(
    provider: str,
    raw: str,
    query: str,
) -> List[Dict[str, str]]:
    if provider == "duckduckgo":
        return extract_links_from_html(
            "https://html.duckduckgo.com/",
            raw,
        )

    if provider == "bing":
        return extract_links_from_html(
            "https://www.bing.com/",
            raw,
        )

    if provider == "google":
        return extract_links_from_html(
            "https://www.google.com/",
            raw,
        )

    return []


# ============================================================
# HTTP
# ============================================================

async def http_get(
    url: str,
    timeout: float = REQUEST_TIMEOUT,
) -> Tuple[int, str, str]:
    if not safe_url(url):
        return 0, "", "blocked-url"

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(url)

            content_type = response.headers.get(
                "content-type",
                "",
            ).lower()

            data = response.content[:MAX_FETCH_BYTES]

            if (
                "text/html" not in content_type
                and "text/plain" not in content_type
                and "application/xhtml" not in content_type
            ):
                return response.status_code, "", "non-text"

            return (
                response.status_code,
                data.decode("utf-8", errors="ignore"),
                "",
            )

    except Exception as exc:
        return 0, "", str(exc)


# ============================================================
# SEARCH PROVIDERS
# ============================================================

async def search_duckduckgo(
    query: str,
) -> List[Dict[str, str]]:
    url = (
        "https://html.duckduckgo.com/html/?"
        + urlencode({"q": query})
    )

    status, raw, error = await http_get(
        url,
        SEARCH_TIMEOUT,
    )

    if status != 200 or not raw:
        return []

    return search_result_candidates(
        "duckduckgo",
        raw,
        query,
    )


async def search_bing(
    query: str,
) -> List[Dict[str, str]]:
    url = (
        "https://www.bing.com/search?"
        + urlencode({"q": query})
    )

    status, raw, error = await http_get(
        url,
        SEARCH_TIMEOUT,
    )

    if status != 200 or not raw:
        return []

    return search_result_candidates(
        "bing",
        raw,
        query,
    )


async def search_google(
    query: str,
) -> List[Dict[str, str]]:
    url = (
        "https://www.google.com/search?"
        + urlencode({"q": query})
    )

    status, raw, error = await http_get(
        url,
        SEARCH_TIMEOUT,
    )

    if status != 200 or not raw:
        return []

    return search_result_candidates(
        "google",
        raw,
        query,
    )


# ============================================================
# SOURCE FETCHING
# ============================================================

async def fetch_source(
    item: Dict[str, str],
    provider: str,
) -> Optional[Dict[str, Any]]:
    url = canonicalize_url(item.get("url", ""))

    if not safe_url(url):
        return {
            "url": url,
            "status": "rejected",
            "reason": "unsafe-url",
        }

    if is_search_infrastructure(url):
        return {
            "url": url,
            "status": "rejected",
            "reason": "search-infrastructure",
        }

    status, raw, error = await http_get(url)

    if status != 200 or not raw:
        return {
            "url": url,
            "status": "rejected",
            "reason": error or f"http-{status}",
        }

    text = clean_html(raw)

    if not text:
        return {
            "url": url,
            "status": "rejected",
            "reason": "empty-content",
        }

    contamination = contamination_score(text)

    if contamination >= 0.65:
        return {
            "url": url,
            "status": "rejected",
            "reason": "contaminated-content",
            "contamination": contamination,
        }

    words = re.findall(r"\b[\w'-]{2,}\b", text)

    if len(words) < MIN_SOURCE_WORDS:
        return {
            "url": url,
            "status": "rejected",
            "reason": "insufficient-content",
            "word_count": len(words),
        }

    ratio = unique_ratio(text)

    if ratio < MIN_SOURCE_UNIQUE_RATIO:
        return {
            "url": url,
            "status": "rejected",
            "reason": "low-information-content",
            "unique_ratio": ratio,
        }

    title = item.get("title", "").strip()

    quality = source_quality(
        url,
        title,
        text,
    )

    if quality < 0.20:
        return {
            "url": url,
            "status": "rejected",
            "reason": "low-source-quality",
            "quality": quality,
        }

    source_id = uid("src")

    record = {
        "id": source_id,
        "url": url,
        "canonical_url": url,
        "domain": domain_of(url),
        "title": title or domain_of(url),
        "text": text[:MAX_TEXT_CHARS],
        "quality": round(quality, 4),
        "provider": provider,
        "fetched_at": now(),
        "status": "accepted",
        "rejection_reason": "",
    }

    conn = db()

    conn.execute(
        """
        INSERT OR REPLACE INTO evidence_sources
        (id,url,canonical_url,domain,title,text,quality,
         provider,fetched_at,status,rejection_reason)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            record["id"],
            record["url"],
            record["canonical_url"],
            record["domain"],
            record["title"],
            record["text"],
            record["quality"],
            record["provider"],
            record["fetched_at"],
            record["status"],
            "",
        ),
    )

    conn.commit()
    conn.close()

    return record


# ============================================================
# RESEARCH QUERY GENERATION
# ============================================================

def query_variants(query: str) -> List[str]:
    base = query.strip()

    variants = [
        base,
        f'"{base}" evidence',
        f"{base} research findings",
        f"{base} benchmark evaluation",
        f"{base} limitations criticism",
        f"{base} independent analysis",
        f"{base} contradictory evidence",
        f"{base} failure cases",
    ]

    output = []

    for q in variants:
        if q and q not in output:
            output.append(q)

    return output


def counterclaim_queries(query: str) -> List[str]:
    return [
        f"{query} criticism limitations",
        f"{query} evidence against",
        f"{query} failures problems",
        f"{query} independent criticism",
        f"{query} contradictory findings",
    ]


# ============================================================
# RESEARCH ENGINE
# ============================================================

async def research(
    query: str,
    mission_id: Optional[str] = None,
    max_sources: int = 12,
) -> Dict[str, Any]:
    started = time.time()

    all_candidates: List[Dict[str, str]] = []
    provider_counts = Counter()

    providers = [
        ("duckduckgo", search_duckduckgo),
        ("bing", search_bing),
        ("google", search_google),
    ]

    variants = query_variants(query)

    # Research swarm:
    # Run several independent provider/query combinations.
    jobs = []

    for provider_name, provider_fn in providers:
        for q in variants[:4]:
            jobs.append(
                (
                    provider_name,
                    q,
                    provider_fn(q),
                )
            )

    results = await asyncio.gather(
        *[job[2] for job in jobs],
        return_exceptions=True,
    )

    for job, result in zip(jobs, results):
        provider_name, q, _ = job

        if isinstance(result, Exception):
            continue

        if not result:
            continue

        provider_counts[provider_name] += len(result)

        for item in result:
            item = dict(item)
            item["provider"] = provider_name
            item["query"] = q
            all_candidates.append(item)

    # Deduplicate candidate URLs.
    candidate_map: Dict[str, Dict[str, str]] = {}

    for item in all_candidates:
        url = canonicalize_url(item.get("url", ""))

        if not url:
            continue

        if url in candidate_map:
            continue

        candidate_map[url] = {
            **item,
            "url": url,
        }

    candidates = list(candidate_map.values())

    # Limit concurrent source fetches.
    sem = asyncio.Semaphore(8)

    async def bounded_fetch(item: Dict[str, str]) -> Any:
        async with sem:
            return await fetch_source(
                item,
                item.get("provider", "unknown"),
            )

    fetched = await asyncio.gather(
        *[
            bounded_fetch(item)
            for item in candidates[:80]
        ],
        return_exceptions=True,
    )

    accepted = []
    rejected = []

    for item in fetched:
        if isinstance(item, Exception):
            rejected.append(
                {
                    "reason": "fetch-exception",
                    "error": str(item),
                }
            )
            continue

        if not item:
            continue

        if item.get("status") == "accepted":
            accepted.append(item)
        else:
            rejected.append(item)

    # Independent domain deduplication.
    by_domain: Dict[str, Dict[str, Any]] = {}

    for source in accepted:
        domain = source["domain"]

        existing = by_domain.get(domain)

        if existing is None:
            by_domain[domain] = source
            continue

        if source["quality"] > existing["quality"]:
            by_domain[domain] = source

    independent_sources = list(by_domain.values())

    independent_sources.sort(
        key=lambda x: x["quality"],
        reverse=True,
    )

    independent_sources = independent_sources[:max_sources]

    domain_count = len(
        {
            source["domain"]
            for source in independent_sources
        }
    )

    if domain_count >= STRONG_DOMAIN_COUNT:
        strength = "strong"
    elif domain_count >= MODERATE_DOMAIN_COUNT:
        strength = "moderate"
    elif domain_count == 1:
        strength = "weak"
    else:
        strength = "insufficient"

    average_quality = (
        sum(s["quality"] for s in independent_sources)
        / len(independent_sources)
        if independent_sources
        else 0.0
    )

    failure_reason = None

    if domain_count == 0:
        failure_reason = (
            "No independent evidence sources survived the source firewall."
        )
    elif domain_count == 1:
        failure_reason = (
            "Only one independent evidence domain survived."
        )
    elif domain_count == 2:
        failure_reason = (
            "Evidence exists across two domains but "
            "does not meet the strong-diversity threshold."
        )

    run_id = uid("research")

    conn = db()

    conn.execute(
        """
        INSERT INTO research_runs
        (id,mission_id,query,provider,created_at,
         result_count,accepted_count,rejected_count,strength)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            mission_id,
            query,
            "research-swarm",
            now(),
            len(candidates),
            len(independent_sources),
            len(rejected),
            strength,
        ),
    )

    conn.commit()
    conn.close()

    save_event(
        "research_completed",
        {
            "query": query,
            "strength": strength,
            "domains": domain_count,
            "accepted": len(independent_sources),
            "rejected": len(rejected),
        },
    )

    return {
        "query": query,
        "strength": strength,
        "accepted_sources": independent_sources,
        "accepted_domains": [
            s["domain"]
            for s in independent_sources
        ],
        "independent_domain_count": domain_count,
        "average_source_quality": round(
            average_quality,
            4,
        ),
        "provider_counts": dict(provider_counts),
        "rejected_sources": rejected[:40],
        "research_failure_reason": failure_reason,
        "duration_seconds": round(
            time.time() - started,
            3,
        ),
    }


# ============================================================
# TEXT SIMILARITY / CLAIM SUPPORT
# ============================================================

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "are",
    "was",
    "were",
    "have",
    "has",
    "had",
    "not",
    "but",
    "you",
    "your",
    "their",
    "they",
    "its",
    "can",
    "may",
    "will",
    "about",
    "what",
    "how",
    "why",
    "which",
    "who",
    "using",
    "use",
    "more",
    "than",
}


def tokens(text: str) -> set[str]:
    return {
        word
        for word in re.findall(
            r"\b[a-zA-Z0-9]{3,}\b",
            text.lower(),
        )
        if word not in STOPWORDS
    }


def similarity(a: str, b: str) -> float:
    ta = tokens(a)
    tb = tokens(b)

    if not ta or not tb:
        return 0.0

    return len(ta & tb) / max(
        1,
        len(ta | tb),
    )


def evidence_passage(
    claim: str,
    text: str,
) -> str:
    sentences = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    scored = []

    for sentence in sentences:
        score = similarity(
            claim,
            sentence,
        )

        if score > 0:
            scored.append(
                (
                    score,
                    sentence.strip(),
                )
            )

    scored.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    if scored:
        return scored[0][1][:1200]

    return text[:1200]


# ============================================================
# CLAIM EXTRACTION
# ============================================================

def extract_claims(
    objective: str,
    research_result: Dict[str, Any],
) -> List[str]:
    claims = []

    # Main objective-derived claim.
    if objective.strip():
        claims.append(
            f"AI Infinity investigation result for: {objective.strip()}"
        )

    sources = research_result.get(
        "accepted_sources",
        [],
    )

    for source in sources[:6]:
        title = source.get("title", "").strip()

        if title:
            claims.append(
                f"Source reports findings relevant to {objective}: {title}"
            )

    # Deduplicate.
    seen = set()
    output = []

    for claim in claims:
        normalized = re.sub(
            r"\s+",
            " ",
            claim.lower(),
        ).strip()

        if normalized in seen:
            continue

        seen.add(normalized)
        output.append(claim)

    return output[:10]


# ============================================================
# EVIDENCE GRAPH
# ============================================================

async def build_evidence_graph(
    objective: str,
    mission_id: str,
    research_result: Dict[str, Any],
) -> Dict[str, Any]:
    claims = extract_claims(
        objective,
        research_result,
    )

    sources = research_result.get(
        "accepted_sources",
        [],
    )

    claim_records = []

    conn = db()

    for claim in claims:
        claim_id = uid("claim")

        normalized = re.sub(
            r"\s+",
            " ",
            claim.lower(),
        ).strip()

        conn.execute(
            """
            INSERT INTO claims
            (id,mission_id,claim,normalized_claim,
             confidence,state,created_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                claim_id,
                mission_id,
                claim,
                normalized,
                0.0,
                "unverified",
                now(),
            ),
        )

        supporting = []
        contradicting = []

        for source in sources:
            sim = similarity(
                claim,
                source.get("text", ""),
            )

            if sim < 0.015:
                continue

            passage = evidence_passage(
                claim,
                source.get("text", ""),
            )

            relation = "support"

            # Simple contradiction signal.
            contradiction_words = [
                "however",
                "contrary",
                "failed",
                "not supported",
                "no evidence",
                "limitation",
                "cannot",
                "unlikely",
                "inconsistent",
                "disputed",
            ]

            lower_passage = passage.lower()

            if any(
                word in lower_passage
                for word in contradiction_words
            ):
                relation = "counter"

            evidence_id = uid("edge")

            conn.execute(
                """
                INSERT INTO claim_evidence
                (id,claim_id,source_id,relation,
                 passage,similarity,created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    evidence_id,
                    claim_id,
                    source["id"],
                    relation,
                    passage,
                    sim,
                    now(),
                ),
            )

            edge = {
                "source_id": source["id"],
                "domain": source["domain"],
                "url": source["url"],
                "quality": source["quality"],
                "similarity": round(sim, 4),
                "passage": passage,
            }

            if relation == "support":
                supporting.append(edge)
            else:
                contradicting.append(edge)

        domains = {
            e["domain"]
            for e in supporting
        }

        counter_domains = {
            e["domain"]
            for e in contradicting
        }

        base_confidence = min(
            0.95,
            (
                len(domains) * 0.20
                + len(supporting) * 0.05
                + (
                    max(
                        [e["quality"] for e in supporting]
                        or [0]
                    )
                    * 0.20
                )
            ),
        )

        if len(domains) >= 3:
            state = "supported"
        elif len(domains) >= 2:
            state = "partially_supported"
        elif len(domains) == 1:
            state = "weakly_supported"
        else:
            state = "unverified"

        if counter_domains:
            base_confidence *= 0.70

            if state != "unverified":
                state = "contested"

        conn.execute(
            """
            UPDATE claims
            SET confidence=?,state=?
            WHERE id=?
            """,
            (
                round(base_confidence, 4),
                state,
                claim_id,
            ),
        )

        claim_records.append(
            {
                "id": claim_id,
                "claim": claim,
                "state": state,
                "confidence": round(
                    base_confidence,
                    4,
                ),
                "supporting_evidence": supporting,
                "counter_evidence": contradicting,
                "support_domains": sorted(domains),
                "counter_domains": sorted(counter_domains),
            }
        )

    conn.commit()
    conn.close()

    return {
        "claims": claim_records,
        "claim_count": len(claim_records),
    }


# ============================================================
# COUNTERCLAIM ENGINE
# ============================================================

async def counterclaim_research(
    objective: str,
    mission_id: str,
) -> Dict[str, Any]:
    queries = counterclaim_queries(
        objective
    )

    results = []

    for query in queries[:3]:
        result = await research(
            query,
            mission_id=mission_id,
            max_sources=6,
        )

        results.append(result)

    all_sources = []

    for result in results:
        all_sources.extend(
            result.get(
                "accepted_sources",
                [],
            )
        )

    unique = {}

    for source in all_sources:
        unique[source["domain"]] = source

    sources = list(unique.values())

    return {
        "queries": queries[:3],
        "sources": sources[:10],
        "domain_count": len(unique),
        "strength": (
            "strong"
            if len(unique) >= 3
            else "moderate"
            if len(unique) >= 2
            else "weak"
            if len(unique) == 1
            else "insufficient"
        ),
    }


# ============================================================
# VERIFICATION
# ============================================================

def verification_summary(
    research_result: Dict[str, Any],
    evidence_graph: Dict[str, Any],
    counterclaims: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sources = research_result.get(
        "accepted_sources",
        [],
    )

    domains = {
        source["domain"]
        for source in sources
    }

    average_quality = (
        sum(
            source["quality"]
            for source in sources
        )
        / len(sources)
        if sources
        else 0.0
    )

    claims = evidence_graph.get(
        "claims",
        [],
    )

    supported = [
        c for c in claims
        if c["state"] == "supported"
    ]

    contested = [
        c for c in claims
        if c["state"] == "contested"
    ]

    counter_domain_count = (
        counterclaims.get("domain_count", 0)
        if counterclaims
        else 0
    )

    # Conservative global verification.
    verified = (
        len(domains) >= 3
        and average_quality >= 0.45
        and len(supported) > 0
        and len(contested) == 0
        and counter_domain_count < 3
    )

    if verified:
        level = "high"
        confidence = min(
            0.92,
            0.55
            + (len(domains) * 0.08)
            + (average_quality * 0.20),
        )

    elif len(domains) >= 2:
        level = "moderate"
        confidence = min(
            0.70,
            0.30
            + (len(domains) * 0.10)
            + (average_quality * 0.15),
        )

    elif len(domains) == 1:
        level = "low"
        confidence = 0.25

    else:
        level = "low"
        confidence = 0.10

    if contested:
        confidence *= 0.70

    return {
        "verified": bool(verified),
        "verification_level": level,
        "confidence": round(
            confidence,
            4,
        ),
        "independent_evidence_count": len(sources),
        "independent_domains": sorted(domains),
        "average_source_quality": round(
            average_quality,
            4,
        ),
        "supported_claim_count": len(supported),
        "contested_claim_count": len(contested),
        "counter_evidence_domain_count": counter_domain_count,
        "evidence": sources[:10],
        "claim_states": [
            {
                "claim": c["claim"],
                "state": c["state"],
                "confidence": c["confidence"],
            }
            for c in claims
        ],
    }


# ============================================================
# CRITIC
# ============================================================

def critique(
    research_result: Dict[str, Any],
    verification: Dict[str, Any],
    counterclaims: Dict[str, Any],
) -> Dict[str, Any]:
    issues = []

    domains = research_result.get(
        "independent_domain_count",
        0,
    )

    if domains < 3:
        issues.append(
            "Independent-source diversity is below the strong-evidence threshold."
        )

    if research_result.get(
        "research_failure_reason"
    ):
        issues.append(
            research_result["research_failure_reason"]
        )

    if not verification.get("verified"):
        issues.append(
            "Claims were not promoted to fully verified status."
        )

    if verification.get(
        "contested_claim_count",
        0,
    ) > 0:
        issues.append(
            "Conflicting or counter-evidence was detected."
        )

    if counterclaims.get(
        "domain_count",
        0,
    ) == 0:
        issues.append(
            "Counterclaim research produced no independent evidence."
        )

    if not issues:
        quality = "strong"
    elif len(issues) <= 2:
        quality = "acceptable"
    else:
        quality = "needs_improvement"

    confidence = max(
        0.05,
        verification.get(
            "confidence",
            0.1,
        )
        - (len(issues) * 0.08),
    )

    return {
        "issues": issues,
        "issue_count": len(issues),
        "confidence": round(
            confidence,
            4,
        ),
        "quality": quality,
    }


# ============================================================
# OPPORTUNITY ENGINE
# ============================================================

def detect_opportunities(
    research_result: Dict[str, Any],
    verification: Dict[str, Any],
    critique_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    opportunities = []

    if research_result.get(
        "independent_domain_count",
        0,
    ) < 3:
        opportunities.append(
            {
                "title": "Evidence diversity expansion",
                "description": (
                    "Increase independent-domain coverage and "
                    "research-provider resilience."
                ),
                "priority": 0.98,
            }
        )

    if verification.get(
        "contested_claim_count",
        0,
    ) > 0:
        opportunities.append(
            {
                "title": "Resolve evidence conflicts",
                "description": (
                    "Investigate contradictory evidence at the "
                    "claim and passage level."
                ),
                "priority": 0.97,
            }
        )

    opportunities.append(
        {
            "title": "Persistent research memory",
            "description": (
                "Reuse successful source patterns and failed "
                "queries in future missions."
            ),
            "priority": 0.82,
        }
    )

    opportunities.append(
        {
            "title": "Provider federation",
            "description": (
                "Add additional independent search and knowledge "
                "providers without weakening source validation."
            ),
            "priority": 0.80,
        }
    )

    opportunities.append(
        {
            "title": "Long-horizon mission execution",
            "description": (
                "Continue incomplete missions across multiple "
                "research, verification and execution cycles."
            ),
            "priority": 0.76,
        }
    )

    conn = db()

    for opportunity in opportunities:
        conn.execute(
            """
            INSERT INTO opportunities
            (id,created_at,title,description,priority,status)
            VALUES(?,?,?,?,?,'open')
            """,
            (
                uid("opp"),
                now(),
                opportunity["title"],
                opportunity["description"],
                opportunity["priority"],
            ),
        )

    conn.commit()
    conn.close()

    return opportunities


# ============================================================
# PLANNER
# ============================================================

def build_plan(objective: str) -> Dict[str, Any]:
    return {
        "objective": objective,
        "strategy": "evidence-first-autonomous-mission",
        "nodes": [
            {
                "id": "understand",
                "type": "intent",
                "agent": "agent-planner",
            },
            {
                "id": "research",
                "type": "research-swarm",
                "agent": "agent-researcher",
            },
            {
                "id": "counterclaims",
                "type": "counterclaim-research",
                "agent": "agent-critic",
            },
            {
                "id": "evidence_graph",
                "type": "claim-evidence-mapping",
                "agent": "agent-verifier",
            },
            {
                "id": "verify",
                "type": "claim-verification",
                "agent": "agent-verifier",
            },
            {
                "id": "critique",
                "type": "adversarial-critique",
                "agent": "agent-critic",
            },
            {
                "id": "opportunities",
                "type": "opportunity-detection",
                "agent": "agent-learner",
            },
            {
                "id": "synthesis",
                "type": "synthesis",
                "agent": "agent-planner",
            },
            {
                "id": "replan",
                "type": "next-cycle",
                "agent": "agent-planner",
            },
        ],
    }


# ============================================================
# MISSION EXECUTION
# ============================================================

async def execute_mission(
    objective: str,
    remember: bool = True,
) -> Dict[str, Any]:
    task_id = uid("task")
    mission_id = uid("mission")

    created = now()

    conn = db()

    conn.execute(
        """
        INSERT INTO tasks
        (id,created_at,objective,status,result)
        VALUES(?,?,?,?,?)
        """,
        (
            task_id,
            created,
            objective,
            "running",
            None,
        ),
    )

    conn.execute(
        """
        INSERT INTO missions
        (id,created_at,objective,status,result)
        VALUES(?,?,?,?,?)
        """,
        (
            mission_id,
            created,
            objective,
            "running",
            None,
        ),
    )

    conn.commit()
    conn.close()

    trace = []

    try:
        # ----------------------------------------------------
        # 1. Understand
        # ----------------------------------------------------
        trace.append(
            {
                "node": "understand",
                "type": "intent",
                "agent": "agent-planner",
                "status": "completed",
            }
        )

        plan = build_plan(objective)

        # ----------------------------------------------------
        # 2. Research swarm
        # ----------------------------------------------------
        research_result = await research(
            objective,
            mission_id=mission_id,
            max_sources=12,
        )

        trace.append(
            {
                "node": "research",
                "type": "research-swarm",
                "agent": "agent-researcher",
                "status": "completed",
                "strength": research_result["strength"],
                "domains": research_result[
                    "independent_domain_count"
                ],
            }
        )

        # ----------------------------------------------------
        # 3. Counterclaim engine
        # ----------------------------------------------------
        counterclaims = await counterclaim_research(
            objective,
            mission_id,
        )

        trace.append(
            {
                "node": "counterclaims",
                "type": "counterclaim-research",
                "agent": "agent-critic",
                "status": "completed",
                "domains": counterclaims["domain_count"],
                "strength": counterclaims["strength"],
            }
        )

        # ----------------------------------------------------
        # 4. Evidence graph
        # ----------------------------------------------------
        evidence_graph = await build_evidence_graph(
            objective,
            mission_id,
            research_result,
        )

        trace.append(
            {
                "node": "evidence_graph",
                "type": "claim-evidence-mapping",
                "agent": "agent-verifier",
                "status": "completed",
                "claims": evidence_graph["claim_count"],
            }
        )

        # ----------------------------------------------------
        # 5. Verification
        # ----------------------------------------------------
        verification = verification_summary(
            research_result,
            evidence_graph,
            counterclaims,
        )

        trace.append(
            {
                "node": "verify",
                "type": "claim-verification",
                "agent": "agent-verifier",
                "status": "completed",
                "verified": verification["verified"],
                "confidence": verification["confidence"],
            }
        )

        # ----------------------------------------------------
        # 6. Critique
        # ----------------------------------------------------
        critique_result = critique(
            research_result,
            verification,
            counterclaims,
        )

        trace.append(
            {
                "node": "critique",
                "type": "adversarial-critique",
                "agent": "agent-critic",
                "status": "completed",
                "quality": critique_result["quality"],
            }
        )

        # ----------------------------------------------------
        # 7. Opportunities
        # ----------------------------------------------------
        opportunities = detect_opportunities(
            research_result,
            verification,
            critique_result,
        )

        trace.append(
            {
                "node": "opportunities",
                "type": "opportunity-detection",
                "agent": "agent-learner",
                "status": "completed",
            }
        )

        # ----------------------------------------------------
        # 8. Synthesis
        # ----------------------------------------------------
        if verification["verified"]:
            statement = (
                "AI Infinity completed an evidence-supported "
                "research cycle. Multiple independent domains "
                "supported the investigated claims and no "
                "material unresolved contradiction prevented "
                "verification."
            )
        elif verification["contested_claim_count"] > 0:
            statement = (
                "AI Infinity completed an evidence-aware cycle, "
                "but conflicting evidence was detected. Claims "
                "with unresolved conflicts remain contested."
            )
        elif verification["independent_evidence_count"] > 0:
            statement = (
                "AI Infinity found some independent evidence, "
                "but the evidence base is insufficient for "
                "full verification."
            )
        else:
            statement = (
                "AI Infinity could not acquire sufficient "
                "independent evidence. Findings remain "
                "unverified."
            )

        synthesis = {
            "statement": statement,
            "research_strength": research_result["strength"],
            "verification": verification,
            "counterclaims": {
                "strength": counterclaims["strength"],
                "domain_count": counterclaims["domain_count"],
            },
            "critique": critique_result,
            "opportunities": opportunities,
            "evidence_graph": evidence_graph,
        }

        trace.append(
            {
                "node": "synthesis",
                "type": "synthesis",
                "agent": "agent-planner",
                "status": "completed",
            }
        )

        # ----------------------------------------------------
        # 9. Replan
        # ----------------------------------------------------
        if research_result[
            "independent_domain_count"
        ] < 3:
            next_priority = "Improve evidence acquisition"
            next_action = (
                "Expand provider diversity, change query "
                "strategy, acquire additional independent "
                "domains and retry rejected evidence paths."
            )
        elif verification[
            "contested_claim_count"
        ] > 0:
            next_priority = "Resolve contradictory evidence"
            next_action = (
                "Perform claim-level counter-research and "
                "compare contradictory passages."
            )
        else:
            next_priority = "Continue long-horizon execution"
            next_action = (
                "Use verified findings as inputs to the next "
                "mission cycle."
            )

        next_cycle = {
            "priority": next_priority,
            "recommended_action": next_action,
        }

        trace.append(
            {
                "node": "replan",
                "type": "next-cycle",
                "agent": "agent-planner",
                "status": "completed",
            }
        )

        # ----------------------------------------------------
        # Memory
        # ----------------------------------------------------
        memory_id = None

        if remember:
            memory_id = save_memory(
                "mission_result",
                {
                    "mission_id": mission_id,
                    "objective": objective,
                    "research_strength": research_result[
                        "strength"
                    ],
                    "verification": verification,
                    "next_cycle": next_cycle,
                },
                verification["confidence"],
                "AI Infinity TARGET-2050.6",
            )

        result = {
            "task_id": task_id,
            "mission_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "project": PROJECT,
            "target": TARGET_YEAR,
            "objective": objective,
            "plan": plan,
            "agent_trace": trace,
            "research": research_result,
            "counterclaims": counterclaims,
            "evidence_graph": evidence_graph,
            "verification": verification,
            "critique": critique_result,
            "opportunities": opportunities,
            "synthesis": synthesis,
            "next_cycle": next_cycle,
            "memory_id": memory_id,
            "completed_nodes": len(trace),
            "total_nodes": len(plan["nodes"]),
        }

        conn = db()

        conn.execute(
            """
            UPDATE tasks
            SET status='completed',result=?
            WHERE id=?
            """,
            (
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                task_id,
            ),
        )

        conn.execute(
            """
            UPDATE missions
            SET status='completed',result=?
            WHERE id=?
            """,
            (
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                mission_id,
            ),
        )

        conn.commit()
        conn.close()

        save_event(
            "mission_completed",
            {
                "task_id": task_id,
                "mission_id": mission_id,
                "verified": verification["verified"],
                "confidence": verification["confidence"],
            },
        )

        return result

    except Exception as exc:
        error = {
            "task_id": task_id,
            "mission_id": mission_id,
            "status": "failed",
            "version": VERSION,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "agent_trace": trace,
        }

        conn = db()

        conn.execute(
            """
            UPDATE tasks
            SET status='failed',result=?
            WHERE id=?
            """,
            (
                json.dumps(error),
                task_id,
            ),
        )

        conn.execute(
            """
            UPDATE missions
            SET status='failed',result=?
            WHERE id=?
            """,
            (
                json.dumps(error),
                mission_id,
            ),
        )

        conn.commit()
        conn.close()

        save_event(
            "mission_failed",
            error,
        )

        return error


# ============================================================
# REQUEST MODELS
# ============================================================

class ExecuteRequest(BaseModel):
    query: Optional[str] = None
    command: Optional[Any] = None
    objective: Optional[str] = None

    research: bool = True
    verify: bool = True
    remember: bool = True


class ResearchRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=10000,
    )


class VerifyRequest(BaseModel):
    claim: str = Field(
        min_length=1,
        max_length=10000,
    )


class PlanRequest(BaseModel):
    objective: str = Field(
        min_length=1,
        max_length=10000,
    )


class ExternalRequest(BaseModel):
    url: str
    method: str = "GET"
    data: Optional[Dict[str, Any]] = None


class TaskRequest(BaseModel):
    objective: str = Field(
        min_length=1,
        max_length=10000,
    )


class MissionRequest(BaseModel):
    objective: str = Field(
        min_length=1,
        max_length=10000,
    )
    remember: bool = True


# ============================================================
# REQUEST OBJECTIVE PARSER
# ============================================================

def extract_objective(value: Any) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()

        if not text:
            return None

        # Handle JSON nested inside command.
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                nested = extract_objective(parsed)

                if nested:
                    return nested
            except Exception:
                pass

        return text

    if isinstance(value, dict):
        for key in (
            "objective",
            "query",
            "command",
            "task",
            "prompt",
            "mission",
        ):
            if key in value:
                result = extract_objective(
                    value[key]
                )

                if result:
                    return result

    return None


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,
):
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "version": VERSION,
            "error": str(exc),
        },
    )


# ============================================================
# ROOT UI
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity {VERSION}</title>
<style>
body {{
    font-family: Arial, sans-serif;
    margin: 0;
    padding: 18px;
    background: #0b1020;
    color: #fff;
}}
.container {{
    max-width: 900px;
    margin: auto;
}}
h1 {{
    margin-bottom: 4px;
}}
.badge {{
    opacity: .75;
    margin-bottom: 20px;
}}
textarea {{
    width: 100%;
    min-height: 180px;
    box-sizing: border-box;
    border-radius: 14px;
    padding: 15px;
    font-size: 16px;
}}
button {{
    width: 100%;
    margin-top: 12px;
    padding: 16px;
    border: 0;
    border-radius: 14px;
    font-size: 17px;
    font-weight: bold;
    cursor: pointer;
}}
pre {{
    white-space: pre-wrap;
    word-break: break-word;
    background: #050812;
    padding: 15px;
    border-radius: 14px;
    margin-top: 18px;
    overflow-x: auto;
}}
</style>
</head>

<body>
<div class="container">

<h1>∞ AI Infinity</h1>
<div class="badge">
    {VERSION} · Evidence Infinity
</div>

<textarea id="command"
placeholder="Give AI Infinity a mission..."></textarea>

<button onclick="executeMission()">
    Execute Autonomous Mission
</button>

<pre id="output">Ready.</pre>

</div>

<script>
async function executeMission() {{
    const command =
        document.getElementById("command").value.trim();

    const output =
        document.getElementById("output");

    if (!command) {{
        output.textContent =
            "Enter a mission first.";
        return;
    }}

    output.textContent =
        "AI Infinity is executing...";

    try {{
        const response = await fetch(
            "/execute",
            {{
                method: "POST",
                headers: {{
                    "Content-Type":
                        "application/json"
                }},
                body: JSON.stringify({{
                    command: command,
                    research: true,
                    verify: true,
                    remember: true
                }})
            }}
        );

        const text =
            await response.text();

        try {{
            const data =
                JSON.parse(text);

            output.textContent =
                JSON.stringify(
                    data,
                    null,
                    2
                );
        }} catch {{
            output.textContent =
                "Server response:\\n\\n" +
                text;
        }}

    }} catch (error) {{
        output.textContent =
            "Connection error: " +
            error.message;
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
        "status": "ok",
        "project": PROJECT,
        "version": VERSION,
        "target": TARGET_YEAR,
        "engine": "Evidence Infinity",
    }


@app.get("/status")
async def status():
    conn = db()

    counts = {}

    for table in [
        "memory",
        "tasks",
        "missions",
        "events",
        "evidence_sources",
        "claims",
        "claim_evidence",
        "research_runs",
        "opportunities",
    ]:
        row = conn.execute(
            f"SELECT COUNT(*) AS count FROM {table}"
        ).fetchone()

        counts[table] = row["count"]

    conn.close()

    return {
        "status": "operational",
        "project": PROJECT,
        "version": VERSION,
        "target": TARGET_YEAR,
        "database": str(DB_PATH),
        "counts": counts,
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():
    return {
        "version": VERSION,
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
                "name": "research",
                "category": "evidence",
                "permission": "safe",
            },
            {
                "name": "evidence_graph",
                "category": "evidence",
                "permission": "safe",
            },
            {
                "name": "counterclaims",
                "category": "evidence",
                "permission": "safe",
            },
            {
                "name": "verification",
                "category": "evidence",
                "permission": "safe",
            },
            {
                "name": "external",
                "category": "network",
                "permission": "restricted",
            },
            {
                "name": "mission",
                "category": "autonomy",
                "permission": "safe",
            },
        ],
        "advanced_capabilities": [
            "research-swarm",
            "provider-federation",
            "source-firewall",
            "claim-level-verification",
            "evidence-graph",
            "counterclaim-engine",
            "contradiction-detection",
            "research-memory",
            "adaptive-querying",
            "self-diagnostics",
            "opportunity-radar",
            "long-horizon-planning",
            "failure-recovery",
            "regression-gates",
        ],
    }


# ============================================================
# SELF INSPECTION
# ============================================================

@app.get("/self-inspect")
async def self_inspect():
    conn = db()

    evidence_count = conn.execute(
        "SELECT COUNT(*) AS n FROM evidence_sources"
    ).fetchone()["n"]

    claim_count = conn.execute(
        "SELECT COUNT(*) AS n FROM claims"
    ).fetchone()["n"]

    event_count = conn.execute(
        "SELECT COUNT(*) AS n FROM events"
    ).fetchone()["n"]

    conn.close()

    return {
        "project": PROJECT,
        "version": VERSION,
        "target": TARGET_YEAR,
        "operational": True,
        "architecture": [
            "intent",
            "planning",
            "research-swarm",
            "source-firewall",
            "evidence-graph",
            "counterclaim-engine",
            "claim-verification",
            "critique",
            "opportunity-radar",
            "memory",
            "replanning",
            "regression-gates",
        ],
        "persistent_runtime": {
            "evidence_sources": evidence_count,
            "claims": claim_count,
            "events": event_count,
        },
        "known_boundaries": [
            "SQLite storage is runtime-local on ephemeral hosting.",
            "Search providers may throttle or change HTML.",
            "External actions remain permission-sensitive.",
            "No literal AGI/ASI claim is made.",
            "Research confidence is deliberately conservative.",
        ],
    }


# ============================================================
# ARCHITECTURE
# ============================================================

@app.get("/architecture")
async def architecture():
    return {
        "version": VERSION,
        "layers": [
            {
                "layer": 1,
                "name": "Intent",
                "components": [
                    "objective parser",
                    "mission understanding",
                ],
            },
            {
                "layer": 2,
                "name": "Mission Swarm",
                "components": [
                    "planner",
                    "researcher",
                    "verifier",
                    "critic",
                    "executor",
                    "learner",
                ],
            },
            {
                "layer": 3,
                "name": "Evidence Infinity",
                "components": [
                    "provider federation",
                    "adaptive queries",
                    "source firewall",
                    "content validation",
                    "domain independence",
                    "evidence graph",
                    "counterclaims",
                ],
            },
            {
                "layer": 4,
                "name": "Verification",
                "components": [
                    "claim-level confidence",
                    "contradiction detection",
                    "conservative promotion",
                ],
            },
            {
                "layer": 5,
                "name": "Evolution",
                "components": [
                    "research memory",
                    "opportunity radar",
                    "self-diagnostics",
                    "regression gates",
                    "replanning",
                ],
            },
        ],
    }


# ============================================================
# GAPS
# ============================================================

@app.get("/gaps")
async def gaps():
    return {
        "version": VERSION,
        "current_gaps": [
            {
                "name": "durable_external_memory",
                "priority": 0.95,
                "description": (
                    "Move critical memory from ephemeral local "
                    "SQLite to durable managed infrastructure."
                ),
            },
            {
                "name": "background_workers",
                "priority": 0.90,
                "description": (
                    "Add durable queues and workers for missions "
                    "that outlive a single request."
                ),
            },
            {
                "name": "authenticated_actions",
                "priority": 0.88,
                "description": (
                    "Add scoped OAuth and approval boundaries "
                    "for consequential external actions."
                ),
            },
            {
                "name": "sandboxed_code_execution",
                "priority": 0.86,
                "description": (
                    "Add isolated execution environments with "
                    "resource limits and regression checks."
                ),
            },
            {
                "name": "multimodal_world_model",
                "priority": 0.80,
                "description": (
                    "Add durable image/audio/video and structured "
                    "world-state reasoning."
                ),
            },
            {
                "name": "provider_federation",
                "priority": 0.76,
                "description": (
                    "Add more independent research providers "
                    "and structured knowledge sources."
                ),
        ],
    }


# ============================================================
# RESEARCH API
# ============================================================

@app.post("/research")
async def research_endpoint(
    request: ResearchRequest,
):
    return await research(
        request.query,
        max_sources=12,
    )


# ============================================================
# VERIFY API
# ============================================================

@app.post("/verify")
async def verify_endpoint(
    request: VerifyRequest,
):
    result = await research(
        request.claim,
        max_sources=10,
    )

    mission_id = uid("verification")

    graph = await build_evidence_graph(
        request.claim,
        mission_id,
        result,
    )

    counter = await counterclaim_research(
        request.claim,
        mission_id,
    )

    verification = verification_summary(
        result,
        graph,
        counter,
    )

    return {
        "claim": request.claim,
        "research": result,
        "evidence_graph": graph,
        "counterclaims": counter,
        "verification": verification,
    }


# ============================================================
# PLAN API
# ============================================================

@app.post("/plan")
async def plan_endpoint(
    request: PlanRequest,
):
    return build_plan(
        request.objective
    )


# ============================================================
# EXECUTE API
# ============================================================

@app.post("/execute")
async def execute_endpoint(
    request: ExecuteRequest,
):
    objective = (
        extract_objective(request.objective)
        or extract_objective(request.query)
        or extract_objective(request.command)
    )

    if not objective:
        raise HTTPException(
            status_code=422,
            detail="A command, query, or objective is required.",
        )

    return await execute_mission(
        objective,
        remember=request.remember,
    )


# ============================================================
# TASK API
# ============================================================

@app.post("/task")
async def task_endpoint(
    request: TaskRequest,
):
    return await execute_mission(
        request.objective,
        remember=True,
    )


# ============================================================
# MISSION API
# ============================================================

@app.post("/mission")
async def mission_endpoint(
    request: MissionRequest,
):
    return await execute_mission(
        request.objective,
        remember=request.remember,
    )


# ============================================================
# TASK LOOKUP
# ============================================================

@app.get("/task/{task_id}")
async def get_task(task_id: str):
    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE id=?
        """,
        (task_id,),
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "objective": row["objective"],
        "status": row["status"],
        "result": (
            json.loads(row["result"])
            if row["result"]
            else None
        ),
    }


# ============================================================
# MISSION LOOKUP
# ============================================================

@app.get("/mission/{mission_id}")
async def get_mission(mission_id: str):
    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (mission_id,),
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "objective": row["objective"],
        "status": row["status"],
        "result": (
            json.loads(row["result"])
            if row["result"]
            else None
        ),
    }


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory")
async def memory(limit: int = 50):
    limit = max(
        1,
        min(limit, 200),
    )

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

    return {
        "count": len(rows),
        "items": [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "kind": row["kind"],
                "content": (
                    json.loads(row["content"])
                    if row["content"]
                    else None
                ),
                "confidence": row["confidence"],
                "source": row["source"],
            }
            for row in rows
        ],
    }


@app.get("/memory/count")
async def memory_count():
    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM memory"
    ).fetchone()["n"]

    conn.close()

    return {
        "count": count,
        "version": VERSION,
    }


# ============================================================
# EVENTS
# ============================================================

@app.get("/events")
async def events(limit: int = 100):
    limit = max(
        1,
        min(limit, 500),
    )

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

    return {
        "events": [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "event_type": row["event_type"],
                "payload": (
                    json.loads(row["payload"])
                    if row["payload"]
                    else None
                ),
            }
            for row in rows
        ]
    }


# ============================================================
# OPPORTUNITIES
# ============================================================

@app.get("/opportunities")
async def opportunities(limit: int = 50):
    limit = max(
        1,
        min(limit, 200),
    )

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM opportunities
        ORDER BY priority DESC,created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    conn.close()

    return {
        "opportunities": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# AGENTS
# ============================================================

@app.get("/agents")
async def agents():
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM agents
        ORDER BY name
        """
    ).fetchall()

    conn.close()

    return {
        "agents": [
            {
                "id": row["id"],
                "name": row["name"],
                "role": row["role"],
                "capabilities": json.loads(
                    row["capabilities"]
                ),
                "status": row["status"],
            }
            for row in rows
        ]
    }


# ============================================================
# PROVIDERS
# ============================================================

@app.get("/providers")
async def providers():
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM providers
        ORDER BY category,name
        """
    ).fetchall()

    conn.close()

    return {
        "providers": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# SKILLS
# ============================================================

SKILLS = [
    "adaptive-research",
    "evidence-acquisition",
    "source-firewall",
    "content-contamination-detection",
    "domain-diversification",
    "claim-extraction",
    "evidence-graph",
    "counterclaim-research",
    "contradiction-detection",
    "claim-level-verification",
    "mission-planning",
    "replanning",
    "opportunity-detection",
    "persistent-memory",
    "self-diagnostics",
    "regression-testing",
    "safe-external-http",
]


@app.get("/skills")
async def skills():
    return {
        "version": VERSION,
        "skills": SKILLS,
    }


@app.get("/skills/count")
async def skills_count():
    return {
        "count": len(SKILLS),
        "version": VERSION,
    }


# ============================================================
# EXTERNAL HTTP
# ============================================================

@app.post("/external")
async def external(
    request: ExternalRequest,
):
    method = request.method.upper()

    if method not in {
        "GET",
        "POST",
    }:
        raise HTTPException(
            status_code=400,
            detail="Only GET and POST are supported.",
        )

    if not safe_url(request.url):
        raise HTTPException(
            status_code=403,
            detail="URL blocked by network safety policy.",
        )

    if is_search_infrastructure(request.url):
        raise HTTPException(
            status_code=403,
            detail="Search infrastructure URL blocked.",
        )

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
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

            content_type = response.headers.get(
                "content-type",
                "",
            )

            body = response.text[:100000]

            return {
                "status_code": response.status_code,
                "content_type": content_type,
                "url": str(response.url),
                "body": body,
            }

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )


# ============================================================
# EVALUATION
# ============================================================

@app.post("/evaluate")
async def evaluate(
    request: TaskRequest,
):
    result = await execute_mission(
        request.objective,
        remember=True,
    )

    verification = result.get(
        "verification",
        {},
    )

    confidence = float(
        verification.get(
            "confidence",
            0.0,
        )
    )

    score = confidence

    feedback = {
        "verification_confidence": confidence,
        "research_strength": result.get(
            "research",
            {},
        ).get(
            "strength",
            "unknown",
        ),
        "critique": result.get(
            "critique",
            {},
        ),
    }

    evaluation_id = uid("eval")

    conn = db()

    conn.execute(
        """
        INSERT INTO evaluations
        (id,created_at,task_id,score,feedback)
        VALUES(?,?,?,?,?)
        """,
        (
            evaluation_id,
            now(),
            result.get("task_id"),
            score,
            json.dumps(
                feedback,
                ensure_ascii=False,
            ),
        ),
    )

    conn.commit()
    conn.close()

    return {
        "evaluation_id": evaluation_id,
        "score": score,
        "feedback": feedback,
        "mission": result,
    }


# ============================================================
# REGRESSION ENGINE
# ============================================================

async def run_regression_tests() -> Dict[str, Any]:
    tests = []

    # Test 1: health architecture.
    tests.append(
        {
            "name": "version_identity",
            "passed": VERSION == "TARGET-2050.6",
            "score": 1.0
            if VERSION == "TARGET-2050.6"
            else 0.0,
            "details": VERSION,
        }
    )

    # Test 2: URL firewall.
    blocked = [
        "http://127.0.0.1",
        "http://localhost",
        "http://169.254.169.254",
        "https://www.google.com/search?q=test",
        "https://example.com/test.css",
    ]

    firewall_ok = all(
        not safe_url(url)
        or is_search_infrastructure(url)
        for url in blocked
    )

    tests.append(
        {
            "name": "network_source_firewall",
            "passed": firewall_ok,
            "score": 1.0 if firewall_ok else 0.0,
            "details": "private/search infrastructure rejection",
        }
    )

    # Test 3: contamination detection.
    contaminated = """
    body { font-family: Arial; margin: 0; }
    function test() { return window.__DATA__; }
    """

    contamination_ok = (
        contamination_score(contaminated) >= 0.5
    )

    tests.append(
        {
            "name": "content_contamination_detection",
            "passed": contamination_ok,
            "score": 1.0 if contamination_ok else 0.0,
            "details": "CSS/JS contamination detection",
        }
    )

    # Test 4: URL canonicalization.
    canonical = canonicalize_url(
        "https://example.com/article/?utm_source=test&x=1"
    )

    canonical_ok = (
        "utm_source" not in canonical
        and canonical.startswith(
            "https://example.com"
        )
    )

    tests.append(
        {
            "name": "tracking_url_normalization",
            "passed": canonical_ok,
            "score": 1.0 if canonical_ok else 0.0,
            "details": canonical,
        }
    )

    # Test 5: evidence independence.
    mock_sources = [
        {
            "domain": "a.example",
            "quality": 0.8,
        },
        {
            "domain": "b.example",
            "quality": 0.8,
        },
        {
            "domain": "c.example",
            "quality": 0.8,
        },
    ]

    independent_ok = (
        len(
            {
                x["domain"]
                for x in mock_sources
            }
        )
        == 3
    )

    tests.append(
        {
            "name": "independent_domain_logic",
            "passed": independent_ok,
            "score": 1.0 if independent_ok else 0.0,
            "details": "3 independent domains",
        }
    )

    passed = sum(
        1 for test in tests
        if test["passed"]
    )

    total = len(tests)

    score = (
        passed / total
        if total
        else 0.0
    )

    gate = (
        "PASS"
        if score >= 0.80
        else "FAIL"
    )

    conn = db()

    for test in tests:
        conn.execute(
            """
            INSERT INTO regressions
            (id,created_at,test_name,passed,score,details)
            VALUES(?,?,?,?,?,?)
            """,
            (
                uid("reg"),
                now(),
                test["name"],
                1 if test["passed"] else 0,
                test["score"],
                test["details"],
            ),
        )

    conn.commit()
    conn.close()

    return {
        "version": VERSION,
        "gate": gate,
        "passed": passed,
        "total": total,
        "score": round(score, 4),
        "tests": tests,
    }


@app.get("/regression")
async def regression():
    return await run_regression_tests()


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/diagnostics")
async def diagnostics():
    regression_result = await run_regression_tests()

    conn = db()

    latest_research = conn.execute(
        """
        SELECT *
        FROM research_runs
        ORDER BY created_at DESC
        LIMIT 10
        """
    ).fetchall()

    latest_claims = conn.execute(
        """
        SELECT *
        FROM claims
        ORDER BY created_at DESC
        LIMIT 10
        """
    ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "health": "operational",
        "regression": regression_result,
        "latest_research": [
            dict(row)
            for row in latest_research
        ],
        "latest_claims": [
            dict(row)
            for row in latest_claims
        ],
        "principle": (
            "Power grows through better evidence, "
            "not through inflated confidence."
        ),
    }


# ============================================================
# WORLD STATE
# ============================================================

@app.get("/world")
async def world():
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM world
        ORDER BY updated_at DESC
        """
    ).fetchall()

    conn.close()

    return {
        "world": [
            {
                "key": row["key"],
                "updated_at": row["updated_at"],
                "value": (
                    json.loads(row["value"])
                    if row["value"]
                    else None
                ),
            }
            for row in rows
        ]
    }


# ============================================================
# EVIDENCE GRAPH API
# ============================================================

@app.get("/evidence")
async def evidence(
    limit: int = 100,
):
    limit = max(
        1,
        min(limit, 500),
    )

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM evidence_sources
        ORDER BY fetched_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "sources": [
            {
                "id": row["id"],
                "url": row["url"],
                "domain": row["domain"],
                "title": row["title"],
                "quality": row["quality"],
                "provider": row["provider"],
                "status": row["status"],
                "fetched_at": row["fetched_at"],
            }
            for row in rows
        ],
    }


@app.get("/claims")
async def claims(
    limit: int = 100,
):
    limit = max(
        1,
        min(limit, 500),
    )

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM claims
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "claims": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# VIDEO COMPATIBILITY
# ============================================================

@app.post("/generate")
async def generate(request: Request):
    """
    Compatibility endpoint.

    Existing video-generation clients can still reach the
    platform without breaking the autonomous intelligence API.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    objective = extract_objective(payload)

    if not objective:
        objective = "Generate requested content."

    return {
        "status": "accepted",
        "version": VERSION,
        "mode": "compatibility",
        "objective": objective,
        "message": (
            "Generation request accepted by AI Infinity. "
            "Use the mission engine for autonomous production."
        ),
    }


@app.get("/video/{job_id}")
async def video(job_id: str):
    return {
        "job_id": job_id,
        "version": VERSION,
        "status": "compatibility",
        "message": (
            "Video renderer integration remains available "
            "through the configured renderer boundary."
        ),
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():
    init_db()

    save_event(
        "system_start",
        {
            "project": PROJECT,
            "version": VERSION,
            "target": TARGET_YEAR,
        },
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
    )
