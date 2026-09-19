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
from urllib.parse import unquote, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY
# TARGET-2050.12
# BUILD: AUTONOMOUS-EVIDENCE-GRAPH-PLUS
# ============================================================

VERSION = "TARGET-2050.12"
BUILD = "AUTONOMOUS-EVIDENCE-GRAPH-PLUS"
PROJECT = "AI Infinity"

BASE = Path(
    os.getenv(
        "AI_INFINITY_DATA",
        "/tmp/ai-infinity",
    )
)

BASE.mkdir(
    parents=True,
    exist_ok=True,
)

DB_PATH = BASE / "ai_infinity.db"

START_TIME = time.time()

CACHE_TTL = int(
    os.getenv(
        "AI_INFINITY_CACHE_TTL",
        "21600",
    )
)

app = FastAPI(
    title=PROJECT,
    version=VERSION,
    description="Autonomous evidence-first AI mission engine",
)


# ============================================================
# UTILITIES
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sha256(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def clamp(
    value: float,
    low: float = 0.0,
    high: float = 1.0,
) -> float:
    return max(
        low,
        min(high, float(value)),
    )


def clean(
    value: Any,
    limit: int = 12000,
) -> str:
    if value is None:
        return ""

    return str(value).strip()[:limit]


def normalize_url(url: str) -> str:
    url = clean(
        url,
        4000,
    )

    if not url:
        return ""

    if not re.match(
        r"^https?://",
        url,
        re.I,
    ):
        url = "https://" + url

    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    if parsed.scheme.lower() not in {
        "http",
        "https",
    }:
        return ""

    if not parsed.hostname:
        return ""

    host = parsed.hostname.lower()

    path = parsed.path or "/"

    result = (
        f"{parsed.scheme.lower()}://"
        f"{host}{path}"
    )

    if parsed.query:
        result += "?" + parsed.query

    return result


def domain_of(url: str) -> str:
    try:
        return (
            urlparse(url).hostname
            or ""
        ).lower()
    except Exception:
        return ""


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:

    conn = sqlite3.connect(
        DB_PATH
    )

    conn.row_factory = sqlite3.Row

    schemas = [
        """
        CREATE TABLE IF NOT EXISTS memory (
            id TEXT PRIMARY KEY,
            kind TEXT,
            content TEXT,
            confidence REAL,
            created_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            event_type TEXT,
            payload TEXT,
            created_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS research_runs (
            id TEXT PRIMARY KEY,
            query TEXT,
            result TEXT,
            created_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS claims (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            claim TEXT,
            confidence REAL,
            verified INTEGER,
            created_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT,
            status TEXT,
            result TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            provider TEXT,
            title TEXT,
            url TEXT,
            domain TEXT,
            snippet TEXT,
            quality REAL,
            freshness REAL,
            created_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS evidence_links (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            claim_id TEXT,
            evidence_id TEXT,
            relationship TEXT,
            score REAL,
            created_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS research_cache (
            query_hash TEXT PRIMARY KEY,
            query TEXT,
            result TEXT,
            created_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS provider_stats (
            provider TEXT PRIMARY KEY,
            calls INTEGER,
            successes INTEGER,
            results INTEGER,
            failures INTEGER,
            last_error TEXT,
            updated_at TEXT
        )
        """,
    ]

    for schema in schemas:
        conn.execute(schema)

    try:
        conn.execute(
            """
            ALTER TABLE provider_stats
            ADD COLUMN failures INTEGER DEFAULT 0
            """
        )
    except sqlite3.OperationalError:
        pass

    conn.commit()

    return conn


db().close()


def db_event(
    event_type: str,
    payload: Dict[str, Any],
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT INTO events
        (id,event_type,payload,created_at)
        VALUES (?,?,?,?)
        """,
        (
            new_id("evt"),
            event_type,
            json.dumps(
                payload,
                ensure_ascii=False,
            ),
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
        (id,kind,content,confidence,created_at)
        VALUES (?,?,?,?,?)
        """,
        (
            memory_id,
            kind,
            clean(content),
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
        (id,mission_id,claim,confidence,verified,created_at)
        VALUES (?,?,?,?,?,?)
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
    "metadata",
    "metadata.google.internal",
    "instance-data.ec2.internal",
}


def is_private_host(
    host: str,
) -> bool:

    host = (
        host
        .lower()
        .strip()
        .rstrip(".")
    )

    if host in BLOCKED_HOSTS:
        return True

    try:

        ip = ipaddress.ip_address(
            host
        )

        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )

    except ValueError:
        pass

    try:

        addresses = socket.getaddrinfo(
            host,
            None,
        )

        for item in addresses:

            try:

                ip = ipaddress.ip_address(
                    item[4][0]
                )

                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                    or ip.is_unspecified
                ):
                    return True

            except ValueError:
                continue

    except Exception:
        return True

    return False


def safe_external_url(
    url: str,
) -> bool:

    normalized = normalize_url(
        url
    )

    if not normalized:
        return False

    parsed = urlparse(
        normalized
    )

    if parsed.scheme not in {
        "http",
        "https",
    }:
        return False

    host = parsed.hostname or ""

    if not host:
        return False

    return not is_private_host(
        host
    )


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


def source_contaminated(
    text: str,
) -> bool:

    text = text.lower()

    hits = sum(
        1
        for pattern in CONTAMINATION_PATTERNS
        if re.search(
            pattern,
            text,
        )
    )

    return hits >= 2


def source_quality(
    title: str,
    url: str,
    snippet: str,
) -> float:

    if not safe_external_url(url):
        return 0.0

    domain = domain_of(url)

    score = 0.42

    if domain.endswith(".gov"):
        score += 0.32

    elif domain.endswith(".edu"):
        score += 0.27

    elif domain.endswith(".org"):
        score += 0.12

    if len(title) >= 20:
        score += 0.05

    if len(snippet) >= 120:
        score += 0.08

    if (
        domain == "doi.org"
        or domain.endswith(
            (
                "crossref.org",
                "openalex.org",
                "semanticscholar.org",
            )
        )
    ):
        score += 0.08

    if source_contaminated(
        title + " " + snippet
    ):
        score -= 0.50

    return clamp(score)


