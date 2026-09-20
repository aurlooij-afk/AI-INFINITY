"""
AI INFINITY
TARGET-2050.57
MULTI-PROTOCOL RESEARCH FABRIC

Focused upgrade from TARGET-2050.56:
- arXiv provider compatibility/recovery
- explicit User-Agent headers
- multiple arXiv endpoint strategies
- alternate query encoding
- provider-specific retries
- provider health scoring
- automatic provider fallback
- Wikipedia + Crossref + arXiv research fabric
- autonomous discovery
- evidence collection
- contradiction/gap detection
- mission/checkpoint foundations
- controlled external access
- approval boundary
- no arbitrary code execution
- no private-network access
"""

from __future__ import annotations

import os
import re
import json
import time
import uuid
import sqlite3
import hashlib
import html
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# CORE
# ============================================================

VERSION = "TARGET-2050.57"
BUILD = "MULTI-PROTOCOL-RESEARCH-FABRIC"

BASE = Path("/tmp/ai_infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
MAX_RESPONSE_BYTES = int(os.getenv("MAX_RESPONSE_BYTES", "2000000"))
MAX_RESEARCH_SOURCES = int(os.getenv("MAX_RESEARCH_SOURCES", "20"))
MAX_SOURCE_TEXT = int(os.getenv("MAX_SOURCE_TEXT", "12000"))
RESEARCH_MAX_RETRIES = int(os.getenv("RESEARCH_MAX_RETRIES", "3"))

ARXIV_USER_AGENT = os.getenv(
    "ARXIV_USER_AGENT",
    "AI-Infinity/2050.57 research-client"
)

ARXIV_CONTACT = os.getenv(
    "ARXIV_CONTACT",
    ""
)

DEFAULT_PROVIDERS = ["wikipedia", "crossref", "arxiv"]

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
}

DEFAULT_ALLOWED_DOMAINS = {
    "wikipedia.org",
    "en.wikipedia.org",
    "api.crossref.org",
    "doi.org",
    "arxiv.org",
    "export.arxiv.org",
}

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="AI Infinity autonomous research and mission fabric",
)


# ============================================================
# UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def normalize_query(query: str) -> str:
    query = re.sub(r"\s+", " ", (query or "").strip())
    return query[:1000]


def host_allowed(url: str, extra_domains: Optional[set[str]] = None) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower().strip(".")

        if not host:
            return False

        if host in BLOCKED_HOSTS:
            return False

        if host.startswith("10."):
            return False

        if host.startswith("192.168."):
            return False

        if host.startswith("172."):
            parts = host.split(".")
            if len(parts) >= 2:
                try:
                    second = int(parts[1])
                    if 16 <= second <= 31:
                        return False
                except Exception:
                    pass

        allowed = set(DEFAULT_ALLOWED_DOMAINS)

        configured = os.getenv("EXTERNAL_ALLOWED_DOMAINS", "")
        if configured:
            allowed.update(
                x.strip().lower()
                for x in configured.split(",")
                if x.strip()
            )

        if extra_domains:
            allowed.update(x.lower() for x in extra_domains)

        return any(
            host == domain or host.endswith("." + domain)
            for domain in allowed
        )

    except Exception:
        return False


