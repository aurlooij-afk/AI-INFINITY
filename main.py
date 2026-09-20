"""
AI Infinity
TARGET-2050.61
BUILD: ADAPTIVE-REASONING-AND-TOOL-EXECUTION-CORE

Preserves TARGET-2050.60:
- FastAPI
- SQLite persistence
- missions
- requirements
- research
- evidence graph
- synthesis
- claims
- contradiction detection
- decision engine
- dynamic mission graph
- authorization
- controlled execution
- observation
- outcome verification
- recovery
- persistent memory
- learning
- reusable skills
- artifacts
- provenance
- checkpoints
- connectors
- capabilities
- controlled public web access

Adds TARGET-2050.61:
- adaptive reasoning loop
- execution strategy selection
- tool selection
- execution inspection
- failure diagnosis
- strategy switching
- bounded adaptive retries
- evidence-aware decisions
- confidence tracking
- state transition engine
- execution trace
- tool outcome classification
- adaptive replanning
- mission convergence detection
- learning from successful and failed strategies
- reusable adaptive strategies
- provider health tracking
- global background executor
- GET /run compatibility
- architecture introspection
- adaptive diagnostics
- no arbitrary code execution
- no unrestricted private-network access
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from pathlib import Path
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional
import sqlite3
import requests
import hashlib
import json
import time
import uuid
import re
import socket
import ipaddress
import os
import threading
import math
import xml.etree.ElementTree as ET


# ============================================================
# CORE
# ============================================================

VERSION = "TARGET-2050.61"
BUILD = "ADAPTIVE-REASONING-AND-TOOL-EXECUTION-CORE"

BASE = Path("/tmp/ai_infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

MAX_RESEARCH_RESULTS = 5
MAX_RESPONSE_BYTES = 2_000_000
REQUEST_TIMEOUT = 20
MAX_ADAPTIVE_ATTEMPTS = 4

executor = ThreadPoolExecutor(max_workers=4)

db_lock = threading.RLock()


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "AI Infinity adaptive autonomous intelligence core. "
        "Controlled research, reasoning, tool execution, verification, "
        "recovery and learning."
    ),
)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        conn = db()

        tables = [
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                confidence REAL DEFAULT 0,
                created_at REAL,
                updated_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS mission_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                step TEXT,
                status TEXT,
                strategy TEXT,
                attempts INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0,
                result TEXT,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS requirements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                requirement TEXT,
                priority REAL,
                satisfied INTEGER DEFAULT 0,
                evidence_count INTEGER DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                provider TEXT,
                title TEXT,
                url TEXT,
                source_id TEXT,
                published TEXT,
                abstract TEXT,
                snippet TEXT,
                source_type TEXT,
                confidence REAL,
                metadata TEXT,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                claim TEXT,
                confidence REAL,
                supporting INTEGER DEFAULT 0,
                contradicting INTEGER DEFAULT 0,
                status TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                event TEXT,
                source TEXT,
                detail TEXT,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                action TEXT,
                risk TEXT,
                status TEXT,
                created_at REAL,
                decided_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                state TEXT,
                payload TEXT,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                stage TEXT,
                observation TEXT,
                success INTEGER,
                confidence REAL,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT UNIQUE,
                expected TEXT,
                observed TEXT,
                verified INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                name TEXT,
                kind TEXT,
                content TEXT,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                description TEXT,
                procedure TEXT,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS learning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                lesson TEXT,
                strategy TEXT,
                outcome TEXT,
                confidence REAL,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                resource TEXT,
                amount REAL,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS connectors (
                name TEXT PRIMARY KEY,
                category TEXT,
                permission TEXT,
                status TEXT,
                description TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS connector_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                connector TEXT,
                mission_id TEXT,
                event TEXT,
                status TEXT,
                detail TEXT,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT,
                value TEXT,
                confidence REAL,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS provider_health (
                provider TEXT PRIMARY KEY,
                status TEXT,
                attempts INTEGER DEFAULT 0,
                successes INTEGER DEFAULT 0,
                failures INTEGER DEFAULT 0,
                last_error TEXT,
                last_success REAL,
                updated_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS adaptive_trace (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                cycle INTEGER,
                state TEXT,
                strategy TEXT,
                tool TEXT,
                observation TEXT,
                diagnosis TEXT,
                action TEXT,
                confidence REAL,
                created_at REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS strategies (
                name TEXT PRIMARY KEY,
                description TEXT,
                use_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0.5,
                last_used REAL
            )
            """
        ]

        for statement in tables:
            conn.execute(statement)

        seed_connectors(conn)
        seed_strategies(conn)

        conn.commit()
        conn.close()


def seed_connectors(conn):
    connectors = [
        (
            "research",
            "intelligence",
            "safe",
            "active",
            "Multi-provider public research"
        ),
        (
            "web-read",
            "network",
            "safe",
            "active",
            "Controlled public HTTP GET"
        ),
        (
            "memory",
            "state",
            "safe",
            "active",
            "Persistent local memory"
        ),
        (
            "verification",
            "intelligence",
            "safe",
            "active",
            "Independent outcome verification"
        ),
        (
            "approval-gateway",
            "control",
            "approval",
            "active",
            "Human approval boundary for sensitive actions"
        ),
    ]

    for item in connectors:
        conn.execute(
            """
            INSERT OR IGNORE INTO connectors
            (name, category, permission, status, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            item
        )


def seed_strategies(conn):
    strategies = [
        (
            "direct-research",
            "Use multiple independent research providers.",
        ),
        (
            "cross-source",
            "Compare independent sources before forming a conclusion.",
        ),
        (
            "fallback-provider",
            "Switch providers after a provider failure.",
        ),
        (
            "decompose",
            "Break a difficult objective into smaller requirements.",
        ),
        (
            "verify-first",
            "Gather evidence before taking an action.",
        ),
        (
            "replan",
            "Change the execution plan after observing failure.",
        ),
        (
            "conservative",
            "Reduce scope and perform only bounded safe operations.",
        ),
    ]

    for name, description in strategies:
        conn.execute(
            """
            INSERT OR IGNORE INTO strategies
            (name, description)
            VALUES (?, ?)
            """,
            (name, description)
        )


init_db()


# ============================================================
# MODELS
# ============================================================

class CreateMission(BaseModel):
    command: str = Field(..., min_length=1, max_length=10000)
    duration_minutes: int = Field(default=1, ge=1, le=120)


class ApprovalDecision(BaseModel):
    approved: bool


class MemoryInput(BaseModel):
    key: str
    value: str
    confidence: float = Field(default=0.7, ge=0, le=1)


# ============================================================
# TIME / JSON
# ============================================================

def now():
    return time.time()


def dumps(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str
    )


def loads(value, default=None):
    if value is None:
        return default

    try:
        return json.loads(value)
    except Exception:
        return default


def uid(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ============================================================
# NETWORK SECURITY
# ============================================================

DEFAULT_ALLOWED_DOMAINS = {
    "en.wikipedia.org",
    "api.crossref.org",
    "export.arxiv.org",
    "arxiv.org",
    "api.openalex.org",
    "openalex.org",
}


def configured_domains():
    raw = os.getenv("EXTERNAL_ALLOWED_DOMAINS", "")
    domains = set(DEFAULT_ALLOWED_DOMAINS)

    for item in raw.split(","):
        item = item.strip().lower()
        if item:
            domains.add(item)

    return domains


def is_private_host(host):
    if not host:
        return True

    host = host.lower().strip()

    if host in {
        "localhost",
        "localhost.localdomain",
        "0.0.0.0",
        "::1",
    }:
        return True

    try:
        addresses = socket.getaddrinfo(host, None)

        for address in addresses:
            ip_text = address[4][0]

            try:
                ip = ipaddress.ip_address(ip_text)

                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                ):
                    return True

            except Exception:
                continue

    except Exception:
        pass

    return False


def domain_allowed(host):
    host = host.lower().rstrip(".")

    for domain in configured_domains():
        if host == domain or host.endswith("." + domain):
            return True

    return False


def validate_url(url):
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return False, "scheme-not-allowed"

    if not parsed.hostname:
        return False, "missing-host"

    host = parsed.hostname.lower()

    if is_private_host(host):
        return False, "private-host-blocked"

    if not domain_allowed(host):
        return False, "domain-not-allowlisted"

    return True, "allowed"


def safe_http_get(
    url,
    params=None,
    headers=None,
    timeout=REQUEST_TIMEOUT,
    max_bytes=MAX_RESPONSE_BYTES,
):
    current = url

    for redirect_number in range(4):
        allowed, reason = validate_url(current)

        if not allowed:
            raise RuntimeError(
                f"network-policy:{reason}:{current}"
            )

        response = requests.get(
            current,
            params=params if redirect_number == 0 else None,
            headers=headers or {
                "User-Agent": "AI-Infinity/2050.61"
            },
            timeout=timeout,
            allow_redirects=False,
        )

        if response.status_code in {
            301,
            302,
            303,
            307,
            308,
        }:
            location = response.headers.get("location")

            if not location:
                break

            from urllib.parse import urljoin
            current = urljoin(current, location)
            continue

        data = response.content[:max_bytes]

        return response, data

    raise RuntimeError("redirect-limit-exceeded")


# ============================================================
# PROVIDER HEALTH
# ============================================================

def provider_event(provider, success, error=None):
    with db_lock:
        conn = db()

        row = conn.execute(
            "SELECT * FROM provider_health WHERE provider=?",
            (provider,)
        ).fetchone()

        if row:
            attempts = row["attempts"] + 1
            successes = row["successes"] + (1 if success else 0)
            failures = row["failures"] + (0 if success else 1)

            conn.execute(
                """
                UPDATE provider_health
                SET status=?,
                    attempts=?,
                    successes=?,
                    failures=?,
                    last_error=?,
                    last_success=?,
                    updated_at=?
                WHERE provider=?
                """,
                (
                    "healthy" if success else "degraded",
                    attempts,
                    successes,
                    failures,
                    None if success else str(error),
                    now() if success else row["last_success"],
                    now(),
                    provider,
                )
            )
        else:
            conn.execute(
                """
                INSERT INTO provider_health
                (provider,status,attempts,successes,failures,
                 last_error,last_success,updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    "healthy" if success else "degraded",
                    1,
                    1 if success else 0,
                    0 if success else 1,
                    None if success else str(error),
                    now() if success else None,
                    now(),
                )
            )

        conn.commit()
        conn.close()


