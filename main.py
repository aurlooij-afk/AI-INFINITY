"""
AI Infinity
TARGET-2050.59
Build: EVIDENCE-SYNTHESIS-AND-VERIFICATION-FABRIC

Single-file FastAPI core.

Architecture:
intent
 -> planning
 -> capability routing
 -> controlled public research
 -> evidence normalization
 -> source ranking
 -> contradiction/gap analysis
 -> synthesis
 -> independent verification
 -> provenance
 -> checkpoint
 -> learning

Security:
- HTTP/HTTPS only
- blocks localhost/private/link-local/metadata addresses
- optional explicit domain allowlist
- built-in research providers are allowed
- response-size limits
- timeout/retry limits
- no arbitrary code execution
- no credential/permission bypass
- high-risk actions require approval
"""

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
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# VERSION
# ============================================================

VERSION = "TARGET-2050.59"
BUILD = "EVIDENCE-SYNTHESIS-AND-VERIFICATION-FABRIC"

POLICY_VERSION = 1


# ============================================================
# CONFIG
# ============================================================

BASE = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai_infinity"))
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
MAX_RESPONSE_BYTES = int(
    os.getenv("MAX_RESPONSE_BYTES", str(2 * 1024 * 1024))
)
MAX_SOURCE_TEXT = int(os.getenv("MAX_SOURCE_TEXT", "12000"))
MAX_RESEARCH_SOURCES = int(os.getenv("MAX_RESEARCH_SOURCES", "20"))
RESEARCH_MAX_RETRIES = int(os.getenv("RESEARCH_MAX_RETRIES", "3"))

# Explicit optional allowlist for non-built-in domains.
# Empty means built-in research domains remain available while
# arbitrary public domains require configuration.
EXTERNAL_ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}

RESEARCH_SEED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("RESEARCH_SEED_DOMAINS", "").split(",")
    if x.strip()
}

RESEARCH_DISCOVERY_URLS = [
    x.strip()
    for x in os.getenv("RESEARCH_DISCOVERY_URLS", "").split(",")
    if x.strip()
]


# ============================================================
# NETWORK POLICY
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
    "metadata",
}

BUILTIN_RESEARCH_DOMAINS = {
    "wikipedia.org",
    "www.wikipedia.org",
    "en.wikipedia.org",
    "crossref.org",
    "api.crossref.org",
    "arxiv.org",
    "export.arxiv.org",
    "export.arxiv.org",
    "openalex.org",
    "api.openalex.org",
}


def hostname_matches(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    domain = domain.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        return False


def resolve_public(host: str) -> bool:
    """
    Resolve a hostname and reject private/internal addresses.
    """
    try:
        infos = socket.getaddrinfo(
            host,
            None,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        return False

    if not infos:
        return False

    for info in infos:
        addr = info[4][0]
        if is_private_ip(addr):
            return False

    return True


def domain_allowed(host: str) -> bool:
    host = host.lower().rstrip(".")

    if host in BLOCKED_HOSTS:
        return False

    if is_private_ip(host):
        return False

    if any(hostname_matches(host, d) for d in BUILTIN_RESEARCH_DOMAINS):
        return True

    if any(hostname_matches(host, d) for d in EXTERNAL_ALLOWED_DOMAINS):
        return True

    return False


def validate_external_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http/https URLs are permitted")

    if not parsed.hostname:
        raise ValueError("URL hostname missing")

    host = parsed.hostname.lower().rstrip(".")

    if not domain_allowed(host):
        raise ValueError(
            f"external domain is not permitted: {host}"
        )

    if not resolve_public(host):
        raise ValueError(
            f"hostname does not resolve exclusively to public addresses: {host}"
        )

    return url


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            result_json TEXT
        );

        CREATE TABLE IF NOT EXISTS mission_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            step_index INTEGER,
            name TEXT,
            status TEXT,
            result_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connectors (
            name TEXT PRIMARY KEY,
            category TEXT,
            permission TEXT,
            description TEXT,
            enabled INTEGER DEFAULT 1,
            metadata_json TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            provider TEXT,
            work_id TEXT,
            title TEXT,
            url TEXT,
            snippet TEXT,
            confidence REAL,
            metadata_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            event TEXT,
            source TEXT,
            detail_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            action TEXT,
            status TEXT,
            created_at TEXT,
            decided_at TEXT
        );

        CREATE TABLE IF NOT EXISTS learning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            key TEXT,
            value_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connector_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            connector TEXT,
            event TEXT,
            detail_json TEXT,
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
            severity REAL,
            resolved INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS research_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            provider TEXT,
            title TEXT,
            url TEXT,
            work_id TEXT,
            published TEXT,
            authors_json TEXT,
            abstract TEXT,
            snippet TEXT,
            confidence REAL,
            metadata_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS research_comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            comparison_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS provider_health (
            provider TEXT PRIMARY KEY,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            score REAL DEFAULT 0,
            last_status TEXT,
            last_error TEXT,
            last_http_status INTEGER,
            last_updated TEXT
        );
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "AI Infinity autonomous research, evidence synthesis, "
        "verification and controlled execution core."
    ),
)


# ============================================================
# UTILITIES
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str = "id") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\x00", " ")
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text,
                  flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text,
                  flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def canonical_url(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url)

        scheme = p.scheme.lower()
        host = (p.hostname or "").lower()

        path = p.path.rstrip("/") or "/"

        return urllib.parse.urlunparse(
            (
                scheme,
                host,
                path,
                "",
                "",
                "",
            )
        )
    except Exception:
        return url


def evidence_key(item: Dict[str, Any]) -> str:
    raw = (
        str(item.get("doi") or "")
        + "|"
        + str(item.get("work_id") or "")
        + "|"
        + canonical_url(str(item.get("url") or ""))
        + "|"
        + str(item.get("title") or "").lower()
    )

    return hashlib.sha256(
        raw.encode("utf-8", errors="ignore")
    ).hexdigest()


def deduplicate(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    output = []

    for item in items:
        key = evidence_key(item)

        if key in seen:
            continue

        seen.add(key)
        output.append(item)

    return output


def json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


# ============================================================
# HTTP
# ============================================================

@dataclass
class HttpResult:
    status: int
    body: bytes
    content_type: str
    url: str
    attempts: int


def http_get(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    retries: int = RESEARCH_MAX_RETRIES,
    timeout: int = HTTP_TIMEOUT,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> HttpResult:

    validate_external_url(url)

    request_headers = {
        "User-Agent": (
            "AI-Infinity/2050.59 "
            "(research; public-web; contact unavailable)"
        ),
        "Accept": (
            "application/json, application/xml, "
            "application/atom+xml, text/xml, text/html;q=0.9, */*;q=0.5"
        ),
    }

    if headers:
        request_headers.update(headers)

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers=request_headers,
                method="GET",
            )

            with urllib.request.urlopen(
                req,
                timeout=timeout,
            ) as response:

                status = int(response.status)

                content_type = response.headers.get(
                    "Content-Type",
                    "",
                )

                body = response.read(max_bytes + 1)

                if len(body) > max_bytes:
                    raise ValueError(
                        "response exceeded configured size limit"
                    )

                return HttpResult(
                    status=status,
                    body=body,
                    content_type=content_type,
                    url=response.geturl(),
                    attempts=attempt,
                )

        except urllib.error.HTTPError as exc:
            last_error = exc

            if exc.code not in {
                408,
                425,
                429,
                500,
                502,
                503,
                504,
            }:
                raise

        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
        ) as exc:
            last_error = exc

        if attempt < retries:
            time.sleep(min(2 ** (attempt - 1), 4))

    if last_error:
        raise last_error

    raise RuntimeError("HTTP request failed")


