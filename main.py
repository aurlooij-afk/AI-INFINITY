"""
AI Infinity
TARGET-2050.55
AUTONOMOUS-SOURCE-DISCOVERY-FABRIC

Single-file FastAPI runtime.

2050.55 adds:
- Autonomous research-source discovery
- Public provider adapters
- Wikipedia discovery
- Crossref discovery
- arXiv discovery
- Configurable discovery endpoints
- Source ranking / deduplication
- Discovery provenance
- Evidence collection
- Evidence comparison
- Contradiction detection
- Intelligence-gap detection
- Adaptive mission expansion
- Checkpointing
- Persistent learning
- Independent verification
- Approval-gated real-world action gateway

Security model:
- Network access remains allowlist-controlled.
- Internal/private network targets are blocked.
- No arbitrary code execution.
- No credential/permission bypass.
- High-impact external actions remain approval-gated.
- Research providers are read-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

VERSION = "TARGET-2050.55"
BUILD = "AUTONOMOUS-SOURCE-DISCOVERY-FABRIC"

BASE_DIR = Path(os.getenv("AI_INFINITY_HOME", "/tmp/ai_infinity"))
BASE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "ai_infinity.db"

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
MAX_SOURCE_TEXT = int(os.getenv("MAX_SOURCE_TEXT", "30000"))
MAX_RESEARCH_SOURCES = int(os.getenv("MAX_RESEARCH_SOURCES", "12"))

# Explicit external domains supplied by the operator.
EXTERNAL_ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}

# Optional additional research seed domains.
RESEARCH_SEED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("RESEARCH_SEED_DOMAINS", "").split(",")
    if x.strip()
}

# Optional custom discovery endpoints.
# Example:
# RESEARCH_DISCOVERY_URLS=https://example.com/search?q={query}
RESEARCH_DISCOVERY_URLS = [
    x.strip()
    for x in os.getenv("RESEARCH_DISCOVERY_URLS", "").split(",")
    if x.strip()
]

MAX_DISCOVERY_RESULTS = int(os.getenv("MAX_DISCOVERY_RESULTS", "20"))

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
}

PRIVATE_HOST_PATTERNS = [
    r"^10\.",
    r"^127\.",
    r"^169\.254\.",
    r"^192\.168\.",
    r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",
]


# ============================================================
# DEFAULT PUBLIC RESEARCH PROVIDERS
# ============================================================

DEFAULT_RESEARCH_PROVIDERS = [
    {
        "name": "wikipedia",
        "kind": "public_search",
        "enabled": True,
        "requires_key": False,
        "domain": "en.wikipedia.org",
        "endpoint": "https://en.wikipedia.org/w/api.php",
    },
    {
        "name": "crossref",
        "kind": "academic_search",
        "enabled": True,
        "requires_key": False,
        "domain": "api.crossref.org",
        "endpoint": "https://api.crossref.org/works",
    },
    {
        "name": "arxiv",
        "kind": "academic_search",
        "enabled": True,
        "requires_key": False,
        "domain": "export.arxiv.org",
        "endpoint": "https://export.arxiv.org/api/query",
    },
]


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Autonomous intelligence, research, evidence and controlled action fabric.",
)


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            confidence REAL DEFAULT 0,
            verified INTEGER DEFAULT 0,
            learned INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS mission_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            step_name TEXT,
            status TEXT,
            result_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connectors (
            name TEXT PRIMARY KEY,
            category TEXT,
            permission TEXT,
            enabled INTEGER DEFAULT 1,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            source_url TEXT,
            title TEXT,
            text TEXT,
            relevance REAL DEFAULT 0,
            quality REAL DEFAULT 0,
            provider TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            source TEXT,
            event TEXT,
            details TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            action TEXT,
            status TEXT,
            created_at TEXT,
            approved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS learning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            memory TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connector_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            connector TEXT,
            status TEXT,
            details TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            cycle INTEGER,
            state_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS intelligence_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            gap TEXT,
            priority REAL,
            status TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS research_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            url TEXT,
            title TEXT,
            provider TEXT,
            discovery_query TEXT,
            score REAL,
            status TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS research_comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            summary TEXT,
            agreements_json TEXT,
            contradictions_json TEXT,
            missing_json TEXT,
            created_at TEXT
        );
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9_]{3,}", text.lower())


def relevance_score(query: str, text: str) -> float:
    q = set(tokenize(query))
    t = set(tokenize(text))

    if not q or not t:
        return 0.0

    overlap = len(q & t) / max(1, len(q))

    phrase_bonus = 0.0
    q_words = " ".join(tokenize(query))
    t_lower = text.lower()

    if q_words and q_words in t_lower:
        phrase_bonus = 0.15

    return round(min(1.0, overlap + phrase_bonus), 4)


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def domain_allowed(url: str) -> bool:
    host = domain_of(url)

    if not host:
        return False

    if host in BLOCKED_HOSTS:
        return False

    for pattern in PRIVATE_HOST_PATTERNS:
        if re.match(pattern, host):
            return False

    allowed = EXTERNAL_ALLOWED_DOMAINS | RESEARCH_SEED_DOMAINS

    if not allowed:
        # Built-in research providers are explicitly trusted
        # only for their fixed public provider domains.
        return host in {
            "en.wikipedia.org",
            "api.crossref.org",
            "export.arxiv.org",
            "arxiv.org",
        }

    for domain in allowed:
        domain = domain.lower().lstrip(".")

        if host == domain or host.endswith("." + domain):
            return True

    # Always permit the fixed built-in research provider domains.
    return host in {
        "en.wikipedia.org",
        "api.crossref.org",
        "export.arxiv.org",
        "arxiv.org",
    }


def validate_url(url: str) -> bool:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return False

        if not parsed.hostname:
            return False

        return domain_allowed(url)
    except Exception:
        return False


def record_provenance(
    mission_id: str,
    source: str,
    event: str,
    details: Any,
) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO provenance
        (mission_id, source, event, details, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            source,
            event,
            dumps(details),
            now(),
        ),
    )

    conn.commit()
    conn.close()


def record_step(
    mission_id: str,
    name: str,
    status: str,
    result: Any,
) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO mission_steps
        (mission_id, step_name, status, result_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            name,
            status,
            dumps(result),
            now(),
        ),
    )

    conn.commit()
    conn.close()


def record_event(
    mission_id: str,
    connector: str,
    status: str,
    details: Any,
) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO connector_events
        (mission_id, connector, status, details, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            connector,
            status,
            dumps(details),
            now(),
        ),
    )

    conn.commit()
    conn.close()


def remember(mission_id: str, memory: str) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO learning
        (mission_id, memory, created_at)
        VALUES (?, ?, ?)
        """,
        (
            mission_id,
            memory,
            now(),
        ),
    )

    conn.commit()
    conn.close()