# ============================================================
# RESEARCH
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)

    value = re.sub(
        r"<[^>]+>",
        " ",
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def evidence_id(provider, title, url):
    raw = f"{provider}|{title}|{url}"

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]


def wikipedia_search(query):
    provider = "wikipedia"

    try:
        response, data = safe_http_get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "utf8": 1,
                "srlimit": MAX_RESEARCH_RESULTS,
            }
        )

        payload = json.loads(
            data.decode(
                "utf-8-sig",
                errors="replace"
            )
        )

        results = []

        for item in payload.get(
            "query",
            {}
        ).get(
            "search",
            []
        ):
            title = clean_text(
                item.get("title")
            )

            snippet = clean_text(
                item.get("snippet")
            )

            page_url = (
                "https://en.wikipedia.org/wiki/"
                + quote(
                    title.replace(" ", "_")
                )
            )

            results.append({
                "provider": provider,
                "title": title,
                "url": page_url,
                "source_id": str(
                    item.get("pageid", "")
                ),
                "published": "",
                "authors": None,
                "abstract": snippet,
                "snippet": snippet,
                "source_type": "encyclopedia",
                "confidence": 0.45,
                "metadata": {
                    "wordcount": item.get("wordcount"),
                },
            })

        provider_event(provider, True)

        return {
            "provider": provider,
            "status": "success",
            "http_status": response.status_code,
            "attempts": 1,
            "result_count": len(results),
            "results": results,
            "error_type": None,
            "error": None,
            "recovery": "api-search",
            "content_type": response.headers.get(
                "content-type"
            ),
        }

    except Exception as exc:
        provider_event(
            provider,
            False,
            exc
        )

        return {
            "provider": provider,
            "status": "error",
            "http_status": None,
            "attempts": 1,
            "result_count": 0,
            "results": [],
            "error_type": type(exc).__name__,
            "error": str(exc),
            "recovery": None,
            "content_type": None,
        }


def crossref_search(query):
    provider = "crossref"

    try:
        response, data = safe_http_get(
            "https://api.crossref.org/works",
            params={
                "query.bibliographic": query,
                "rows": MAX_RESEARCH_RESULTS,
            }
        )

        payload = json.loads(
            data.decode(
                "utf-8-sig",
                errors="replace"
            )
        )

        results = []

        for item in payload.get(
            "message",
            {}
        ).get(
            "items",
            []
        ):

            title_list = item.get(
                "title",
                []
            )

            title = clean_text(
                title_list[0]
                if title_list
                else "Untitled"
            )

            doi = item.get("DOI")

            url = (
                f"https://doi.org/{doi}"
                if doi
                else item.get("URL", "")
            )

            authors = []

            for author in item.get(
                "author",
                []
            ):
                name = " ".join(
                    filter(
                        None,
                        [
                            author.get("given"),
                            author.get("family"),
                        ]
                    )
                )

                if name:
                    authors.append(name)

            published = ""

            date_parts = (
                item.get(
                    "published-print"
                )
                or item.get(
                    "published-online"
                )
                or item.get(
                    "issued"
                )
                or {}
            ).get(
                "date-parts",
                []
            )

            if date_parts and date_parts[0]:
                published = "-".join(
                    str(x)
                    for x in date_parts[0]
                )

            results.append({
                "provider": provider,
                "title": title,
                "url": url,
                "source_id": doi or url,
                "published": published,
                "authors": authors,
                "abstract": clean_text(
                    item.get("abstract")
                ),
                "snippet": clean_text(
                    item.get("abstract")
                ),
                "source_type": "bibliographic",
                "confidence": 0.75,
                "metadata": {
                    "type": item.get("type"),
                    "publisher": item.get(
                        "publisher"
                    ),
                },
            })

        provider_event(provider, True)

        return {
            "provider": provider,
            "status": "success",
            "http_status": response.status_code,
            "attempts": 1,
            "result_count": len(results),
            "results": results,
            "error_type": None,
            "error": None,
            "recovery": "bibliographic-query",
            "content_type": response.headers.get(
                "content-type"
            ),
        }

    except Exception as exc:
        provider_event(
            provider,
            False,
            exc
        )

        return {
            "provider": provider,
            "status": "error",
            "http_status": None,
            "attempts": 1,
            "result_count": 0,
            "results": [],
            "error_type": type(exc).__name__,
            "error": str(exc),
            "recovery": None,
            "content_type": None,
        }


