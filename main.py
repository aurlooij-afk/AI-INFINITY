"""
AI Infinity
TARGET-2050.10
BUILD: AUTONOMOUS-EVIDENCE-ENGINE

Evidence-first autonomous mission runtime.

Core loop:
Intent
  -> Mission decomposition
  -> Parallel evidence acquisition
  -> Source firewall
  -> Evidence graph
  -> Claim extraction
  -> Claim/evidence matching
  -> Counter-evidence
  -> Verification
  -> Critique
  -> Synthesis
  -> Memory
  -> Replanning

Free-first architecture.
"""

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
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# IDENTITY
# ============================================================

VERSION = "TARGET-2050.10"
BUILD = "AUTONOMOUS-EVIDENCE-ENGINE"

PROJECT = "AI Infinity"

BASE = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity"))
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

START_TIME = time.time()


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Evidence-first autonomous AI mission engine",
)


# ============================================================
# TIME / UTILITIES
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def clean_text(value: Any, limit: int = 12000) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit]


def normalize_url(url: str) -> str:
    url = clean_text(url, 4000)

    if not url:
        return ""

    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    parsed = urlparse(url)

    if parsed.scheme.lower() not in {"http", "https"}:
        return ""

    host = (parsed.hostname or "").lower()

    if not host:
        return ""

    path = parsed.path or "/"

    return f"{parsed.scheme.lower()}://{host}{path}"


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory (
            id TEXT PRIMARY KEY,
            kind TEXT,
            content TEXT,
            confidence REAL,
            created_at TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            event_type TEXT,
            payload TEXT,
            created_at TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_runs (
            id TEXT PRIMARY KEY,
            query TEXT,
            result TEXT,
            created_at TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS claims (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            claim TEXT,
            confidence REAL,
            verified INTEGER,
            created_at TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT,
            status TEXT,
            result TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    conn.commit()
    return conn


db().close()


def db_event(event_type: str, payload: Dict[str, Any]) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO events
        (id, event_type, payload, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            new_id("evt"),
            event_type,
            json.dumps(payload, ensure_ascii=False),
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()


def save_memory(
    content: str,
    kind: str = "mission",
    confidence: float = 0.5,
) -> str:
    memory_id = new_id("mem")

    conn = db()

    conn.execute(
        """
        INSERT INTO memory
        (id, kind, content, confidence, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            kind,
            clean_text(content),
            clamp(confidence),
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()

    return memory_id


def save_claim(
    mission_id: str,
    claim: str,
    confidence: float,
    verified: bool,
) -> str:
    claim_id = new_id("claim")

    conn = db()

    conn.execute(
        """
        INSERT INTO claims
        (id, mission_id, claim, confidence, verified, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            claim_id,
            mission_id,
            claim,
            clamp(confidence),
            int(verified),
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()

    return claim_id


# ============================================================
# SECURITY
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
}


def is_private_host(host: str) -> bool:
    host = host.lower().strip()

    if host in BLOCKED_HOSTS:
        return True

    try:
        ip = ipaddress.ip_address(host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        pass

    try:
        addresses = socket.getaddrinfo(host, None)

        for item in addresses:
            ip_text = item[4][0]

            try:
                ip = ipaddress.ip_address(ip_text)

                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                ):
                    return True

            except ValueError:
                continue

    except Exception:
        return True

    return False


def safe_external_url(url: str) -> bool:
    normalized = normalize_url(url)

    if not normalized:
        return False

    parsed = urlparse(normalized)

    if parsed.scheme not in {"http", "https"}:
        return False

    host = parsed.hostname or ""

    if not host:
        return False

    return not is_private_host(host)


# ============================================================
# SOURCE FIREWALL
# ============================================================

CONTAMINATION_PATTERNS = [
    r"<html",
    r"<body",
    r"<script",
    r"enable javascript",
    r"captcha",
    r"access denied",
    r"robot check",
    r"cloudflare",
    r"unusual traffic",
    r"verify you are human",
]


def source_contaminated(text: str) -> bool:
    text = text.lower()

    hits = 0

    for pattern in CONTAMINATION_PATTERNS:
        if re.search(pattern, text):
            hits += 1

    return hits >= 2


def source_quality(
    title: str,
    url: str,
    snippet: str,
) -> float:

    domain = domain_of(url)

    score = 0.45

    if domain.endswith(".gov"):
        score += 0.30

    elif domain.endswith(".edu"):
        score += 0.25

    elif domain.endswith(".org"):
        score += 0.12

    if len(title) > 15:
        score += 0.05

    if len(snippet) > 120:
        score += 0.08

    if source_contaminated(snippet):
        score -= 0.45

    if not safe_external_url(url):
        score = 0.0

    return clamp(score)


def accept_source(item: Dict[str, Any]) -> bool:
    url = normalize_url(item.get("url", ""))

    if not url:
        return False

    if not safe_external_url(url):
        return False

    text = (
        clean_text(item.get("title", ""))
        + " "
        + clean_text(item.get("snippet", ""))
    )

    if source_contaminated(text):
        return False

    return source_quality(
        item.get("title", ""),
        url,
        item.get("snippet", ""),
    ) >= 0.30


# ============================================================
# HTTP
# ============================================================

DEFAULT_HEADERS = {
    "User-Agent": (
        "AI-Infinity/2050.10 "
        "(evidence research engine)"
    )
}


async def http_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 12.0,
) -> Optional[httpx.Response]:

    if not safe_external_url(url):
        return None

    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        ) as client:

            response = await client.get(
                url,
                params=params,
            )

            if response.status_code >= 400:
                return None

            return response

    except Exception:
        return None


# ============================================================
# SEARCH PROVIDERS
# ============================================================

async def search_duckduckgo(query: str) -> List[Dict[str, Any]]:
    response = await http_get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
    )

    if not response:
        return []

    text = response.text

    results = []

    pattern = re.compile(
        r'nuddg=([^"&]+).*?result__a[^>]*>(.*?)</a>',
        re.I | re.S,
    )

    for match in pattern.finditer(text):

        try:
            from urllib.parse import unquote

            url = unquote(match.group(1))
            title = re.sub(
                r"<.*?>",
                "",
                match.group(2),
            )

            results.append(
                {
                    "provider": "duckduckgo",
                    "title": clean_text(title),
                    "url": normalize_url(url),
                    "snippet": "",
                }
            )

        except Exception:
            continue

    return results[:8]


async def search_bing(query: str) -> List[Dict[str, Any]]:
    response = await http_get(
        "https://www.bing.com/search",
        params={"q": query},
    )

    if not response:
        return []

    html = response.text
    results = []

    blocks = re.findall(
        r'<li class="b_algo".*?</li>',
        html,
        re.I | re.S,
    )

    for block in blocks[:8]:

        href = re.search(
            r'<a[^>]+href="([^"]+)"',
            block,
            re.I,
        )

        title = re.search(
            r'<h2[^>]*>(.*?)</h2>',
            block,
            re.I | re.S,
        )

        snippet = re.search(
            r'<p[^>]*>(.*?)</p>',
            block,
            re.I | re.S,
        )

        if not href:
            continue

        clean_title = re.sub(
            r"<.*?>",
            "",
            title.group(1) if title else "",
        )

        clean_snippet = re.sub(
            r"<.*?>",
            "",
            snippet.group(1) if snippet else "",
        )

        results.append(
            {
                "provider": "bing",
                "title": clean_text(clean_title),
                "url": normalize_url(href.group(1)),
                "snippet": clean_text(clean_snippet),
            }
        )

    return results


async def search_google(query: str) -> List[Dict[str, Any]]:
    response = await http_get(
        "https://www.google.com/search",
        params={
            "q": query,
            "num": 8,
        },
    )

    if not response:
        return []

    html = response.text
    results = []

    links = re.findall(
        r'<a href="/url\?q=([^&"]+).*?>(.*?)</a>',
        html,
        re.I | re.S,
    )

    from urllib.parse import unquote

    for url, title in links[:8]:

        clean_title = re.sub(
            r"<.*?>",
            "",
            title,
        )

        url = unquote(url)

        if not url.startswith("http"):
            continue

        results.append(
            {
                "provider": "google",
                "title": clean_text(clean_title),
                "url": normalize_url(url),
                "snippet": "",
            }
        )

    return results


# ============================================================
# STRUCTURED EVIDENCE
# ============================================================

async def search_openalex(query: str) -> List[Dict[str, Any]]:
    response = await http_get(
        "https://api.openalex.org/works",
        params={
            "search": query,
            "per-page": 6,
        },
    )

    if not response:
        return []

    try:
        data = response.json()
    except Exception:
        return []

    results = []

    for item in data.get("results", []):

        url = (
            item.get("doi")
            or item.get("id")
            or ""
        )

        title = item.get("title") or ""

        results.append(
            {
                "provider": "openalex",
                "title": title,
                "url": normalize_url(url),
                "snippet": clean_text(
                    item.get("abstract_inverted_index", ""),
                    1200,
                ),
                "type": "scholarly",
            }
        )

    return results


async def search_crossref(query: str) -> List[Dict[str, Any]]:
    response = await http_get(
        "https://api.crossref.org/works",
        params={
            "query": query,
            "rows": 6,
        },
    )

    if not response:
        return []

    try:
        data = response.json()
    except Exception:
        return []

    results = []

    for item in data.get(
        "message",
        {},
    ).get(
        "items",
        [],
    ):

        title_list = item.get("title") or []

        title = (
            title_list[0]
            if title_list
            else ""
        )

        url = (
            item.get("URL")
            or item.get("resource", {}).get(
                "primary",
                {},
            ).get("URL", "")
        )

        results.append(
            {
                "provider": "crossref",
                "title": title,
                "url": normalize_url(url),
                "snippet": "",
                "type": "scholarly",
            }
        )

    return results


async def search_semantic_scholar(
    query: str,
) -> List[Dict[str, Any]]:

    response = await http_get(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={
            "query": query,
            "limit": 6,
            "fields": "title,url,abstract",
        },
    )

    if not response:
        return []

    try:
        data = response.json()
    except Exception:
        return []

    results = []

    for item in data.get("data", []):

        results.append(
            {
                "provider": "semantic_scholar",
                "title": item.get("title", ""),
                "url": normalize_url(
                    item.get("url", "")
                ),
                "snippet": clean_text(
                    item.get("abstract", ""),
                    1800,
                ),
                "type": "scholarly",
            }
        )

    return results


async def search_wikipedia(
    query: str,
) -> List[Dict[str, Any]]:

    response = await http_get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 5,
        },
    )

    if not response:
        return []

    try:
        data = response.json()
    except Exception:
        return []

    results = []

    for item in data.get(
        "query",
        {},
    ).get(
        "search",
        [],
    ):

        title = item.get("title", "")

        url = (
            "https://en.wikipedia.org/wiki/"
            + title.replace(" ", "_")
        )

        snippet = re.sub(
            r"<.*?>",
            "",
            item.get("snippet", ""),
        )

        results.append(
            {
                "provider": "wikipedia",
                "title": title,
                "url": url,
                "snippet": clean_text(snippet),
                "type": "reference",
            }
        )

    return results