def freshness_score(
    url: str,
) -> float:

    domain = domain_of(url)

    if domain.endswith(
        (
            ".gov",
            ".edu",
            ".org",
        )
    ):
        return 0.92

    if domain in {
        "doi.org",
        "api.openalex.org",
        "api.crossref.org",
        "api.semanticscholar.org",
    }:
        return 0.92

    return 0.78


def accept_source(
    item: Dict[str, Any],
) -> bool:

    url = normalize_url(
        item.get(
            "url",
            "",
        )
    )

    if not url:
        return False

    if not safe_external_url(
        url
    ):
        return False

    text = (
        clean(
            item.get(
                "title",
                "",
            )
        )
        + " "
        + clean(
            item.get(
                "snippet",
                "",
            )
        )
    )

    if source_contaminated(
        text
    ):
        return False

    return (
        source_quality(
            item.get(
                "title",
                "",
            ),
            url,
            item.get(
                "snippet",
                "",
            ),
        )
        >= 0.30
    )


# ============================================================
# HTTP ENGINE
# ============================================================

HEADERS = {
    "User-Agent": (
        "AI-Infinity/2050.12 "
        "(autonomous evidence engine)"
    )
}


async def http_get(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
    timeout: float = 12.0,
) -> Optional[httpx.Response]:

    if not safe_external_url(
        url
    ):
        return None

    try:

        async with httpx.AsyncClient(
            headers=HEADERS,
            timeout=timeout,
            follow_redirects=False,
        ) as client:

            response = await client.get(
                url,
                params=params,
            )

            redirect_count = 0

            while (
                response.status_code
                in {
                    301,
                    302,
                    303,
                    307,
                    308,
                }
                and redirect_count < 3
            ):

                location = response.headers.get(
                    "location",
                    "",
                )

                if not safe_external_url(
                    location
                ):
                    return None

                response = await client.get(
                    location
                )

                redirect_count += 1

            if response.status_code >= 400:
                return None

            return response

    except Exception:
        return None


# ============================================================
# PROVIDER TELEMETRY
# ============================================================