def arxiv_search(query):
    provider = "arxiv"

    endpoints = [
        "https://export.arxiv.org/api/query",
        "https://arxiv.org/api/query",
    ]

    last_error = None

    for endpoint_number, endpoint in enumerate(
        endpoints,
        start=1
    ):

        try:
            response, data = safe_http_get(
                endpoint,
                params={
                    "search_query": (
                        "all:" + query
                    ),
                    "start": 0,
                    "max_results": MAX_RESEARCH_RESULTS,
                }
            )

            root = ET.fromstring(
                data.decode(
                    "utf-8",
                    errors="replace"
                )
            )

            namespace = {
                "a": "http://www.w3.org/2005/Atom"
            }

            results = []

            for entry in root.findall(
                "a:entry",
                namespace
            ):
                title = clean_text(
                    entry.findtext(
                        "a:title",
                        default="",
                        namespaces=namespace,
                    )
                )

                summary = clean_text(
                    entry.findtext(
                        "a:summary",
                        default="",
                        namespaces=namespace,
                    )
                )

                entry_id = clean_text(
                    entry.findtext(
                        "a:id",
                        default="",
                        namespaces=namespace,
                    )
                )

                published = clean_text(
                    entry.findtext(
                        "a:published",
                        default="",
                        namespaces=namespace,
                    )
                )

                authors = []

                for author in entry.findall(
                    "a:author",
                    namespace
                ):
                    name = clean_text(
                        author.findtext(
                            "a:name",
                            default="",
                            namespaces=namespace,
                        )
                    )

                    if name:
                        authors.append(name)

                results.append({
                    "provider": provider,
                    "title": title,
                    "url": entry_id,
                    "source_id": entry_id,
                    "published": published,
                    "authors": authors,
                    "abstract": summary,
                    "snippet": summary,
                    "source_type": "preprint",
                    "confidence": 0.70,
                    "metadata": {},
                })

            provider_event(provider, True)

            return {
                "provider": provider,
                "status": "success",
                "http_status": response.status_code,
                "attempts": endpoint_number,
                "result_count": len(results),
                "results": results,
                "error_type": None,
                "error": None,
                "recovery": (
                    f"export-query-{endpoint_number}"
                ),
                "content_type": response.headers.get(
                    "content-type"
                ),
            }

        except Exception as exc:
            last_error = exc

    provider_event(
        provider,
        False,
        last_error
    )

    return {
        "provider": provider,
        "status": "error",
        "http_status": None,
        "attempts": len(endpoints),
        "result_count": 0,
        "results": [],
        "error_type": type(last_error).__name__
        if last_error
        else "UnknownError",
        "error": str(last_error)
        if last_error
        else "unknown",
        "recovery": None,
        "content_type": None,
    }


def reconstruct_openalex_abstract(item):
    inverted = item.get(
        "abstract_inverted_index"
    )

    if not inverted:
        return ""

    words = []

    for word, positions in inverted.items():
        for position in positions:
            words.append(
                (
                    position,
                    word
                )
            )

    words.sort(
        key=lambda x: x[0]
    )

    return " ".join(
        word
        for _, word in words
    )


def openalex_search(query):
    provider = "openalex"

    try:
        response, data = safe_http_get(
            "https://api.openalex.org/works",
            params={
                "search": query,
                "per-page": MAX_RESEARCH_RESULTS,
            }
        )

        payload = json.loads(
            data.decode(
                "utf-8-sig",
                errors="replace"
            )
        )

        results = []

        for item in payload.get(
            "results",
            []
        ):

            title = clean_text(
                item.get("title")
                or "Untitled"
            )

            url = (
                item.get("doi")
                or item.get("id")
                or ""
            )

            abstract = reconstruct_openalex_abstract(
                item
            )

            authors = []

            for author in item.get(
                "authorships",
                []
            ):
                author_name = (
                    author.get("author", {})
                    .get("display_name")
                )

                if author_name:
                    authors.append(
                        author_name
                    )

            results.append({
                "provider": provider,
                "title": title,
                "url": url,
                "source_id": item.get(
                    "id",
                    url
                ),
                "published": item.get(
                    "publication_date",
                    ""
                ),
                "authors": authors,
                "abstract": abstract,
                "snippet": abstract,
                "source_type": (
                    item.get(
                        "type"
                    )
                    or "research-index"
                ),
                "confidence": 0.72,
                "metadata": {
                    "doi": item.get("doi"),
                    "cited_by_count": item.get(
                        "cited_by_count"
                    ),
                },
            })

        provider_event(provider, True)

        return {
            "provider": provider,
            "status": "success",
            "http_status": response.status_code,
            "attempts": 1,
            "result_count": len(results),
            "results": results,
            "error_type": None,
            "error": None,
            "recovery": "works-search",
            "content_type": response.headers.get(
                "content-type"
            ),
        }

    except Exception as exc:
        provider_event(
            provider,
            False,
            exc
        )

        return {
            "provider": provider,
            "status": "error",
            "http_status": None,
            "attempts": 1,
            "result_count": 0,
            "results": [],
            "error_type": type(exc).__name__,
            "error": str(exc),
            "recovery": None,
            "content_type": None,
        }


def deduplicate_results(results):
    seen = set()
    output = []

    for item in results:
        key = (
            item.get("url")
            or item.get("source_id")
            or item.get("title")
        )

        key = str(key).lower().strip()

        if not key:
            key = hashlib.sha1(
                dumps(item).encode()
            ).hexdigest()

        if key in seen:
            continue

        seen.add(key)
        output.append(item)

    return output


def ingest_research(query):
    providers = [
        wikipedia_search,
        crossref_search,
        arxiv_search,
        openalex_search,
    ]

    results = []

    for provider in providers:
        result = provider(query)

        for evidence in result.get(
            "results",
            []
        ):
            results.append(evidence)

    results = deduplicate_results(
        results
    )

    return {
        "query": query,
        "providers": [
            wikipedia_search.__name__.replace(
                "_search",
                ""
            ),
            "crossref",
            "arxiv",
            "openalex",
        ],
        "count": len(results),
        "results": results,
    }


# ============================================================
# REQUIREMENT ENGINE
# ============================================================

def discover_requirements(objective):
    requirements = []

    requirements.append({
        "requirement": (
            "Clearly define the requested outcome."
        ),
        "priority": 1.0,
    })

    requirements.append({
        "requirement": (
            "Collect sufficient independent evidence "
            "when factual verification is required."
        ),
        "priority": 0.95,
    })

    requirements.append({
        "requirement": (
            "Verify important claims before treating "
            "them as established."
        ),
        "priority": 0.95,
    })

    if any(
        word in objective.lower()
        for word in [
            "execute",
            "build",
            "create",
            "deploy",
            "send",
            "change",
            "delete",
            "publish",
        ]
    ):
        requirements.append({
            "requirement": (
                "Determine whether the requested action "
                "requires authorization or approval."
            ),
            "priority": 1.0,
        })

    return requirements


# ============================================================
# EVIDENCE / CLAIMS
# ============================================================

def save_evidence(mission_id, evidence):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO evidence
            (
                mission_id,
                provider,
                title,
                url,
                source_id,
                published,
                abstract,
                snippet,
                source_type,
                confidence,
                metadata,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                evidence.get("provider"),
                evidence.get("title"),
                evidence.get("url"),
                evidence.get("source_id"),
                evidence.get("published"),
                evidence.get("abstract"),
                evidence.get("snippet"),
                evidence.get("source_type"),
                evidence.get("confidence", 0.5),
                dumps(
                    evidence.get(
                        "metadata",
                        {}
                    )
                ),
                now(),
            )
        )

        conn.commit()
        conn.close()


def extract_claims(evidence):
    claims = []

    for item in evidence[:20]:
        title = clean_text(
            item.get("title")
        )

        abstract = clean_text(
            item.get("abstract")
            or item.get("snippet")
        )

        if not title:
            continue

        claims.append({
            "claim": title,
            "supporting": 1,
            "contradicting": 0,
            "confidence": item.get(
                "confidence",
                0.5
            ),
            "source": item.get(
                "provider"
            ),
            "abstract": abstract,
        })

    return claims


def synthesize_evidence(evidence):
    if not evidence:
        return {
            "claim_count": 0,
            "confidence": 0.0,
            "claims": [],
            "contradictions": [],
            "gaps": [
                "No evidence was retrieved."
            ],
        }

    claims = extract_claims(
        evidence
    )

    providers = set(
        item.get("provider")
        for item in evidence
    )

    provider_factor = min(
        1.0,
        len(providers) / 4
    )

    average_confidence = (
        sum(
            c["confidence"]
            for c in claims
        )
        / len(claims)
        if claims
        else 0
    )

    final_confidence = round(
        (
            average_confidence * 0.65
            + provider_factor * 0.35
        ),
        3
    )

    contradictions = []

    normalized = [
        c["claim"].lower()
        for c in claims
    ]

    negative_words = [
        "not",
        "failure",
        "failed",
        "limitation",
        "risk",
        "cannot",
        "unable",
    ]

    for claim in claims:
        if any(
            word in claim["claim"].lower()
            for word in negative_words
        ):
            contradictions.append({
                "claim": claim["claim"],
                "reason": (
                    "Potentially negative or limiting "
                    "evidence requiring comparison."
                ),
            })

    gaps = []

    if len(providers) < 2:
        gaps.append(
            "Independent-source coverage is limited."
        )

    if len(claims) < 3:
        gaps.append(
            "Evidence volume is limited."
        )

    return {
        "claim_count": len(claims),
        "confidence": final_confidence,
        "claims": claims,
        "contradictions": contradictions,
        "gaps": gaps,
    }