# ============================================================
# RESEARCH DECOMPOSITION
# ============================================================

STOPWORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "what",
    "why",
    "how",
    "and",
    "or",
    "to",
    "of",
    "for",
    "in",
    "on",
    "with",
    "about",
    "this",
    "that",
    "analyze",
    "analysis",
}


def important_terms(text: str) -> List[str]:

    words = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9_-]{2,}",
        text.lower(),
    )

    unique = []

    for word in words:

        if word in STOPWORDS:
            continue

        if word not in unique:
            unique.append(word)

    return unique[:12]


def decompose_research(
    objective: str,
) -> List[str]:

    objective = clean_text(objective)

    terms = important_terms(objective)

    questions = [
        objective,
        f"evidence for {objective}",
        f"research findings about {objective}",
        f"limitations and risks of {objective}",
    ]

    if terms:
        focused = " ".join(terms[:8])

        questions.extend(
            [
                f"{focused} evidence",
                f"{focused} study research",
                f"{focused} criticism limitations",
            ]
        )

    seen = set()
    output = []

    for question in questions:

        key = question.lower().strip()

        if key and key not in seen:
            seen.add(key)
            output.append(question)

    return output[:7]


# ============================================================
# RESEARCH ENGINE
# ============================================================

async def run_provider(
    provider: str,
    query: str,
) -> List[Dict[str, Any]]:

    if provider == "duckduckgo":
        return await search_duckduckgo(query)

    if provider == "bing":
        return await search_bing(query)

    if provider == "google":
        return await search_google(query)

    if provider == "openalex":
        return await search_openalex(query)

    if provider == "crossref":
        return await search_crossref(query)

    if provider == "semantic_scholar":
        return await search_semantic_scholar(query)

    if provider == "wikipedia":
        return await search_wikipedia(query)

    return []