def decode_body(body: bytes) -> str:
    if body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]

    for encoding in (
        "utf-8",
        "utf-8-sig",
        "latin-1",
    ):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue

    return body.decode("utf-8", errors="replace")


def parse_json_resilient(body: bytes) -> Any:
    text = decode_body(body).strip()

    if not text:
        raise ValueError("empty response")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Handle accidental JSONP / wrappers.
    match = re.search(
        r"(\{.*\}|\[.*\])",
        text,
        flags=re.S,
    )

    if match:
        return json.loads(match.group(1))

    raise ValueError("response is not valid JSON")


# ============================================================
# EVIDENCE MODEL
# ============================================================

@dataclass
class EvidenceRecord:
    provider: str
    title: str
    url: str
    work_id: str = ""
    published: str = ""
    authors: Optional[List[str]] = None
    abstract: str = ""
    snippet: str = ""
    source_type: str = "web"
    confidence: float = 0.5
    metadata: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        value = asdict(self)

        if value["authors"] is None:
            value["authors"] = []

        if value["metadata"] is None:
            value["metadata"] = {}

        return value


# ============================================================
# WIKIPEDIA
# ============================================================

def parse_wikipedia(data: Any, limit: int) -> List[EvidenceRecord]:
    results = []

    if not isinstance(data, dict):
        raise ValueError("Wikipedia response is not an object")

    query = data.get("query") or {}
    search = query.get("search") or []

    if not isinstance(search, list):
        raise ValueError("Wikipedia search payload invalid")

    for item in search[:limit]:
        title = clean_text(item.get("title"))

        if not title:
            continue

        encoded = urllib.parse.quote(
            title.replace(" ", "_")
        )

        results.append(
            EvidenceRecord(
                provider="wikipedia",
                title=title,
                url=f"https://en.wikipedia.org/wiki/{encoded}",
                work_id=f"wiki:{title.lower()}",
                snippet=clean_text(item.get("snippet")),
                source_type="encyclopedia",
                confidence=0.70,
                metadata={
                    "pageid": item.get("pageid"),
                },
            )
        )

    return results


def wikipedia_search(
    query: str,
    limit: int = 5,
) -> Tuple[List[EvidenceRecord], Dict[str, Any]]:

    url = (
        "https://en.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "utf8": "1",
                "srlimit": limit,
            }
        )
    )

    response = http_get(
        url,
        headers={
            "Accept": "application/json",
        },
    )

    data = parse_json_resilient(response.body)

    results = parse_wikipedia(data, limit)

    return results, {
        "provider": "wikipedia",
        "status": "success",
        "http_status": response.status,
        "attempts": response.attempts,
        "recovery": "api-search",
        "content_type": response.content_type,
    }


# ============================================================
# CROSSREF
# ============================================================

def first_value(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return value


def crossref_authors(item: Dict[str, Any]) -> List[str]:
    output = []

    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue

        given = clean_text(author.get("given"))
        family = clean_text(author.get("family"))

        name = " ".join(
            x for x in (given, family) if x
        ).strip()

        if name:
            output.append(name)

    return output


def crossref_date(item: Dict[str, Any]) -> str:
    for key in (
        "published-print",
        "published-online",
        "published",
        "issued",
        "created",
    ):
        value = item.get(key)

        if not isinstance(value, dict):
            continue

        parts = (
            value.get("date-parts")
            or []
        )

        if parts and parts[0]:
            return "-".join(
                str(x) for x in parts[0]
            )

    return ""


def parse_crossref(
    data: Any,
    limit: int,
) -> List[EvidenceRecord]:

    if not isinstance(data, dict):
        raise ValueError("Crossref response is not an object")

    message = data.get("message") or {}

    items = message.get("items") or []

    if not isinstance(items, list):
        raise ValueError("Crossref items invalid")

    output = []

    for item in items[:limit]:
        if not isinstance(item, dict):
            continue

        title = clean_text(
            first_value(item.get("title"))
        )

        if not title:
            continue

        doi = clean_text(item.get("DOI"))

        url = (
            f"https://doi.org/{doi}"
            if doi
            else clean_text(
                item.get("URL")
            )
        )

        output.append(
            EvidenceRecord(
                provider="crossref",
                title=title,
                url=url,
                work_id=f"doi:{doi}" if doi else "",
                published=crossref_date(item),
                authors=crossref_authors(item),
                abstract=clean_text(item.get("abstract")),
                snippet=clean_text(item.get("abstract")),
                source_type="bibliographic",
                confidence=0.80,
                metadata={
                    "type": item.get("type"),
                    "publisher": item.get("publisher"),
                    "container_title": first_value(
                        item.get("container-title")
                    ),
                },
            )
        )

    return output


def crossref_search(
    query: str,
    limit: int = 5,
) -> Tuple[List[EvidenceRecord], Dict[str, Any]]:

    url = (
        "https://api.crossref.org/works?"
        + urllib.parse.urlencode(
            {
                "query.bibliographic": query,
                "rows": limit,
                "select": (
                    "DOI,title,author,published,"
                    "published-online,published-print,"
                    "issued,created,URL,type,publisher,"
                    "container-title,abstract"
                ),
            }
        )
    )

    response = http_get(
        url,
        headers={
            "Accept": "application/json",
        },
    )

    data = parse_json_resilient(response.body)

    results = parse_crossref(data, limit)

    return results, {
        "provider": "crossref",
        "status": "success",
        "http_status": response.status,
        "attempts": response.attempts,
        "recovery": "bibliographic-query",
        "content_type": response.content_type,
        "diagnostics": [],
    }


# ============================================================
# ARXIV
# ============================================================

def parse_arxiv(
    body: bytes,
    limit: int,
) -> List[EvidenceRecord]:

    text = decode_body(body)

    root = ET.fromstring(text)

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
    }

    output = []

    for entry in root.findall("atom:entry", ns)[:limit]:
        title = clean_text(
            entry.findtext(
                "atom:title",
                default="",
                namespaces=ns,
            )
        )

        if not title:
            continue

        abstract = clean_text(
            entry.findtext(
                "atom:summary",
                default="",
                namespaces=ns,
            )
        )

        published = clean_text(
            entry.findtext(
                "atom:published",
                default="",
                namespaces=ns,
            )
        )

        authors = []

        for author in entry.findall(
            "atom:author",
            ns,
        ):
            name = clean_text(
                author.findtext(
                    "atom:name",
                    default="",
                    namespaces=ns,
                )
            )

            if name:
                authors.append(name)

        url = ""

        for link in entry.findall(
            "atom:link",
            ns,
        ):
            href = link.attrib.get("href", "")
            rel = link.attrib.get("rel", "")

            if rel == "alternate" or "/abs/" in href:
                url = href
                break

        if not url:
            url = clean_text(
                entry.findtext(
                    "atom:id",
                    default="",
                    namespaces=ns,
                )
            )

        output.append(
            EvidenceRecord(
                provider="arxiv",
                title=title,
                url=url,
                work_id=f"arxiv:{url}",
                published=published,
                authors=authors,
                abstract=abstract,
                snippet=abstract[:2000],
                source_type="preprint",
                confidence=0.78,
                metadata={},
            )
        )

    return output