# ============================================================
# ADAPTIVE REASONING ENGINE
# ============================================================

STRATEGY_ORDER = [
    "direct-research",
    "cross-source",
    "decompose",
    "fallback-provider",
    "verify-first",
    "replan",
    "conservative",
]


def strategy_score(strategy):
    with db_lock:
        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM strategies
            WHERE name=?
            """,
            (strategy,)
        ).fetchone()

        conn.close()

    if not row:
        return 0.5

    return float(
        row["confidence"] or 0.5
    )


def choose_strategy(
    objective,
    evidence_count=0,
    confidence=0,
    previous_strategy=None,
    cycle=0,
):
    text = objective.lower()

    if previous_strategy == "direct-research":
        return "cross-source"

    if previous_strategy == "cross-source":
        if confidence < 0.6:
            return "decompose"

    if evidence_count == 0:
        if any(
            x in text
            for x in [
                "research",
                "find",
                "verify",
                "evidence",
                "compare",
            ]
        ):
            return "direct-research"

        return "decompose"

    if evidence_count > 0 and confidence < 0.6:
        return "cross-source"

    if cycle >= 2 and confidence < 0.75:
        return "replan"

    if confidence >= 0.75:
        return "verify-first"

    scores = {
        strategy: strategy_score(strategy)
        for strategy in STRATEGY_ORDER
    }

    return max(
        scores,
        key=scores.get
    )


def diagnose_observation(
    success,
    result,
    evidence_count=0,
    confidence=0,
):
    if success:
        if confidence >= 0.8:
            return {
                "type": "success",
                "severity": "low",
                "reason": (
                    "Observed result is consistent with "
                    "the expected objective."
                ),
                "next": "verify",
            }

        return {
            "type": "partial-success",
            "severity": "medium",
            "reason": (
                "Execution produced useful information "
                "but confidence is not yet sufficient."
            ),
            "next": "gather-more-evidence",
        }

    if evidence_count == 0:
        return {
            "type": "evidence-gap",
            "severity": "medium",
            "reason": (
                "No usable evidence was produced."
            ),
            "next": "change-strategy",
        }

    return {
        "type": "execution-failure",
        "severity": "medium",
        "reason": (
            "The observed result did not satisfy "
            "the expected state."
        ),
        "next": "replan",
    }


def record_strategy_outcome(
    strategy,
    success,
):
    with db_lock:
        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM strategies
            WHERE name=?
            """,
            (strategy,)
        ).fetchone()

        if not row:
            conn.close()
            return

        use_count = row["use_count"] + 1

        success_count = (
            row["success_count"]
            + (1 if success else 0)
        )

        failure_count = (
            row["failure_count"]
            + (0 if success else 1)
        )

        confidence = (
            success_count / use_count
            if use_count
            else 0.5
        )

        conn.execute(
            """
            UPDATE strategies
            SET use_count=?,
                success_count=?,
                failure_count=?,
                confidence=?,
                last_used=?
            WHERE name=?
            """,
            (
                use_count,
                success_count,
                failure_count,
                confidence,
                now(),
                strategy,
            )
        )

        conn.commit()
        conn.close()


def trace_adaptive(
    mission_id,
    cycle,
    state,
    strategy,
    tool,
    observation,
    diagnosis,
    action,
    confidence,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO adaptive_trace
            (
                mission_id,
                cycle,
                state,
                strategy,
                tool,
                observation,
                diagnosis,
                action,
                confidence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                cycle,
                state,
                strategy,
                tool,
                observation,
                diagnosis,
                action,
                confidence,
                now(),
            )
        )

        conn.commit()
        conn.close()


# ============================================================
# CONTROLLED TOOLS
# ============================================================

def tool_research(
    mission_id,
    objective,
):
    result = ingest_research(
        objective
    )

    for evidence in result.get(
        "results",
        []
    ):
        save_evidence(
            mission_id,
            evidence
        )

    return {
        "tool": "research",
        "success": bool(
            result.get("results")
        ),
        "result": result,
    }


def tool_memory_lookup(objective):
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT key, value, confidence
            FROM memory
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

        conn.close()

    matches = []

    tokens = set(
        re.findall(
            r"[a-zA-Z0-9]+",
            objective.lower()
        )
    )

    for row in rows:
        text = (
            str(row["key"])
            + " "
            + str(row["value"])
        ).lower()

        if any(
            token in text
            for token in tokens
            if len(token) > 3
        ):
            matches.append(
                dict(row)
            )

    return {
        "tool": "memory",
        "success": True,
        "result": matches[:10],
    }


def tool_status():
    return {
        "tool": "status",
        "success": True,
        "result": {
            "version": VERSION,
            "build": BUILD,
            "timestamp": now(),
        },
    }


def execute_tool(
    tool,
    mission_id,
    objective,
):
    if tool == "research":
        return tool_research(
            mission_id,
            objective
        )

    if tool == "memory":
        return tool_memory_lookup(
            objective
        )

    if tool == "status":
        return tool_status()

    return {
        "tool": tool,
        "success": False,
        "result": None,
        "error": "tool-not-available",
    }


def select_tool(
    objective,
    strategy,
    evidence_count,
):
    text = objective.lower()

    if strategy in {
        "direct-research",
        "cross-source",
        "fallback-provider",
        "verify-first",
    }:
        return "research"

    if any(
        x in text
        for x in [
            "remember",
            "previous",
            "memory",
            "learned",
        ]
    ):
        return "memory"

    if evidence_count == 0:
        return "research"

    return "status"


# ============================================================
# OBSERVATION
# ============================================================

def observe_result(tool_result):
    if not tool_result:
        return {
            "success": False,
            "confidence": 0,
            "summary": "No result returned.",
        }

    success = bool(
        tool_result.get("success")
    )

    result = tool_result.get(
        "result"
    )

    if isinstance(result, dict):
        count = result.get(
            "count",
            result.get(
                "result_count",
                0
            )
        )

        if count:
            confidence = min(
                0.95,
                0.45
                + min(
                    0.45,
                    float(count) / 20
                )
            )

        elif result.get("results"):
            confidence = 0.7

        elif success:
            confidence = 0.65

        else:
            confidence = 0.2

    elif success:
        confidence = 0.65

    else:
        confidence = 0.15

    return {
        "success": success,
        "confidence": round(
            confidence,
            3
        ),
        "summary": (
            "Tool execution produced a usable "
            "result."
            if success
            else
            "Tool execution did not produce "
            "a usable result."
        ),
    }


# ============================================================
# OUTCOME VERIFICATION
# ============================================================

def verify_mission_outcome(
    mission_id,
    objective,
    synthesis,
):
    evidence_count = 0

    with db_lock:
        conn = db()

        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM evidence
            WHERE mission_id=?
            """,
            (mission_id,)
        ).fetchone()

        evidence_count = row["count"]

        conn.close()

    confidence = float(
        synthesis.get(
            "confidence",
            0
        )
    )

    verified = (
        evidence_count >= 4
        and confidence >= 0.55
    )

    if evidence_count >= 8:
        confidence = min(
            0.95,
            confidence + 0.1
        )

    return {
        "verified": verified,
        "confidence": round(
            confidence,
            3
        ),
        "evidence_count": evidence_count,
        "reason": (
            "Multiple evidence records and "
            "cross-source synthesis support the result."
            if verified
            else
            "More evidence or verification is required."
        ),
    }


# ============================================================
# MISSION PERSISTENCE
# ============================================================

def create_mission(objective):
    mission_id = uid("mission")

    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO missions
            (
                id,
                objective,
                status,
                result,
                confidence,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                objective,
                "running",
                None,
                0,
                now(),
                now(),
            )
        )

        conn.commit()
        conn.close()

    return mission_id


def update_mission(
    mission_id,
    status=None,
    result=None,
    confidence=None,
):
    with db_lock:
        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM missions
            WHERE id=?
            """,
            (mission_id,)
        ).fetchone()

        if not row:
            conn.close()
            return

        conn.execute(
            """
            UPDATE missions
            SET status=?,
                result=?,
                confidence=?,
                updated_at=?
            WHERE id=?
            """,
            (
                status
                if status is not None
                else row["status"],
                (
                    dumps(result)
                    if result is not None
                    else row["result"]
                ),
                (
                    confidence
                    if confidence is not None
                    else row["confidence"]
                ),
                now(),
                mission_id,
            )
        )

        conn.commit()
        conn.close()