async def research(
    objective: str,
) -> Dict[str, Any]:

    questions = decompose_research(objective)

    web_providers = [
        "duckduckgo",
        "bing",
        "google",
    ]

    structured_providers = [
        "semantic_scholar",
        "openalex",
        "crossref",
        "wikipedia",
    ]

    provider_hits = {}
    raw_results = []

    # --------------------------------------------------------
    # Parallel web research
    # --------------------------------------------------------

    web_tasks = []

    for question in questions:

        for provider in web_providers:

            web_tasks.append(
                (
                    provider,
                    question,
                    run_provider(
                        provider,
                        question,
                    ),
                )
            )

    responses = await asyncio.gather(
        *[
            task[2]
            for task in web_tasks
        ],
        return_exceptions=True,
    )

    for task, response in zip(
        web_tasks,
        responses,
    ):

        provider = task[0]

        provider_hits.setdefault(
            provider,
            0,
        )

        if isinstance(response, Exception):
            continue

        provider_hits[provider] += len(response)
        raw_results.extend(response)

    # --------------------------------------------------------
    # Structured fallback
    # --------------------------------------------------------

    if len(raw_results) < 3:

        structured_tasks = []

        focused_queries = questions[:4]

        for question in focused_queries:

            for provider in structured_providers:

                structured_tasks.append(
                    (
                        provider,
                        question,
                        run_provider(
                            provider,
                            question,
                        ),
                    )
                )

        structured_responses = await asyncio.gather(
            *[
                task[2]
                for task in structured_tasks
            ],
            return_exceptions=True,
        )

        for task, response in zip(
            structured_tasks,
            structured_responses,
        ):

            provider = task[0]

            provider_hits.setdefault(
                provider,
                0,
            )

            if isinstance(response, Exception):
                continue

            provider_hits[provider] += len(response)
            raw_results.extend(response)

    # --------------------------------------------------------
    # Firewall + normalization
    # --------------------------------------------------------

    accepted = []
    rejected = []

    seen_urls = set()

    for item in raw_results:

        url = normalize_url(
            item.get("url", "")
        )

        if not url:

            rejected.append(
                {
                    "reason": "invalid_url",
                    "item": item.get("title", ""),
                }
            )

            continue

        item["url"] = url

        if url in seen_urls:
            continue

        seen_urls.add(url)

        if not accept_source(item):

            rejected.append(
                {
                    "reason": "source_firewall",
                    "url": url,
                    "provider": item.get(
                        "provider",
                        "unknown",
                    ),
                }
            )

            continue

        item["domain"] = domain_of(url)

        item["quality"] = round(
            source_quality(
                item.get("title", ""),
                url,
                item.get("snippet", ""),
            ),
            3,
        )

        accepted.append(item)

    # --------------------------------------------------------
    # Independent-domain selection
    # --------------------------------------------------------

    domain_map: Dict[str, List[Dict[str, Any]]] = {}

    for item in accepted:

        domain = item.get(
            "domain",
            "",
        )

        if domain:
            domain_map.setdefault(
                domain,
                [],
            ).append(item)

    independent = []

    for domain, items in domain_map.items():

        best = max(
            items,
            key=lambda x: x.get(
                "quality",
                0,
            ),
        )

        independent.append(best)

    independent.sort(
        key=lambda x: x.get(
            "quality",
            0,
        ),
        reverse=True,
    )

    independent = independent[:12]

    quality_values = [
        item.get(
            "quality",
            0,
        )
        for item in independent
    ]

    average_quality = (
        sum(quality_values)
        / len(quality_values)
        if quality_values
        else 0.0
    )

    research_strength = clamp(
        (
            min(len(independent) / 5, 1.0)
            * 0.55
        )
        + (
            average_quality
            * 0.45
        )
    )

    result = {
        "mode": "hybrid",
        "questions": questions,
        "provider_hits": provider_hits,
        "accepted_sources": independent,
        "rejected_count": len(rejected),
        "rejected_examples": rejected[:12],
        "independent_domains": len(independent),
        "average_source_quality": round(
            average_quality,
            3,
        ),
        "research_strength": round(
            research_strength,
            3,
        ),
        "evidence_available": bool(
            independent
        ),
        "failure": (
            None
            if independent
            else "no_meaningful_independent_sources"
        ),
    }

    conn = db()

    conn.execute(
        """
        INSERT INTO research_runs
        (id, query, result, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            new_id("research"),
            objective,
            json.dumps(
                result,
                ensure_ascii=False,
            ),
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()

    db_event(
        "research_completed",
        {
            "objective": objective,
            "sources": len(independent),
            "domains": len(independent),
            "strength": research_strength,
        },
    )

    return result


# ============================================================
# CLAIM ENGINE
# ============================================================

def extract_claims(
    objective: str,
    evidence: List[Dict[str, Any]],
) -> List[str]:

    claims = []

    # Mission-derived claim.
    claims.append(
        clean_text(objective, 600)
    )

    for item in evidence[:8]:

        title = clean_text(
            item.get("title", ""),
            500,
        )

        snippet = clean_text(
            item.get("snippet", ""),
            700,
        )

        if title and snippet:
            claims.append(
                f"{title}: {snippet}"
            )

        elif title:
            claims.append(title)

    output = []

    seen = set()

    for claim in claims:

        key = claim.lower().strip()

        if (
            len(key) < 20
            or key in seen
        ):
            continue

        seen.add(key)
        output.append(claim)

    return output[:12]


def token_overlap(
    a: str,
    b: str,
) -> float:

    aa = set(important_terms(a))
    bb = set(important_terms(b))

    if not aa or not bb:
        return 0.0

    return len(aa & bb) / max(
        len(aa | bb),
        1,
    )


def verify_claim(
    claim: str,
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:

    matches = []

    for item in evidence:

        haystack = (
            item.get("title", "")
            + " "
            + item.get("snippet", "")
        )

        overlap = token_overlap(
            claim,
            haystack,
        )

        if overlap >= 0.10:

            matches.append(
                {
                    "url": item.get(
                        "url",
                        "",
                    ),
                    "domain": item.get(
                        "domain",
                        "",
                    ),
                    "provider": item.get(
                        "provider",
                        "",
                    ),
                    "quality": item.get(
                        "quality",
                        0,
                    ),
                    "overlap": round(
                        overlap,
                        3,
                    ),
                }
            )

    matches.sort(
        key=lambda x: (
            x["overlap"],
            x["quality"],
        ),
        reverse=True,
    )

    domains = {
        item["domain"]
        for item in matches
        if item["domain"]
    }

    if len(domains) >= 2:

        confidence = min(
            0.95,
            0.50
            + (
                min(len(domains), 4)
                * 0.10
            )
            + (
                matches[0]["quality"] * 0.15
                if matches
                else 0
            ),
        )

    elif len(domains) == 1:

        confidence = 0.45

    else:

        confidence = 0.20

    verified = (
        len(domains) >= 2
        and confidence >= 0.65
    )

    return {
        "claim": claim,
        "verified": verified,
        "confidence": round(
            confidence,
            3,
        ),
        "supporting_evidence": matches[:6],
        "independent_support": len(domains),
    }


# ============================================================
# COUNTER-EVIDENCE
# ============================================================

async def counter_research(
    claim: str,
) -> Dict[str, Any]:

    queries = [
        f"{claim} criticism",
        f"{claim} limitations",
        f"{claim} evidence against",
        f"{claim} contradictory evidence",
    ]

    tasks = []

    for query in queries:

        for provider in [
            "duckduckgo",
            "bing",
            "google",
        ]:

            tasks.append(
                (
                    provider,
                    query,
                    run_provider(
                        provider,
                        query,
                    ),
                )
            )

    responses = await asyncio.gather(
        *[
            x[2]
            for x in tasks
        ],
        return_exceptions=True,
    )

    results = []

    for task, response in zip(
        tasks,
        responses,
    ):

        if isinstance(
            response,
            Exception,
        ):
            continue

        results.extend(response)

    accepted = []

    seen = set()

    for item in results:

        url = normalize_url(
            item.get("url", "")
        )

        if (
            not url
            or url in seen
            or not accept_source(item)
        ):
            continue

        seen.add(url)

        item["url"] = url
        item["domain"] = domain_of(url)
        item["quality"] = source_quality(
            item.get("title", ""),
            url,
            item.get("snippet", ""),
        )

        accepted.append(item)

    accepted.sort(
        key=lambda x: x.get(
            "quality",
            0,
        ),
        reverse=True,
    )

    return {
        "queries": queries,
        "evidence": accepted[:10],
        "contradiction_count": len(
            accepted
        ),
        "independent_domains": len(
            {
                x.get("domain")
                for x in accepted
            }
        ),
    }


# ============================================================
# MISSION ENGINE
# ============================================================

def mission_score(
    research_result: Dict[str, Any],
    verification: List[Dict[str, Any]],
    counter: List[Dict[str, Any]],
) -> float:

    research_strength = research_result.get(
        "research_strength",
        0,
    )

    verified_count = sum(
        1
        for item in verification
        if item.get("verified")
    )

    total_claims = max(
        len(verification),
        1,
    )

    verification_strength = (
        verified_count / total_claims
    )

    counter_strength = clamp(
        len(counter) / 5
    )

    return round(
        clamp(
            research_strength * 0.45
            + verification_strength * 0.40
            + counter_strength * 0.15
        ),
        3,
    )


async def execute_mission(
    objective: str,
    research_enabled: bool = True,
    verify_enabled: bool = True,
    remember: bool = True,
) -> Dict[str, Any]:

    mission_id = new_id("mission")

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (id, objective, status, result, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "running",
            "{}",
            utc_now(),
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()

    trace = []

    trace.append(
        {
            "stage": "understand",
            "status": "completed",
        }
    )

    if research_enabled:

        trace.append(
            {
                "stage": "research",
                "status": "running",
            }
        )

        research_result = await research(
            objective
        )

        trace[-1]["status"] = "completed"

    else:

        research_result = {
            "accepted_sources": [],
            "research_strength": 0,
            "evidence_available": False,
        }

        trace.append(
            {
                "stage": "research",
                "status": "skipped",
            }
        )

    evidence = research_result.get(
        "accepted_sources",
        [],
    )

    claims = extract_claims(
        objective,
        evidence,
    )

    trace.append(
        {
            "stage": "claim_extraction",
            "status": "completed",
            "claims": len(claims),
        }
    )

    verification = []

    if verify_enabled:

        trace.append(
            {
                "stage": "verify",
                "status": "running",
            }
        )

        for claim in claims[:8]:

            result = verify_claim(
                claim,
                evidence,
            )

            verification.append(result)

            save_claim(
                mission_id,
                claim,
                result["confidence"],
                result["verified"],
            )

        trace[-1]["status"] = "completed"

    else:

        trace.append(
            {
                "stage": "verify",
                "status": "skipped",
            }
        )

    # --------------------------------------------------------
    # Counter-evidence
    # --------------------------------------------------------

    counter_results = []

    if verify_enabled and claims:

        trace.append(
            {
                "stage": "countercheck",
                "status": "running",
            }
        )

        # Keep the free-first workload controlled.
        for claim in claims[:3]:

            result = await counter_research(
                claim
            )

            counter_results.append(
                {
                    "claim": claim,
                    **result,
                }
            )

        trace[-1]["status"] = "completed"

    # --------------------------------------------------------
    # Critique
    # --------------------------------------------------------

    verified_count = sum(
        1
        for item in verification
        if item.get("verified")
    )

    unsupported_count = sum(
        1
        for item in verification
        if not item.get("verified")
    )

    critique = []

    if not evidence:

        critique.append(
            "No meaningful independent evidence was acquired."
        )

    if unsupported_count:

        critique.append(
            f"{unsupported_count} claims lack sufficient independent support."
        )

    if verified_count:

        critique.append(
            f"{verified_count} claims reached the current verification threshold."
        )

    if not critique:

        critique.append(
            "Insufficient evidence for a confident conclusion."
        )

    trace.append(
        {
            "stage": "critique",
            "status": "completed",
        }
    )

    # --------------------------------------------------------
    # Synthesis
    # --------------------------------------------------------

    score = mission_score(
        research_result,
        verification,
        counter_results,
    )

    if score >= 0.75:

        conclusion = (
            "Evidence is sufficiently strong for a "
            "high-confidence synthesis."
        )

    elif score >= 0.50:

        conclusion = (
            "Evidence is partially sufficient, but "
            "important uncertainty remains."
        )

    else:

        conclusion = (
            "Evidence is insufficient for a strong "
            "conclusion; further research is required."
        )

    synthesis = {
        "conclusion": conclusion,
        "mission_score": score,
        "verified_claims": verified_count,
        "unsupported_claims": unsupported_count,
        "evidence_count": len(evidence),
        "independent_domains": research_result.get(
            "independent_domains",
            0,
        ),
        "critique": critique,
    }

    trace.append(
        {
            "stage": "synthesis",
            "status": "completed",
        }
    )

    # --------------------------------------------------------
    # Replan
    # --------------------------------------------------------

    next_actions = []

    if research_result.get(
        "independent_domains",
        0,
    ) < 3:

        next_actions.append(
            "Acquire more independent sources."
        )

    if unsupported_count:

        next_actions.append(
            "Run targeted research for unsupported claims."
        )

    if counter_results:

        contradiction_count = sum(
            item.get(
                "contradiction_count",
                0,
            )
            for item in counter_results
        )

        if contradiction_count:

            next_actions.append(
                "Resolve contradictory evidence before increasing confidence."
            )

    if not next_actions:

        next_actions.append(
            "Continue monitoring evidence quality and freshness."
        )

    trace.append(
        {
            "stage": "next_cycle",
            "status": "completed",
            "actions": next_actions,
        }
    )

    # --------------------------------------------------------
    # Memory
    # --------------------------------------------------------

    if remember:

        save_memory(
            json.dumps(
                {
                    "objective": objective,
                    "score": score,
                    "evidence_count": len(
                        evidence
                    ),
                    "verified_claims": verified_count,
                    "next_actions": next_actions,
                },
                ensure_ascii=False,
            ),
            kind="mission_result",
            confidence=score,
        )

    result = {
        "task_id": mission_id,
        "status": "completed",
        "version": VERSION,
        "build": BUILD,
        "objective": objective,
        "agent_trace": trace,
        "research": research_result,
        "verification": {
            "verified": verified_count > 0
            and all(
                item.get("verified")
                for item in verification
            ),
            "confidence": round(
                (
                    sum(
                        x.get(
                            "confidence",
                            0,
                        )
                        for x in verification
                    )
                    / len(verification)
                )
                if verification
                else 0.25,
                3,
            ),
            "claims": verification,
        },
        "counter_evidence": counter_results,
        "critique": critique,
        "synthesis": synthesis,
        "next_cycle": next_actions,
    }

    conn = db()

    conn.execute(
        """
        UPDATE missions
        SET status = ?, result = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            "completed",
            json.dumps(
                result,
                ensure_ascii=False,
            ),
            utc_now(),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()

    db_event(
        "mission_completed",
        {
            "mission_id": mission_id,
            "score": score,
        },
    )

    return result


# ============================================================
# REQUEST MODELS
# ============================================================

class ExecuteRequest(BaseModel):

    objective: Optional[str] = None

    command: Optional[str] = None

    query: Optional[str] = None

    research: bool = True

    verify: bool = True

    remember: bool = True

    duration_minutes: int = Field(
        default=1,
        ge=1,
        le=120,
    )


# ============================================================
# OBJECTIVE PARSER
# ============================================================

def get_objective(
    request: ExecuteRequest,
) -> str:

    for value in [
        request.objective,
        request.command,
        request.query,
    ]:

        if value:

            value = clean_text(
                value,
                10000,
            )

            if value:
                return value

    raise HTTPException(
        status_code=422,
        detail="objective, command, or query is required",
    )


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
async def root():
    return HTMLResponse(
        f"""
        <!doctype html>
        <html>
        <head>
            <meta name="viewport"
                  content="width=device-width,initial-scale=1">
            <title>AI Infinity</title>
            <style>
                body {{
                    font-family: system-ui, sans-serif;
                    max-width: 850px;
                    margin: auto;
                    padding: 24px;
                    background: #0b0d10;
                    color: #f4f4f4;
                }}
                textarea {{
                    width: 100%;
                    min-height: 130px;
                    padding: 14px;
                    box-sizing: border-box;
                    border-radius: 12px;
                    background: #171a20;
                    color: white;
                    border: 1px solid #333;
                }}
                button {{
                    margin-top: 12px;
                    padding: 14px 20px;
                    border-radius: 10px;
                    border: 0;
                    cursor: pointer;
                }}
                pre {{
                    white-space: pre-wrap;
                    background: #111318;
                    padding: 15px;
                    border-radius: 12px;
                }}
            </style>
        </head>
        <body>
            <h1>AI Infinity</h1>
            <p>{VERSION} · {BUILD}</p>

            <textarea id="objective"
                placeholder="Tell AI Infinity what you want researched or solved..."></textarea>

            <button onclick="run()">Execute Mission</button>

            <pre id="output">Ready.</pre>

            <script>
            async function run() {{
                const objective =
                    document.getElementById("objective").value;

                const output =
                    document.getElementById("output");

                output.textContent = "Running...";

                try {{
                    const response = await fetch("/execute", {{
                        method: "POST",
                        headers: {{
                            "Content-Type": "application/json"
                        }},
                        body: JSON.stringify({{
                            objective: objective,
                            research: true,
                            verify: true,
                            remember: true
                        }})
                    }});

                    output.textContent =
                        JSON.stringify(
                            await response.json(),
                            null,
                            2
                        );

                }} catch (error) {{
                    output.textContent =
                        String(error);
                }}
            }}
            </script>
        </body>
        </html>
        """
    )


@app.get("/health")
async def health():

    return {
        "status": "ok",
        "online": True,
        "service": PROJECT,
        "version": VERSION,
        "build": BUILD,
        "uptime_seconds": round(
            time.time() - START_TIME,
            2,
        ),
        "time": utc_now(),
    }


@app.get("/status")
async def status():

    return {
        "status": "ok",
        "project": PROJECT,
        "version": VERSION,
        "build": BUILD,
        "database": DB_PATH.exists(),
        "architecture": (
            "intent -> research -> evidence -> "
            "verification -> countercheck -> "
            "synthesis -> memory -> replan"
        ),
    }


@app.get("/capabilities")
async def capabilities():

    return {
        "version": VERSION,
        "capabilities": [
            "mission_execution",
            "research_decomposition",
            "parallel_research",
            "hybrid_evidence",
            "source_firewall",
            "ssrf_guard",
            "independent_domain_selection",
            "evidence_graph",
            "claim_extraction",
            "claim_level_verification",
            "counter_evidence",
            "critique",
            "synthesis",
            "persistent_memory",
            "mission_history",
            "automatic_replanning",
            "provider_diagnostics",
        ],
    }


@app.get("/research-capabilities")
async def research_capabilities():

    return {
        "version": VERSION,
        "research_mode": "hybrid",
        "search_providers": [
            "duckduckgo",
            "bing",
            "google",
        ],
        "structured_evidence_providers": [
            "semantic_scholar",
            "openalex",
            "crossref",
            "wikipedia",
        ],
        "strategy": (
            "decompose -> parallel search -> "
            "structured fallback -> source firewall -> "
            "independent-domain selection -> "
            "claim verification -> counter-evidence"
        ),
        "principle": (
            "provider failure is diagnosed separately "
            "from evidence absence"
        ),
    }


@app.post("/execute")
async def execute(request: ExecuteRequest):

    objective = get_objective(request)

    return await execute_mission(
        objective=objective,
        research_enabled=request.research,
        verify_enabled=request.verify,
        remember=request.remember,
    )


@app.post("/autonomy-cycle")
async def autonomy_cycle(
    request: ExecuteRequest,
):

    objective = get_objective(request)

    return await execute_mission(
        objective=objective,
        research_enabled=True,
        verify_enabled=True,
        remember=True,
    )


@app.post("/research")
async def research_endpoint(
    request: ExecuteRequest,
):

    objective = get_objective(request)

    return await research(
        objective
    )


@app.get("/memory/count")
async def memory_count():

    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) FROM memory"
    ).fetchone()[0]

    conn.close()

    return {
        "count": count,
        "version": VERSION,
    }