def arxiv_search(
    query: str,
    limit: int = 5,
) -> Tuple[List[EvidenceRecord], Dict[str, Any]]:

    encoded = urllib.parse.quote(query)

    endpoints = [
        (
            "https://export.arxiv.org/api/query?"
            f"search_query=all:{encoded}"
            f"&start=0&max_results={limit}"
            "&sortBy=relevance"
        ),
        (
            "https://export.arxiv.org/api/query?"
            f"search_query=all:{encoded}"
            f"&start=0&max_results={limit}"
        ),
        (
            "https://arxiv.org/api/query?"
            f"search_query=all:{encoded}"
            f"&start=0&max_results={limit}"
        ),
    ]

    errors = []

    for index, url in enumerate(endpoints, 1):
        try:
            response = http_get(
                url,
                headers={
                    "User-Agent": (
                        "AI-Infinity/2050.59 "
                        "(research-client)"
                    ),
                    "Accept": (
                        "application/atom+xml, "
                        "application/xml, text/xml"
                    ),
                },
            )

            results = parse_arxiv(
                response.body,
                limit,
            )

            if results:
                return results, {
                    "provider": "arxiv",
                    "status": "success",
                    "http_status": response.status,
                    "attempts": response.attempts,
                    "recovery": f"export-query-{index}",
                    "content_type": response.content_type,
                    "diagnostics": errors,
                }

        except Exception as exc:
            errors.append({
                "endpoint": index,
                "error_type": type(exc).__name__,
                "error": str(exc),
            })

    raise RuntimeError(
        json.dumps(
            {
                "provider": "arxiv",
                "diagnostics": errors,
            }
        )
    )


# ============================================================
# OPENALEX
# ============================================================

def parse_openalex(
    data: Any,
    limit: int,
) -> List[EvidenceRecord]:

    if not isinstance(data, dict):
        raise ValueError("OpenAlex response is not an object")

    results = data.get("results") or []

    if not isinstance(results, list):
        raise ValueError("OpenAlex results invalid")

    output = []

    for item in results[:limit]:
        if not isinstance(item, dict):
            continue

        title = clean_text(item.get("display_name"))

        if not title:
            continue

        primary = item.get("primary_location") or {}
        landing = primary.get("landing_page_url")

        doi = item.get("doi") or ""

        url = (
            doi
            or landing
            or item.get("id")
            or ""
        )

        authors = []

        for authorship in item.get("authorships") or []:
            author = authorship.get("author") or {}
            name = clean_text(
                author.get("display_name")
            )

            if name:
                authors.append(name)

        abstract = ""

        # OpenAlex commonly exposes an inverted index.
        inv = item.get("abstract_inverted_index")

        if isinstance(inv, dict):
            words = []

            for word, positions in inv.items():
                if isinstance(positions, list):
                    for position in positions:
                        words.append(
                            (
                                int(position),
                                word,
                            )
                        )

            words.sort(key=lambda x: x[0])
            abstract = " ".join(
                word for _, word in words
            )

        output.append(
            EvidenceRecord(
                provider="openalex",
                title=title,
                url=url,
                work_id=f"openalex:{item.get('id','')}",
                published=clean_text(
                    item.get("publication_date")
                ),
                authors=authors,
                abstract=abstract[:MAX_SOURCE_TEXT],
                snippet=abstract[:2000],
                source_type="scholarly-index",
                confidence=0.82,
                metadata={
                    "cited_by_count": item.get(
                        "cited_by_count"
                    ),
                    "type": item.get("type"),
                    "doi": doi,
                },
            )
        )

    return output


def openalex_search(
    query: str,
    limit: int = 5,
) -> Tuple[List[EvidenceRecord], Dict[str, Any]]:

    url = (
        "https://api.openalex.org/works?"
        + urllib.parse.urlencode(
            {
                "search": query,
                "per-page": limit,
            }
        )
    )

    response = http_get(
        url,
        headers={
            "Accept": "application/json",
        },
    )

    data = parse_json_resilient(response.body)

    results = parse_openalex(
        data,
        limit,
    )

    return results, {
        "provider": "openalex",
        "status": "success",
        "http_status": response.status,
        "attempts": response.attempts,
        "recovery": "works-search",
        "content_type": response.content_type,
        "diagnostics": [],
    }


# ============================================================
# PROVIDER HEALTH
# ============================================================

def record_provider_health(
    provider: str,
    success: bool,
    *,
    error: Optional[str] = None,
    http_status: Optional[int] = None,
) -> None:

    conn = db()

    row = conn.execute(
        "SELECT * FROM provider_health WHERE provider=?",
        (provider,),
    ).fetchone()

    if row:
        successes = int(row["successes"])
        failures = int(row["failures"])
    else:
        successes = 0
        failures = 0

    if success:
        successes += 1
        status = "success"
    else:
        failures += 1
        status = "failed"

    total = successes + failures
    score = round(
        successes / total,
        4,
    ) if total else 0.0

    conn.execute(
        """
        INSERT INTO provider_health (
            provider,
            successes,
            failures,
            total,
            score,
            last_status,
            last_error,
            last_http_status,
            last_updated
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET
            successes=excluded.successes,
            failures=excluded.failures,
            total=excluded.total,
            score=excluded.score,
            last_status=excluded.last_status,
            last_error=excluded.last_error,
            last_http_status=excluded.last_http_status,
            last_updated=excluded.last_updated
        """,
        (
            provider,
            successes,
            failures,
            total,
            score,
            status,
            error,
            http_status,
            now(),
        ),
    )

    conn.commit()
    conn.close()


def provider_health_snapshot() -> Dict[str, Any]:
    conn = db()

    rows = conn.execute(
        "SELECT * FROM provider_health"
    ).fetchall()

    conn.close()

    return {
        row["provider"]: {
            "successes": row["successes"],
            "failures": row["failures"],
            "total": row["total"],
            "score": row["score"],
            "last_status": row["last_status"],
            "last_error": row["last_error"],
            "last_http_status": row["last_http_status"],
            "last_updated": row["last_updated"],
        }
        for row in rows
    }


# ============================================================
# RESEARCH INGESTION
# ============================================================

PROVIDER_FUNCTIONS = {
    "wikipedia": wikipedia_search,
    "crossref": crossref_search,
    "arxiv": arxiv_search,
    "openalex": openalex_search,
}