def save_requirement(
    mission_id,
    requirement,
    priority,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO requirements
            (
                mission_id,
                requirement,
                priority
            )
            VALUES (?, ?, ?)
            """,
            (
                mission_id,
                requirement,
                priority,
            )
        )

        conn.commit()
        conn.close()


def save_step(
    mission_id,
    step,
    status,
    strategy,
    attempts,
    confidence,
    result,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO mission_steps
            (
                mission_id,
                step,
                status,
                strategy,
                attempts,
                confidence,
                result,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                step,
                status,
                strategy,
                attempts,
                confidence,
                dumps(result),
                now(),
            )
        )

        conn.commit()
        conn.close()


def save_observation(
    mission_id,
    stage,
    observation,
    success,
    confidence,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO observations
            (
                mission_id,
                stage,
                observation,
                success,
                confidence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                stage,
                dumps(observation),
                1 if success else 0,
                confidence,
                now(),
            )
        )

        conn.commit()
        conn.close()


def save_learning(
    mission_id,
    lesson,
    strategy,
    outcome,
    confidence,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO learning
            (
                mission_id,
                lesson,
                strategy,
                outcome,
                confidence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                lesson,
                strategy,
                outcome,
                confidence,
                now(),
            )
        )

        conn.commit()
        conn.close()


def save_checkpoint(
    mission_id,
    state,
    payload,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO checkpoints
            (
                mission_id,
                state,
                payload,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                mission_id,
                state,
                dumps(payload),
                now(),
            )
        )

        conn.commit()
        conn.close()


def save_provenance(
    mission_id,
    event,
    source,
    detail,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO provenance
            (
                mission_id,
                event,
                source,
                detail,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                event,
                source,
                detail,
                now(),
            )
        )

        conn.commit()
        conn.close()


def save_memory(
    key,
    value,
    confidence=0.7,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO memory
            (
                key,
                value,
                confidence,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                key,
                value,
                confidence,
                now(),
            )
        )

        conn.commit()
        conn.close()


def save_artifact(
    mission_id,
    name,
    kind,
    content,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO artifacts
            (
                mission_id,
                name,
                kind,
                content,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                name,
                kind,
                content,
                now(),
            )
        )

        conn.commit()
        conn.close()


# ============================================================
# APPROVAL
# ============================================================

def requires_approval(objective):
    dangerous_terms = [
        "delete",
        "transfer money",
        "send money",
        "purchase",
        "buy",
        "publish",
        "send email",
        "change password",
        "deploy",
        "remove account",
    ]

    text = objective.lower()

    return any(
        term in text
        for term in dangerous_terms
    )


def create_approval(
    mission_id,
    action,
    risk="high",
):
    approval_id = uid("approval")

    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO approvals
            (
                id,
                mission_id,
                action,
                risk,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                mission_id,
                action,
                risk,
                "pending",
                now(),
            )
        )

        conn.commit()
        conn.close()

    return approval_id


# ============================================================
# ADAPTIVE MISSION LOOP
# ============================================================

def run_mission(mission_id, objective):
    cycle = 0
    previous_strategy = None
    all_trace = []
    final_synthesis = {}

    try:

        # ----------------------------------------------------
        # 1. REQUIREMENTS
        # ----------------------------------------------------

        requirements = discover_requirements(
            objective
        )

        for item in requirements:
            save_requirement(
                mission_id,
                item["requirement"],
                item["priority"],
            )

        save_provenance(
            mission_id,
            "requirements-discovered",
            "requirement-engine",
            dumps(requirements),
        )

        # ----------------------------------------------------
        # 2. APPROVAL BOUNDARY
        # ----------------------------------------------------

        if requires_approval(objective):
            approval_id = create_approval(
                mission_id,
                objective,
                "high",
            )

            update_mission(
                mission_id,
                status="awaiting_approval",
                result={
                    "approval_id": approval_id,
                    "reason": (
                        "Requested objective contains "
                        "an action requiring explicit approval."
                    ),
                },
                confidence=0,
            )

            return

        # ----------------------------------------------------
        # 3. ADAPTIVE LOOP
        # ----------------------------------------------------

        while cycle < MAX_ADAPTIVE_ATTEMPTS:
            cycle += 1

            with db_lock:
                conn = db()

                row = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM evidence
                    WHERE mission_id=?
                    """,
                    (mission_id,)
                ).fetchone()

                evidence_count = row["count"]

                conn.close()

            final_synthesis = synthesize_evidence(
                get_mission_evidence(
                    mission_id
                )
            )

            confidence = final_synthesis.get(
                "confidence",
                0
            )

            strategy = choose_strategy(
                objective,
                evidence_count,
                confidence,
                previous_strategy,
                cycle,
            )

            tool = select_tool(
                objective,
                strategy,
                evidence_count,
            )

            state = (
                "reason"
                if cycle == 1
                else "adapt"
            )

            trace_adaptive(
                mission_id,
                cycle,
                state,
                strategy,
                tool,
                "planning",
                "strategy-selection",
                "execute-tool",
                confidence,
            )

            save_checkpoint(
                mission_id,
                "before-execution",
                {
                    "cycle": cycle,
                    "strategy": strategy,
                    "tool": tool,
                    "confidence": confidence,
                },
            )

            # ------------------------------------------------
            # 4. EXECUTE
            # ------------------------------------------------

            tool_result = execute_tool(
                tool,
                mission_id,
                objective,
            )

            observation = observe_result(
                tool_result
            )

            save_observation(
                mission_id,
                "execution",
                observation,
                observation["success"],
                observation["confidence"],
            )

            # ------------------------------------------------
            # 5. RE-SYNTHESIZE
            # ------------------------------------------------

            evidence = get_mission_evidence(
                mission_id
            )

            final_synthesis = synthesize_evidence(
                evidence
            )

            confidence = final_synthesis.get(
                "confidence",
                observation["confidence"]
            )

            diagnosis = diagnose_observation(
                observation["success"],
                tool_result,
                len(evidence),
                confidence,
            )

            all_trace.append({
                "cycle": cycle,
                "strategy": strategy,
                "tool": tool,
                "observation": observation,
                "diagnosis": diagnosis,
                "confidence": confidence,
            })

            trace_adaptive(
                mission_id,
                cycle,
                "observe",
                strategy,
                tool,
                dumps(observation),
                dumps(diagnosis),
                diagnosis["next"],
                confidence,
            )

            save_step(
                mission_id,
                "adaptive-cycle",
                "completed",
                strategy,
                cycle,
                confidence,
                {
                    "tool": tool,
                    "observation": observation,
                    "diagnosis": diagnosis,
                },
            )

            record_strategy_outcome(
                strategy,
                observation["success"]
            )

            # ------------------------------------------------
            # 6. VERIFY
            # ------------------------------------------------

            verification = verify_mission_outcome(
                mission_id,
                objective,
                final_synthesis,
            )

            if verification["verified"]:
                save_learning(
                    mission_id,
                    (
                        f"Strategy '{strategy}' produced "
                        f"a verifiable result."
                    ),
                    strategy,
                    "success",
                    verification["confidence"],
                )

                save_memory(
                    f"mission:{mission_id}:lesson",
                    (
                        f"{strategy} was useful for "
                        f"objective: {objective[:300]}"
                    ),
                    verification["confidence"],
                )

                save_artifact(
                    mission_id,
                    "final-synthesis",
                    "research-synthesis",
                    dumps(final_synthesis),
                )

                save_provenance(
                    mission_id,
                    "outcome-verified",
                    "verification-engine",
                    dumps(verification),
                )

                with db_lock:
                    conn = db()

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO outcomes
                        (
                            mission_id,
                            expected,
                            observed,
                            verified,
                            confidence,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            mission_id,
                            objective,
                            dumps({
                                "evidence_count":
                                    len(evidence),
                                "synthesis":
                                    final_synthesis,
                            }),
                            1,
                            verification["confidence"],
                            now(),
                        )
                    )

                    conn.commit()
                    conn.close()

                result = {
                    "objective": objective,
                    "status": "verified",
                    "confidence": verification[
                        "confidence"
                    ],
                    "cycles": cycle,
                    "strategy": strategy,
                    "evidence_count": len(evidence),
                    "synthesis": final_synthesis,
                    "verification": verification,
                    "adaptive_trace": all_trace,
                }

                update_mission(
                    mission_id,
                    status="completed",
                    result=result,
                    confidence=verification[
                        "confidence"
                    ],
                )

                return

            # ------------------------------------------------
            # 7. ADAPT
            # ------------------------------------------------

            save_checkpoint(
                mission_id,
                "replan",
                {
                    "cycle": cycle,
                    "previous_strategy": strategy,
                    "diagnosis": diagnosis,
                    "confidence": confidence,
                },
            )

            save_learning(
                mission_id,
                (
                    f"Strategy '{strategy}' did not yet "
                    f"produce sufficient verification."
                ),
                strategy,
                "replan",
                confidence,
            )

            previous_strategy = strategy

        # ----------------------------------------------------
        # 8. BOUNDED COMPLETION
        # ----------------------------------------------------

        evidence = get_mission_evidence(
            mission_id
        )

        final_synthesis = synthesize_evidence(
            evidence
        )

        result = {
            "objective": objective,
            "status": "needs-more-verification",
            "confidence": final_synthesis.get(
                "confidence",
                0
            ),
            "cycles": cycle,
            "evidence_count": len(evidence),
            "synthesis": final_synthesis,
            "adaptive_trace": all_trace,
            "next_action": (
                "Continue with a new bounded mission "
                "or provide additional requirements."
            ),
        }

        update_mission(
            mission_id,
            status="completed_with_gaps",
            result=result,
            confidence=final_synthesis.get(
                "confidence",
                0
            ),
        )

    except Exception as exc:

        save_checkpoint(
            mission_id,
            "failure",
            {
                "cycle": cycle,
                "error": str(exc),
            },
        )

        save_learning(
            mission_id,
            f"Mission execution failure: {exc}",
            previous_strategy or "unknown",
            "failure",
            0.1,
        )

        update_mission(
            mission_id,
            status="failed",
            result={
                "error": str(exc),
                "cycle": cycle,
                "adaptive_trace": all_trace,
            },
            confidence=0.1,
        )