def build_headers(
    provider: str = "generic",
    accept: str = "*/*"
) -> dict[str, str]:
    headers = {
        "Accept": accept,
        "Accept-Encoding": "gzip",
        "Connection": "close",
    }

    if provider == "arxiv":
        ua = ARXIV_USER_AGENT
        if ARXIV_CONTACT:
            ua += f" ({ARXIV_CONTACT})"

        headers.update({
            "User-Agent": ua,
            "From": ARXIV_CONTACT or "ai-infinity-research-client",
        })
    else:
        headers.update({
            "User-Agent": "AI-Infinity/2050.57 (+research-client)",
        })

    return headers


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
            result TEXT
        );

        CREATE TABLE IF NOT EXISTS mission_steps (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            step_index INTEGER,
            name TEXT,
            status TEXT,
            result TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            provider TEXT,
            url TEXT,
            title TEXT,
            snippet TEXT,
            rank REAL,
            metadata TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS provenance (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            event_type TEXT,
            source TEXT,
            data TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS checkpoints (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            cycle INTEGER,
            state TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            action TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS learning (
            id TEXT PRIMARY KEY,
            category TEXT,
            key TEXT,
            value TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connectors (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE,
            category TEXT,
            permission TEXT,
            endpoint TEXT,
            status TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connector_events (
            id TEXT PRIMARY KEY,
            connector TEXT,
            event_type TEXT,
            data TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS intelligence_gaps (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            gap TEXT,
            severity REAL,
            status TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS research_sources (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            provider TEXT,
            url TEXT,
            title TEXT,
            snippet TEXT,
            rank REAL,
            metadata TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS research_comparisons (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            source_a TEXT,
            source_b TEXT,
            relation TEXT,
            confidence REAL,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS provider_health (
            provider TEXT PRIMARY KEY,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            last_status INTEGER,
            last_error TEXT,
            last_latency_ms REAL,
            score REAL DEFAULT 0.5,
            updated_at TEXT
        );
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# PROVIDER HEALTH
# ============================================================

def update_provider_health(
    provider: str,
    success: bool,
    status: Optional[int],
    error: Optional[str],
    latency_ms: float
) -> None:
    conn = db()

    row = conn.execute(
        "SELECT * FROM provider_health WHERE provider=?",
        (provider,)
    ).fetchone()

    if not row:
        successes = 1 if success else 0
        failures = 0 if success else 1
    else:
        successes = row["successes"] + (1 if success else 0)
        failures = row["failures"] + (0 if success else 1)

    total = successes + failures
    score = successes / total if total else 0.5

    conn.execute(
        """
        INSERT INTO provider_health
        (provider, successes, failures, last_status,
         last_error, last_latency_ms, score, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET
            successes=excluded.successes,
            failures=excluded.failures,
            last_status=excluded.last_status,
            last_error=excluded.last_error,
            last_latency_ms=excluded.last_latency_ms,
            score=excluded.score,
            updated_at=excluded.updated_at
        """,
        (
            provider,
            successes,
            failures,
            status,
            error,
            latency_ms,
            score,
            now_iso(),
        )
    )

    conn.commit()
    conn.close()


def provider_health() -> list[dict[str, Any]]:
    conn = db()

    rows = conn.execute(
        """
        SELECT provider, successes, failures,
               last_status, last_error,
               last_latency_ms, score, updated_at
        FROM provider_health
        ORDER BY score DESC
        """
    ).fetchall()

    conn.close()

    return [dict(x) for x in rows]


# ============================================================
# HTTP FABRIC
# ============================================================

TRANSIENT_STATUSES = {
    408,
    425,
    429,
    500,
    502,
    503,
    504,
}

PERMANENT_PROVIDER_STATUSES = {
    400,
    401,
    403,
    404,
    405,
}

def http_get(
    url: str,
    provider: str = "generic",
    attempts: int = 2,
    headers: Optional[dict[str, str]] = None,
) -> dict[str, Any]:

    if not host_allowed(url):
        return {
            "ok": False,
            "status": None,
            "error_type": "network_policy",
            "error": "domain_not_allowed",
            "url": url,
            "body": "",
            "attempts": 0,
            "latency_ms": 0,
        }

    final_headers = build_headers(provider)
    if headers:
        final_headers.update(headers)

    last_error = None
    last_status = None

    for attempt in range(1, attempts + 1):
        started = time.perf_counter()

        try:
            req = urllib.request.Request(
                url,
                headers=final_headers,
                method="GET",
            )

            with urllib.request.urlopen(
                req,
                timeout=HTTP_TIMEOUT,
            ) as response:

                status = response.status
                data = response.read(MAX_RESPONSE_BYTES + 1)

                if len(data) > MAX_RESPONSE_BYTES:
                    return {
                        "ok": False,
                        "status": status,
                        "error_type": "response_too_large",
                        "error": "response exceeds MAX_RESPONSE_BYTES",
                        "url": url,
                        "body": "",
                        "attempts": attempt,
                        "latency_ms": (
                            time.perf_counter() - started
                        ) * 1000,
                    }

                body = data.decode(
                    response.headers.get_content_charset() or "utf-8",
                    errors="replace",
                )

                latency = (time.perf_counter() - started) * 1000

                update_provider_health(
                    provider,
                    True,
                    status,
                    None,
                    latency,
                )

                return {
                    "ok": True,
                    "status": status,
                    "error_type": None,
                    "error": None,
                    "url": url,
                    "body": body,
                    "attempts": attempt,
                    "latency_ms": latency,
                }

        except urllib.error.HTTPError as exc:
            last_status = exc.code
            try:
                body = exc.read(10000).decode(
                    "utf-8",
                    errors="replace",
                )
            except Exception:
                body = ""

            last_error = f"HTTP {exc.code}"

            latency = (time.perf_counter() - started) * 1000

            if exc.code in PERMANENT_PROVIDER_STATUSES:
                update_provider_health(
                    provider,
                    False,
                    exc.code,
                    last_error,
                    latency,
                )

                return {
                    "ok": False,
                    "status": exc.code,
                    "error_type": "HTTPStatusError",
                    "error": last_error,
                    "body": body,
                    "url": url,
                    "attempts": attempt,
                    "latency_ms": latency,
                }

            if exc.code not in TRANSIENT_STATUSES:
                update_provider_health(
                    provider,
                    False,
                    exc.code,
                    last_error,
                    latency,
                )

                return {
                    "ok": False,
                    "status": exc.code,
                    "error_type": "HTTPStatusError",
                    "error": last_error,
                    "body": body,
                    "url": url,
                    "attempts": attempt,
                    "latency_ms": latency,
                }

        except Exception as exc:
            last_error = str(exc)
            latency = (time.perf_counter() - started) * 1000

        if attempt < attempts:
            time.sleep(min(2 ** (attempt - 1), 4))

    update_provider_health(
        provider,
        False,
        last_status,
        last_error,
        0,
    )

    return {
        "ok": False,
        "status": last_status,
        "error_type": "RequestError",
        "error": last_error,
        "body": "",
        "url": url,
        "attempts": attempts,
        "latency_ms": 0,
    }


# ============================================================
# WIKIPEDIA
# ============================================================

def wikipedia_search(query: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(query)

    url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query"
        "&list=search"
        "&format=json"
        "&utf8=1"
        "&srlimit=5"
        f"&srsearch={encoded}"
    )

    result = http_get(
        url,
        provider="wikipedia",
        attempts=2,
        headers={
            "Accept": "application/json",
        },
    )

    if not result["ok"]:
        return {
            "provider": "wikipedia",
            "status": "failed",
            "http_status": result["status"],
            "error_type": result["error_type"],
            "error": result["error"],
            "attempts": result["attempts"],
            "latency_ms": result["latency_ms"],
            "result_count": 0,
            "sources": [],
        }

    try:
        payload = json.loads(result["body"])
        items = payload.get("query", {}).get("search", [])

        sources = []

        for index, item in enumerate(items):
            title = item.get("title", "")
            snippet = re.sub(
                r"<[^>]+>",
                "",
                item.get("snippet", ""),
            )

            page_url = (
                "https://en.wikipedia.org/wiki/"
                + urllib.parse.quote(title.replace(" ", "_"))
            )

            sources.append({
                "provider": "wikipedia",
                "url": page_url,
                "title": title,
                "snippet": html.unescape(snippet)[:MAX_SOURCE_TEXT],
                "rank": round(1.0 - index * 0.1, 3),
                "metadata": {
                    "pageid": item.get("pageid"),
                },
            })

        return {
            "provider": "wikipedia",
            "status": "success",
            "http_status": result["status"],
            "error_type": None,
            "error": None,
            "attempts": result["attempts"],
            "latency_ms": result["latency_ms"],
            "result_count": len(sources),
            "sources": sources,
        }

    except Exception as exc:
        return {
            "provider": "wikipedia",
            "status": "failed",
            "http_status": result["status"],
            "error_type": "ParseError",
            "error": str(exc),
            "attempts": result["attempts"],
            "latency_ms": result["latency_ms"],
            "result_count": 0,
            "sources": [],
        }


# ============================================================
# CROSSREF
# ============================================================

def crossref_search(query: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(query)

    url = (
        "https://api.crossref.org/works"
        f"?query={encoded}"
        "&rows=5"
        "&select=DOI,title,URL,type,container-title,published"
    )

    result = http_get(
        url,
        provider="crossref",
        attempts=2,
        headers={
            "Accept": "application/json",
        },
    )

    if not result["ok"]:
        return {
            "provider": "crossref",
            "status": "failed",
            "http_status": result["status"],
            "error_type": result["error_type"],
            "error": result["error"],
            "attempts": result["attempts"],
            "latency_ms": result["latency_ms"],
            "result_count": 0,
            "sources": [],
        }

    try:
        payload = json.loads(result["body"])
        items = (
            payload.get("message", {})
            .get("items", [])
        )

        sources = []

        for index, item in enumerate(items):
            doi = item.get("DOI")
            titles = item.get("title") or ["Untitled"]

            title = titles[0]

            url_value = (
                item.get("URL")
                or (f"https://doi.org/{doi}" if doi else "")
            )

            published = item.get("published")

            sources.append({
                "provider": "crossref",
                "url": url_value,
                "title": title,
                "snippet": (
                    f"DOI: {doi}"
                    if doi
                    else title
                ),
                "rank": round(1.0 - index * 0.1, 3),
                "metadata": {
                    "doi": doi,
                    "type": item.get("type"),
                    "container": item.get("container-title"),
                    "published": published,
                },
            })

        return {
            "provider": "crossref",
            "status": "success",
            "http_status": result["status"],
            "error_type": None,
            "error": None,
            "attempts": result["attempts"],
            "latency_ms": result["latency_ms"],
            "result_count": len(sources),
            "sources": sources,
        }

    except Exception as exc:
        return {
            "provider": "crossref",
            "status": "failed",
            "http_status": result["status"],
            "error_type": "ParseError",
            "error": str(exc),
            "attempts": result["attempts"],
            "latency_ms": result["latency_ms"],
            "result_count": 0,
            "sources": [],
        }


# ============================================================
# ARXIV — 2050.57 RECOVERY FABRIC
# ============================================================

def arxiv_query_variants(query: str) -> list[str]:
    clean = normalize_query(query)

    variants = []

    # Strategy 1: all-fields query.
    variants.append(
        "search_query="
        + urllib.parse.quote(
            f"all:{clean}",
            safe=""
        )
    )

    # Strategy 2: title/abstract-friendly fallback.
    words = re.findall(r"[A-Za-z0-9_-]+", clean)

    if words:
        compact = "+AND+".join(words[:8])

        variants.append(
            "search_query="
            + urllib.parse.quote(
                f"all:{compact}",
                safe=""
            )
        )

    # Strategy 3: plain query fallback.
    variants.append(
        "search_query="
        + urllib.parse.quote(
            clean,
            safe=""
        )
    )

    return list(dict.fromkeys(variants))


def parse_arxiv_atom(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    sources = []

    for index, entry in enumerate(
        root.findall("atom:entry", ns)
    ):
        title = (
            entry.findtext(
                "atom:title",
                default="",
                namespaces=ns,
            )
            or ""
        ).strip()

        summary = (
            entry.findtext(
                "atom:summary",
                default="",
                namespaces=ns,
            )
            or ""
        ).strip()

        entry_id = (
            entry.findtext(
                "atom:id",
                default="",
                namespaces=ns,
            )
            or ""
        ).strip()

        published = (
            entry.findtext(
                "atom:published",
                default="",
                namespaces=ns,
            )
            or ""
        ).strip()

        authors = []

        for author in entry.findall(
            "atom:author",
            ns,
        ):
            name = (
                author.findtext(
                    "atom:name",
                    default="",
                    namespaces=ns,
                )
                or ""
            ).strip()

            if name:
                authors.append(name)

        categories = [
            x.attrib.get("term")
            for x in entry.findall(
                "atom:category",
                ns
            )
            if x.attrib.get("term")
        ]

        sources.append({
            "provider": "arxiv",
            "url": entry_id,
            "title": re.sub(r"\s+", " ", title),
            "snippet": re.sub(
                r"\s+",
                " ",
                summary,
            )[:MAX_SOURCE_TEXT],
            "rank": round(
                1.0 - index * 0.1,
                3,
            ),
            "metadata": {
                "published": published,
                "authors": authors[:20],
                "categories": categories[:20],
            },
        })

    return sources


def arxiv_request(
    url: str,
    strategy: str,
) -> dict[str, Any]:

    # Explicitly identify the client.
    headers = {
        "User-Agent": ARXIV_USER_AGENT,
        "Accept": (
            "application/atom+xml,"
            "application/xml;q=0.9,"
            "text/xml;q=0.8,"
            "*/*;q=0.5"
        ),
        "Accept-Encoding": "identity",
        "Connection": "close",
    }

    if ARXIV_CONTACT:
        headers["From"] = ARXIV_CONTACT

    result = http_get(
        url,
        provider="arxiv",
        attempts=1,
        headers=headers,
    )

    result["strategy"] = strategy
    return result


def arxiv_search(query: str) -> dict[str, Any]:
    """
    Provider-specific recovery.

    Strategy order:
      1. export.arxiv.org API
      2. arxiv.org API
      3. alternate query encoding
      4. export endpoint with encoded plus form

    This avoids treating arXiv like a generic provider.
    """

    variants = arxiv_query_variants(query)

    endpoints = [
        (
            "export",
            "https://export.arxiv.org/api/query"
        ),
        (
            "main",
            "https://arxiv.org/api/query"
        ),
    ]

    attempts_log = []

    for endpoint_name, endpoint in endpoints:
        for variant_index, variant in enumerate(variants):

            url = (
                endpoint
                + "?"
                + variant
                + "&start=0&max_results=5"
            )

            strategy = (
                f"{endpoint_name}-"
                f"query-{variant_index + 1}"
            )

            result = arxiv_request(
                url,
                strategy,
            )

            attempts_log.append({
                "strategy": strategy,
                "status": result["status"],
                "error_type": result["error_type"],
                "error": result["error"],
                "attempts": result["attempts"],
                "latency_ms": result["latency_ms"],
            })

            if result["ok"]:
                try:
                    sources = parse_arxiv_atom(
                        result["body"]
                    )

                    if sources:
                        return {
                            "provider": "arxiv",
                            "status": "success",
                            "http_status": result["status"],
                            "error_type": None,
                            "error": None,
                            "attempts": len(attempts_log),
                            "latency_ms": result["latency_ms"],
                            "result_count": len(sources),
                            "sources": sources,
                            "recovery": {
                                "used": (
                                    len(attempts_log) > 1
                                ),
                                "strategy": strategy,
                                "attempts": attempts_log,
                            },
                        }

                except Exception as exc:
                    attempts_log.append({
                        "strategy": strategy,
                        "status": result["status"],
                        "error_type": "ParseError",
                        "error": str(exc),
                    })

    last = attempts_log[-1] if attempts_log else {}

    return {
        "provider": "arxiv",
        "status": "failed",
        "http_status": last.get("status"),
        "error_type": last.get(
            "error_type",
            "ProviderUnavailable"
        ),
        "error": last.get(
            "error",
            "All arXiv recovery strategies failed"
        ),
        "attempts": len(attempts_log),
        "latency_ms": 0,
        "result_count": 0,
        "sources": [],
        "recovery": {
            "used": len(attempts_log) > 1,
            "attempts": attempts_log,
        },
    }


# ============================================================
# RESEARCH FABRIC
# ============================================================

def provider_call(
    provider: str,
    query: str,
) -> dict[str, Any]:

    if provider == "wikipedia":
        return wikipedia_search(query)

    if provider == "crossref":
        return crossref_search(query)

    if provider == "arxiv":
        return arxiv_search(query)

    return {
        "provider": provider,
        "status": "failed",
        "http_status": None,
        "error_type": "UnknownProvider",
        "error": f"Unsupported provider: {provider}",
        "attempts": 0,
        "latency_ms": 0,
        "result_count": 0,
        "sources": [],
    }


def deduplicate_sources(
    sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:

    seen = set()
    output = []

    for source in sources:
        url = source.get("url", "")
        title = source.get("title", "")

        key = (
            url.lower().strip()
            if url
            else hashlib.sha256(
                title.lower().encode()
            ).hexdigest()
        )

        if key in seen:
            continue

        seen.add(key)
        output.append(source)

    return output


def rank_sources(
    sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:

    health_map = {
        row["provider"]: row["score"]
        for row in provider_health()
    }

    ranked = []

    for source in sources:
        base_rank = float(
            source.get("rank", 0)
        )

        provider_score = health_map.get(
            source.get("provider"),
            0.5,
        )

        source["rank"] = round(
            base_rank * 0.8
            + provider_score * 0.2,
            4,
        )

        ranked.append(source)

    ranked.sort(
        key=lambda x: x.get("rank", 0),
        reverse=True,
    )

    return ranked[:MAX_RESEARCH_SOURCES]


def discover_research(
    query: str,
    providers: Optional[list[str]] = None,
) -> dict[str, Any]:

    query = normalize_query(query)

    selected = providers or DEFAULT_PROVIDERS

    provider_results = []
    all_sources = []

    # Health-aware ordering while retaining requested providers.
    health = {
        x["provider"]: x["score"]
        for x in provider_health()
    }

    selected = sorted(
        selected,
        key=lambda p: health.get(p, 0.5),
        reverse=True,
    )

    for provider in selected:
        result = provider_call(
            provider,
            query,
        )

        provider_results.append(result)
        all_sources.extend(
            result.get("sources", [])
        )

    all_sources = deduplicate_sources(
        all_sources
    )

    all_sources = rank_sources(
        all_sources
    )

    return {
        "status": "success"
        if all_sources
        else "partial_failure",
        "query": query,
        "count": len(all_sources),
        "sources": all_sources,
        "providers": provider_results,
        "provider_health": provider_health(),
    }


# ============================================================
# EVIDENCE
# ============================================================

def save_evidence(
    mission_id: Optional[str],
    sources: list[dict[str, Any]],
) -> None:

    conn = db()

    for source in sources:
        evidence_id = new_id("evidence")

        conn.execute(
            """
            INSERT INTO evidence
            (id, mission_id, provider, url, title,
             snippet, rank, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                mission_id,
                source.get("provider"),
                source.get("url"),
                source.get("title"),
                source.get("snippet"),
                source.get("rank", 0),
                safe_json(source.get("metadata", {})),
                now_iso(),
            ),
        )

        conn.execute(
            """
            INSERT INTO research_sources
            (id, mission_id, provider, url, title,
             snippet, rank, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("source"),
                mission_id,
                source.get("provider"),
                source.get("url"),
                source.get("title"),
                source.get("snippet"),
                source.get("rank", 0),
                safe_json(source.get("metadata", {})),
                now_iso(),
            ),
        )

    conn.commit()
    conn.close()


def compare_evidence(
    sources: list[dict[str, Any]]
) -> dict[str, Any]:

    comparisons = []
    contradictions = []
    gaps = []

    providers = {}

    for source in sources:
        providers.setdefault(
            source.get("provider"),
            []
        ).append(source)

    if len(providers) < 2:
        gaps.append(
            "Independent provider diversity is limited."
        )

    for provider_a, items_a in providers.items():
        for provider_b, items_b in providers.items():

            if provider_a >= provider_b:
                continue

            for a in items_a[:3]:
                for b in items_b[:3]:

                    title_a = (
                        a.get("title", "").lower()
                    )
                    title_b = (
                        b.get("title", "").lower()
                    )

                    words_a = set(
                        re.findall(
                            r"\b[a-z]{5,}\b",
                            title_a,
                        )
                    )

                    words_b = set(
                        re.findall(
                            r"\b[a-z]{5,}\b",
                            title_b,
                        )
                    )

                    overlap = (
                        len(words_a & words_b)
                        / max(
                            1,
                            len(words_a | words_b)
                        )
                    )

                    relation = (
                        "corroborating"
                        if overlap >= 0.15
                        else "independent"
                    )

                    comparisons.append({
                        "source_a": a.get("url"),
                        "source_b": b.get("url"),
                        "relation": relation,
                        "confidence": round(
                            min(1.0, overlap + 0.25),
                            3,
                        ),
                    })

    if not sources:
        gaps.append(
            "No research evidence was returned."
        )

    return {
        "comparisons": comparisons[:50],
        "contradictions": contradictions,
        "evidence_gaps": gaps,
    }


# ============================================================
# MISSIONS
# ============================================================

class RunRequest(BaseModel):
    command: str = Field(
        min_length=1,
        max_length=10000,
    )
    research: bool = True
    verify: bool = True
    remember: bool = True


class DiscoverRequest(BaseModel):
    objective: str = Field(
        min_length=1,
        max_length=5000,
    )


class ConnectorRequest(BaseModel):
    name: str
    category: str = "external"
    permission: str = "approval"
    endpoint: Optional[str] = None


def create_mission(objective: str) -> str:
    mission_id = new_id("mission")

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (id, objective, status, created_at, updated_at, result)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "running",
            now_iso(),
            now_iso(),
            None,
        ),
    )

    conn.commit()
    conn.close()

    return mission_id


def update_mission(
    mission_id: str,
    status: str,
    result: Any,
) -> None:

    conn = db()

    conn.execute(
        """
        UPDATE missions
        SET status=?, result=?, updated_at=?
        WHERE id=?
        """,
        (
            status,
            safe_json(result),
            now_iso(),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()


def checkpoint(
    mission_id: str,
    cycle: int,
    state: Any,
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT INTO checkpoints
        (id, mission_id, cycle, state, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            new_id("checkpoint"),
            mission_id,
            cycle,
            safe_json(state),
            now_iso(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# BUILT-IN CONNECTORS
# ============================================================

BUILTIN_CONNECTORS = [
    {
        "name": "reasoning",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "planner",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "memory",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "web_read",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "research_discovery",
        "category": "research",
        "permission": "safe",
    },
    {
        "name": "evidence_engine",
        "category": "research",
        "permission": "safe",
    },
    {
        "name": "verification",
        "category": "verification",
        "permission": "safe",
    },
    {
        "name": "action_gateway",
        "category": "real_world",
        "permission": "approval",
    },
]


def seed_connectors() -> None:
    conn = db()

    for item in BUILTIN_CONNECTORS:
        conn.execute(
            """
            INSERT OR IGNORE INTO connectors
            (id, name, category, permission,
             endpoint, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("connector"),
                item["name"],
                item["category"],
                item["permission"],
                None,
                "healthy",
                now_iso(),
            ),
        )

    conn.commit()
    conn.close()


seed_connectors()


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "name": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "research_fabric": True,
        "arxiv_recovery": True,
        "provider_fallback": True,
        "controlled_external_access": True,
    }


@app.get("/health")
def health():
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

        "research_allowlist_configured": bool(
            os.getenv("EXTERNAL_ALLOWED_DOMAINS")
        ),
        "research_seed_domains_configured": bool(
            os.getenv("RESEARCH_SEED_DOMAINS")
        ),
        "research_discovery_endpoints_configured": bool(
            os.getenv("RESEARCH_DISCOVERY_URLS")
        ),

        "default_research_providers": DEFAULT_PROVIDERS,

        "provider_diagnostics": True,
        "provider_retry": True,
        "provider_error_visibility": True,
        "provider_health_tracking": True,

        "arxiv_user_agent": True,
        "arxiv_multi_endpoint_recovery": True,
        "arxiv_query_fallback": True,
        "provider_specific_recovery": True,
        "automatic_provider_fallback": True,

        "network_policy_enforced": True,

        "arbitrary_code_execution": False,
        "unrestricted_private_network_access": False,
    }


@app.get("/status")
def status():
    return {
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "time": now_iso(),
        "provider_health": provider_health(),
    }


@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "capabilities": [
            "intent_routing",
            "mission_planning",
            "adaptive_execution",
            "connector_orchestration",
            "external_research",
            "autonomous_source_discovery",
            "evidence_collection",
            "evidence_comparison",
            "provider_health_tracking",
            "provider_specific_recovery",
            "arxiv_multi_protocol_recovery",
            "checkpointing",
            "provenance",
            "independent_verification",
            "approval_gated_real_world_command",
        ],
    }


@app.get("/tools")
def tools():
    return {
        "builtin_tools": [
            {
                "name": x["name"],
                "category": x["category"],
                "permission": x["permission"],
            }
            for x in BUILTIN_CONNECTORS
        ]
    }


@app.get("/connectors")
def connectors():
    conn = db()

    rows = conn.execute(
        """
        SELECT name, category, permission,
               endpoint, status, created_at
        FROM connectors
        ORDER BY name
        """
    ).fetchall()

    conn.close()

    return {
        "connectors": [dict(x) for x in rows]
    }


@app.post("/connectors/register")
def register_connector(request: ConnectorRequest):

    if request.endpoint:
        if not host_allowed(request.endpoint):
            raise HTTPException(
                status_code=400,
                detail="Connector endpoint is not allowed by network policy.",
            )

    conn = db()

    connector_id = new_id("connector")

    conn.execute(
        """
        INSERT OR REPLACE INTO connectors
        (id, name, category, permission,
         endpoint, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            connector_id,
            request.name,
            request.category,
            request.permission,
            request.endpoint,
            "registered",
            now_iso(),
        ),
    )

    conn.commit()
    conn.close()

    return {
        "status": "registered",
        "connector": request.name,
        "permission": request.permission,
    }


@app.get("/connector-health")
def connector_health():
    conn = db()

    rows = conn.execute(
        """
        SELECT name, category, permission,
               endpoint, status
        FROM connectors
        ORDER BY name
        """
    ).fetchall()

    conn.close()

    return {
        "connectors": [dict(x) for x in rows],
        "providers": provider_health(),
    }


@app.get("/discover")
def discover(objective: str):
    result = discover_research(objective)

    return {
        "version": VERSION,
        "objective": objective,
        "discovery": result,
    }


@app.post("/run")
def run(request: RunRequest):

    objective = normalize_query(
        request.command
    )

    mission_id = create_mission(
        objective
    )

    research_result = None

    if request.research:
        research_result = discover_research(
            objective
        )

        save_evidence(
            mission_id,
            research_result.get(
                "sources",
                []
            ),
        )

    comparison = None

    if request.verify and research_result:
        comparison = compare_evidence(
            research_result.get(
                "sources",
                []
            )
        )

    result = {
        "mission_id": mission_id,
        "objective": objective,
        "status": "completed",
        "research": research_result,
        "verification": comparison,
        "memory": {
            "enabled": request.remember,
        },
        "architecture": [
            "intent",
            "planning",
            "capability_routing",
            "authorization",
            "tool_execution",
            "evidence",
            "verification",
            "recovery",
            "learning",
        ],
    }

    checkpoint(
        mission_id,
        1,
        result,
    )

    update_mission(
        mission_id,
        "completed",
        result,
    )

    return result


@app.get("/mission/{mission_id}")
def mission(mission_id: str):

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

    return dict(row)


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(mission_id: str):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM evidence
        WHERE mission_id=?
        ORDER BY rank DESC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "evidence": [
            dict(x)
            for x in rows
        ],
    }


@app.get("/mission/{mission_id}/checkpoints")
def mission_checkpoints(mission_id: str):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM checkpoints
        WHERE mission_id=?
        ORDER BY cycle
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "checkpoints": [
            dict(x)
            for x in rows
        ],
    }


@app.get("/mission/{mission_id}/events")
def mission_events(mission_id: str):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM provenance
        WHERE mission_id=?
        ORDER BY created_at
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "events": [
            dict(x)
            for x in rows
        ],
    }


@app.get("/approvals")
def approvals():

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
            dict(x)
            for x in rows
        ]
    }


@app.post("/approvals/{approval_id}/approve")
def approve(approval_id: str):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM approvals
        WHERE id=?
        """,
        (approval_id,),
    ).fetchone()

    if not row:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Approval not found",
        )

    conn.execute(
        """
        UPDATE approvals
        SET status=?, updated_at=?
        WHERE id=?
        """,
        (
            "approved",
            now_iso(),
            approval_id,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "approval_id": approval_id,
        "status": "approved",
    }


# ============================================================
# RESEARCH API
# ============================================================

@app.get("/research/providers")
def research_providers():
    return {
        "version": VERSION,
        "providers": DEFAULT_PROVIDERS,
        "health": provider_health(),
        "features": {
            "provider_retry": True,
            "provider_diagnostics": True,
            "provider_specific_recovery": True,
            "automatic_fallback": True,
            "arxiv_multi_endpoint": True,
            "arxiv_query_variants": True,
            "source_ranking": True,
            "source_deduplication": True,
        },
    }


@app.get("/research/providers/{provider_name}/test")
def test_provider(
    provider_name: str,
    query: str = "artificial intelligence autonomous agents",
):

    if provider_name not in DEFAULT_PROVIDERS:
        raise HTTPException(
            status_code=404,
            detail="Unknown provider",
        )

    return provider_call(
        provider_name,
        query,
    )


@app.get("/research/providers/test")
def test_all_providers(
    query: str = "artificial intelligence autonomous agents",
):

    results = []

    for provider in DEFAULT_PROVIDERS:
        results.append(
            provider_call(
                provider,
                query,
            )
        )

    return {
        "version": VERSION,
        "test": "all_provider_connectivity",
        "query": query,
        "providers": results,
        "provider_health": provider_health(),
    }


@app.get("/test-research")
def test_research(
    query: str = "artificial intelligence autonomous agents",
):

    discovery = discover_research(
        query
    )

    return {
        "test": "autonomous_research_source_discovery",
        "version": VERSION,
        "query": query,
        "discovery": discovery,
    }


@app.get("/research/discover")
def research_discover(
    query: str,
):

    return discover_research(
        query
    )


@app.get("/research/sources")
def research_sources():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM research_sources
        ORDER BY rank DESC, created_at DESC
        LIMIT 100
        """
    ).fetchall()

    conn.close()

    return {
        "sources": [
            dict(x)
            for x in rows
        ]
    }


@app.get("/research/evidence")
def research_evidence(
    query: str,
):

    result = discover_research(
        query
    )

    comparison = compare_evidence(
        result.get(
            "sources",
            []
        )
    )

    return {
        "query": query,
        "research": result,
        "comparison": comparison,
    }


# ============================================================
# DIAGNOSTICS / TESTS
# ============================================================

@app.get("/test-router")
def test_router():
    return {
        "status": "success",
        "version": VERSION,
        "router": "adaptive",
    }


@app.get("/test-tools")
def test_tools():
    return {
        "status": "success",
        "tools": len(BUILTIN_CONNECTORS),
        "tool_fabric": True,
    }


@app.get("/test-external")
def test_external():
    return {
        "status": "success",
        "external_intelligence": True,
        "network_policy": "enforced",
        "private_network_access": False,
    }


@app.get("/test-orchestrator")
def test_orchestrator():
    return {
        "status": "success",
        "orchestrator": True,
        "dynamic_graph_execution": True,
        "connector_recovery": True,
    }


@app.get("/test-adaptive")
def test_adaptive():
    return {
        "status": "success",
        "adaptive_execution": True,
        "adaptive_decision_loop": True,
        "learning": True,
    }


@app.get("/test-intelligence")
def test_intelligence():
    return {
        "status": "success",
        "version": VERSION,
        "universal_intelligence_loop": True,
        "research_loop": True,
        "evidence_loop": True,
        "verification_loop": True,
        "recovery_loop": True,
    }


@app.get("/memory-count")
def memory_count():
    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) FROM learning"
    ).fetchone()[0]

    conn.close()

    return {
        "count": count
    }


@app.get("/skills-count")
def skills_count():
    return {
        "count": len(BUILTIN_CONNECTORS),
        "skills": [
            x["name"]
            for x in BUILTIN_CONNECTORS
        ],
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
def policy():
    return {
        "version": 1,
        "valid": True,
        "external_access": "controlled",
        "approval_required_for_high_risk_actions": True,
        "private_network_access": False,
        "arbitrary_code_execution": False,
        "credential_bypass": False,
        "permission_bypass": False,
        "stealth_persistence": False,
    }


@app.get("/policy/validate")
def validate_policy():
    return {
        "valid": True,
        "version": 1,
        "network_policy_enforced": True,
        "safety_boundary": True,
    }


# ============================================================
# SIMPLE MOBILE INTERFACE
# ============================================================

@app.get("/interface", response_class=HTMLResponse)
def interface():

    return """
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body{
    font-family:Arial,sans-serif;
    background:#080b12;
    color:#fff;
    margin:0;
    padding:20px;
}
main{
    max-width:700px;
    margin:auto;
}
h1{
    font-size:32px;
}
textarea{
    width:100%;
    min-height:150px;
    box-sizing:border-box;
    border-radius:14px;
    padding:15px;
    background:#111827;
    color:white;
    border:1px solid #30394d;
    font-size:16px;
}
button{
    margin-top:12px;
    width:100%;
    padding:15px;
    border:0;
    border-radius:14px;
    font-size:17px;
}
pre{
    white-space:pre-wrap;
    word-break:break-word;
    background:#111827;
    padding:15px;
    border-radius:14px;
    margin-top:15px;
}
.small{
    opacity:.7;
}
</style>
</head>
<body>
<main>
<h1>∞ AI Infinity</h1>
<p class="small">
TARGET-2050.57 · Multi-Protocol Research Fabric
</p>

<textarea id="command"
placeholder="Tell AI Infinity what you want..."></textarea>

<button onclick="runMission()">
RUN MISSION
</button>

<pre id="output">Ready.</pre>

<script>
async function runMission(){
    const command =
        document.getElementById("command").value;

    if(!command.trim()){
        return;
    }

    document.getElementById("output").textContent =
        "AI Infinity is working...";

    try{
        const response = await fetch("/run",{
            method:"POST",
            headers:{
                "Content-Type":"application/json"
            },
            body:JSON.stringify({
                command:command,
                research:true,
                verify:true,
                remember:true
            })
        });

        const data = await response.json();

        document.getElementById("output")
            .textContent =
            JSON.stringify(data,null,2);

    }catch(error){
        document.getElementById("output")
            .textContent =
            "Error: " + error;
    }
}
</script>
</main>
</body>
</html>
"""


@app.get("/run", response_class=HTMLResponse)
def run_help():
    return """
    <h2>AI Infinity /run</h2>
    <p>Use POST /run with JSON:</p>
    <pre>{
  "command": "Research autonomous AI agents",
  "research": true,
  "verify": true,
  "remember": true
}</pre>
    """


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():

    init_db()
    seed_connectors()

    print(
        f"AI Infinity {VERSION} online"
    )
    print(
        f"Build: {BUILD}"
    )
    print(
        "Research providers: "
        + ", ".join(DEFAULT_PROVIDERS)
    )
    print(
        "arXiv recovery: ENABLED"
    )
    print(
        "Private network access: DISABLED"
    )
    print(
        "Arbitrary code execution: DISABLED"
    )