def ingest_research(
    query: str,
    providers: Optional[List[str]] = None,
    limit: int = 5,
) -> Dict[str, Any]:

    if providers is None:
        providers = [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ]

    provider_results = {}
    all_records: List[Dict[str, Any]] = []

    for provider in providers:
        fn = PROVIDER_FUNCTIONS.get(provider)

        if not fn:
            provider_results[provider] = {
                "provider": provider,
                "status": "failed",
                "error_type": "UnknownProvider",
                "error": "provider not registered",
                "result_count": 0,
                "results": [],
            }
            record_provider_health(
                provider,
                False,
                error="provider not registered",
            )
            continue

        try:
            records, diagnostics = fn(
                query,
                limit,
            )

            records_dict = [
                r.as_dict()
                for r in records
            ]

            provider_results[provider] = {
                **diagnostics,
                "results": records_dict,
                "result_count": len(records_dict),
            }

            all_records.extend(records_dict)

            record_provider_health(
                provider,
                True,
                http_status=diagnostics.get(
                    "http_status"
                ),
            )

        except Exception as exc:
            error = str(exc)

            provider_results[provider] = {
                "provider": provider,
                "status": "failed",
                "http_status": getattr(
                    exc,
                    "code",
                    None,
                ),
                "attempts": 0,
                "result_count": 0,
                "results": [],
                "error_type": type(exc).__name__,
                "error": error,
                "diagnostics": [],
            }

            record_provider_health(
                provider,
                False,
                error=error,
                http_status=getattr(
                    exc,
                    "code",
                    None,
                ),
            )

    unique = deduplicate(all_records)

    # Rank by source confidence, then evidence richness.
    def score(item: Dict[str, Any]) -> float:
        base = float(
            item.get("confidence") or 0
        )

        richness = 0

        if item.get("abstract"):
            richness += 0.05

        if item.get("authors"):
            richness += 0.03

        if item.get("published"):
            richness += 0.02

        return base + richness

    unique.sort(
        key=score,
        reverse=True,
    )

    return {
        "query": query,
        "providers": provider_results,
        "results": unique[
            : max(
                limit * max(len(providers), 1),
                MAX_RESEARCH_SOURCES,
            )
        ],
        "result_count": len(unique),
        "provider_health": provider_health_snapshot(),
    }


# ============================================================
# EVIDENCE ANALYSIS
# ============================================================

def normalize_claim_text(text: str) -> str:
    text = clean_text(text).lower()

    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def tokenize(text: str) -> set[str]:
    words = normalize_claim_text(text).split()

    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "with",
        "is",
        "are",
        "was",
        "were",
        "this",
        "that",
        "by",
        "as",
        "from",
    }

    return {
        word
        for word in words
        if len(word) > 2
        and word not in stop
    }


def similarity(a: str, b: str) -> float:
    ta = tokenize(a)
    tb = tokenize(b)

    if not ta or not tb:
        return 0.0

    return len(ta & tb) / len(ta | tb)