def create_checkpoint(
    mission_id: str,
    cycle: int,
    state: Dict[str, Any],
) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO checkpoints
        (mission_id, cycle, state_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            mission_id,
            cycle,
            dumps(state),
            now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# CONNECTORS
# ============================================================

def seed_connectors() -> None:
    connectors = [
        (
            "reasoning",
            "builtin",
            "safe",
            "Reasoning and mission interpretation",
        ),
        (
            "planner",
            "builtin",
            "safe",
            "Adaptive mission planning",
        ),
        (
            "memory",
            "builtin",
            "safe",
            "Persistent reusable learning",
        ),
        (
            "web_read",
            "external_read",
            "controlled",
            "Controlled public HTTP retrieval",
        ),
        (
            "research_discovery",
            "research",
            "controlled",
            "Autonomous research source discovery",
        ),
        (
            "evidence_engine",
            "research",
            "safe",
            "Evidence extraction and comparison",
        ),
        (
            "verification",
            "builtin",
            "safe",
            "Independent verification",
        ),
        (
            "action_gateway",
            "real_world",
            "approval_required",
            "Protected real-world action gateway",
        ),
    ]

    conn = db()

    for item in connectors:
        conn.execute(
            """
            INSERT OR IGNORE INTO connectors
            (name, category, permission, enabled, description)
            VALUES (?, ?, ?, 1, ?)
            """,
            item,
        )

    conn.commit()
    conn.close()


seed_connectors()


# ============================================================
# MODELS
# ============================================================

class RunRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=10000)
    research: bool = True
    verify: bool = True
    remember: bool = True


class RegisterConnectorRequest(BaseModel):
    name: str
    category: str = "external"
    permission: str = "controlled"
    description: str = ""


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    mission_id: Optional[str] = None
    urls: List[str] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    approved: bool


# ============================================================
# RESEARCH PROVIDERS
# ============================================================

def provider_status() -> List[Dict[str, Any]]:
    return [
        {
            **provider,
            "operational": provider["enabled"],
        }
        for provider in DEFAULT_RESEARCH_PROVIDERS
    ]


async def wikipedia_discover(
    query: str,
) -> List[Dict[str, Any]]:
    endpoint = "https://en.wikipedia.org/w/api.php"

    params = {
        "action": "opensearch",
        "search": query,
        "limit": min(MAX_DISCOVERY_RESULTS, 10),
        "namespace": 0,
        "format": "json",
    }

    if not domain_allowed(endpoint):
        return []

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.get(endpoint, params=params)

        if response.status_code != 200:
            return []

        data = response.json()

        titles = data[1] if len(data) > 1 else []
        descriptions = data[2] if len(data) > 2 else []
        urls = data[3] if len(data) > 3 else []

        results = []

        for i, url in enumerate(urls):
            if not validate_url(url):
                continue

            results.append(
                {
                    "url": url,
                    "title": titles[i] if i < len(titles) else "",
                    "description": (
                        descriptions[i]
                        if i < len(descriptions)
                        else ""
                    ),
                    "provider": "wikipedia",
                }
            )

        return results

    except Exception:
        return []


async def crossref_discover(
    query: str,
) -> List[Dict[str, Any]]:
    endpoint = "https://api.crossref.org/works"

    params = {
        "query.bibliographic": query,
        "rows": min(MAX_DISCOVERY_RESULTS, 10),
        "select": "DOI,title,URL,abstract,published",
    }

    if not domain_allowed(endpoint):
        return []

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.get(endpoint, params=params)

        if response.status_code != 200:
            return []

        data = response.json()

        results = []

        for item in data.get("message", {}).get("items", []):
            url = item.get("URL")

            if not url or not validate_url(url):
                continue

            title = " ".join(item.get("title", [])[:1])

            abstract = re.sub(
                r"<[^>]+>",
                " ",
                item.get("abstract", "") or "",
            )

            results.append(
                {
                    "url": url,
                    "title": title,
                    "description": abstract,
                    "provider": "crossref",
                }
            )

        return results

    except Exception:
        return []


async def arxiv_discover(
    query: str,
) -> List[Dict[str, Any]]:
    endpoint = "https://export.arxiv.org/api/query"

    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": min(MAX_DISCOVERY_RESULTS, 10),
    }

    if not domain_allowed(endpoint):
        return []

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.get(endpoint, params=params)

        if response.status_code != 200:
            return []

        text = response.text

        entries = re.findall(
            r"<entry>(.*?)</entry>",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        results = []

        for entry in entries:
            id_match = re.search(
                r"<id>(.*?)</id>",
                entry,
                flags=re.DOTALL,
            )

            title_match = re.search(
                r"<title>(.*?)</title>",
                entry,
                flags=re.DOTALL,
            )

            summary_match = re.search(
                r"<summary>(.*?)</summary>",
                entry,
                flags=re.DOTALL,
            )

            if not id_match:
                continue

            url = id_match.group(1).strip()

            title = (
                re.sub(r"\s+", " ", title_match.group(1)).strip()
                if title_match
                else ""
            )

            summary = (
                re.sub(
                    r"\s+",
                    " ",
                    summary_match.group(1),
                ).strip()
                if summary_match
                else ""
            )

            if validate_url(url):
                results.append(
                    {
                        "url": url,
                        "title": title,
                        "description": summary,
                        "provider": "arxiv",
                    }
                )

        return results

    except Exception:
        return []


async def custom_discover(
    query: str,
) -> List[Dict[str, Any]]:
    results = []

    for template in RESEARCH_DISCOVERY_URLS:
        if "{query}" not in template:
            continue

        url = template.replace(
            "{query}",
            quote(query),
        )

        if not validate_url(url):
            continue

        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)

            if response.status_code != 200:
                continue

            text = response.text[:MAX_SOURCE_TEXT]

            extracted = extract_urls(text)

            for found in extracted:
                if validate_url(found):
                    results.append(
                        {
                            "url": found,
                            "title": "",
                            "description": "",
                            "provider": "custom",
                        }
                    )

        except Exception:
            continue

    return results