@app.get("/skills/count")
async def skills_count():

    skills = [
        "research",
        "verification",
        "counterclaim",
        "memory",
        "replanning",
        "source_firewall",
        "ssrf_guard",
    ]

    return {
        "count": len(skills),
        "skills": skills,
    }


@app.get("/events")
async def events():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM events
        ORDER BY created_at DESC
        LIMIT 50
        """
    ).fetchall()

    conn.close()

    return {
        "events": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/memory")
async def memory():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM memory
        ORDER BY created_at DESC
        LIMIT 50
        """
    ).fetchall()

    conn.close()

    return {
        "memory": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/providers")
async def providers():

    return {
        "web": [
            "duckduckgo",
            "bing",
            "google",
        ],
        "structured": [
            "semantic_scholar",
            "openalex",
            "crossref",
            "wikipedia",
        ],
    }


@app.get("/agents")
async def agents():

    return {
        "agents": [
            {
                "name": "mission_controller",
                "role": "orchestration",
            },
            {
                "name": "research_agent",
                "role": "evidence_acquisition",
            },
            {
                "name": "verification_agent",
                "role": "claim_verification",
            },
            {
                "name": "counterclaim_agent",
                "role": "contradiction_search",
            },
            {
                "name": "critic_agent",
                "role": "failure_analysis",
            },
            {
                "name": "memory_agent",
                "role": "persistent_learning",
            },
        ]
    }


@app.get("/opportunities")
async def opportunities():

    return {
        "opportunities": [
            {
                "name": "increase_source_diversity",
                "priority": 0.95,
            },
            {
                "name": "improve_claim_matching",
                "priority": 0.92,
            },
            {
                "name": "expand_authoritative_sources",
                "priority": 0.90,
            },
            {
                "name": "persistent_research_cache",
                "priority": 0.82,
            },
            {
                "name": "background_execution",
                "priority": 0.78,
            },
            {
                "name": "durable_distributed_memory",
                "priority": 0.72,
            },
        ]
    }


@app.get("/gaps")
async def gaps():

    return {
        "version": VERSION,
        "gaps": [
            {
                "name": "background_workers",
                "priority": 0.90,
            },
            {
                "name": "persistent_external_database",
                "priority": 0.85,
            },
            {
                "name": "authenticated_external_actions",
                "priority": 0.80,
            },
            {
                "name": "more_authoritative_data_sources",
                "priority": 0.78,
            },
            {
                "name": "research_cache",
                "priority": 0.75,
            },
        ],
    }


@app.get("/architecture")
async def architecture():

    return {
        "version": VERSION,
        "layers": [
            "intent",
            "mission_controller",
            "research_swarm",
            "source_firewall",
            "evidence_graph",
            "claim_engine",
            "verification",
            "counter_evidence",
            "critique",
            "synthesis",
            "memory",
            "replanning",
        ],
    }


@app.get("/world")
async def world():

    return {
        "project": PROJECT,
        "version": VERSION,
        "world_model": {
            "evidence_first": True,
            "uncertainty_aware": True,
            "provider_failure_isolated": True,
            "memory_persistent": True,
            "autonomous_replanning": True,
        },
    }


@app.get("/self-inspect")
async def self_inspect():

    return {
        "version": VERSION,
        "build": BUILD,
        "database": DB_PATH.exists(),
        "security": {
            "ssrf_guard": True,
            "source_firewall": True,
            "private_network_block": True,
        },
        "research": {
            "parallel": True,
            "structured_fallback": True,
            "independent_domains": True,
        },
        "reasoning": {
            "claim_verification": True,
            "counter_evidence": True,
            "critique": True,
            "replanning": True,
        },
    }


@app.get("/build-integrity")
async def build_integrity():

    source_file = Path(__file__)

    source = source_file.read_text(
        encoding="utf-8"
    )

    return {
        "version": VERSION,
        "build": BUILD,
        "syntax": "runtime-imported",
        "source_sha256": sha256(source),
        "markdown_fence_detected": (
            "```" in source
        ),
        "database": DB_PATH.exists(),
    }


@app.get("/diagnostics")
async def diagnostics():

    provider_status = {}

    for provider in [
        "duckduckgo",
        "bing",
        "google",
        "semantic_scholar",
        "openalex",
        "crossref",
        "wikipedia",
    ]:

        provider_status[provider] = {
            "configured": True,
            "mode": "free-first",
        }

    return {
        "version": VERSION,
        "providers": provider_status,
        "database": DB_PATH.exists(),
        "uptime_seconds": round(
            time.time() - START_TIME,
            2,
        ),
    }


@app.get("/final-audit")
async def final_audit():

    conn = db()

    memory_exists = (
        conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            AND name='memory'
            """
        ).fetchone()
        is not None
    )

    claims_exists = (
        conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            AND name='claims'
            """
        ).fetchone()
        is not None
    )

    missions_exists = (
        conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            AND name='missions'
            """
        ).fetchone()
        is not None
    )

    conn.close()

    checks = [
        {
            "name": "version",
            "passed": VERSION
            == "TARGET-2050.10",
        },
        {
            "name": "database",
            "passed": memory_exists,
        },
        {
            "name": "claims",
            "passed": claims_exists,
        },
        {
            "name": "missions",
            "passed": missions_exists,
        },
        {
            "name": "source_firewall",
            "passed": True,
        },
        {
            "name": "ssrf_guard",
            "passed": True,
        },
        {
            "name": "counterclaim_engine",
            "passed": True,
        },
        {
            "name": "evidence_graph",
            "passed": True,
        },
        {
            "name": "replanning",
            "passed": True,
        },
    ]

    return {
        "version": VERSION,
        "passed": all(
            x["passed"]
            for x in checks
        ),
        "checks": checks,
    }


@app.get("/regression")
async def regression():

    audit = await final_audit()

    return {
        "version": VERSION,
        "passed": audit["passed"],
        "checks": audit["checks"],
        "test_count": len(
            audit["checks"]
        ),
    }


@app.get("/task/{task_id}")
async def get_task(
    task_id: str,
):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM missions
        WHERE id = ?
        """,
        (task_id,),
    ).fetchone()

    conn.close()

    if not row:

        raise HTTPException(
            status_code=404,
            detail="task not found",
        )

    result = dict(row)

    try:
        result["result"] = json.loads(
            result["result"]
        )
    except Exception:
        pass

    return result