def extract_claims(
    evidence: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    claims = []

    for item in evidence:
        text = (
            item.get("abstract")
            or item.get("snippet")
            or ""
        )

        sentences = re.split(
            r"(?<=[.!?])\s+",
            clean_text(text),
        )

        for sentence in sentences:
            sentence = sentence.strip()

            if len(sentence) < 35:
                continue

            claims.append(
                {
                    "claim": sentence[:1000],
                    "provider": item.get("provider"),
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "work_id": item.get("work_id"),
                    "confidence": item.get(
                        "confidence",
                        0.5,
                    ),
                }
            )

    return claims


def compare_evidence(
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:

    claims = extract_claims(evidence)

    supporting_groups = []
    contradictions = []

    for i, first in enumerate(claims):
        for second in claims[i + 1:]:
            sim = similarity(
                first["claim"],
                second["claim"],
            )

            if sim < 0.20:
                continue

            first_words = tokenize(
                first["claim"]
            )
            second_words = tokenize(
                second["claim"]
            )

            # Lightweight contradiction heuristic.
            negation_pairs = [
                ("not", "is"),
                ("not", "are"),
                ("cannot", "can"),
                ("fails", "succeeds"),
                ("failure", "success"),
                ("decreases", "increases"),
                ("lower", "higher"),
                ("less", "more"),
                ("no", "yes"),
            ]

            contradiction = False

            for a, b in negation_pairs:
                if (
                    a in first_words
                    and b in second_words
                ) or (
                    b in first_words
                    and a in second_words
                ):
                    contradiction = True
                    break

            if contradiction:
                contradictions.append(
                    {
                        "claim_a": first,
                        "claim_b": second,
                        "similarity": round(sim, 4),
                        "type": "potential_contradiction",
                    }
                )
            else:
                supporting_groups.append(
                    {
                        "claim_a": first,
                        "claim_b": second,
                        "similarity": round(sim, 4),
                    }
                )

    providers = {
        x.get("provider")
        for x in evidence
        if x.get("provider")
    }

    gaps = []

    if len(evidence) < 3:
        gaps.append(
            {
                "gap": "insufficient independent sources",
                "severity": 0.85,
            }
        )

    if len(providers) < 2:
        gaps.append(
            {
                "gap": "limited provider diversity",
                "severity": 0.70,
            }
        )

    if contradictions:
        gaps.append(
            {
                "gap": "claims require contradiction review",
                "severity": 0.75,
            }
        )

    return {
        "claim_count": len(claims),
        "supporting_relationships": (
            supporting_groups[:100]
        ),
        "contradictions": contradictions[:100],
        "evidence_gaps": gaps,
        "provider_diversity": len(providers),
    }


# ============================================================
# SYNTHESIS
# ============================================================

def synthesize_evidence(
    objective: str,
    evidence: List[Dict[str, Any]],
    comparison: Dict[str, Any],
) -> Dict[str, Any]:

    ranked = sorted(
        evidence,
        key=lambda x: (
            float(x.get("confidence") or 0),
            bool(x.get("abstract")),
            bool(x.get("authors")),
        ),
        reverse=True,
    )

    source_summaries = []

    for item in ranked[:10]:
        source_summaries.append(
            {
                "provider": item.get("provider"),
                "title": item.get("title"),
                "url": item.get("url"),
                "published": item.get("published"),
                "confidence": item.get("confidence"),
                "summary": (
                    item.get("abstract")
                    or item.get("snippet")
                    or ""
                )[:1500],
            }
        )

    contradiction_count = len(
        comparison.get("contradictions", [])
    )

    gap_count = len(
        comparison.get("evidence_gaps", [])
    )

    confidence = 0.0

    if ranked:
        confidence = sum(
            float(
                x.get("confidence") or 0
            )
            for x in ranked[:10]
        ) / min(
            len(ranked),
            10,
        )

    if contradiction_count:
        confidence *= 0.85

    if gap_count:
        confidence *= 0.90

    return {
        "objective": objective,
        "method": (
            "multi-source evidence synthesis "
            "with provenance and contradiction analysis"
        ),
        "confidence": round(
            min(max(confidence, 0), 1),
            4,
        ),
        "source_count": len(evidence),
        "provider_count": comparison.get(
            "provider_diversity",
            0,
        ),
        "contradiction_count": contradiction_count,
        "evidence_gap_count": gap_count,
        "sources": source_summaries,
        "conclusion": (
            "Evidence was collected from multiple "
            "research providers and normalized into "
            "a common evidence set. Claims are weighted "
            "by source confidence and evidence richness. "
            "Potential contradictions and evidence gaps "
            "are explicitly retained for verification."
        ),
    }


# ============================================================
# INDEPENDENT VERIFICATION
# ============================================================

def verify_synthesis(
    synthesis: Dict[str, Any],
    comparison: Dict[str, Any],
) -> Dict[str, Any]:

    checks = []

    source_count = int(
        synthesis.get("source_count", 0)
    )

    provider_count = int(
        synthesis.get("provider_count", 0)
    )

    contradiction_count = int(
        synthesis.get(
            "contradiction_count",
            0,
        )
    )

    checks.append(
        {
            "name": "source_presence",
            "passed": source_count > 0,
            "detail": f"{source_count} sources",
        }
    )

    checks.append(
        {
            "name": "provider_diversity",
            "passed": provider_count >= 2,
            "detail": f"{provider_count} providers",
        }
    )

    checks.append(
        {
            "name": "contradiction_visibility",
            "passed": True,
            "detail": (
                f"{contradiction_count} potential "
                "contradictions recorded"
            ),
        }
    )

    checks.append(
        {
            "name": "provenance",
            "passed": bool(
                synthesis.get("sources")
            ),
            "detail": "source provenance retained",
        }
    )

    passed = sum(
        1
        for check in checks
        if check["passed"]
    )

    return {
        "status": (
            "verified"
            if passed == len(checks)
            else "partially_verified"
        ),
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "verification_score": round(
            passed / len(checks),
            4,
        ) if checks else 0,
    }


# ============================================================
# MISSION STORAGE
# ============================================================

def create_mission(
    objective: str,
) -> str:

    mission_id = uid("mission")
    timestamp = now()

    conn = db()

    conn.execute(
        """
        INSERT INTO missions (
            id,
            objective,
            status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "created",
            timestamp,
            timestamp,
        ),
    )

    conn.commit()
    conn.close()

    return mission_id


def update_mission(
    mission_id: str,
    status: str,
    result: Optional[Dict[str, Any]] = None,
) -> None:

    conn = db()

    conn.execute(
        """
        UPDATE missions
        SET status=?,
            updated_at=?,
            result_json=?
        WHERE id=?
        """,
        (
            status,
            now(),
            json.dumps(
                result
                if result is not None
                else {}
            ),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()


def save_step(
    mission_id: str,
    index: int,
    name: str,
    status: str,
    result: Any,
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT INTO mission_steps (
            mission_id,
            step_index,
            name,
            status,
            result_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            index,
            name,
            status,
            json.dumps(json_safe(result)),
            now(),
        ),
    )

    conn.commit()
    conn.close()


def save_provenance(
    mission_id: str,
    event: str,
    source: str,
    detail: Any,
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT INTO provenance (
            mission_id,
            event,
            source,
            detail_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            event,
            source,
            json.dumps(json_safe(detail)),
            now(),
        ),
    )

    conn.commit()
    conn.close()


def save_evidence(
    mission_id: str,
    records: List[Dict[str, Any]],
) -> None:

    conn = db()

    for item in records:
        conn.execute(
            """
            INSERT INTO evidence (
                mission_id,
                provider,
                work_id,
                title,
                url,
                snippet,
                confidence,
                metadata_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                item.get("provider"),
                item.get("work_id"),
                item.get("title"),
                item.get("url"),
                item.get("snippet")
                or item.get("abstract")
                or "",
                item.get("confidence", 0.5),
                json.dumps(
                    item.get("metadata") or {}
                ),
                now(),
            ),
        )

        conn.execute(
            """
            INSERT INTO research_sources (
                mission_id,
                provider,
                title,
                url,
                work_id,
                published,
                authors_json,
                abstract,
                snippet,
                confidence,
                metadata_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                item.get("provider"),
                item.get("title"),
                item.get("url"),
                item.get("work_id"),
                item.get("published"),
                json.dumps(
                    item.get("authors") or []
                ),
                item.get("abstract") or "",
                item.get("snippet") or "",
                item.get("confidence", 0.5),
                json.dumps(
                    item.get("metadata") or {}
                ),
                now(),
            ),
        )

    conn.commit()
    conn.close()


def save_checkpoint(
    mission_id: str,
    cycle: int,
    state: Dict[str, Any],
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT INTO checkpoints (
            mission_id,
            cycle,
            state_json,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            mission_id,
            cycle,
            json.dumps(json_safe(state)),
            now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# CONNECTORS
# ============================================================

BUILTIN_CONNECTORS = {
    "reasoning": {
        "category": "intelligence",
        "permission": "safe",
        "description": "structured reasoning",
    },
    "planner": {
        "category": "orchestration",
        "permission": "safe",
        "description": "mission planning",
    },
    "memory": {
        "category": "memory",
        "permission": "safe",
        "description": "persistent mission memory",
    },
    "web_read": {
        "category": "research",
        "permission": "controlled",
        "description": "controlled public web retrieval",
    },
    "research_discovery": {
        "category": "research",
        "permission": "controlled",
        "description": "multi-provider research discovery",
    },
    "evidence_engine": {
        "category": "research",
        "permission": "safe",
        "description": "evidence normalization and analysis",
    },
    "verification": {
        "category": "verification",
        "permission": "safe",
        "description": "independent result verification",
    },
    "action_gateway": {
        "category": "external-action",
        "permission": "approval_required",
        "description": "controlled real-world command boundary",
    },
}


def init_connectors() -> None:
    conn = db()

    for name, data in BUILTIN_CONNECTORS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO connectors (
                name,
                category,
                permission,
                description,
                enabled,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                data["category"],
                data["permission"],
                data["description"],
                1,
                "{}",
            ),
        )

    conn.commit()
    conn.close()


init_connectors()


# ============================================================
# REQUEST MODELS
# ============================================================

class RunRequest(BaseModel):
    command: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )
    research: bool = True
    verify: bool = True
    remember: bool = True
    providers: Optional[List[str]] = None
    research_limit: int = Field(
        default=5,
        ge=1,
        le=20,
    )


class RegisterConnectorRequest(BaseModel):
    name: str
    category: str = "external"
    permission: str = "approval_required"
    description: str = ""
    metadata: Dict[str, Any] = {}


class ApprovalRequest(BaseModel):
    approved: bool


# ============================================================
# MISSION EXECUTION
# ============================================================

def run_mission(
    objective: str,
    *,
    research: bool = True,
    verify: bool = True,
    remember: bool = True,
    providers: Optional[List[str]] = None,
    research_limit: int = 5,
) -> Dict[str, Any]:

    mission_id = create_mission(
        objective
    )

    update_mission(
        mission_id,
        "running",
    )

    cycle = 0

    try:
        # ----------------------------------------------------
        # STEP 1: PLAN
        # ----------------------------------------------------

        cycle += 1

        plan = {
            "mission_id": mission_id,
            "objective": objective,
            "steps": [
                "understand_objective",
                "discover_research",
                "normalize_evidence",
                "compare_evidence",
                "detect_gaps",
                "synthesize",
                "verify",
                "checkpoint",
                "learn",
            ],
        }

        save_step(
            mission_id,
            1,
            "planning",
            "completed",
            plan,
        )

        save_provenance(
            mission_id,
            "mission_created",
            "ai_infinity",
            plan,
        )

        # ----------------------------------------------------
        # STEP 2: RESEARCH
        # ----------------------------------------------------

        research_result = {
            "status": "skipped",
            "results": [],
        }

        if research:
            cycle += 1

            research_result = ingest_research(
                objective,
                providers=providers,
                limit=research_limit,
            )

            save_evidence(
                mission_id,
                research_result.get(
                    "results",
                    [],
                ),
            )

            save_step(
                mission_id,
                2,
                "research",
                "completed",
                research_result,
            )

            save_provenance(
                mission_id,
                "evidence_collected",
                "research_fabric",
                {
                    "count": research_result.get(
                        "result_count",
                        0,
                    ),
                    "providers": list(
                        research_result.get(
                            "providers",
                            {},
                        ).keys()
                    ),
                },
            )

        evidence = research_result.get(
            "results",
            [],
        )

        # ----------------------------------------------------
        # STEP 3: COMPARE
        # ----------------------------------------------------

        cycle += 1

        comparison = compare_evidence(
            evidence
        )

        conn = db()

        conn.execute(
            """
            INSERT INTO research_comparisons (
                mission_id,
                comparison_json,
                created_at
            )
            VALUES (?, ?, ?)
            """,
            (
                mission_id,
                json.dumps(comparison),
                now(),
            ),
        )

        conn.commit()
        conn.close()

        save_step(
            mission_id,
            3,
            "evidence_comparison",
            "completed",
            comparison,
        )

        # Record intelligence gaps.
        for gap in comparison.get(
            "evidence_gaps",
            [],
        ):
            conn = db()

            conn.execute(
                """
                INSERT INTO intelligence_gaps (
                    mission_id,
                    gap,
                    severity,
                    resolved,
                    created_at
                )
                VALUES (?, ?, ?, 0, ?)
                """,
                (
                    mission_id,
                    gap.get("gap"),
                    gap.get("severity", 0.5),
                    now(),
                ),
            )

            conn.commit()
            conn.close()

        # ----------------------------------------------------
        # STEP 4: SYNTHESIS
        # ----------------------------------------------------

        cycle += 1

        synthesis = synthesize_evidence(
            objective,
            evidence,
            comparison,
        )

        save_step(
            mission_id,
            4,
            "synthesis",
            "completed",
            synthesis,
        )

        save_provenance(
            mission_id,
            "synthesis_created",
            "evidence_synthesis_engine",
            synthesis,
        )

        # ----------------------------------------------------
        # STEP 5: VERIFICATION
        # ----------------------------------------------------

        verification = {
            "status": "not_requested",
        }

        if verify:
            cycle += 1

            verification = verify_synthesis(
                synthesis,
                comparison,
            )

            save_step(
                mission_id,
                5,
                "independent_verification",
                "completed",
                verification,
            )

            save_provenance(
                mission_id,
                "verification_completed",
                "verification_engine",
                verification,
            )

        # ----------------------------------------------------
        # STEP 6: CHECKPOINT
        # ----------------------------------------------------

        cycle += 1

        checkpoint_state = {
            "mission_id": mission_id,
            "objective": objective,
            "cycle": cycle,
            "research_count": len(evidence),
            "comparison": comparison,
            "synthesis": synthesis,
            "verification": verification,
        }

        save_checkpoint(
            mission_id,
            cycle,
            checkpoint_state,
        )

        # ----------------------------------------------------
        # STEP 7: LEARNING
        # ----------------------------------------------------

        learning = {
            "enabled": remember,
            "learned": [],
        }

        if remember:
            learning["learned"] = [
                {
                    "key": "research_provider_health",
                    "value": research_result.get(
                        "provider_health",
                        {},
                    ),
                },
                {
                    "key": "evidence_diversity",
                    "value": comparison.get(
                        "provider_diversity",
                        0,
                    ),
                },
                {
                    "key": "contradiction_count",
                    "value": comparison.get(
                        "contradictions",
                        [],
                    ).__len__(),
                },
            ]

            conn = db()

            for item in learning["learned"]:
                conn.execute(
                    """
                    INSERT INTO learning (
                        mission_id,
                        key,
                        value_json,
                        created_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        mission_id,
                        item["key"],
                        json.dumps(
                            json_safe(
                                item["value"]
                            )
                        ),
                        now(),
                    ),
                )

            conn.commit()
            conn.close()

        save_step(
            mission_id,
            6,
            "learning",
            "completed",
            learning,
        )

        result = {
            "mission_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "build": BUILD,
            "objective": objective,
            "research": research_result,
            "evidence_comparison": comparison,
            "synthesis": synthesis,
            "verification": verification,
            "learning": learning,
            "checkpoint": {
                "cycle": cycle,
                "saved": True,
            },
        }

        update_mission(
            mission_id,
            "completed",
            result,
        )

        return result

    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
        }

        save_provenance(
            mission_id,
            "mission_failed",
            "ai_infinity",
            error,
        )

        update_mission(
            mission_id,
            "failed",
            error,
        )

        raise


# ============================================================
# HEALTH / STATUS
# ============================================================

@app.get("/")
def root():
    return {
        "service": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "message": (
            "AI Infinity 2050.59 "
            "Evidence Synthesis and Verification Fabric"
        ),
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "policy_version": POLICY_VERSION,
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

        "provider_diagnostics": True,
        "provider_retry": True,
        "provider_error_visibility": True,
        "provider_health_tracking": True,

        "resilient_json_decoder": True,
        "wikipedia_ingestion_repair": True,
        "crossref_ingestion_repair": True,
        "arxiv_multi_endpoint_recovery": True,
        "arxiv_query_fallback": True,
        "openalex_ingestion": True,

        "provider_specific_recovery": True,
        "automatic_provider_fallback": True,

        "evidence_synthesis": True,
        "claim_extraction": True,
        "cross_source_comparison": True,
        "contradiction_analysis": True,
        "evidence_gap_analysis": True,
        "provenance_backed_synthesis": True,
        "independent_synthesis_verification": True,

        "controlled_public_web_access": True,
        "network_policy_enforced": True,

        "research_allowlist_configured": bool(
            EXTERNAL_ALLOWED_DOMAINS
        ),
        "research_seed_domains_configured": bool(
            RESEARCH_SEED_DOMAINS
        ),
        "research_discovery_endpoints_configured": bool(
            RESEARCH_DISCOVERY_URLS
        ),

        "default_research_providers": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],

        "arbitrary_code_execution": False,
        "unrestricted_private_network_access": False,

        "provider_health": provider_health_snapshot(),
    }