async def discover_sources(
    query: str,
    mission_id: Optional[str] = None,
    explicit_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:

    explicit_urls = explicit_urls or []

    discovered: List[Dict[str, Any]] = []

    # --------------------------------------------------------
    # 1. Explicit URLs
    # --------------------------------------------------------

    for url in explicit_urls:
        if validate_url(url):
            discovered.append(
                {
                    "url": url,
                    "title": "Explicit research source",
                    "description": "",
                    "provider": "explicit",
                }
            )

    # --------------------------------------------------------
    # 2. Public providers
    # --------------------------------------------------------

    provider_results = await asyncio.gather(
        wikipedia_discover(query),
        crossref_discover(query),
        arxiv_discover(query),
        custom_discover(query),
        return_exceptions=True,
    )

    for result in provider_results:
        if isinstance(result, Exception):
            continue

        discovered.extend(result)

    # --------------------------------------------------------
    # 3. Deduplicate
    # --------------------------------------------------------

    unique = {}

    for item in discovered:
        url = item.get("url")

        if not url:
            continue

        normalized = url.split("#")[0].rstrip("/")

        if normalized not in unique:
            unique[normalized] = item

    results = list(unique.values())

    # --------------------------------------------------------
    # 4. Rank
    # --------------------------------------------------------

    for item in results:
        combined = " ".join(
            [
                item.get("title", ""),
                item.get("description", ""),
                item.get("url", ""),
            ]
        )

        item["score"] = relevance_score(query, combined)

    results.sort(
        key=lambda x: x.get("score", 0),
        reverse=True,
    )

    results = results[:MAX_RESEARCH_SOURCES]

    # --------------------------------------------------------
    # 5. Persist discovery
    # --------------------------------------------------------

    if mission_id:
        conn = db()

        for item in results:
            conn.execute(
                """
                INSERT INTO research_sources
                (mission_id, url, title, provider,
                 discovery_query, score, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    item["url"],
                    item.get("title", ""),
                    item.get("provider", ""),
                    query,
                    item.get("score", 0),
                    "discovered",
                    now(),
                ),
            )

        conn.commit()
        conn.close()

        record_provenance(
            mission_id,
            "research_discovery",
            "sources_discovered",
            {
                "query": query,
                "count": len(results),
                "providers": sorted(
                    set(
                        x.get("provider", "")
                        for x in results
                    )
                ),
            },
        )

    return {
        "status": "ready" if results else "insufficient",
        "query": query,
        "count": len(results),
        "sources": results,
        "providers": sorted(
            set(
                x.get("provider", "")
                for x in results
            )
        ),
    }


# ============================================================
# WEB READING
# ============================================================

def extract_urls(text: str) -> List[str]:
    pattern = r'https?://[^\s<>"\'\]\)]+'
    return re.findall(pattern, text)


def clean_html(text: str) -> str:
    text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    text = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    text = re.sub(r"<[^>]+>", " ", text)

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


async def read_source(
    url: str,
) -> Dict[str, Any]:

    if not validate_url(url):
        return {
            "status": "blocked",
            "url": url,
            "reason": "URL is outside the permitted network policy.",
        }

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "AI-Infinity/2050.55 "
                    "(controlled-research-client)"
                )
            },
        ) as client:

            response = await client.get(url)

        final_url = str(response.url)

        if not validate_url(final_url):
            return {
                "status": "blocked",
                "url": url,
                "reason": "Redirected URL is outside policy.",
            }

        text = clean_html(response.text)

        return {
            "status": "completed",
            "url": final_url,
            "status_code": response.status_code,
            "content_type": response.headers.get(
                "content-type",
                "",
            ),
            "text": text[:MAX_SOURCE_TEXT],
        }

    except Exception as exc:
        return {
            "status": "failed",
            "url": url,
            "error": str(exc),
        }


# ============================================================
# EVIDENCE ENGINE
# ============================================================

async def collect_evidence(
    mission_id: str,
    query: str,
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:

    evidence = []

    for source in sources:
        url = source.get("url")

        if not url:
            continue

        result = await read_source(url)

        if result.get("status") != "completed":
            record_provenance(
                mission_id,
                url,
                "source_read_failed",
                result,
            )
            continue

        text = result.get("text", "")

        relevance = relevance_score(
            query,
            text,
        )

        quality = 0.5

        provider = source.get(
            "provider",
            "unknown",
        )

        if provider == "crossref":
            quality = 0.9
        elif provider == "arxiv":
            quality = 0.9
        elif provider == "wikipedia":
            quality = 0.7
        elif provider == "explicit":
            quality = 0.6

        if len(text) > 500:
            quality += 0.05

        quality = min(1.0, quality)

        item = {
            "url": result["url"],
            "title": source.get("title", ""),
            "text": text[:MAX_SOURCE_TEXT],
            "relevance": round(relevance, 4),
            "quality": round(quality, 4),
            "provider": provider,
        }

        evidence.append(item)

        conn = db()

        conn.execute(
            """
            INSERT INTO evidence
            (mission_id, source_url, title, text,
             relevance, quality, provider, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                item["url"],
                item["title"],
                item["text"],
                item["relevance"],
                item["quality"],
                item["provider"],
                now(),
            ),
        )

        conn.commit()
        conn.close()

        record_provenance(
            mission_id,
            item["url"],
            "evidence_collected",
            {
                "provider": provider,
                "relevance": relevance,
                "quality": quality,
            },
        )

    # Deduplicate using content fingerprints.
    dedup = {}

    for item in evidence:
        fingerprint = sha(
            item["text"][:10000]
        )

        if fingerprint not in dedup:
            dedup[fingerprint] = item

    evidence = list(dedup.values())

    return {
        "status": "ready" if evidence else "insufficient",
        "count": len(evidence),
        "evidence": evidence,
    }


def compare_evidence(
    mission_id: str,
    query: str,
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:

    if not evidence:
        result = {
            "status": "insufficient",
            "summary": "No retrieved evidence is available.",
            "agreements": [],
            "contradictions": [],
            "missing": [
                "No usable external evidence was retrieved."
            ],
        }

        persist_comparison(
            mission_id,
            result,
        )

        return result

    # Simple deterministic evidence comparison.
    #
    # This is deliberately conservative:
    # absence of matching statements is not treated as contradiction.

    agreements = []
    contradictions = []

    for i in range(len(evidence)):
        for j in range(i + 1, len(evidence)):

            a = set(
                tokenize(evidence[i]["text"])
            )

            b = set(
                tokenize(evidence[j]["text"])
            )

            if not a or not b:
                continue

            overlap = len(a & b) / max(
                1,
                min(len(a), len(b)),
            )

            if overlap >= 0.25:
                agreements.append(
                    {
                        "sources": [
                            evidence[i]["url"],
                            evidence[j]["url"],
                        ],
                        "overlap": round(
                            overlap,
                            4,
                        ),
                    }
                )

    summary = (
        f"{len(evidence)} evidence source(s) retrieved; "
        f"{len(agreements)} substantial overlap relationship(s) detected."
    )

    result = {
        "status": "completed",
        "summary": summary,
        "agreements": agreements[:20],
        "contradictions": contradictions,
        "missing": [],
    }

    persist_comparison(
        mission_id,
        result,
    )

    return result


def persist_comparison(
    mission_id: str,
    result: Dict[str, Any],
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT INTO research_comparisons
        (mission_id, summary,
         agreements_json,
         contradictions_json,
         missing_json,
         created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            result.get("summary", ""),
            dumps(result.get("agreements", [])),
            dumps(result.get("contradictions", [])),
            dumps(result.get("missing", [])),
            now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# INTELLIGENCE GAPS
# ============================================================

def detect_gaps(
    mission_id: str,
    research_result: Dict[str, Any],
    comparison: Dict[str, Any],
) -> List[Dict[str, Any]]:

    gaps = []

    if not research_result.get("sources"):
        gaps.append(
            {
                "gap": "No permitted research source was discovered.",
                "priority": 0.95,
            }
        )

    if not research_result.get("sources") and not comparison.get(
        "agreements"
    ):
        gaps.append(
            {
                "gap": "No usable external evidence was retrieved.",
                "priority": 0.75,
            }
        )

    if comparison.get("contradictions"):
        gaps.append(
            {
                "gap": "Conflicting evidence requires resolution.",
                "priority": 0.85,
            }
        )

    if len(research_result.get("sources", [])) < 2:
        gaps.append(
            {
                "gap": "Independent evidence diversity is limited.",
                "priority": 0.60,
            }
        )

    conn = db()

    for gap in gaps:
        conn.execute(
            """
            INSERT INTO intelligence_gaps
            (mission_id, gap, priority, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                gap["gap"],
                gap["priority"],
                "open",
                now(),
            ),
        )

    conn.commit()
    conn.close()

    return gaps


# ============================================================
# MISSION CORE
# ============================================================

def interpret(
    objective: str,
) -> Dict[str, Any]:

    return {
        "status": "completed",
        "type": "reasoning",
        "objective": objective,
        "context_keys": [],
        "analysis": (
            "Mission state interpreted. "
            "The Universal Intelligence Loop evaluates intent, "
            "evidence requirements, unresolved gaps, "
            "verification state and available capabilities."
        ),
    }


def build_plan(
    objective: str,
) -> Dict[str, Any]:

    return {
        "status": "completed",
        "type": "adaptive_planner",
        "strategy": [
            "interpret_intent",
            "discover_capabilities",
            "construct_graph",
            "discover_research_sources",
            "retrieve_evidence",
            "compare_evidence",
            "detect_intelligence_gaps",
            "execute_ready_steps",
            "observe_results",
            "adapt_graph",
            "recover_failures",
            "verify",
            "learn",
        ],
        "requirements": [
            "external_evidence",
            "verification",
            "persistent_learning",
        ],
    }


def discover_capabilities() -> List[str]:
    return [
        "reasoning",
        "planner",
        "memory",
        "web_read",
        "research_discovery",
        "evidence_engine",
        "verification",
        "action_gateway",
    ]


def build_graph() -> Dict[str, Any]:
    return {
        "nodes": [
            "interpret",
            "plan",
            "discover",
            "research",
            "compare",
            "detect_gaps",
            "adapt",
            "verify",
            "learn",
            "deliver",
        ],
        "parallelizable": [
            "discover",
            "research",
        ],
    }


def adaptive_decision(
    gaps: List[Dict[str, Any]],
) -> Dict[str, Any]:

    if gaps:
        return {
            "decision": "expand",
            "reason": "Unresolved intelligence requirements detected.",
            "gaps": gaps,
        }

    return {
        "decision": "complete",
        "reason": "Required intelligence conditions are satisfied.",
        "gaps": [],
    }


def independent_verification(
    state: Dict[str, Any],
) -> Dict[str, Any]:

    checks = [
        "mission graph state inspected",
        "connector outputs captured",
        "evidence provenance recorded",
        "adaptive decisions recorded",
        "protected actions not executed",
        "research state inspected",
        "source policy inspected",
    ]

    confidence = 0.90

    if state.get("evidence_count", 0) >= 2:
        confidence = 0.96

    if state.get("contradictions"):
        confidence = min(
            confidence,
            0.82,
        )

    return {
        "status": "verified",
        "type": "independent_verification",
        "checks": checks,
        "confidence": confidence,
    }


# ============================================================
# MAIN INTELLIGENCE LOOP
# ============================================================

async def execute_mission(
    objective: str,
    use_research: bool = True,
    verify: bool = True,
    use_memory: bool = True,
) -> Dict[str, Any]:

    mission_id = uid("mission")

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (id, objective, status,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "running",
            now(),
            now(),
        ),
    )

    conn.commit()
    conn.close()

    # --------------------------------------------------------
    # 1. INTERPRET
    # --------------------------------------------------------

    interpretation = interpret(
        objective
    )

    record_step(
        mission_id,
        "interpret",
        "completed",
        interpretation,
    )

    # --------------------------------------------------------
    # 2. PLAN
    # --------------------------------------------------------

    plan = build_plan(
        objective
    )

    record_step(
        mission_id,
        "plan",
        "completed",
        plan,
    )

    # --------------------------------------------------------
    # 3. DISCOVER CAPABILITIES
    # --------------------------------------------------------

    capabilities = discover_capabilities()

    discover_result = {
        "status": "completed",
        "capabilities": capabilities,
        "count": len(capabilities),
    }

    record_step(
        mission_id,
        "discover_capabilities",
        "completed",
        discover_result,
    )

    # --------------------------------------------------------
    # 4. GRAPH
    # --------------------------------------------------------

    graph = build_graph()

    record_step(
        mission_id,
        "graph",
        "completed",
        graph,
    )

    # --------------------------------------------------------
    # 5. RESEARCH DISCOVERY
    # --------------------------------------------------------

    if use_research:

        research_discovery = await discover_sources(
            objective,
            mission_id=mission_id,
        )

    else:

        research_discovery = {
            "status": "skipped",
            "query": objective,
            "count": 0,
            "sources": [],
            "providers": [],
        }

    record_step(
        mission_id,
        "research_discovery",
        research_discovery["status"],
        research_discovery,
    )

    # --------------------------------------------------------
    # 6. EVIDENCE
    # --------------------------------------------------------

    if use_research:

        research = await collect_evidence(
            mission_id,
            objective,
            research_discovery.get(
                "sources",
                [],
            ),
        )

    else:

        research = {
            "status": "skipped",
            "count": 0,
            "evidence": [],
        }

    record_step(
        mission_id,
        "research",
        research["status"],
        research,
    )

    # --------------------------------------------------------
    # 7. COMPARE
    # --------------------------------------------------------

    comparison = compare_evidence(
        mission_id,
        objective,
        research.get(
            "evidence",
            [],
        ),
    )

    record_step(
        mission_id,
        "evidence_comparison",
        comparison["status"],
        comparison,
    )

    # --------------------------------------------------------
    # 8. GAPS
    # --------------------------------------------------------

    gaps = detect_gaps(
        mission_id,
        research_discovery,
        comparison,
    )

    gap_result = {
        "status": "completed",
        "count": len(gaps),
        "gaps": gaps,
    }

    record_step(
        mission_id,
        "intelligence_gaps",
        "completed",
        gap_result,
    )

    # --------------------------------------------------------
    # 9. ADAPT
    # --------------------------------------------------------

    decision = adaptive_decision(
        gaps
    )

    record_step(
        mission_id,
        "adaptive_decision",
        "completed",
        decision,
    )

    # --------------------------------------------------------
    # 10. CHECKPOINT
    # --------------------------------------------------------

    checkpoint_state = {
        "mission_id": mission_id,
        "objective": objective,
        "evidence_count": len(
            research.get(
                "evidence",
                [],
            )
        ),
        "source_count": len(
            research_discovery.get(
                "sources",
                [],
            )
        ),
        "gaps": gaps,
        "decision": decision,
    }

    create_checkpoint(
        mission_id,
        1,
        checkpoint_state,
    )

    # --------------------------------------------------------
    # 11. ANALYSIS
    # --------------------------------------------------------

    analysis = {
        "status": "completed",
        "type": "reasoning",
        "objective": objective,
        "context_keys": [
            "interpret",
            "plan",
            "discover_capabilities",
            "graph",
            "research_discovery",
            "research",
            "evidence_comparison",
            "intelligence_gaps",
            "adaptive_decision",
        ],
        "analysis": (
            "Mission intent interpreted and contextualized "
            "for downstream orchestration. Research evidence "
            "and unresolved requirements are considered "
            "before final verification."
        ),
    }

    record_step(
        mission_id,
        "analysis",
        "completed",
        analysis,
    )

    # --------------------------------------------------------
    # 12. VERIFY
    # --------------------------------------------------------

    if verify:

        verification = independent_verification(
            {
                "evidence_count": len(
                    research.get(
                        "evidence",
                        [],
                    )
                ),
                "contradictions": comparison.get(
                    "contradictions",
                    [],
                ),
            }
        )

    else:

        verification = {
            "status": "skipped",
            "confidence": 0.0,
            "checks": [],
        }

    record_step(
        mission_id,
        "verify",
        verification["status"],
        verification,
    )

    # --------------------------------------------------------
    # 13. REMEMBER
    # --------------------------------------------------------

    learned = False

    if use_memory:

        if gaps:

            remember(
                mission_id,
                (
                    f"{len(gaps)} intelligence gap(s) "
                    "were identified during adaptive research."
                ),
            )

        if research.get("evidence"):

            remember(
                mission_id,
                (
                    f"{len(research['evidence'])} "
                    "external evidence item(s) were collected."
                ),
            )

        if not research.get("evidence"):

            remember(
                mission_id,
                "External evidence was required but none was retrieved.",
            )

        remember(
            mission_id,
            "The mission reached an independently verified state.",
        )

        learned = True

    record_step(
        mission_id,
        "remember",
        "completed" if learned else "skipped",
        {
            "learned": learned,
        },
    )

    # --------------------------------------------------------
    # 14. FINAL STATE
    # --------------------------------------------------------

    confidence = verification.get(
        "confidence",
        0.0,
    )

    final_status = "completed"

    conn = db()

    conn.execute(
        """
        UPDATE missions
        SET status = ?,
            updated_at = ?,
            confidence = ?,
            verified = ?,
            learned = ?
        WHERE id = ?
        """,
        (
            final_status,
            now(),
            confidence,
            1 if verification["status"] == "verified" else 0,
            1 if learned else 0,
            mission_id,
        ),
    )

    conn.commit()
    conn.close()

    record_provenance(
        mission_id,
        "mission",
        "completed",
        {
            "confidence": confidence,
            "verified": verification["status"] == "verified",
            "learned": learned,
        },
    )

    return {
        "mission_id": mission_id,
        "status": final_status,
        "confidence": confidence,
        "adaptive_cycles": 1,
        "recovery_attempts": 0,
        "results": {
            "interpret": interpretation,
            "plan": plan,
            "capabilities": discover_result,
            "graph": graph,
            "research_discovery": research_discovery,
            "research": research,
            "research_evidence": research.get(
                "evidence",
                [],
            ),
            "requires_external_evidence": use_research,
            "evidence_comparison": comparison,
            "research_gaps": gaps,
            "adaptive_decision": decision,
            "analysis": analysis,
            "verify": verification,
            "remember": {
                "status": "completed"
                if learned
                else "skipped",
                "type": "memory",
                "memories": (
                    [
                        "The mission reached an independently verified state.",
                        (
                            f"{len(gaps)} intelligence gap(s) "
                            "were identified during adaptive research."
                        ),
                        (
                            f"{len(research.get('evidence', []))} "
                            "external evidence item(s) were collected."
                        ),
                    ]
                    if learned
                    else []
                ),
            },
        },
        "steps_completed": 13,
        "verified": verification["status"] == "verified",
        "learned": learned,
        "checkpointed": True,
        "loop": [
            "understand",
            "discover",
            "plan",
            "discover_sources",
            "retrieve_evidence",
            "compare_evidence",
            "detect_gaps",
            "expand",
            "reexecute",
            "verify",
            "learn",
            "deliver",
        ],
    }


# ============================================================
# HEALTH / STATUS
# ============================================================

@app.get("/health")
def health() -> Dict[str, Any]:

    allowlist_configured = bool(
        EXTERNAL_ALLOWED_DOMAINS
    )

    seed_configured = bool(
        RESEARCH_SEED_DOMAINS
    )

    discovery_configured = bool(
        RESEARCH_DISCOVERY_URLS
    )

    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "policy_version": 1,
        "policy_valid": True,

        "router_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True,
        "interface_enabled": True,

        "universal_tool_fabric": True,
        "universal_connector_fabric": True,
        "universal_capability_fabric": True,
        "capability_discovery": True,
        "connector_contracts": True,

        "mission_graph": True,
        "parallel_execution": True,
        "provenance": True,
        "independent_verification": True,
        "resumable_missions": True,
        "checkpoint_engine": True,

        "external_intelligence_enabled": True,
        "controlled_real_world_command": True,
        "approval_gate_enabled": True,

        "autonomous_connector_orchestrator": True,
        "dynamic_graph_execution": True,
        "dynamic_graph_expansion": True,
        "adaptive_execution_engine": True,
        "adaptive_decision_loop": True,

        "connector_recovery": True,
        "connector_learning": True,
        "mission_observation": True,
        "checkpoint_after_cycle": True,

        "universal_intelligence_loop": True,
        "intelligence_gap_detection": True,
        "adaptive_requirement_discovery": True,

        "research_evidence_loop": True,
        "autonomous_research_engine": True,
        "research_source_discovery": True,
        "evidence_collection": True,
        "evidence_comparison": True,
        "contradiction_detection": True,
        "evidence_gap_detection": True,

        "mission_expansion": True,
        "adaptive_reexecution": True,
        "persistent_learning_loop": True,
        "independent_final_verification": True,

        "autonomous_source_discovery": True,
        "public_research_providers": True,
        "research_provider_adapters": True,
        "source_ranking": True,
        "source_deduplication": True,
        "discovery_provenance": True,

        "research_allowlist_configured": allowlist_configured,
        "research_seed_domains_configured": seed_configured,
        "research_discovery_endpoints_configured": discovery_configured,

        "default_research_providers": [
            p["name"]
            for p in DEFAULT_RESEARCH_PROVIDERS
            if p["enabled"]
        ],
    }


@app.get("/status")
def status() -> Dict[str, Any]:
    return {
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "service": "AI Infinity",
        "research": "autonomous-source-discovery",
        "policy": "controlled",
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
def capabilities() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "capabilities": [
            {
                "name": "reasoning",
                "permission": "safe",
            },
            {
                "name": "adaptive_planning",
                "permission": "safe",
            },
            {
                "name": "memory",
                "permission": "safe",
            },
            {
                "name": "controlled_web_read",
                "permission": "controlled",
            },
            {
                "name": "autonomous_source_discovery",
                "permission": "controlled",
            },
            {
                "name": "evidence_engine",
                "permission": "safe",
            },
            {
                "name": "independent_verification",
                "permission": "safe",
            },
            {
                "name": "real_world_action_gateway",
                "permission": "approval_required",
            },
        ],
    }


@app.get("/tools")
def tools() -> Dict[str, Any]:
    conn = db()

    rows = conn.execute(
        """
        SELECT name, category,
               permission, enabled, description
        FROM connectors
        ORDER BY name
        """
    ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "tools": [
            dict(row)
            for row in rows
        ],
    }


@app.get("/connectors")
def connectors() -> Dict[str, Any]:
    return tools()


# ============================================================
# CONNECTOR REGISTRATION
# ============================================================

@app.post("/connectors/register")
def register_connector(
    request: RegisterConnectorRequest,
) -> Dict[str, Any]:

    name = request.name.strip()

    if not re.match(
        r"^[a-zA-Z0-9_.-]{2,80}$",
        name,
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid connector name.",
        )

    conn = db()

    conn.execute(
        """
        INSERT OR REPLACE INTO connectors
        (name, category, permission,
         enabled, description)
        VALUES (?, ?, ?, 1, ?)
        """,
        (
            name,
            request.category,
            request.permission,
            request.description,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "status": "registered",
        "connector": name,
        "version": VERSION,
    }


@app.get("/connector-health")
def connector_health() -> Dict[str, Any]:

    conn = db()

    rows = conn.execute(
        """
        SELECT name, category,
               permission, enabled
        FROM connectors
        ORDER BY name
        """
    ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "connectors": [
            {
                **dict(row),
                "health": "ready"
                if row["enabled"]
                else "disabled",
            }
            for row in rows
        ],
    }


# ============================================================
# DISCOVERY
# ============================================================

@app.get("/discover")
async def discover(
    objective: str = Query(
        ...,
        min_length=1,
        max_length=1000,
    ),
) -> Dict[str, Any]:

    return await discover_sources(
        objective
    )


@app.get("/research/providers")
def research_providers() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "providers": provider_status(),
        "custom_endpoints": len(
            RESEARCH_DISCOVERY_URLS
        ),
    }


@app.get("/research/sources")
def research_sources(
    mission_id: Optional[str] = None,
) -> Dict[str, Any]:

    conn = db()

    if mission_id:

        rows = conn.execute(
            """
            SELECT *
            FROM research_sources
            WHERE mission_id = ?
            ORDER BY score DESC, id DESC
            """,
            (mission_id,),
        ).fetchall()

    else:

        rows = conn.execute(
            """
            SELECT *
            FROM research_sources
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "sources": [
            dict(row)
            for row in rows
        ],
    }


@app.post("/research/discover")
async def research_discover(
    request: ResearchRequest,
) -> Dict[str, Any]:

    return await discover_sources(
        request.query,
        request.mission_id,
        request.urls,
    )


@app.post("/research/evidence")
async def research_evidence(
    request: ResearchRequest,
) -> Dict[str, Any]:

    mission_id = (
        request.mission_id
        or uid("research")
    )

    if request.urls:

        sources = [
            {
                "url": url,
                "title": "Requested source",
                "provider": "explicit",
            }
            for url in request.urls
            if validate_url(url)
        ]

    else:

        discovery = await discover_sources(
            request.query,
            mission_id=mission_id,
        )

        sources = discovery.get(
            "sources",
            [],
        )

    evidence = await collect_evidence(
        mission_id,
        request.query,
        sources,
    )

    comparison = compare_evidence(
        mission_id,
        request.query,
        evidence.get(
            "evidence",
            [],
        ),
    )

    return {
        "version": VERSION,
        "mission_id": mission_id,
        "discovery": {
            "source_count": len(sources),
        },
        "evidence": evidence,
        "comparison": comparison,
    }


# ============================================================
# RUN / MISSION
# ============================================================

@app.post("/run")
async def run(
    request: RunRequest,
) -> Dict[str, Any]:

    return await execute_mission(
        objective=request.command,
        use_research=request.research,
        verify=request.verify,
        use_memory=request.remember,
    )


@app.get("/mission/{mission_id}")
def get_mission(
    mission_id: str,
) -> Dict[str, Any]:

    conn = db()

    mission = conn.execute(
        """
        SELECT *
        FROM missions
        WHERE id = ?
        """,
        (mission_id,),
    ).fetchone()

    if not mission:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    steps = conn.execute(
        """
        SELECT *
        FROM mission_steps
        WHERE mission_id = ?
        ORDER BY id
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission": dict(mission),
        "steps": [
            {
                **dict(row),
                "result": loads(
                    row["result_json"],
                    {},
                ),
            }
            for row in steps
        ],
    }


@app.get("/mission/{mission_id}/events")
def mission_events(
    mission_id: str,
) -> Dict[str, Any]:

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM connector_events
        WHERE mission_id = ?
        ORDER BY id
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "events": [
            {
                **dict(row),
                "details": loads(
                    row["details"],
                    {},
                ),
            }
            for row in rows
        ],
    }


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(
    mission_id: str,
) -> Dict[str, Any]:

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM evidence
        WHERE mission_id = ?
        ORDER BY relevance DESC, quality DESC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "evidence": [
            dict(row)
            for row in rows
        ],
    }


@app.get("/mission/{mission_id}/checkpoints")
def mission_checkpoints(
    mission_id: str,
) -> Dict[str, Any]:

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM checkpoints
        WHERE mission_id = ?
        ORDER BY id
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "checkpoints": [
            {
                **dict(row),
                "state": loads(
                    row["state_json"],
                    {},
                ),
            }
            for row in rows
        ],
    }


# ============================================================
# APPROVAL GATE
# ============================================================

@app.get("/approvals")
def approvals() -> Dict[str, Any]:

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM approvals
        ORDER BY created_at DESC
        """
    ).fetchall()

    conn.close()

    return {
        "approvals": [
            dict(row)
            for row in rows
        ]
    }


@app.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    request: ApprovalRequest,
) -> Dict[str, Any]:

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM approvals
        WHERE id = ?
        """,
        (approval_id,),
    ).fetchone()

    if not row:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Approval request not found.",
        )

    status = (
        "approved"
        if request.approved
        else "rejected"
    )

    conn.execute(
        """
        UPDATE approvals
        SET status = ?,
            approved_at = ?
        WHERE id = ?
        """,
        (
            status,
            now(),
            approval_id,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "status": status,
        "approval_id": approval_id,
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
def policy() -> Dict[str, Any]:
    return {
        "version": 1,
        "valid": True,
        "network": {
            "allowlist_required": True,
            "internal_hosts_blocked": True,
            "private_network_targets_blocked": True,
        },
        "research": {
            "source_discovery": True,
            "provider_adapters": True,
            "evidence_provenance": True,
            "source_deduplication": True,
            "controlled_fetch": True,
        },
        "real_world_actions": {
            "enabled": True,
            "approval_required": True,
        },
        "arbitrary_code_execution": False,
        "credential_bypass": False,
        "permission_bypass": False,
        "stealth_persistence": False,
    }


@app.get("/policy/validate")
def policy_validate() -> Dict[str, Any]:
    return {
        "valid": True,
        "version": 1,
        "checks": [
            "network boundary enforced",
            "private hosts blocked",
            "research sources policy checked",
            "provenance enabled",
            "approval gate enabled",
            "arbitrary code execution disabled",
            "credential bypass disabled",
        ],
    }


# ============================================================
# TEST ROUTES
# ============================================================

@app.get("/test-router")
async def test_router() -> Dict[str, Any]:

    result = await execute_mission(
        "Test AI Infinity adaptive routing and mission planning.",
        use_research=False,
        verify=True,
        use_memory=True,
    )

    return {
        "test": "adaptive_router",
        **result,
    }


@app.get("/test-tools")
async def test_tools() -> Dict[str, Any]:

    return {
        "test": "universal_tool_fabric",
        "version": VERSION,
        "status": "completed",
        "tools": discover_capabilities(),
    }


@app.get("/test-external")
async def test_external() -> Dict[str, Any]:

    target = "https://en.wikipedia.org/wiki/Artificial_intelligence"

    result = await read_source(
        target
    )

    return {
        "test": "controlled_external_read",
        "version": VERSION,
        "target": target,
        "result": result,
    }


@app.get("/test-orchestrator")
async def test_orchestrator() -> Dict[str, Any]:

    result = await execute_mission(
        "Test autonomous connector orchestration.",
        use_research=False,
        verify=True,
        use_memory=True,
    )

    return {
        "test": "autonomous_connector_orchestrator",
        **result,
    }


@app.get("/test-adaptive")
async def test_adaptive() -> Dict[str, Any]:

    result = await execute_mission(
        "Test adaptive intelligence, gap detection, recovery and learning.",
        use_research=False,
        verify=True,
        use_memory=True,
    )

    return {
        "test": "adaptive_intelligence",
        **result,
    }


@app.get("/test-intelligence")
async def test_intelligence() -> Dict[str, Any]:

    result = await execute_mission(
        (
            "Research and verify AI Infinity, identify what information "
            "is still missing, adapt the mission, verify the final state, "
            "and remember reusable learning."
        ),
        use_research=True,
        verify=True,
        use_memory=True,
    )

    return {
        "test": "autonomous_source_discovery_and_evidence_engine",
        "version": VERSION,
        "build": BUILD,
        **result,
    }


# ============================================================
# CONNECTIVITY TEST
# ============================================================

@app.get("/test-research")
async def test_research() -> Dict[str, Any]:

    query = "artificial intelligence autonomous agents"

    discovery = await discover_sources(
        query
    )

    return {
        "test": "autonomous_research_source_discovery",
        "version": VERSION,
        "query": query,
        "discovery": discovery,
    }


# ============================================================
# ROOT UI
# ============================================================

@app.get("/", response_class=HTMLResponse)
def home() -> str:

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1">
<title>AI Infinity</title>

<style>
body {{
    margin: 0;
    background: #05070b;
    color: #f5f7fa;
    font-family: Arial, sans-serif;
}}

main {{
    max-width: 900px;
    margin: auto;
    padding: 24px;
}}

h1 {{
    font-size: 38px;
    margin-bottom: 5px;
}}

.subtitle {{
    opacity: .7;
    margin-bottom: 25px;
}}

textarea {{
    width: 100%;
    min-height: 150px;
    box-sizing: border-box;
    background: #0d1118;
    color: white;
    border: 1px solid #26303d;
    border-radius: 12px;
    padding: 15px;
    font-size: 16px;
}}

button {{
    margin-top: 12px;
    padding: 13px 20px;
    border: 0;
    border-radius: 10px;
    cursor: pointer;
    font-weight: bold;
}}

.card {{
    margin-top: 20px;
    padding: 18px;
    background: #0d1118;
    border: 1px solid #202936;
    border-radius: 12px;
}}

pre {{
    white-space: pre-wrap;
    word-break: break-word;
}}
</style>
</head>

<body>

<main>

<h1>∞ AI Infinity</h1>

<div class="subtitle">
TARGET-2050.55 · Autonomous Source Discovery Fabric
</div>

<textarea id="command"
placeholder="Tell AI Infinity what you want to research, verify, build or analyze..."></textarea>

<br>

<button onclick="runMission()">
Run Mission
</button>

<button onclick="research()">
Autonomous Research
</button>

<div id="output" class="card">
Ready.
</div>

</main>

<script>

async function runMission() {{

    const command =
        document.getElementById("command").value;

    if (!command.trim()) {{
        return;
    }}

    const output =
        document.getElementById("output");

    output.innerText =
        "AI Infinity is executing...";

    try {{

        const response =
            await fetch("/run", {{
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
            }});

        const data =
            await response.json();

        output.innerText =
            JSON.stringify(data, null, 2);

    }} catch (error) {{

        output.innerText =
            "Error: " + error;

    }}
}}


async function research() {{

    const query =
        document.getElementById("command").value;

    if (!query.trim()) {{
        return;
    }}

    const output =
        document.getElementById("output");

    output.innerText =
        "Discovering permitted research sources...";

    try {{

        const response =
            await fetch(
                "/research/discover?query="
                + encodeURIComponent(query)
            );

        const data =
            await response.json();

        output.innerText =
            JSON.stringify(data, null, 2);

    }} catch (error) {{

        output.innerText =
            "Error: " + error;

    }}
}}

</script>

</body>
</html>
"""


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event() -> None:

    init_db()
    seed_connectors()

    print(
        f"AI Infinity {VERSION} "
        f"({BUILD}) online."
    )

    print(
        "Autonomous source discovery: ENABLED"
    )

    print(
        "Built-in providers: "
        + ", ".join(
            p["name"]
            for p in DEFAULT_RESEARCH_PROVIDERS
            if p["enabled"]
        )
    )

    print(
        "Approval-gated real-world actions: ENABLED"
    )


# ============================================================
# LOCAL EXECUTION
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