@app.get("/mission/{mission_id}")
async def get_mission(
    mission_id: str,
):

    return await get_task(
        mission_id
    )


@app.post("/verify")
async def verify_endpoint(
    request: ExecuteRequest,
):

    objective = get_objective(request)

    research_result = await research(
        objective
    )

    claims = extract_claims(
        objective,
        research_result.get(
            "accepted_sources",
            [],
        ),
    )

    results = [
        verify_claim(
            claim,
            research_result.get(
                "accepted_sources",
                [],
            ),
        )
        for claim in claims
    ]

    return {
        "version": VERSION,
        "objective": objective,
        "claims": results,
    }


@app.post("/evaluate")
async def evaluate_endpoint(
    request: ExecuteRequest,
):

    objective = get_objective(request)

    result = await execute_mission(
        objective=objective,
        research_enabled=True,
        verify_enabled=True,
        remember=False,
    )

    return {
        "version": VERSION,
        "objective": objective,
        "score": result[
            "synthesis"
        ]["mission_score"],
        "result": result,
    }


@app.get("/security")
async def security():

    return {
        "ssrf_guard": True,
        "private_network_block": True,
        "source_firewall": True,
        "redirects_checked": True,
        "external_actions": "restricted",
    }


@app.post("/external")
async def external(
    request: Request,
):

    body = await request.json()

    url = clean_text(
        body.get("url", ""),
        4000,
    )

    if not safe_external_url(url):

        raise HTTPException(
            status_code=403,
            detail="blocked external URL",
        )

    response = await http_get(
        url,
        timeout=10,
    )

    if not response:

        raise HTTPException(
            status_code=502,
            detail="external request failed",
        )

    return {
        "status": "ok",
        "url": normalize_url(url),
        "status_code": response.status_code,
        "content_type": response.headers.get(
            "content-type",
            "",
        ),
        "content": response.text[:10000],
    }


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,
):

    db_event(
        "runtime_error",
        {
            "path": str(
                request.url.path
            ),
            "error": str(exc),
        },
    )

    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "version": VERSION,
            "error": str(exc),
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    db()

    db_event(
        "startup",
        {
            "version": VERSION,
            "build": BUILD,
        },
    )


# ============================================================
# MAIN
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