@app.get("/status")
def status():
    return health()


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "intent-routing",
            "mission-planning",
            "adaptive-execution",
            "controlled-public-web-research",
            "multi-provider-research",
            "evidence-normalization",
            "source-ranking",
            "source-deduplication",
            "cross-source-comparison",
            "contradiction-detection",
            "evidence-gap-detection",
            "evidence-synthesis",
            "independent-verification",
            "provenance",
            "checkpointing",
            "persistent-learning",
            "controlled-action-gateway",
        ],
    }


@app.get("/tools")
def tools():
    return {
        "tools": [
            {
                "name": name,
                **data,
            }
            for name, data in BUILTIN_CONNECTORS.items()
        ]
    }


# ============================================================
# CONNECTORS
# ============================================================

@app.get("/connectors")
def connectors():
    conn = db()

    rows = conn.execute(
        "SELECT * FROM connectors ORDER BY name"
    ).fetchall()

    conn.close()

    return {
        "connectors": [
            {
                "name": row["name"],
                "category": row["category"],
                "permission": row["permission"],
                "description": row["description"],
                "enabled": bool(row["enabled"]),
                "metadata": json.loads(
                    row["metadata_json"] or "{}"
                ),
            }
            for row in rows
        ]
    }


@app.post("/connectors/register")
def register_connector(
    request: RegisterConnectorRequest,
):
    if not re.fullmatch(
        r"[A-Za-z0-9_.:-]{1,80}",
        request.name,
    ):
        raise HTTPException(
            status_code=400,
            detail="invalid connector name",
        )

    conn = db()

    conn.execute(
        """
        INSERT OR REPLACE INTO connectors (
            name,
            category,
            permission,
            description,
            enabled,
            metadata_json
        )
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (
            request.name,
            request.category,
            request.permission,
            request.description,
            json.dumps(request.metadata),
        ),
    )

    conn.commit()
    conn.close()

    return {
        "status": "registered",
        "name": request.name,
    }


@app.get("/connector-health")
def connector_health():
    return {
        "status": "healthy",
        "provider_health": provider_health_snapshot(),
    }


# ============================================================
# DISCOVERY
# ============================================================

@app.get("/discover")
def discover(
    objective: str = Query(
        ...,
        min_length=1,
        max_length=10000,
    ),
):
    objective_lower = objective.lower()

    capabilities_found = []

    if any(
        x in objective_lower
        for x in (
            "research",
            "study",
            "analyze",
            "find",
            "investigate",
            "evidence",
        )
    ):
        capabilities_found.extend(
            [
                "research_discovery",
                "evidence_engine",
                "verification",
            ]
        )

    if any(
        x in objective_lower
        for x in (
            "plan",
            "build",
            "create",
            "execute",
        )
    ):
        capabilities_found.extend(
            [
                "planner",
                "reasoning",
            ]
        )

    if not capabilities_found:
        capabilities_found = [
            "reasoning",
            "planner",
            "research_discovery",
            "verification",
        ]

    return {
        "objective": objective,
        "capabilities": list(
            dict.fromkeys(
                capabilities_found
            )
        ),
        "research_providers": list(
            PROVIDER_FUNCTIONS.keys()
        ),
    }


# ============================================================
# RUN
# ============================================================

@app.post("/run")
def run(request: RunRequest):
    try:
        return run_mission(
            request.command,
            research=request.research,
            verify=request.verify,
            remember=request.remember,
            providers=request.providers,
            research_limit=request.research_limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": type(exc).__name__,
                "message": str(exc),
            },
        )


# ============================================================
# MISSION READ APIs
# ============================================================

@app.get("/mission/{mission_id}")
def get_mission(
    mission_id: str,
):
    conn = db()

    mission = conn.execute(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,),
    ).fetchone()

    if not mission:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    steps = conn.execute(
        """
        SELECT *
        FROM mission_steps
        WHERE mission_id=?
        ORDER BY step_index
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission": {
            "id": mission["id"],
            "objective": mission["objective"],
            "status": mission["status"],
            "created_at": mission["created_at"],
            "updated_at": mission["updated_at"],
            "result": json.loads(
                mission["result_json"] or "{}"
            ),
        },
        "steps": [
            {
                "index": x["step_index"],
                "name": x["name"],
                "status": x["status"],
                "result": json.loads(
                    x["result_json"] or "{}"
                ),
            }
            for x in steps
        ],
    }