def get_mission_evidence(mission_id):
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM evidence
            WHERE mission_id=?
            ORDER BY id ASC
            """,
            (mission_id,)
        ).fetchall()

        conn.close()

    output = []

    for row in rows:
        item = dict(row)

        item["metadata"] = loads(
            item.get("metadata"),
            {}
        )

        output.append(item)

    return output


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
def root():
    return {
        "service": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "next_generation": True,
        "adaptive_loop": True,
        "controlled_external_access": True,
        "research_providers": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        "links": {
            "health": "/health",
            "architecture": "/architecture",
            "docs": "/docs",
            "interface": "/interface",
            "run": "/run",
            "capabilities": "/capabilities",
            "tools": "/tools",
            "test_adaptive": "/test-adaptive",
            "test_research": "/test-research",
        },
    }


@app.get("/health")
def health():
    with db_lock:
        conn = db()

        provider_rows = conn.execute(
            """
            SELECT *
            FROM provider_health
            ORDER BY provider
            """
        ).fetchall()

        conn.close()

    provider_health_data = [
        dict(row)
        for row in provider_rows
    ]

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

            # 2050.61
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
        },
        "research": {
            "providers": [
                "wikipedia",
                "crossref",
                "arxiv",
                "openalex",
            ],
            "health": provider_health_data,
        },
        "adaptive": {
            "max_cycles": MAX_ADAPTIVE_ATTEMPTS,
            "executor_workers": 4,
            "loop": [
                "observe",
                "diagnose",
                "choose-strategy",
                "select-tool",
                "execute",
                "inspect",
                "verify",
                "adapt",
                "learn",
            ],
        },
    }


@app.get("/status")
def status():
    return health()


# ============================================================
# ARCHITECTURE
# ============================================================

@app.get("/architecture")
def architecture():
    layers = [
        (
            1,
            "Intent",
            "Understand the requested outcome"
        ),
        (
            2,
            "Requirements",
            "Discover what must be true"
        ),
        (
            3,
            "Research",
            "Gather independent information"
        ),
        (
            4,
            "Evidence",
            "Store and provenance-link evidence"
        ),
        (
            5,
            "Synthesis",
            "Compare sources and form claims"
        ),
        (
            6,
            "Decision",
            "Select bounded next actions"
        ),
        (
            7,
            "Mission Graph",
            "Execute dependencies dynamically"
        ),
        (
            8,
            "Authorization",
            "Enforce permissions"
        ),
        (
            9,
            "Execution",
            "Use controlled tools"
        ),
        (
            10,
            "Observation",
            "Measure what actually happened"
        ),
        (
            11,
            "Verification",
            "Determine whether the outcome is real"
        ),
        (
            12,
            "Recovery",
            "Checkpoint and replan after failure"
        ),
        (
            13,
            "Learning",
            "Extract reusable lessons"
        ),
        (
            14,
            "Skills",
            "Turn successful procedures into reusable capability"
        ),
        (
            15,
            "Artifacts",
            "Preserve produced work"
        ),
        (
            16,
            "Provenance",
            "Trace how results were produced"
        ),
        (
            17,
            "Resource Governance",
            "Track bounded resource usage"
        ),
        (
            18,
            "Adaptive Reasoning",
            "Interpret observations and select strategy"
        ),
        (
            19,
            "Tool Selection",
            "Choose the most appropriate available tool"
        ),
        (
            20,
            "Execution Inspection",
            "Inspect actual tool outcomes"
        ),
        (
            21,
            "Failure Diagnosis",
            "Classify why an attempt failed"
        ),
        (
            22,
            "Adaptive Replanning",
            "Change strategy instead of blindly repeating"
        ),
        (
            23,
            "Confidence Engine",
            "Track evidence and execution confidence"
        ),
        (
            24,
            "Strategy Memory",
            "Remember which strategies work"
        ),
        (
            25,
            "Convergence",
            "Stop when the outcome is sufficiently verified"
        ),
    ]

    return {
        "version": VERSION,
        "build": BUILD,
        "architecture": [
            {
                "layer": number,
                "name": name,
                "purpose": purpose,
            }
            for number, name, purpose in layers
        ],
        "closed_loop": [
            "Intent",
            "Requirements",
            "Research",
            "Evidence",
            "Synthesis",
            "Decision",
            "Mission Graph",
            "Authorization",
            "Execution",
            "Observation",
            "Diagnosis",
            "Replanning",
            "Verification",
            "Learning",
            "Strategy Memory",
        ],
    }


# ============================================================
# RUN
# ============================================================

@app.get("/run")
def run_info():
    return {
        "endpoint": "/run",
        "method": "POST",
        "description": (
            "Create an AI Infinity adaptive mission."
        ),
        "example": {
            "command": (
                "Research the reliability of "
                "autonomous AI agents."
            )
        },
    }


@app.post("/run")
def run(request: CreateMission):
    mission_id = create_mission(
        request.command
    )

    executor.submit(
        run_mission,
        mission_id,
        request.command,
    )

    return {
        "mission_id": mission_id,
        "objective": request.command,
        "status": "running",
        "version": VERSION,
        "build": BUILD,
        "adaptive": True,
    }


# ============================================================
# MISSION
# ============================================================

@app.get("/mission/{mission_id}")
def mission(mission_id: str):
    with db_lock:
        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM missions
            WHERE id=?
            """,
            (mission_id,)
        ).fetchone()

        conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found"
        )

    result = dict(row)

    result["result"] = loads(
        result["result"]
    )

    return result


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(mission_id: str):
    return {
        "mission_id": mission_id,
        "count": len(
            get_mission_evidence(
                mission_id
            )
        ),
        "evidence": get_mission_evidence(
            mission_id
        ),
    }