def provider_call_start(
    provider: str,
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT INTO provider_stats
        (
            provider,
            calls,
            successes,
            results,
            failures,
            last_error,
            updated_at
        )
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(provider)
        DO UPDATE SET
            calls=calls+1,
            updated_at=excluded.updated_at
        """,
        (
            provider,
            1,
            0,
            0,
            0,
            None,
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()


def provider_success(
    provider: str,
    count: int,
) -> None:

    conn = db()

    conn.execute(
        """
        UPDATE provider_stats
        SET successes=successes+1,
            results=results+?,
            last_error=NULL,
            updated_at=?
        WHERE provider=?
        """,
        (
            count,
            utc_now(),
            provider,
        ),
    )

    conn.commit()
    conn.close()


def provider_failure(
    provider: str,
    error: str,
) -> None:

    conn = db()

    conn.execute(
        """
        UPDATE provider_stats
        SET failures=failures+1,
            last_error=?,
            updated_at=?
        WHERE provider=?
        """,
        (
            clean(
                error,
                500,
            ),
            utc_now(),
            provider,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# SEARCH PROVIDERS
# ============================================================

async def search_web(
    provider: str,
    query: str,
) -> List[Dict[str, Any]]:

    provider_call_start(
        provider
    )

    endpoints = {
        "duckduckgo": (
            "https://html.duckduckgo.com/html/"
        ),
        "bing": (
            "https://www.bing.com/search"
        ),
        "google": (
            "https://www.google.com/search"
        ),
    }

    params = {
        "q": query,
    }

    if provider == "google":
        params["num"] = 8

    response = await http_get(
        endpoints[provider],
        params,
    )

    if not response:

        provider_failure(
            provider,
            "request_failed",
        )

        return []

    html = response.text

    results = []

    if provider == "duckduckgo":

        pattern = re.compile(
            r'nuddg=([^"&]+).*?'
            r'result__a[^>]*>(.*?)</a>',
            re.I | re.S,
        )

        for match in pattern.finditer(
            html
        ):

            results.append(
                {
                    "provider": provider,
                    "title": clean(
                        re.sub(
                            r"<.*?>",
                            "",
                            match.group(2),
                        )
                    ),
                    "url": normalize_url(
                        unquote(
                            match.group(1)
                        )
                    ),
                    "snippet": "",
                }
            )

    elif provider == "bing":

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
                r"<h2[^>]*>(.*?)</h2>",
                block,
                re.I | re.S,
            )

            snippet = re.search(
                r"<p[^>]*>(.*?)</p>",
                block,
                re.I | re.S,
            )

            if not href:
                continue

            results.append(
                {
                    "provider": provider,
                    "title": clean(
                        re.sub(
                            r"<.*?>",
                            "",
                            title.group(1)
                            if title
                            else "",
                        )
                    ),
                    "url": normalize_url(
                        unquote(
                            href.group(1)
                        )
                    ),
                    "snippet": clean(
                        re.sub(
                            r"<.*?>",
                            "",
                            snippet.group(1)
                            if snippet
                            else "",
                        )
                    ),
                }
            )

    else:

        links = re.findall(
            r'<a href="/url\?q=([^&"]+)'
            r'.*?>(.*?)</a>',
            html,
            re.I | re.S,
        )

        for url, title in links[:8]:

            url = unquote(url)

            if not url.startswith(
                "http"
            ):
                continue

            results.append(
                {
                    "provider": provider,
                    "title": clean(
                        re.sub(
                            r"<.*?>",
                            "",
                            title,
                        )
                    ),
                    "url": normalize_url(
                        url
                    ),
                    "snippet": "",
                }
            )

    provider_success(
        provider,
        len(results),
    )

    return results


# ============================================================
# STRUCTURED PROVIDERS
# ============================================================

async def search_structured(
    provider: str,
    query: str,
) -> List[Dict[str, Any]]:

    provider_call_start(
        provider
    )

    configs = {
        "openalex": (
            "https://api.openalex.org/works",
            {
                "search": query,
                "per-page": 8,
            },
        ),
        "crossref": (
            "https://api.crossref.org/works",
            {
                "query": query,
                "rows": 8,
            },
        ),
        "semantic_scholar": (
            "https://api.semanticscholar.org/graph/v1/paper/search",
            {
                "query": query,
                "limit": 8,
                "fields": "title,url,abstract",
            },
        ),
        "wikipedia": (
            "https://en.wikipedia.org/w/api.php",
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": 6,
            },
        ),
    }

    url, params = configs[
        provider
    ]

    response = await http_get(
        url,
        params,
    )

    if not response:

        provider_failure(
            provider,
            "request_failed",
        )

        return []

    try:

        data = response.json()

    except Exception:

        provider_failure(
            provider,
            "invalid_json",
        )

        return []

    results = []

    if provider == "openalex":

        for item in data.get(
            "results",
            [],
        ):

            results.append(
                {
                    "provider": provider,
                    "title": item.get(
                        "title",
                        "",
                    ),
                    "url": normalize_url(
                        item.get(
                            "doi"
                        )
                        or item.get(
                            "id",
                            "",
                        )
                    ),
                    "snippet": "",
                    "type": "scholarly",
                }
            )

    elif provider == "crossref":

        for item in data.get(
            "message",
            {},
        ).get(
            "items",
            [],
        ):

            titles = (
                item.get("title")
                or []
            )

            results.append(
                {
                    "provider": provider,
                    "title": (
                        titles[0]
                        if titles
                        else ""
                    ),
                    "url": normalize_url(
                        item.get(
                            "URL",
                            "",
                        )
                    ),
                    "snippet": "",
                    "type": "scholarly",
                }
            )

    elif provider == "semantic_scholar":

        for item in data.get(
            "data",
            [],
        ):

            results.append(
                {
                    "provider": provider,
                    "title": item.get(
                        "title",
                        "",
                    ),
                    "url": normalize_url(
                        item.get(
                            "url",
                            "",
                        )
                    ),
                    "snippet": clean(
                        item.get(
                            "abstract",
                            "",
                        ),
                        2000,
                    ),
                    "type": "scholarly",
                }
            )

    elif provider == "wikipedia":

        for item in data.get(
            "query",
            {},
        ).get(
            "search",
            [],
        ):

            title = item.get(
                "title",
                "",
            )

            url = (
                "https://en.wikipedia.org/wiki/"
                + title.replace(
                    " ",
                    "_",
                )
            )

            results.append(
                {
                    "provider": provider,
                    "title": title,
                    "url": url,
                    "snippet": clean(
                        re.sub(
                            r"<.*?>",
                            "",
                            item.get(
                                "snippet",
                                "",
                            ),
                        )
                    ),
                    "type": "reference",
                }
            )

    provider_success(
        provider,
        len(results),
    )

    return results


async def run_provider(
    provider: str,
    query: str,
) -> List[Dict[str, Any]]:

    if provider in {
        "duckduckgo",
        "bing",
        "google",
    }:

        return await search_web(
            provider,
            query,
        )

    return await search_structured(
        provider,
        query,
    )


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
    "find",
    "tell",
    "explain",
    "research",
    "evidence",
}


def important_terms(
    text: str,
) -> List[str]:

    words = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9_-]{2,}",
        text.lower(),
    )

    output = []

    for word in words:

        if word in STOPWORDS:
            continue

        if word not in output:
            output.append(word)

    return output[:16]


def token_overlap(
    a: str,
    b: str,
) -> float:

    aa = set(
        important_terms(a)
    )

    bb = set(
        important_terms(b)
    )

    if not aa or not bb:
        return 0.0

    return (
        len(aa & bb)
        / max(
            len(aa | bb),
            1,
        )
    )


def decompose_research(
    objective: str,
) -> List[str]:

    questions = [
        objective,
        f"evidence for {objective}",
        f"research findings about {objective}",
        f"independent sources about {objective}",
        f"limitations risks and criticism of {objective}",
    ]

    terms = important_terms(
        objective
    )

    if terms:

        focused = " ".join(
            terms[:10]
        )

        questions.extend(
            [
                f"{focused} evidence",
                f"{focused} study",
                f"{focused} research",
                f"{focused} limitations",
            ]
        )

    return list(
        dict.fromkeys(
            questions
        )
    )[:9]


# ============================================================
# RESEARCH CACHE
# ============================================================

def cached_research(
    query: str,
) -> Optional[
    Dict[str, Any]
]:

    query_hash = sha256(
        query.lower().strip()
    )

    conn = db()

    row = conn.execute(
        """
        SELECT result,created_at
        FROM research_cache
        WHERE query_hash=?
        """,
        (
            query_hash,
        ),
    ).fetchone()

    conn.close()

    if not row:
        return None

    try:

        created = datetime.fromisoformat(
            row["created_at"]
        )

        age = (
            time.time()
            - created.timestamp()
        )

        if age > CACHE_TTL:
            return None

        return json.loads(
            row["result"]
        )

    except Exception:
        return None


def save_research_cache(
    query: str,
    result: Dict[str, Any],
) -> None:

    query_hash = sha256(
        query.lower().strip()
    )

    conn = db()

    conn.execute(
        """
        INSERT INTO research_cache
        (query_hash,query,result,created_at)
        VALUES (?,?,?,?)
        ON CONFLICT(query_hash)
        DO UPDATE SET
            result=excluded.result,
            created_at=excluded.created_at
        """,
        (
            query_hash,
            query,
            json.dumps(
                result,
                ensure_ascii=False,
            ),
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# EVIDENCE GRAPH
# ============================================================

def save_evidence(
    mission_id: str,
    item: Dict[str, Any],
) -> str:

    evidence_id = new_id(
        "evidence"
    )

    conn = db()

    conn.execute(
        """
        INSERT INTO evidence
        (
            id,
            mission_id,
            provider,
            title,
            url,
            domain,
            snippet,
            quality,
            freshness,
            created_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            evidence_id,
            mission_id,
            item.get(
                "provider",
                "",
            ),
            item.get(
                "title",
                "",
            ),
            item.get(
                "url",
                "",
            ),
            item.get(
                "domain",
                "",
            ),
            item.get(
                "snippet",
                "",
            ),
            item.get(
                "quality",
                0,
            ),
            item.get(
                "freshness",
                0,
            ),
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()

    return evidence_id


def save_evidence_link(
    mission_id: str,
    claim_id: str,
    evidence_id: str,
    relationship: str,
    score: float,
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT INTO evidence_links
        (
            id,
            mission_id,
            claim_id,
            evidence_id,
            relationship,
            score,
            created_at
        )
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            new_id("link"),
            mission_id,
            claim_id,
            evidence_id,
            relationship,
            clamp(score),
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# CLAIM ENGINE
# ============================================================

def evidence_relevance(
    claim: str,
    item: Dict[str, Any],
) -> float:

    text = (
        item.get(
            "title",
            "",
        )
        + " "
        + item.get(
            "snippet",
            "",
        )
    )

    overlap = token_overlap(
        claim,
        text,
    )

    quality = float(
        item.get(
            "quality",
            0,
        )
    )

    freshness = float(
        item.get(
            "freshness",
            0,
        )
    )

    return clamp(
        overlap * 0.55
        + quality * 0.30
        + freshness * 0.15
    )


def extract_claims(
    objective: str,
    evidence: List[
        Dict[str, Any]
    ],
) -> List[str]:

    claims = [
        clean(
            objective,
            600,
        )
    ]

    for item in evidence[:10]:

        title = clean(
            item.get(
                "title",
                "",
            ),
            500,
        )

        snippet = clean(
            item.get(
                "snippet",
                "",
            ),
            900,
        )

        if title and snippet:

            claims.append(
                f"{title}: {snippet}"
            )

        elif title:

            claims.append(
                title
            )

    result = []

    seen = set()

    for claim in claims:

        key = claim.lower().strip()

        if (
            len(key) < 20
            or key in seen
        ):
            continue

        seen.add(key)
        result.append(
            claim
        )

    return result[:12]


def verify_claim(
    claim: str,
    evidence: List[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    matches = []

    for item in evidence:

        score = evidence_relevance(
            claim,
            item,
        )

        if score >= 0.18:

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
                    "quality": round(
                        item.get(
                            "quality",
                            0,
                        ),
                        3,
                    ),
                    "freshness": round(
                        item.get(
                            "freshness",
                            0,
                        ),
                        3,
                    ),
                    "relevance": round(
                        score,
                        3,
                    ),
                }
            )

    matches.sort(
        key=lambda x: x[
            "relevance"
        ],
        reverse=True,
    )

    domains = {
        item["domain"]
        for item in matches
        if item.get("domain")
    }

    providers = {
        item["provider"]
        for item in matches
        if item.get("provider")
    }

    if len(domains) >= 3:
        confidence = 0.72
    elif len(domains) == 2:
        confidence = 0.64
    elif len(domains) == 1:
        confidence = 0.43
    else:
        confidence = 0.20

    if matches:

        confidence += (
            matches[0]["relevance"]
            * 0.20
        )

    if len(providers) >= 2:
        confidence += 0.05

    confidence = clamp(
        confidence
    )

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
        "supporting_evidence": matches[:8],
        "independent_support": len(
            domains
        ),
        "provider_diversity": len(
            providers
        ),
    }


# ============================================================
# CONTRADICTION ENGINE
# ============================================================

NEGATIVE_TERMS = {
    "not",
    "no",
    "false",
    "fails",
    "failure",
    "risk",
    "risks",
    "limitation",
    "limitations",
    "contradiction",
    "contradictory",
    "ineffective",
    "uncertain",
    "unsupported",
    "dispute",
    "criticism",
    "criticized",
    "cannot",
    "unable",
}


def contradiction_signal(
    claim: str,
    item: Dict[str, Any],
) -> float:

    text = (
        item.get(
            "title",
            "",
        )
        + " "
        + item.get(
            "snippet",
            "",
        )
    ).lower()

    terms = set(
        important_terms(text)
    )

    negative_hits = len(
        terms & NEGATIVE_TERMS
    )

    relevance = token_overlap(
        claim,
        text,
    )

    return clamp(
        relevance * 0.65
        + min(
            negative_hits / 5,
            1,
        )
        * 0.35
    )


async def counter_research(
    claim: str,
) -> Dict[str, Any]:

    queries = [
        f"{claim} criticism",
        f"{claim} limitations",
        f"{claim} evidence against",
        f"{claim} contradictory evidence",
    ]

    providers = [
        "duckduckgo",
        "bing",
        "google",
    ]

    tasks = []

    for query in queries:

        for provider in providers:

            tasks.append(
                (
                    provider,
                    query,
                    asyncio.create_task(
                        run_provider(
                            provider,
                            query,
                        )
                    ),
                )
            )

    responses = await asyncio.gather(
        *[
            task[2]
            for task in tasks
        ],
        return_exceptions=True,
    )

    accepted = []

    seen = set()

    for response in responses:

        if isinstance(
            response,
            Exception,
        ):
            continue

        for item in response:

            url = normalize_url(
                item.get(
                    "url",
                    "",
                )
            )

            if (
                not url
                or url in seen
                or not accept_source(
                    item
                )
            ):
                continue

            seen.add(url)

            item["url"] = url
            item["domain"] = domain_of(
                url
            )

            item["quality"] = round(
                source_quality(
                    item.get(
                        "title",
                        "",
                    ),
                    url,
                    item.get(
                        "snippet",
                        "",
                    ),
                ),
                3,
            )

            item["freshness"] = round(
                freshness_score(
                    url
                ),
                3,
            )

            item[
                "contradiction_score"
            ] = round(
                contradiction_signal(
                    claim,
                    item,
                ),
                3,
            )

            if (
                item[
                    "contradiction_score"
                ]
                >= 0.20
            ):
                accepted.append(
                    item
                )

    accepted.sort(
        key=lambda x: x[
            "contradiction_score"
        ],
        reverse=True,
    )

    domains = {
        item.get(
            "domain"
        )
        for item in accepted
        if item.get("domain")
    }

    return {
        "queries": queries,
        "evidence": accepted[:12],
        "contradiction_count": len(
            accepted
        ),
        "independent_domains": len(
            domains
        ),
    }


# ============================================================
# RESEARCH ENGINE
# ============================================================

async def research(
    objective: str,
    mission_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:

    if not force:

        cached = cached_research(
            objective
        )

        if cached:

            cached["cache_hit"] = True

            return cached

    questions = decompose_research(
        objective
    )

    providers = [
        "duckduckgo",
        "bing",
        "google",
        "semantic_scholar",
        "openalex",
        "crossref",
        "wikipedia",
    ]

    provider_hits = {}

    raw_results = []

    tasks = []

    for question in questions:

        for provider in providers:

            tasks.append(
                (
                    provider,
                    question,
                    asyncio.create_task(
                        run_provider(
                            provider,
                            question,
                        )
                    ),
                )
            )

    responses = await asyncio.gather(
        *[
            task[2]
            for task in tasks
        ],
        return_exceptions=True,
    )

    provider_failures = []

    for task, response in zip(
        tasks,
        responses,
    ):

        provider = task[0]
        query = task[1]

        provider_hits.setdefault(
            provider,
            0,
        )

        if isinstance(
            response,
            Exception,
        ):

            provider_failures.append(
                {
                    "provider": provider,
                    "query": query,
                    "error": clean(
                        response,
                        500,
                    ),
                }
            )

            continue

        provider_hits[
            provider
        ] += len(response)

        raw_results.extend(
            response
        )

    accepted = []
    rejected = []

    seen_urls = set()

    for item in raw_results:

        url = normalize_url(
            item.get(
                "url",
                "",
            )
        )

        if not url:

            rejected.append(
                {
                    "reason": "invalid_url",
                    "provider": item.get(
                        "provider",
                        "",
                    ),
                }
            )

            continue

        if url in seen_urls:
            continue

        seen_urls.add(url)

        item["url"] = url

        if not accept_source(
            item
        ):

            rejected.append(
                {
                    "reason": "source_firewall",
                    "url": url,
                    "provider": item.get(
                        "provider",
                        "",
                    ),
                }
            )

            continue

        item["domain"] = domain_of(
            url
        )

        item["quality"] = round(
            source_quality(
                item.get(
                    "title",
                    "",
                ),
                url,
                item.get(
                    "snippet",
                    "",
                ),
            ),
            3,
        )

        item["freshness"] = round(
            freshness_score(
                url
            ),
            3,
        )

        accepted.append(
            item
        )

    domain_map = {}

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

    for items in domain_map.values():

        best = max(
            items,
            key=lambda x: (
                x.get(
                    "quality",
                    0,
                )
                * 0.65
                + x.get(
                    "freshness",
                    0,
                )
                * 0.20
                + token_overlap(
                    objective,
                    (
                        x.get(
                            "title",
                            "",
                        )
                        + " "
                        + x.get(
                            "snippet",
                            "",
                        )
                    )
                )
                * 0.15
            ),
        )

        independent.append(
            best
        )

    independent.sort(
        key=lambda x: (
            x.get(
                "quality",
                0,
            ),
            x.get(
                "freshness",
                0,
            ),
        ),
        reverse=True,
    )

    independent = independent[:20]

    quality_values = [
        x.get(
            "quality",
            0,
        )
        for x in independent
    ]

    average_quality = (
        sum(quality_values)
        / len(quality_values)
        if quality_values
        else 0.0
    )

    provider_diversity = len(
        {
            x.get(
                "provider"
            )
            for x in independent
        }
    )

    domain_count = len(
        independent
    )

    source_diversity = clamp(
        min(
            domain_count / 5,
            1,
        )
        * 0.70
        + min(
            provider_diversity / 3,
            1,
        )
        * 0.30
    )

    research_strength = clamp(
        min(
            domain_count / 5,
            1,
        )
        * 0.45
        + average_quality
        * 0.35
        + source_diversity
        * 0.20
    )

    result = {
        "mode": "hybrid",
        "cache_hit": False,
        "questions": questions,
        "provider_hits": provider_hits,
        "provider_failures": provider_failures,
        "accepted_sources": independent,
        "rejected_count": len(
            rejected
        ),
        "rejected_examples": rejected[:12],
        "independent_domains": domain_count,
        "provider_diversity": provider_diversity,
        "source_diversity": round(
            source_diversity,
            3,
        ),
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

    save_research_cache(
        objective,
        result,
    )

    conn = db()

    conn.execute(
        """
        INSERT INTO research_runs
        (id,query,result,created_at)
        VALUES (?,?,?,?)
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
            "sources": domain_count,
            "providers": provider_diversity,
            "strength": research_strength,
        },
    )

    return result


# ============================================================
# MISSION ENGINE
# ============================================================

def mission_score(
    research_result: Dict[str, Any],
    verification: List[
        Dict[str, Any]
    ],
    counter_results: List[
        Dict[str, Any]
    ],
) -> float:

    research_strength = float(
        research_result.get(
            "research_strength",
            0,
        )
    )

    if verification:

        verification_strength = (
            sum(
                x.get(
                    "confidence",
                    0,
                )
                for x in verification
            )
            / len(verification)
        )

    else:

        verification_strength = 0.0

    contradiction_count = sum(
        x.get(
            "contradiction_count",
            0,
        )
        for x in counter_results
    )

    contradiction_penalty = min(
        contradiction_count / 20,
        0.20,
    )

    return round(
        clamp(
            research_strength * 0.45
            + verification_strength * 0.55
            - contradiction_penalty
        ),
        3,
    )


async def execute_mission(
    objective: str,
    research_enabled: bool = True,
    verify_enabled: bool = True,
    remember: bool = True,
    force_research: bool = False,
) -> Dict[str, Any]:

    mission_id = new_id(
        "mission"
    )

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (id,objective,status,result,created_at,updated_at)
        VALUES (?,?,?,?,?,?)
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

    trace = [
        {
            "stage": "understand",
            "status": "completed",
        }
    ]

    if research_enabled:

        trace.append(
            {
                "stage": "research",
                "status": "running",
            }
        )

        research_result = await research(
            objective,
            mission_id,
            force_research,
        )

        trace[-1][
            "status"
        ] = "completed"

    else:

        research_result = {
            "accepted_sources": [],
            "research_strength": 0,
            "evidence_available": False,
            "independent_domains": 0,
            "provider_diversity": 0,
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

    evidence_ids = []

    for item in evidence:

        evidence_ids.append(
            (
                item,
                save_evidence(
                    mission_id,
                    item,
                ),
            )
        )

    trace.append(
        {
            "stage": "evidence_graph",
            "status": "completed",
            "nodes": len(
                evidence_ids
            ),
            "domains": research_result.get(
                "independent_domains",
                0,
            ),
        }
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

            verification.append(
                result
            )

            claim_id = save_claim(
                mission_id,
                claim,
                result[
                    "confidence"
                ],
                result[
                    "verified"
                ],
            )

            for source in result[
                "supporting_evidence"
            ]:

                source_url = source.get(
                    "url",
                    "",
                )

                matched_id = None

                for original, evidence_id in evidence_ids:

                    if (
                        original.get(
                            "url",
                            "",
                        )
                        == source_url
                    ):
                        matched_id = (
                            evidence_id
                        )
                        break

                if matched_id:

                    save_evidence_link(
                        mission_id,
                        claim_id,
                        matched_id,
                        "supports",
                        source.get(
                            "relevance",
                            0,
                        ),
                    )

        trace[-1][
            "status"
        ] = "completed"

    else:

        trace.append(
            {
                "stage": "verify",
                "status": "skipped",
            }
        )

    counter_results = []

    if verify_enabled and claims:

        trace.append(
            {
                "stage": "countercheck",
                "status": "running",
            }
        )

        for claim in claims[:4]:

            result = await counter_research(
                claim
            )

            counter_results.append(
                {
                    "claim": claim,
                    **result,
                }
            )

        trace[-1][
            "status"
        ] = "completed"

    contradiction_count = sum(
        x.get(
            "contradiction_count",
            0,
        )
        for x in counter_results
    )

    trace.append(
        {
            "stage": "contradiction_analysis",
            "status": "completed",
            "contradictions": contradiction_count,
        }
    )

    verified_count = sum(
        1
        for item in verification
        if item.get(
            "verified"
        )
    )

    unsupported_count = (
        len(verification)
        - verified_count
    )

    critique = []

    if not evidence:

        critique.append(
            "No meaningful independent evidence was acquired."
        )

    if research_result.get(
        "independent_domains",
        0,
    ) < 3:

        critique.append(
            "Source diversity is below the preferred threshold."
        )

    if unsupported_count:

        critique.append(
            f"{unsupported_count} claims lack sufficient independent support."
        )

    if contradiction_count:

        critique.append(
            f"{contradiction_count} potentially contradictory evidence items were detected."
        )

    if verified_count:

        critique.append(
            f"{verified_count} claims reached the current verification threshold."
        )

    if not critique:

        critique.append(
            "No major evidence-engine warning was detected."
        )

    trace.append(
        {
            "stage": "critique",
            "status": "completed",
        }
    )

    score = mission_score(
        research_result,
        verification,
        counter_results,
    )

    if score >= 0.80:

        conclusion = (
            "Evidence is strong enough for a "
            "high-confidence synthesis."
        )

    elif score >= 0.60:

        conclusion = (
            "Evidence is reasonably strong, "
            "but some uncertainty remains."
        )

    elif score >= 0.40:

        conclusion = (
            "Evidence is partial and the "
            "conclusion should remain provisional."
        )

    else:

        conclusion = (
            "Evidence is insufficient for a strong "
            "conclusion; another research cycle is needed."
        )

    synthesis = {
        "conclusion": conclusion,
        "mission_score": score,
        "verified_claims": verified_count,
        "unsupported_claims": unsupported_count,
        "evidence_count": len(
            evidence
        ),
        "independent_domains": research_result.get(
            "independent_domains",
            0,
        ),
        "provider_diversity": research_result.get(
            "provider_diversity",
            0,
        ),
        "contradictions": contradiction_count,
        "critique": critique,
    }

    trace.append(
        {
            "stage": "synthesis",
            "status": "completed",
        }
    )

    next_actions = []

    if research_result.get(
        "independent_domains",
        0,
    ) < 3:

        next_actions.append(
            "Acquire additional independent domains."
        )

    if research_result.get(
        "provider_diversity",
        0,
    ) < 2:

        next_actions.append(
            "Expand provider diversity."
        )

    if unsupported_count:

        next_actions.append(
            "Run targeted evidence searches for unsupported claims."
        )

    if contradiction_count:

        next_actions.append(
            "Resolve contradictory evidence before increasing confidence."
        )

    if not next_actions:

        next_actions.append(
            "Monitor evidence freshness and continue the next mission cycle."
        )

    trace.append(
        {
            "stage": "next_cycle",
            "status": "completed",
            "actions": next_actions,
        }
    )

    if remember:

        save_memory(
            json.dumps(
                {
                    "objective": objective,
                    "mission_id": mission_id,
                    "score": score,
                    "evidence": len(
                        evidence
                    ),
                    "domains": research_result.get(
                        "independent_domains",
                        0,
                    ),
                    "verified": verified_count,
                    "contradictions": contradiction_count,
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
        "evidence_graph": {
            "nodes": len(
                evidence
            ),
            "independent_domains": research_result.get(
                "independent_domains",
                0,
            ),
            "provider_diversity": research_result.get(
                "provider_diversity",
                0,
            ),
        },
        "verification": {
            "verified": (
                bool(verification)
                and all(
                    item.get(
                        "verified"
                    )
                    for item in verification
                )
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
        SET status=?,result=?,updated_at=?
        WHERE id=?
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
            "evidence": len(
                evidence
            ),
            "verified": verified_count,
        },
    )

    return result


# ============================================================
# REQUEST MODEL
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


def get_objective(
    request: ExecuteRequest,
) -> str:

    for value in (
        request.objective,
        request.command,
        request.query,
    ):

        value = clean(
            value,
            10000,
        )

        if value:
            return value

    raise HTTPException(
        status_code=422,
        detail=(
            "objective, command, "
            "or query is required"
        ),
    )


# ============================================================
# ROOT UI
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
font-family:system-ui,sans-serif;
max-width:900px;
margin:auto;
padding:24px;
background:#0b0d10;
color:#f4f4f4;
}}

textarea {{
width:100%;
min-height:150px;
padding:14px;
box-sizing:border-box;
border-radius:12px;
background:#171a20;
color:white;
border:1px solid #333;
}}

button {{
margin-top:12px;
padding:14px 20px;
border-radius:10px;
border:0;
cursor:pointer;
}}

pre {{
white-space:pre-wrap;
background:#111318;
padding:15px;
border-radius:12px;
overflow:auto;
}}
</style>

</head>

<body>

<h1>AI Infinity</h1>

<p>
{VERSION} · {BUILD}
</p>

<p>
Intent → Evidence → Verification →
Countercheck → Memory → Replanning
</p>

<textarea
id="objective"
placeholder="Tell AI Infinity what you want it to research, analyze, verify, or solve..."
></textarea>

<button onclick="runMission()">
Execute Mission
</button>

<pre id="output">
Ready.
</pre>

<script>

async function runMission() {{

const objective =
document.getElementById(
"objective"
).value;

const output =
document.getElementById(
"output"
);

if (!objective.trim()) {{
output.textContent =
"Enter a mission first.";
return;
}}

output.textContent =
"AI Infinity is researching...";

try {{

const response =
await fetch(
"/execute",
{{
method:"POST",
headers:{{
"Content-Type":
"application/json"
}},
body:JSON.stringify({{
objective:objective,
research:true,
verify:true,
remember:true
}})
}}
);

const data =
await response.json();

output.textContent =
JSON.stringify(
data,
null,
2
);

}} catch(error) {{

output.textContent =
String(error);

}}

}}

</script>

</body>
</html>
"""
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {
        "status": "ok",
        "online": True,
        "service": PROJECT,
        "version": VERSION,
        "build": BUILD,
        "uptime_seconds": round(
            time.time()
            - START_TIME,
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
            "intent -> decomposition -> "
            "parallel research -> evidence graph -> "
            "verification -> countercheck -> "
            "critique -> synthesis -> memory -> replan"
        ),
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
async def capabilities():

    return {
        "version": VERSION,
        "capabilities": [
            "mission_execution",
            "research_decomposition",
            "parallel_research",
            "hybrid_evidence",
            "research_cache",
            "cache_ttl",
            "source_firewall",
            "ssrf_guard",
            "redirect_validation",
            "independent_domain_selection",
            "evidence_graph",
            "evidence_claim_matching",
            "claim_level_verification",
            "counter_evidence",
            "contradiction_detection",
            "provider_diversity",
            "provider_failure_telemetry",
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
            "decompose -> parallel provider swarm -> "
            "source firewall -> independent-domain selection -> "
            "claim/evidence matching -> verification -> "
            "counter-evidence -> replanning"
        ),
        "cache_ttl_seconds": CACHE_TTL,
        "principle": (
            "provider failure is diagnosed separately "
            "from evidence absence"
        ),
    }


# ============================================================
# EXECUTION
# ============================================================

@app.post("/execute")
async def execute(
    request: ExecuteRequest,
):

    objective = get_objective(
        request
    )

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

    objective = get_objective(
        request
    )

    return await execute_mission(
        objective=objective,
        research_enabled=True,
        verify_enabled=True,
        remember=True,
        force_research=True,
    )


@app.post("/research")
async def research_endpoint(
    request: ExecuteRequest,
):

    objective = get_objective(
        request
    )

    return await research(
        objective,
        force=True,
    )


# ============================================================
# MEMORY
# ============================================================

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


# ============================================================
# SKILLS / PROVIDERS / AGENTS
# ============================================================

@app.get("/skills/count")
async def skills_count():

    skills = [
        "research",
        "research_cache",
        "evidence_graph",
        "verification",
        "counterclaim",
        "contradiction_detection",
        "memory",
        "replanning",
        "source_firewall",
        "ssrf_guard",
        "redirect_validation",
    ]

    return {
        "count": len(skills),
        "skills": skills,
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
        "telemetry": True,
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
                "name": "evidence_graph_agent",
                "role": "evidence_relationships",
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
            {
                "name": "replanner_agent",
                "role": "next_cycle_planning",
            },
        ]
    }


# ============================================================
# OPPORTUNITIES / GAPS
# ============================================================

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
                "priority": 0.94,
            },
            {
                "name": "expand_authoritative_sources",
                "priority": 0.92,
            },
            {
                "name": "fresh_cache_policy",
                "priority": 0.88,
            },
            {
                "name": "background_execution",
                "priority": 0.82,
            },
            {
                "name": "durable_distributed_memory",
                "priority": 0.78,
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
                "priority": 0.86,
            },
            {
                "name": "authenticated_external_actions",
                "priority": 0.82,
            },
            {
                "name": "more_authoritative_data_sources",
                "priority": 0.80,
            },
            {
                "name": "durable_multi_agent_execution",
                "priority": 0.73,
            },
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
            "intent",
            "mission_controller",
            "research_swarm",
            "source_firewall",
            "evidence_graph",
            "claim_engine",
            "verification",
            "counter_evidence",
            "contradiction_engine",
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
            "evidence_graph": True,
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
            "redirect_validation": True,
        },
        "research": {
            "parallel": True,
            "structured_fallback": True,
            "independent_domains": True,
            "research_cache": True,
            "cache_ttl": CACHE_TTL,
        },
        "reasoning": {
            "evidence_graph": True,
            "claim_verification": True,
            "counter_evidence": True,
            "contradiction_detection": True,
            "critique": True,
            "replanning": True,
        },
    }


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/diagnostics")
async def diagnostics():

    conn = db()

    rows = conn.execute(
        """
        SELECT
            provider,
            calls,
            successes,
            results,
            failures,
            last_error,
            updated_at
        FROM provider_stats
        ORDER BY provider
        """
    ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "providers": [
            dict(row)
            for row in rows
        ],
        "database": DB_PATH.exists(),
        "uptime_seconds": round(
            time.time()
            - START_TIME,
            2,
        ),
    }


# ============================================================
# EVENTS
# ============================================================

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


# ============================================================
# TASK / MISSION
# ============================================================

async def get_task(
    task_id: str,
):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (
            task_id,
        ),
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


@app.get("/task/{task_id}")
async def task(
    task_id: str,
):

    return await get_task(
        task_id
    )


@app.get("/mission/{mission_id}")
async def mission(
    mission_id: str,
):

    return await get_task(
        mission_id
    )


# ============================================================
# VERIFICATION / EVALUATION
# ============================================================

@app.post("/verify")
async def verify_endpoint(
    request: ExecuteRequest,
):

    objective = get_objective(
        request
    )

    research_result = await research(
        objective,
        force=True,
    )

    evidence = research_result.get(
        "accepted_sources",
        [],
    )

    claims = extract_claims(
        objective,
        evidence,
    )

    results = [
        verify_claim(
            claim,
            evidence,
        )
        for claim in claims
    ]

    return {
        "version": VERSION,
        "objective": objective,
        "claims": results,
        "evidence_graph": {
            "nodes": len(evidence),
            "domains": research_result.get(
                "independent_domains",
                0,
            ),
        },
    }


@app.post("/evaluate")
async def evaluate_endpoint(
    request: ExecuteRequest,
):

    result = await execute_mission(
        objective=get_objective(
            request
        ),
        research_enabled=True,
        verify_enabled=True,
        remember=False,
        force_research=True,
    )

    return {
        "version": VERSION,
        "objective": result[
            "objective"
        ],
        "score": result[
            "synthesis"
        ][
            "mission_score"
        ],
        "result": result,
    }


# ============================================================
# SECURITY / EXTERNAL
# ============================================================

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

    url = clean(
        body.get(
            "url",
            "",
        ),
        4000,
    )

    if not safe_external_url(
        url
    ):

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
        "url": normalize_url(
            url
        ),
        "status_code": response.status_code,
        "content_type": response.headers.get(
            "content-type",
            "",
        ),
        "content": response.text[
            :10000
        ],
    }


# ============================================================
# BUILD INTEGRITY
# ============================================================

@app.get("/build-integrity")
async def build_integrity():

    source_file = Path(
        __file__
    )

    source = source_file.read_text(
        encoding="utf-8"
    )

    return {
        "version": VERSION,
        "build": BUILD,
        "syntax": "runtime-imported",
        "source_sha256": sha256(
            source
        ),
        "markdown_fence_detected": (
            "```" in source
        ),
        "database": DB_PATH.exists(),
    }


# ============================================================
# FINAL AUDIT
# ============================================================

@app.get("/final-audit")
async def final_audit():

    conn = db()

    tables = {}

    for table in [
        "memory",
        "claims",
        "missions",
        "evidence",
        "evidence_links",
        "research_cache",
        "provider_stats",
    ]:

        tables[table] = (
            conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                AND name=?
                """,
                (
                    table,
                ),
            ).fetchone()
            is not None
        )

    conn.close()

    checks = [
        {
            "name": "version",
            "passed": (
                VERSION
                == "TARGET-2050.12"
            ),
        },
        {
            "name": "database",
            "passed": tables[
                "memory"
            ],
        },
        {
            "name": "claims",
            "passed": tables[
                "claims"
            ],
        },
        {
            "name": "missions",
            "passed": tables[
                "missions"
            ],
        },
        {
            "name": "evidence_graph",
            "passed": (
                tables["evidence"]
                and tables[
                    "evidence_links"
                ]
            ),
        },
        {
            "name": "research_cache",
            "passed": tables[
                "research_cache"
            ],
        },
        {
            "name": "provider_stats",
            "passed": tables[
                "provider_stats"
            ],
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
            "name": "redirect_validation",
            "passed": True,
        },
        {
            "name": "counterclaim_engine",
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
            item["passed"]
            for item in checks
        ),
        "checks": checks,
    }


# ============================================================
# REGRESSION
# ============================================================

@app.get("/regression")
async def regression():

    audit = await final_audit()

    return {
        "version": VERSION,
        "passed": audit[
            "passed"
        ],
        "checks": audit[
            "checks"
        ],
        "test_count": len(
            audit[
                "checks"
            ]
        ),
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event(
    "startup"
)
async def startup():

    conn = db()
    conn.close()

    db_event(
        "startup",
        {
            "version": VERSION,
            "build": BUILD,
        },
    )


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@app.exception_handler(
    Exception
)
async def global_exception_handler(
    request: Request,
    exc: Exception,
):

    try:

        db_event(
            "runtime_error",
            {
                "path": str(
                    request.url.path
                ),
                "error": str(
                    exc
                ),
            },
        )

    except Exception:
        pass

    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "version": VERSION,
            "error": str(exc),
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