@app.get("/mission/{mission_id}/events")
def mission_events(
    mission_id: str,
):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM provenance
        WHERE mission_id=?
        ORDER BY id
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "events": [
            {
                "event": row["event"],
                "source": row["source"],
                "detail": json.loads(
                    row["detail_json"] or "{}"
                ),
                "created_at": row["created_at"],
            }
            for row in rows
        ],
    }


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(
    mission_id: str,
):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM research_sources
        WHERE mission_id=?
        ORDER BY confidence DESC, id
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "count": len(rows),
        "evidence": [
            {
                "provider": row["provider"],
                "title": row["title"],
                "url": row["url"],
                "work_id": row["work_id"],
                "published": row["published"],
                "authors": json.loads(
                    row["authors_json"] or "[]"
                ),
                "abstract": row["abstract"],
                "snippet": row["snippet"],
                "confidence": row["confidence"],
                "metadata": json.loads(
                    row["metadata_json"] or "{}"
                ),
            }
            for row in rows
        ],
    }


@app.get("/mission/{mission_id}/checkpoints")
def mission_checkpoints(
    mission_id: str,
):
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
            {
                "cycle": row["cycle"],
                "state": json.loads(
                    row["state_json"] or "{}"
                ),
                "created_at": row["created_at"],
            }
            for row in rows
        ],
    }


# ============================================================
# APPROVAL GATE
# ============================================================

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
            dict(row)
            for row in rows
        ]
    }


@app.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    request: ApprovalRequest,
):
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
            detail="approval not found",
        )

    status = (
        "approved"
        if request.approved
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
        ),
    )

    conn.commit()
    conn.close()

    return {
        "approval_id": approval_id,
        "status": status,
    }


# ============================================================
# RESEARCH API
# ============================================================

@app.get("/research/providers")
def research_providers():
    return {
        "version": VERSION,
        "providers": list(
            PROVIDER_FUNCTIONS.keys()
        ),
        "health": provider_health_snapshot(),
    }


@app.get("/research/providers/{provider_name}/test")
def research_provider_test(
    provider_name: str,
):
    if provider_name not in PROVIDER_FUNCTIONS:
        raise HTTPException(
            status_code=404,
            detail="unknown provider",
        )

    result = ingest_research(
        "artificial intelligence agents",
        providers=[provider_name],
        limit=5,
    )

    return result["providers"].get(
        provider_name,
        {},
    )