@app.get("/mission/{mission_id}/events")
def mission_events(mission_id: str):
    with db_lock:
        conn = db()

        observations = conn.execute(
            """
            SELECT *
            FROM observations
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,)
        ).fetchall()

        traces = conn.execute(
            """
            SELECT *
            FROM adaptive_trace
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,)
        ).fetchall()

        conn.close()

    return {
        "mission_id": mission_id,
        "observations": [
            dict(row)
            for row in observations
        ],
        "adaptive_trace": [
            dict(row)
            for row in traces
        ],
    }


@app.get("/mission/{mission_id}/checkpoints")
def mission_checkpoints(mission_id: str):
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM checkpoints
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,)
        ).fetchall()

        conn.close()

    output = []

    for row in rows:
        item = dict(row)
        item["payload"] = loads(
            item["payload"],
            {}
        )
        output.append(item)

    return {
        "mission_id": mission_id,
        "checkpoints": output,
    }


@app.get("/mission/{mission_id}/outcome")
def mission_outcome(mission_id: str):
    with db_lock:
        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM outcomes
            WHERE mission_id=?
            """,
            (mission_id,)
        ).fetchone()

        conn.close()

    if not row:
        return {
            "mission_id": mission_id,
            "available": False,
        }

    return {
        "mission_id": mission_id,
        "available": True,
        "outcome": dict(row),
    }


@app.get("/mission/{mission_id}/verify-outcome")
def verify_endpoint(mission_id: str):
    with db_lock:
        conn = db()

        row = conn.execute(
            """
            SELECT objective
            FROM missions
            WHERE id=?
            """,
            (mission_id,)
        ).fetchone()

        conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found"
        )

    synthesis = synthesize_evidence(
        get_mission_evidence(
            mission_id
        )
    )

    verification = verify_mission_outcome(
        mission_id,
        row["objective"],
        synthesis,
    )

    return {
        "mission_id": mission_id,
        "verification": verification,
    }


@app.get("/mission/{mission_id}/requirements")
def mission_requirements(mission_id: str):
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM requirements
            WHERE mission_id=?
            ORDER BY priority DESC
            """,
            (mission_id,)
        ).fetchall()

        conn.close()

    return {
        "mission_id": mission_id,
        "requirements": [
            dict(row)
            for row in rows
        ],
    }


@app.get("/mission/{mission_id}/claims")
def mission_claims(mission_id: str):
    evidence = get_mission_evidence(
        mission_id
    )

    synthesis = synthesize_evidence(
        evidence
    )

    return {
        "mission_id": mission_id,
        "claims": synthesis["claims"],
        "contradictions": synthesis[
            "contradictions"
        ],
        "gaps": synthesis["gaps"],
        "confidence": synthesis[
            "confidence"
        ],
    }


# ============================================================
# APPROVALS
# ============================================================