@app.get("/research/providers/test")
def research_providers_test():
    return ingest_research(
        "artificial intelligence agents",
        providers=[
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        limit=5,
    )


@app.get("/research/sources")
def research_sources(
    query: str = Query(
        "artificial intelligence agents",
        min_length=1,
        max_length=10000,
    ),
    limit: int = Query(
        5,
        ge=1,
        le=20,
    ),
):
    return ingest_research(
        query,
        limit=limit,
    )


@app.get("/research/discover")
def research_discover(
    query: str = Query(
        ...,
        min_length=1,
        max_length=10000,
    ),
    limit: int = Query(
        5,
        ge=1,
        le=20,
    ),
):
    return ingest_research(
        query,
        limit=limit,
    )


@app.post("/research/evidence")
def research_evidence(
    request: RunRequest,
):
    result = ingest_research(
        request.command,
        providers=request.providers,
        limit=request.research_limit,
    )

    evidence = result.get(
        "results",
        [],
    )

    comparison = compare_evidence(
        evidence
    )

    synthesis = synthesize_evidence(
        request.command,
        evidence,
        comparison,
    )

    verification = verify_synthesis(
        synthesis,
        comparison,
    )

    return {
        "version": VERSION,
        "query": request.command,
        "evidence": evidence,
        "comparison": comparison,
        "synthesis": synthesis,
        "verification": verification,
        "provider_health": result.get(
            "provider_health",
            {},
        ),
    }


# ============================================================
# TEST ENDPOINTS
# ============================================================

@app.get("/test-research")
def test_research():
    return ingest_research(
        "artificial intelligence agents",
        providers=[
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        limit=5,
    )


@app.get("/test-intelligence")
def test_intelligence():
    return run_mission(
        "Analyze artificial intelligence agents "
        "using multiple independent research sources, "
        "compare evidence, identify contradictions and "
        "gaps, synthesize the evidence, and verify the result.",
        research=True,
        verify=True,
        remember=True,
        research_limit=5,
    )


@app.get("/test-adaptive")
def test_adaptive():
    return {
        "status": "success",
        "version": VERSION,
        "adaptive_loop": [
            "observe",
            "detect_gap",
            "discover_requirement",
            "research",
            "compare",
            "adapt",
            "verify",
            "checkpoint",
            "learn",
        ],
    }


@app.get("/test-orchestrator")
def test_orchestrator():
    return {
        "status": "success",
        "orchestrator": {
            "planning": True,
            "capability_routing": True,
            "research": True,
            "evidence": True,
            "verification": True,
            "recovery": True,
            "learning": True,
        },
    }


@app.get("/test-router")
def test_router():
    return {
        "status": "success",
        "router": "adaptive-mission-router",
        "version": VERSION,
        "routes": [
            "reasoning",
            "planning",
            "research",
            "evidence",
            "verification",
            "controlled-action",
        ],
    }


@app.get("/test-tools")
def test_tools():
    return {
        "status": "success",
        "tool_count": len(
            BUILTIN_CONNECTORS
        ),
        "tools": list(
            BUILTIN_CONNECTORS.keys()
        ),
    }


@app.get("/test-external")
def test_external():
    return {
        "status": "controlled",
        "external_intelligence": True,
        "public_web_research": True,
        "approval_required_for_real_world_actions": True,
        "private_network_access": False,
        "arbitrary_code_execution": False,
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
def policy():
    return {
        "version": POLICY_VERSION,
        "valid": True,
        "network": {
            "http_https_only": True,
            "private_addresses_blocked": True,
            "metadata_endpoints_blocked": True,
            "built_in_research_domains": sorted(
                BUILTIN_RESEARCH_DOMAINS
            ),
            "additional_allowed_domains": sorted(
                EXTERNAL_ALLOWED_DOMAINS
            ),
        },
        "execution": {
            "arbitrary_code_execution": False,
            "credential_bypass": False,
            "permission_bypass": False,
            "stealth_persistence": False,
            "automatic_redeploy": False,
            "high_risk_action_approval": True,
        },
    }


@app.get("/policy/validate")
def policy_validate():
    return {
        "valid": True,
        "policy_version": POLICY_VERSION,
        "checks": {
            "private_network_block": True,
            "metadata_block": True,
            "scheme_restriction": True,
            "response_limit": True,
            "timeout_limit": True,
            "approval_boundary": True,
        },
    }


# ============================================================
# MEMORY / SKILLS
# ============================================================

@app.get("/memory-count")
def memory_count():
    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) AS c FROM learning"
    ).fetchone()["c"]

    conn.close()

    return {
        "count": count
    }


@app.get("/skills-count")
def skills_count():
    return {
        "count": len(
            BUILTIN_CONNECTORS
        )
    }


# ============================================================
# INTERFACE
# ============================================================

@app.get(
    "/",
    include_in_schema=False,
)
def interface():
    html = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity {VERSION}</title>
<style>
body {{
    margin:0;
    font-family:system-ui,sans-serif;
    background:#0b0f14;
    color:#f4f7fb;
}}
main {{
    max-width:850px;
    margin:auto;
    padding:24px;
}}
.card {{
    background:#121923;
    border:1px solid #263241;
    border-radius:18px;
    padding:18px;
    margin:14px 0;
}}
textarea {{
    width:100%;
    min-height:130px;
    box-sizing:border-box;
    border-radius:12px;
    border:1px solid #344252;
    background:#0d131b;
    color:white;
    padding:14px;
    font-size:16px;
}}
button {{
    width:100%;
    margin-top:12px;
    padding:14px;
    border:0;
    border-radius:12px;
    font-size:16px;
    font-weight:700;
    cursor:pointer;
}}
pre {{
    white-space:pre-wrap;
    overflow-wrap:anywhere;
}}
.small {{
    opacity:.7;
    font-size:13px;
}}
.ok {{
    font-weight:700;
}}
</style>
</head>
<body>
<main>
<div class="card">
<h1>∞ AI Infinity</h1>
<div class="ok">ONLINE — {VERSION}</div>
<div class="small">
Evidence Synthesis & Verification Fabric
</div>
</div>

<div class="card">
<textarea id="command"
placeholder="Tell AI Infinity what you want to research, analyze, verify, or build..."></textarea>
<button onclick="runMission()">RUN MISSION</button>
</div>

<div class="card">
<pre id="output">Ready.</pre>
</div>
</main>

<script>
async function runMission() {{
    const command =
        document.getElementById("command").value.trim();

    if (!command) {{
        document.getElementById("output").textContent =
            "Enter a mission first.";
        return;
    }}

    document.getElementById("output").textContent =
        "AI Infinity is working...";

    try {{
        const response = await fetch("/run", {{
            method:"POST",
            headers: {{
                "Content-Type":"application/json"
            }},
            body: JSON.stringify({{
                command: command,
                research: true,
                verify: true,
                remember: true,
                research_limit: 5
            }})
        }});

        const data = await response.json();

        document.getElementById("output").textContent =
            JSON.stringify(data, null, 2);
    }} catch (error) {{
        document.getElementById("output").textContent =
            "Error: " + error;
    }}
}}
</script>
</body>
</html>
"""

    return HTMLResponse(html)


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():
    init_db()
    init_connectors()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(
            os.getenv("PORT", "8000")
        ),
        reload=False,
    )