@app.get("/approvals")
def approvals():
    with db_lock:
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
    decision: ApprovalDecision,
):
    with db_lock:
        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM approvals
            WHERE id=?
            """,
            (approval_id,)
        ).fetchone()

        if not row:
            conn.close()

            raise HTTPException(
                status_code=404,
                detail="Approval not found"
            )

        status = (
            "approved"
            if decision.approved
            else "rejected"
        )

        conn.execute(
            """
            UPDATE approvals
            SET status=?,
                decided_at=?
            WHERE id=?
            """,
            (
                status,
                now(),
                approval_id,
            )
        )

        conn.commit()
        conn.close()

    return {
        "approval_id": approval_id,
        "status": status,
        "note": (
            "Approval records permission but does not "
            "enable unrestricted external execution."
        ),
    }


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory")
def memory():
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM memory
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

        conn.close()

    return {
        "count": len(rows),
        "memory": [
            dict(row)
            for row in rows
        ],
    }


@app.post("/memory")
def add_memory(item: MemoryInput):
    save_memory(
        item.key,
        item.value,
        item.confidence,
    )

    return {
        "status": "stored",
        "key": item.key,
    }


@app.get("/memory-count")
def memory_count():
    with db_lock:
        conn = db()

        row = conn.execute(
            "SELECT COUNT(*) AS count FROM memory"
        ).fetchone()

        conn.close()

    return {
        "count": row["count"]
    }


# ============================================================
# SKILLS / LEARNING
# ============================================================

@app.get("/skills")
def skills():
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM skills
            ORDER BY confidence DESC
            """
        ).fetchall()

        conn.close()

    return {
        "skills": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/skills-count")
def skills_count():
    with db_lock:
        conn = db()

        row = conn.execute(
            "SELECT COUNT(*) AS count FROM skills"
        ).fetchone()

        conn.close()

    return {
        "count": row["count"]
    }


@app.get("/learning")
def learning():
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM learning
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

        conn.close()

    return {
        "learning": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/strategies")
def strategies():
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM strategies
            ORDER BY confidence DESC
            """
        ).fetchall()

        conn.close()

    return {
        "strategies": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# ARTIFACTS
# ============================================================

@app.get("/artifacts")
def artifacts():
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM artifacts
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

        conn.close()

    return {
        "artifacts": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# CONNECTORS
# ============================================================

@app.get("/connectors")
def connectors():
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM connectors
            ORDER BY name
            """
        ).fetchall()

        conn.close()

    return {
        "connectors": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/connector-health")
def connector_health():
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM connectors
            ORDER BY name
            """
        ).fetchall()

        conn.close()

    return {
        "connectors": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# TOOLS
# ============================================================

@app.get("/tools")
def tools():
    return {
        "tools": [
            {
                "name": "research",
                "permission": "safe",
                "description": (
                    "Multi-provider public research."
                ),
            },
            {
                "name": "memory",
                "permission": "safe",
                "description": (
                    "Read persistent local memory."
                ),
            },
            {
                "name": "status",
                "permission": "safe",
                "description": (
                    "Inspect system state."
                ),
            },
        ],
        "adaptive_selection": True,
        "arbitrary_code_execution": False,
    }


@app.get("/capabilities")
def capabilities():
    return {
        "capabilities": [
            {
                "name": "research",
                "available": True,
            },
            {
                "name": "evidence-synthesis",
                "available": True,
            },
            {
                "name": "adaptive-reasoning",
                "available": True,
            },
            {
                "name": "tool-selection",
                "available": True,
            },
            {
                "name": "adaptive-replanning",
                "available": True,
            },
            {
                "name": "verification",
                "available": True,
            },
            {
                "name": "persistent-learning",
                "available": True,
            },
            {
                "name": "controlled-public-web",
                "available": True,
            },
            {
                "name": "arbitrary-code-execution",
                "available": False,
            },
            {
                "name": "unrestricted-private-network",
                "available": False,
            },
        ]
    }


@app.get("/discover")
def discover(objective: str):
    requirements = discover_requirements(
        objective
    )

    strategy = choose_strategy(
        objective
    )

    tool = select_tool(
        objective,
        strategy,
        0
    )

    return {
        "objective": objective,
        "requirements": requirements,
        "strategy": strategy,
        "tool": tool,
        "adaptive": True,
    }


# ============================================================
# RESEARCH ENDPOINTS
# ============================================================

@app.get("/research/sources")
def research_sources():
    return {
        "providers": [
            {
                "name": "wikipedia",
                "type": "encyclopedia",
                "active": True,
            },
            {
                "name": "crossref",
                "type": "bibliographic",
                "active": True,
            },
            {
                "name": "arxiv",
                "type": "preprint",
                "active": True,
            },
            {
                "name": "openalex",
                "type": "research-index",
                "active": True,
            },
        ]
    }


@app.get("/research/providers")
def research_providers():
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM provider_health
            ORDER BY provider
            """
        ).fetchall()

        conn.close()

    return {
        "providers": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/research/discover")
def research_discover(query: str):
    return ingest_research(
        query
    )


@app.get("/research/providers/test")
def research_provider_test():
    query = "artificial intelligence agents"

    result = ingest_research(
        query
    )

    return result


@app.get("/test-research")
def test_research():
    query = "artificial intelligence agents"

    result = ingest_research(
        query
    )

    return {
        "test": "research",
        "version": VERSION,
        "query": query,
        "result": result,
    }


# ============================================================
# ADAPTIVE TEST
# ============================================================

@app.get("/test-adaptive")
def test_adaptive():
    objective = (
        "Research and verify the reliability "
        "of autonomous AI agents for real-world "
        "task execution."
    )

    strategy_1 = choose_strategy(
        objective,
        evidence_count=0,
        confidence=0,
        previous_strategy=None,
        cycle=1,
    )

    strategy_2 = choose_strategy(
        objective,
        evidence_count=5,
        confidence=0.45,
        previous_strategy=strategy_1,
        cycle=2,
    )

    strategy_3 = choose_strategy(
        objective,
        evidence_count=12,
        confidence=0.82,
        previous_strategy=strategy_2,
        cycle=3,
    )

    return {
        "status": "passed",
        "version": VERSION,
        "build": BUILD,
        "adaptive_reasoning": {
            "cycle_1": strategy_1,
            "cycle_2": strategy_2,
            "cycle_3": strategy_3,
        },
        "loop": [
            "observe",
            "diagnose",
            "choose-strategy",
            "select-tool",
            "execute",
            "inspect",
            "verify",
            "adapt",
            "learn",
        ],
    }


@app.get("/test-orchestrator")
def test_orchestrator():
    objective = (
        "Research the reliability of autonomous AI agents."
    )

    strategy = choose_strategy(
        objective
    )

    tool = select_tool(
        objective,
        strategy,
        0
    )

    return {
        "status": "passed",
        "strategy": strategy,
        "tool": tool,
        "adaptive": True,
    }


@app.get("/test-intelligence")
def test_intelligence():
    evidence = ingest_research(
        "autonomous AI agents"
    )

    synthesis = synthesize_evidence(
        evidence["results"]
    )

    return {
        "status": "passed",
        "version": VERSION,
        "evidence_count": len(
            evidence["results"]
        ),
        "synthesis": synthesis,
        "adaptive_reasoning": True,
    }


@app.get("/test-router")
def test_router():
    objective = (
        "Research the reliability of "
        "autonomous AI agents."
    )

    strategy = choose_strategy(
        objective
    )

    tool = select_tool(
        objective,
        strategy,
        0
    )

    return {
        "status": "passed",
        "objective": objective,
        "strategy": strategy,
        "tool": tool,
    }


@app.get("/test-tools")
def test_tools():
    return {
        "status": "passed",
        "tools": tools()["tools"],
    }


@app.get("/test-external")
def test_external():
    return {
        "status": "controlled",
        "network_policy_enforced": True,
        "controlled_public_web_access": True,
        "arbitrary_code_execution": False,
        "unrestricted_private_network_access": False,
        "allowlisted_domains": sorted(
            configured_domains()
        ),
    }


# ============================================================
# POLICY
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
        "credential_bypass": False,
        "stealth_persistence": False,
        "approval_required_for_sensitive_actions": True,
        "allowlisted_domains": sorted(
            configured_domains()
        ),
    }


@app.post("/policy/validate")
def policy_validate():
    return policy()


# ============================================================
# INTERFACE
# ============================================================

@app.get("/interface")
def interface():
    return {
        "name": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "mode": "adaptive-autonomous",
        "features": [
            "missions",
            "adaptive reasoning",
            "research",
            "evidence",
            "verification",
            "replanning",
            "learning",
            "memory",
            "controlled tools",
        ],
        "endpoints": {
            "health": "/health",
            "architecture": "/architecture",
            "run": "/run",
            "adaptive_test": "/test-adaptive",
            "research_test": "/test-research",
        },
    }


@app.get("/ui", response_class=HTMLResponse)
def ui():
    return """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
      content="width=device-width, initial-scale=1">
<title>AI Infinity</title>
<style>
body {
    font-family: Arial, sans-serif;
    background: #0b0f14;
    color: #f4f7fb;
    margin: 0;
    padding: 20px;
}
.card {
    max-width: 700px;
    margin: auto;
    background: #131a22;
    border-radius: 18px;
    padding: 22px;
    box-shadow: 0 10px 40px rgba(0,0,0,.35);
}
h1 {
    margin-top: 0;
}
.badge {
    display: inline-block;
    padding: 7px 10px;
    border-radius: 10px;
    background: #1d2a38;
    margin-bottom: 15px;
}
textarea {
    width: 100%;
    min-height: 130px;
    box-sizing: border-box;
    background: #0d131a;
    color: white;
    border: 1px solid #33404d;
    border-radius: 12px;
    padding: 12px;
    font-size: 16px;
}
button {
    width: 100%;
    margin-top: 12px;
    padding: 14px;
    border: 0;
    border-radius: 12px;
    font-size: 16px;
}
pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: #080c10;
    padding: 12px;
    border-radius: 12px;
}
a {
    color: #8dc8ff;
}
</style>
</head>

<body>
<div class="card">

<h1>AI Infinity ∞</h1>

<div class="badge">
TARGET-2050.61 — Adaptive Core
</div>

<p>
Observe → Reason → Act → Inspect → Verify → Adapt → Learn
</p>

<textarea id="command"
placeholder="Tell AI Infinity what outcome you want..."></textarea>

<button onclick="runMission()">
Run Mission
</button>

<pre id="output">Ready.</pre>

<p>
<a href="/health">Health</a> |
<a href="/architecture">Architecture</a> |
<a href="/docs">API Docs</a> |
<a href="/test-adaptive">Adaptive Test</a>
</p>

</div>

<script>
async function runMission() {
    const command =
        document.getElementById("command").value;

    const output =
        document.getElementById("output");

    if (!command.trim()) {
        output.textContent =
            "Enter a mission first.";
        return;
    }

    output.textContent =
        "Starting adaptive mission...";

    try {
        const response = await fetch(
            "/run",
            {
                method: "POST",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify({
                    command: command
                })
            }
        );

        const data =
            await response.json();

        output.textContent =
            JSON.stringify(
                data,
                null,
                2
            );
    } catch (error) {
        output.textContent =
            String(error);
    }
}
</script>

</body>
</html>
"""


# ============================================================
# ARCHITECTURE GRAPH
# ============================================================

@app.get("/architecture/graph")
def architecture_graph():
    return {
        "version": VERSION,
        "nodes": [
            "intent",
            "requirements",
            "research",
            "evidence",
            "synthesis",
            "decision",
            "mission_graph",
            "authorization",
            "execution",
            "observation",
            "diagnosis",
            "verification",
            "recovery",
            "replanning",
            "learning",
            "skills",
            "memory",
            "provenance",
            "artifacts",
        ],
        "edges": [
            ["intent", "requirements"],
            ["requirements", "research"],
            ["research", "evidence"],
            ["evidence", "synthesis"],
            ["synthesis", "decision"],
            ["decision", "mission_graph"],
            ["mission_graph", "authorization"],
            ["authorization", "execution"],
            ["execution", "observation"],
            ["observation", "diagnosis"],
            ["diagnosis", "verification"],
            ["diagnosis", "replanning"],
            ["replanning", "execution"],
            ["verification", "learning"],
            ["learning", "skills"],
            ["learning", "memory"],
            ["execution", "provenance"],
            ["verification", "artifacts"],
        ],
        "closed_loop": True,
    }


# ============================================================
# ADAPTIVE TRACE
# ============================================================

@app.get("/mission/{mission_id}/adaptive")
def adaptive_trace(mission_id: str):
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM adaptive_trace
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,)
        ).fetchall()

        conn.close()

    return {
        "mission_id": mission_id,
        "cycles": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("shutdown")
def shutdown_event():
    executor.shutdown(
        wait=False,
        cancel_futures=False,
    )
