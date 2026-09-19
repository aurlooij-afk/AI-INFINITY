"""
AI Infinity
TARGET-2050.23
BUILD: ASYNC-AUTONOMOUS-EVIDENCE-CORE

Major upgrade from TARGET-2050.22:

- Asynchronous mission execution
- Immediate mission creation
- Background research worker
- Mission status polling
- Defensive JSON handling in dashboard
- Evidence integrity validation
- PDF extraction
- HTML challenge detection
- SSRF protection
- Source deduplication
- DOI/title canonicalization
- Provider diversity tracking
- Independent-domain verification
- Claim extraction
- Support / contradiction analysis
- Counter-evidence research
- Evidence graph
- SQLite persistence
- Automatic error recovery
- Mobile-friendly dashboard
- Existing API compatibility
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import sqlite3
import threading
import time
import traceback
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


# ============================================================
# CONFIGURATION
# ============================================================

VERSION = "TARGET-2050.23"
BUILD = "ASYNC-AUTONOMOUS-EVIDENCE-CORE"

APP_NAME = "AI Infinity"

BASE_DIR = Path("/tmp/ai-infinity")
BASE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "ai_infinity.db"

REQUEST_TIMEOUT = 10
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 100_000

MAX_RESEARCH_QUERIES = 4
MAX_RESULTS_PER_PROVIDER = 5
MAX_SOURCE_FETCHES = 12
MAX_CLAIMS = 12

WORKERS = 3

USER_AGENT = (
    "AI-Infinity/2050.23 "
    "(evidence-research; +https://ai-infinity-ca5e.onrender.com)"
)

POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")
RENDERER_URL = os.getenv("RENDERER_URL", "")

DB_LOCK = threading.RLock()

EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=WORKERS,
    thread_name_prefix="ai-infinity-worker",
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="AI Infinity autonomous research and evidence engine.",
)


# ============================================================
# MODELS
# ============================================================

class MissionRequest(BaseModel):
    objective: Optional[str] = None
    command: Optional[str] = None

    research: bool = True
    verify: bool = True
    remember: bool = True

    max_queries: int = Field(
        default=MAX_RESEARCH_QUERIES,
        ge=1,
        le=8,
    )


class ResearchRequest(BaseModel):
    objective: Optional[str] = None
    command: Optional[str] = None

    verify: bool = True
    remember: bool = True


class CommandRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=20_000)

    research: bool = True
    verify: bool = True
    remember: bool = True


# ============================================================
# DATABASE
# ============================================================

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def db_execute(
    sql: str,
    params: tuple = (),
    fetch: bool = False,
    many: Optional[List[tuple]] = None,
):
    with DB_LOCK:
        conn = db_connect()

        try:
            cur = conn.cursor()

            if many is not None:
                cur.executemany(sql, many)
            else:
                cur.execute(sql, params)

            rows = cur.fetchall() if fetch else None

            conn.commit()

            return rows

        finally:
            conn.close()


def ensure_column(
    table: str,
    column: str,
    definition: str,
):
    with DB_LOCK:
        conn = db_connect()

        try:
            rows = conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()

            existing = {
                row["name"]
                for row in rows
            }

            if column not in existing:
                conn.execute(
                    f"ALTER TABLE {table} "
                    f"ADD COLUMN {column} {definition}"
                )

            conn.commit()

        finally:
            conn.close()


def init_db():
    with DB_LOCK:
        conn = db_connect()

        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version TEXT,
                    build TEXT,
                    created_at REAL,
                    started_at REAL,
                    completed_at REAL,
                    result_json TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT,
                    source_key TEXT,
                    title TEXT,
                    url TEXT,
                    domain TEXT,
                    provider TEXT,
                    source_type TEXT,
                    status TEXT,
                    quality REAL DEFAULT 0,
                    independent INTEGER DEFAULT 0,
                    content_chars INTEGER DEFAULT 0,
                    evidence_text TEXT,
                    metadata_json TEXT,
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT,
                    claim TEXT,
                    status TEXT,
                    confidence REAL DEFAULT 0,
                    supporting_sources INTEGER DEFAULT 0,
                    contradicting_sources INTEGER DEFAULT 0,
                    source_keys_json TEXT,
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT,
                    event_type TEXT,
                    message TEXT,
                    data_json TEXT,
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT,
                    objective TEXT,
                    summary TEXT,
                    created_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_sources_mission
                ON sources(mission_id);

                CREATE INDEX IF NOT EXISTS idx_claims_mission
                ON claims(mission_id);

                CREATE INDEX IF NOT EXISTS idx_events_mission
                ON events(mission_id);
                """
            )

            conn.commit()

        finally:
            conn.close()

    # Compatibility with older versions.
    ensure_column("missions", "started_at", "REAL")
    ensure_column("missions", "completed_at", "REAL")
    ensure_column("missions", "error", "TEXT")

    ensure_column("sources", "source_type", "TEXT")
    ensure_column("sources", "quality", "REAL DEFAULT 0")
    ensure_column("sources", "independent", "INTEGER DEFAULT 0")
    ensure_column("sources", "content_chars", "INTEGER DEFAULT 0")
    ensure_column("sources", "evidence_text", "TEXT")
    ensure_column("sources", "metadata_json", "TEXT")

    ensure_column("claims", "confidence", "REAL DEFAULT 0")
    ensure_column("claims", "supporting_sources", "INTEGER DEFAULT 0")
    ensure_column("claims", "contradicting_sources", "INTEGER DEFAULT 0")
    ensure_column("claims", "source_keys_json", "TEXT")


init_db()


# ============================================================
# HELPERS
# ============================================================

def now() -> float:
    return time.time()


def safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def clean_text(text: Any, limit: int = MAX_TEXT_CHARS) -> str:
    if text is None:
        return ""

    text = str(text)

    text = text.replace("\x00", " ")

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()[:limit]


def normalize_title(title: str) -> str:
    title = clean_text(title).lower()

    title = re.sub(
        r"[^a-z0-9\s]",
        " ",
        title,
    )

    title = re.sub(
        r"\s+",
        " ",
        title,
    )

    return title.strip()


def normalize_url(url: str) -> str:
    try:
        parsed = urlparse(url)

        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
        )

        tracking = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
        }

        query = {
            key: values
            for key, values in query.items()
            if key.lower() not in tracking
        }

        new_query = urlencode(
            query,
            doseq=True,
        )

        cleaned = parsed._replace(
            fragment="",
            query=new_query,
        )

        result = urlunparse(cleaned)

        if result.endswith("/"):
            result = result[:-1]

        return result

    except Exception:
        return url


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""


def hash_key(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8", errors="ignore")
    ).hexdigest()[:24]


# ============================================================
# SSRF / URL FIREWALL
# ============================================================

def is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)

        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    except ValueError:
        return False


def validate_public_url(url: str) -> bool:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {
            "http",
            "https",
        }:
            return False

        hostname = parsed.hostname

        if not hostname:
            return False

        hostname = hostname.lower()

        blocked_names = {
            "localhost",
            "localhost.localdomain",
            "metadata.google.internal",
            "metadata",
        }

        if hostname in blocked_names:
            return False

        if hostname.endswith(".local"):
            return False

        try:
            resolved = socket.gethostbyname_ex(
                hostname
            )[2]
        except Exception:
            resolved = []

        for address in resolved:
            if is_private_ip(address):
                return False

        if is_private_ip(hostname):
            return False

        return True

    except Exception:
        return False


# ============================================================
# CONTENT INTEGRITY
# ============================================================

def looks_like_pdf(
    content: bytes,
    content_type: str = "",
) -> bool:
    return (
        content.startswith(b"%PDF-")
        or "application/pdf" in content_type.lower()
    )


def looks_binary(content: bytes) -> bool:
    if not content:
        return False

    sample = content[:4096]

    if b"\x00" in sample:
        return True

    try:
        decoded = sample.decode(
            "utf-8",
            errors="strict",
        )
    except Exception:
        return True

    bad = sum(
        1
        for char in decoded
        if ord(char) < 9
        or (
            13 < ord(char) < 32
        )
    )

    return bad > max(10, len(decoded) * 0.05)


def detect_page_problem(text: str) -> Optional[str]:
    lower = clean_text(text).lower()

    if not lower:
        return "empty_content"

    challenge_patterns = [
        "client challenge",
        "javascript is disabled",
        "enable javascript",
        "checking your browser",
        "verify you are human",
        "just a moment",
        "cf-chl",
        "cloudflare ray id",
        "access denied",
        "security verification",
        "captcha",
        "robot check",
    ]

    for pattern in challenge_patterns:
        if pattern in lower:
            return "challenge_page"

    login_patterns = [
        "sign in to continue",
        "log in to continue",
        "please log in",
        "please sign in",
    ]

    for pattern in login_patterns:
        if pattern in lower:
            return "login_page"

    error_patterns = [
        "internal server error",
        "bad gateway",
        "service unavailable",
        "page not found",
        "404 not found",
        "500 internal server error",
    ]

    for pattern in error_patterns:
        if pattern in lower:
            return "error_page"

    return None


def validate_evidence_text(
    text: str,
) -> Dict[str, Any]:
    text = clean_text(text)

    if len(text) < 200:
        return {
            "valid": False,
            "reason": "too_little_text",
            "text": text,
        }

    if text.startswith("%PDF-"):
        return {
            "valid": False,
            "reason": "raw_pdf_binary",
            "text": "",
        }

    problem = detect_page_problem(text)

    if problem:
        return {
            "valid": False,
            "reason": problem,
            "text": "",
        }

    letters = sum(
        1
        for char in text
        if char.isalpha()
    )

    if letters < 100:
        return {
            "valid": False,
            "reason": "insufficient_readable_text",
            "text": "",
        }

    return {
        "valid": True,
        "reason": None,
        "text": text[:MAX_TEXT_CHARS],
    }


# ============================================================
# PDF EXTRACTION
# ============================================================

def extract_pdf_text(
    content: bytes,
) -> str:
    if PdfReader is None:
        return ""

    try:
        import io

        reader = PdfReader(
            io.BytesIO(content)
        )

        pages = []

        for page in reader.pages[:30]:
            try:
                page_text = page.extract_text() or ""
                pages.append(page_text)
            except Exception:
                continue

        return clean_text(
            "\n".join(pages),
            MAX_TEXT_CHARS,
        )

    except Exception:
        return ""


# ============================================================
# HTTP FETCH
# ============================================================

def fetch_url(
    url: str,
) -> Dict[str, Any]:

    if not validate_public_url(url):
        return {
            "ok": False,
            "status": "rejected",
            "reason": "ssrf_or_invalid_url",
            "url": url,
        }

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/pdf,text/plain;q=0.9,*/*;q=0.5"
        ),
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )

        final_url = response.url

        if not validate_public_url(final_url):
            response.close()

            return {
                "ok": False,
                "status": "rejected",
                "reason": "unsafe_redirect",
                "url": url,
            }

        chunks = []
        total = 0

        for chunk in response.iter_content(
            chunk_size=32_768
        ):
            if not chunk:
                continue

            total += len(chunk)

            if total > MAX_DOWNLOAD_BYTES:
                break

            chunks.append(chunk)

        response.close()

        content = b"".join(chunks)

        content_type = response.headers.get(
            "content-type",
            "",
        )

        if looks_like_pdf(
            content,
            content_type,
        ):
            text = extract_pdf_text(content)

            validation = validate_evidence_text(
                text
            )

            if not validation["valid"]:
                return {
                    "ok": False,
                    "status": "invalid",
                    "reason": validation["reason"],
                    "url": final_url,
                    "content_type": content_type,
                }

            return {
                "ok": True,
                "status": "accepted",
                "url": final_url,
                "content_type": content_type,
                "text": validation["text"],
                "source_type": "pdf",
                "http_status": response.status_code,
            }

        if looks_binary(content):
            return {
                "ok": False,
                "status": "invalid",
                "reason": "binary_content",
                "url": final_url,
                "content_type": content_type,
            }

        text = content.decode(
            "utf-8",
            errors="replace",
        )

        # Remove scripts/styles before evidence analysis.
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
            r"<[^>]+>",
            " ",
            text,
        )

        text = clean_text(text)

        validation = validate_evidence_text(
            text
        )

        if not validation["valid"]:
            return {
                "ok": False,
                "status": "invalid",
                "reason": validation["reason"],
                "url": final_url,
                "content_type": content_type,
                "http_status": response.status_code,
            }

        return {
            "ok": True,
            "status": "accepted",
            "url": final_url,
            "content_type": content_type,
            "text": validation["text"],
            "source_type": "web",
            "http_status": response.status_code,
        }

    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "error",
            "reason": str(exc)[:500],
            "url": url,
        }

    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "reason": str(exc)[:500],
            "url": url,
        }


# ============================================================
# DATABASE EVENTS
# ============================================================

def add_event(
    mission_id: str,
    event_type: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
):
    db_execute(
        """
        INSERT INTO events
        (
            mission_id,
            event_type,
            message,
            data_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            event_type,
            message,
            json.dumps(
                data or {},
                ensure_ascii=False,
            ),
            now(),
        ),
    )


def update_mission_status(
    mission_id: str,
    status: str,
    error: Optional[str] = None,
):
    db_execute(
        """
        UPDATE missions
        SET status = ?,
            error = ?
        WHERE id = ?
        """,
        (
            status,
            error,
            mission_id,
        ),
    )


# ============================================================
# PROVIDER SEARCH
# ============================================================

def search_openalex(
    query: str,
) -> List[Dict[str, Any]]:
    url = (
        "https://api.openalex.org/works?"
        + urlencode(
            {
                "search": query,
                "per-page": MAX_RESULTS_PER_PROVIDER,
            }
        )
    )

    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        data = response.json()

        results = []

        for item in data.get("results", []):
            title = clean_text(
                item.get("title")
            )

            primary = (
                item.get("primary_location")
                or {}
            )

            landing = (
                primary.get("landing_page_url")
                or item.get("doi")
                or ""
            )

            if not landing:
                continue

            abstract = ""

            inverted = item.get(
                "abstract_inverted_index"
            )

            if isinstance(inverted, dict):
                words = []

                for word, positions in inverted.items():
                    for pos in positions:
                        words.append(
                            (pos, word)
                        )

                words.sort(
                    key=lambda x: x[0]
                )

                abstract = " ".join(
                    word
                    for _, word in words
                )

            results.append(
                {
                    "provider": "openalex",
                    "title": title,
                    "url": landing,
                    "snippet": clean_text(
                        abstract,
                        3000,
                    ),
                    "doi": item.get("doi"),
                    "source_type": "scholarly",
                }
            )

        return results

    except Exception:
        return []


def search_crossref(
    query: str,
) -> List[Dict[str, Any]]:
    url = (
        "https://api.crossref.org/works?"
        + urlencode(
            {
                "query.bibliographic": query,
                "rows": MAX_RESULTS_PER_PROVIDER,
            }
        )
    )

    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        data = response.json()

        results = []

        for item in (
            data.get("message", {})
            .get("items", [])
        ):
            title_list = item.get(
                "title",
                [],
            )

            title = clean_text(
                title_list[0]
                if title_list
                else ""
            )

            url_value = (
                item.get("URL")
                or ""
            )

            if not url_value:
                continue

            abstract = clean_text(
                item.get("abstract", ""),
                3000,
            )

            results.append(
                {
                    "provider": "crossref",
                    "title": title,
                    "url": url_value,
                    "snippet": abstract,
                    "doi": item.get("DOI"),
                    "source_type": "scholarly",
                }
            )

        return results

    except Exception:
        return []


def search_duckduckgo(
    query: str,
) -> List[Dict[str, Any]]:
    url = (
        "https://html.duckduckgo.com/html/?"
        + urlencode(
            {
                "q": query,
            }
        )
    )

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        html = response.text

        if detect_page_problem(html):
            return []

        results = []

        # DuckDuckGo result anchors.
        pattern = re.compile(
            r'class="result__a"[^>]+href="([^"]+)"'
            r'[^>]*>(.*?)</a>',
            re.I | re.S,
        )

        for match in pattern.finditer(html):
            if len(results) >= MAX_RESULTS_PER_PROVIDER:
                break

            href = match.group(1)

            title = re.sub(
                r"<[^>]+>",
                " ",
                match.group(2),
            )

            title = clean_text(title)

            if not href.startswith("http"):
                continue

            results.append(
                {
                    "provider": "duckduckgo",
                    "title": title,
                    "url": href,
                    "snippet": "",
                    "source_type": "web",
                }
            )

        return results

    except Exception:
        return []


def search_wikipedia(
    query: str,
) -> List[Dict[str, Any]]:
    api = (
        "https://en.wikipedia.org/w/api.php"
    )

    try:
        response = requests.get(
            api,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": MAX_RESULTS_PER_PROVIDER,
            },
            headers={
                "User-Agent": USER_AGENT
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        data = response.json()

        results = []

        for item in (
            data.get("query", {})
            .get("search", [])
        ):
            title = clean_text(
                item.get("title")
            )

            if not title:
                continue

            url = (
                "https://en.wikipedia.org/wiki/"
                + title.replace(" ", "_")
            )

            snippet = re.sub(
                r"<[^>]+>",
                " ",
                item.get("snippet", ""),
            )

            results.append(
                {
                    "provider": "wikipedia",
                    "title": title,
                    "url": url,
                    "snippet": clean_text(
                        snippet,
                        2000,
                    ),
                    "source_type": "encyclopedia",
                }
            )

        return results

    except Exception:
        return []


PROVIDERS = {
    "openalex": search_openalex,
    "crossref": search_crossref,
    "duckduckgo": search_duckduckgo,
    "wikipedia": search_wikipedia,
}


# ============================================================
# QUERY GENERATION
# ============================================================

def sanitize_query(text: str) -> str:
    text = clean_text(text, 1000)

    text = re.sub(
        r"[%{}[\]<>\"'`]",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def build_queries(
    objective: str,
    max_queries: int = MAX_RESEARCH_QUERIES,
) -> List[str]:

    objective = sanitize_query(objective)

    queries = [
        objective,
        f"{objective} evidence research",
        f"{objective} limitations criticism",
        f"{objective} failures replication independent evidence",
    ]

    output = []

    for query in queries:
        query = sanitize_query(query)

        if len(query) >= 5 and query not in output:
            output.append(query)

        if len(output) >= max_queries:
            break

    return output


# ============================================================
# SOURCE CANONICALIZATION
# ============================================================

def source_key(
    item: Dict[str, Any],
) -> str:

    doi = clean_text(
        item.get("doi")
    ).lower()

    if doi:
        doi = doi.replace(
            "https://doi.org/",
            "",
        )

        doi = doi.replace(
            "http://doi.org/",
            "",
        )

        return "doi:" + doi

    title = normalize_title(
        item.get("title", "")
    )

    if title:
        return "title:" + hash_key(title)

    url = normalize_url(
        item.get("url", "")
    )

    return "url:" + hash_key(url)


def deduplicate_sources(
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    selected: Dict[str, Dict[str, Any]] = {}

    for item in items:
        key = source_key(item)

        if key not in selected:
            item["source_key"] = key
            selected[key] = item
            continue

        existing = selected[key]

        existing_score = len(
            existing.get("snippet", "")
        )

        new_score = len(
            item.get("snippet", "")
        )

        if new_score > existing_score:
            item["source_key"] = key
            selected[key] = item

    return list(selected.values())


# ============================================================
# SOURCE QUALITY
# ============================================================

def source_quality(
    source: Dict[str, Any],
) -> float:

    provider = source.get(
        "provider",
        "",
    )

    source_type = source.get(
        "source_type",
        "",
    )

    text_len = len(
        source.get("evidence_text", "")
    )

    score = 0.35

    if provider == "openalex":
        score += 0.25

    elif provider == "crossref":
        score += 0.22

    elif provider == "wikipedia":
        score += 0.12

    elif provider == "duckduckgo":
        score += 0.08

    if source_type == "pdf":
        score += 0.18

    elif source_type == "scholarly":
        score += 0.10

    if text_len >= 3000:
        score += 0.10

    elif text_len >= 1000:
        score += 0.06

    return min(
        round(score, 3),
        1.0,
    )


# ============================================================
# SOURCE COLLECTION
# ============================================================

def collect_search_results(
    objective: str,
    queries: List[str],
    mission_id: str,
) -> List[Dict[str, Any]]:

    discovered = []

    provider_counts = Counter()

    add_event(
        mission_id,
        "research",
        "Starting multi-provider discovery.",
        {
            "queries": queries,
            "providers": list(PROVIDERS.keys()),
        },
    )

    jobs = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=8
    ) as pool:

        for query in queries:

            for provider_name, function in PROVIDERS.items():

                jobs.append(
                    (
                        query,
                        provider_name,
                        pool.submit(
                            function,
                            query,
                        ),
                    )
                )

        for query, provider_name, future in jobs:

            try:
                results = future.result()

                if not isinstance(results, list):
                    continue

                for result in results:
                    if not result.get("url"):
                        continue

                    result["query"] = query

                    discovered.append(result)

                    provider_counts[
                        provider_name
                    ] += 1

            except Exception as exc:

                add_event(
                    mission_id,
                    "provider_error",
                    f"{provider_name} failed.",
                    {
                        "error": str(exc)[:500],
                        "query": query,
                    },
                )

    deduped = deduplicate_sources(
        discovered
    )

    add_event(
        mission_id,
        "research",
        "Discovery completed.",
        {
            "raw_results": len(discovered),
            "unique_sources": len(deduped),
            "provider_counts": dict(
                provider_counts
            ),
        },
    )

    return deduped


# ============================================================
# FETCH SOURCE EVIDENCE
# ============================================================

def fetch_sources(
    sources: List[Dict[str, Any]],
    mission_id: str,
) -> List[Dict[str, Any]]:

    sources = sources[
        :MAX_SOURCE_FETCHES
    ]

    accepted = []

    def worker(source):
        url = source.get("url", "")

        result = fetch_url(url)

        source_copy = dict(source)

        source_copy.update(result)

        return source_copy

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=6
    ) as pool:

        futures = [
            pool.submit(
                worker,
                source,
            )
            for source in sources
        ]

        for future in futures:

            try:
                source = future.result()

                if source.get("ok"):

                    source["evidence_text"] = clean_text(
                        source.get("text", "")
                    )

                    source["quality"] = source_quality(
                        source
                    )

                    accepted.append(source)

                else:

                    add_event(
                        mission_id,
                        "source_rejected",
                        "Source rejected during evidence ingestion.",
                        {
                            "url": source.get("url"),
                            "reason": source.get("reason"),
                        },
                    )

            except Exception as exc:

                add_event(
                    mission_id,
                    "source_error",
                    "Source processing failed.",
                    {
                        "error": str(exc)[:500]
                    },
                )

    add_event(
        mission_id,
        "research",
        "Evidence ingestion completed.",
        {
            "accepted_sources": len(accepted),
            "attempted_sources": len(sources),
        },
    )

    return accepted


# ============================================================
# CLAIM EXTRACTION
# ============================================================

ASSERTION_MARKERS = (
    "found",
    "finds",
    "shows",
    "showed",
    "demonstrates",
    "demonstrated",
    "suggests",
    "suggested",
    "reported",
    "reports",
    "concluded",
    "concludes",
    "observed",
    "observes",
    "increased",
    "decreased",
    "improved",
    "reduced",
    "failed",
    "failure",
    "effective",
    "ineffective",
    "associated",
    "correlated",
    "evidence",
    "results",
)


def split_sentences(
    text: str,
) -> List[str]:

    text = clean_text(text)

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    output = []

    for sentence in sentences:

        sentence = clean_text(
            sentence,
            800,
        )

        if 80 <= len(sentence) <= 700:
            output.append(sentence)

    return output


def extract_claims(
    sources: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    claims = []

    seen = set()

    for source in sources:

        text = source.get(
            "evidence_text",
            "",
        )

        sentences = split_sentences(
            text
        )

        for sentence in sentences:

            lower = sentence.lower()

            if not any(
                marker in lower
                for marker in ASSERTION_MARKERS
            ):
                continue

            if detect_page_problem(sentence):
                continue

            normalized = normalize_title(
                sentence
            )

            if not normalized:
                continue

            key = hash_key(
                normalized
            )

            if key in seen:
                continue

            seen.add(key)

            claims.append(
                {
                    "claim": sentence,
                    "source_key": source.get(
                        "source_key"
                    ),
                    "source_domain": get_domain(
                        source.get("url", "")
                    ),
                }
            )

            if len(claims) >= MAX_CLAIMS:
                return claims

    return claims


# ============================================================
# CLAIM SIMILARITY
# ============================================================

STOPWORDS = {
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
    "these",
    "those",
    "by",
    "from",
    "as",
    "at",
    "it",
    "be",
    "has",
    "have",
    "had",
    "their",
    "they",
    "than",
    "which",
    "can",
    "may",
}


def tokens(
    text: str,
) -> set:

    words = re.findall(
        r"[a-zA-Z]{3,}",
        text.lower(),
    )

    return {
        word
        for word in words
        if word not in STOPWORDS
    }


def similarity(
    a: str,
    b: str,
) -> float:

    aa = tokens(a)
    bb = tokens(b)

    if not aa or not bb:
        return 0.0

    return len(
        aa & bb
    ) / max(
        1,
        len(aa | bb),
    )


def negation_score(
    text: str,
) -> int:

    lower = text.lower()

    patterns = [
        "not ",
        "no ",
        "never",
        "failed",
        "failure",
        "unable",
        "cannot",
        "could not",
        "did not",
        "does not",
        "didn't",
        "doesn't",
        "ineffective",
        "limited",
        "limitation",
        "lack of",
        "insufficient",
    ]

    return sum(
        1
        for pattern in patterns
        if pattern in lower
    )


def claim_relation(
    target: str,
    evidence: str,
) -> str:

    score = similarity(
        target,
        evidence,
    )

    if score < 0.08:
        return "unrelated"

    target_neg = negation_score(
        target
    )

    evidence_neg = negation_score(
        evidence
    )

    if abs(
        target_neg - evidence_neg
    ) >= 1:

        return "contradict"

    return "support"


# ============================================================
# EVIDENCE GRAPH
# ============================================================

def build_evidence_graph(
    claims: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:

    graph = {
        "nodes": [],
        "edges": [],
    }

    for source in sources:

        graph["nodes"].append(
            {
                "id": source.get(
                    "source_key"
                ),
                "type": "source",
                "domain": get_domain(
                    source.get("url", "")
                ),
                "provider": source.get(
                    "provider"
                ),
                "quality": source.get(
                    "quality",
                    0,
                ),
            }
        )

    for index, claim in enumerate(
        claims
    ):

        claim_id = f"claim-{index + 1}"

        graph["nodes"].append(
            {
                "id": claim_id,
                "type": "claim",
                "text": claim.get(
                    "claim"
                ),
            }
        )

        for source in sources:

            relation = claim_relation(
                claim.get("claim", ""),
                source.get(
                    "evidence_text",
                    "",
                ),
            )

            if relation in {
                "support",
                "contradict",
            }:

                graph["edges"].append(
                    {
                        "from": claim_id,
                        "to": source.get(
                            "source_key"
                        ),
                        "relation": relation,
                    }
                )

    return graph


# ============================================================
# VERIFICATION
# ============================================================

def verify_claim(
    claim: Dict[str, Any],
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:

    claim_text = claim.get(
        "claim",
        "",
    )

    supporting = []
    contradicting = []

    for source in sources:

        relation = claim_relation(
            claim_text,
            source.get(
                "evidence_text",
                "",
            ),
        )

        if relation == "support":
            supporting.append(source)

        elif relation == "contradict":
            contradicting.append(source)

    support_domains = {
        get_domain(
            source.get("url", "")
        )
        for source in supporting
        if get_domain(
            source.get("url", "")
        )
    }

    contradiction_domains = {
        get_domain(
            source.get("url", "")
        )
        for source in contradicting
        if get_domain(
            source.get("url", "")
        )
    }

    if (
        len(support_domains) >= 2
        and not contradicting
    ):
        status = "VERIFIED"

        confidence = min(
            0.95,
            0.60
            + 0.10 * len(support_domains),
        )

    elif (
        supporting
        and contradicting
    ):
        status = "UNCERTAIN"

        confidence = 0.45

    elif supporting:
        status = "INSUFFICIENT"

        confidence = 0.30

    else:
        status = "REJECTED"

        confidence = 0.05

    return {
        "claim": claim_text,
        "status": status,
        "confidence": round(
            confidence,
            3,
        ),
        "supporting_sources": len(
            supporting
        ),
        "contradicting_sources": len(
            contradicting
        ),
        "support_domains": sorted(
            support_domains
        ),
        "contradiction_domains": sorted(
            contradiction_domains
        ),
        "source_keys": [
            source.get("source_key")
            for source in (
                supporting
                + contradicting
            )
        ],
    }


def verify_claims(
    claims: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    results = []

    for claim in claims:

        result = verify_claim(
            claim,
            sources,
        )

        results.append(result)

    return results


# ============================================================
# COUNTER-EVIDENCE
# ============================================================

def counter_queries(
    objective: str,
) -> List[str]:

    base = sanitize_query(
        objective
    )

    return [
        f"{base} limitations",
        f"{base} criticism",
        f"{base} failures",
        f"{base} negative results",
    ]


def search_counter_evidence(
    objective: str,
    mission_id: str,
) -> Dict[str, Any]:

    queries = counter_queries(
        objective
    )

    raw = collect_search_results(
        objective,
        queries,
        mission_id,
    )

    sources = fetch_sources(
        raw,
        mission_id,
    )

    return {
        "queries": queries,
        "sources": sources,
    }


# ============================================================
# PERSISTENCE
# ============================================================

def save_source(
    mission_id: str,
    source: Dict[str, Any],
):
    domain = get_domain(
        source.get("url", "")
    )

    db_execute(
        """
        INSERT INTO sources
        (
            mission_id,
            source_key,
            title,
            url,
            domain,
            provider,
            source_type,
            status,
            quality,
            independent,
            content_chars,
            evidence_text,
            metadata_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            source.get("source_key"),
            clean_text(
                source.get("title", ""),
                1000,
            ),
            source.get("url"),
            domain,
            source.get("provider"),
            source.get(
                "source_type",
                "web",
            ),
            "accepted",
            source.get(
                "quality",
                0,
            ),
            1,
            len(
                source.get(
                    "evidence_text",
                    "",
                )
            ),
            source.get(
                "evidence_text",
                "",
            ),
            json.dumps(
                {
                    "doi": source.get("doi"),
                    "query": source.get("query"),
                    "http_status": source.get(
                        "http_status"
                    ),
                    "content_type": source.get(
                        "content_type"
                    ),
                },
                ensure_ascii=False,
            ),
            now(),
        ),
    )


def save_claim(
    mission_id: str,
    result: Dict[str, Any],
):
    db_execute(
        """
        INSERT INTO claims
        (
            mission_id,
            claim,
            status,
            confidence,
            supporting_sources,
            contradicting_sources,
            source_keys_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            result.get("claim"),
            result.get("status"),
            result.get(
                "confidence",
                0,
            ),
            result.get(
                "supporting_sources",
                0,
            ),
            result.get(
                "contradicting_sources",
                0,
            ),
            json.dumps(
                result.get(
                    "source_keys",
                    [],
                ),
                ensure_ascii=False,
            ),
            now(),
        ),
    )


def save_memory(
    mission_id: str,
    objective: str,
    summary: str,
):
    db_execute(
        """
        INSERT INTO memory
        (
            mission_id,
            objective,
            summary,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            summary,
            now(),
        ),
    )


# ============================================================
# SYNTHESIS
# ============================================================

def synthesize(
    objective: str,
    sources: List[Dict[str, Any]],
    verified_claims: List[Dict[str, Any]],
    counter_sources: List[Dict[str, Any]],
) -> Dict[str, Any]:

    verified = [
        item
        for item in verified_claims
        if item.get("status")
        == "VERIFIED"
    ]

    uncertain = [
        item
        for item in verified_claims
        if item.get("status")
        == "UNCERTAIN"
    ]

    insufficient = [
        item
        for item in verified_claims
        if item.get("status")
        == "INSUFFICIENT"
    ]

    rejected = [
        item
        for item in verified_claims
        if item.get("status")
        == "REJECTED"
    ]

    domains = {
        get_domain(
            source.get("url", "")
        )
        for source in sources
        if get_domain(
            source.get("url", "")
        )
    }

    contradictions = sum(
        item.get(
            "contradicting_sources",
            0,
        )
        for item in verified_claims
    )

    if (
        len(verified) > 0
        and len(domains) >= 2
        and contradictions == 0
    ):
        conclusion = (
            "The available evidence contains "
            "multiple independent supporting sources "
            "for at least some claims."
        )

    elif (
        verified
        or uncertain
        or insufficient
    ):
        conclusion = (
            "The evidence supports some aspects "
            "of the objective, but uncertainty, "
            "limited independence, or conflicting "
            "evidence prevents a strong conclusion."
        )

    else:
        conclusion = (
            "Evidence is insufficient for a strong "
            "conclusion."
        )

    next_actions = []

    if len(domains) < 2:
        next_actions.append(
            "Find additional evidence from independent domains."
        )

    if contradictions:
        next_actions.append(
            "Review contradictory evidence and resolve claim-level conflicts."
        )

    if insufficient:
        next_actions.append(
            "Collect additional primary or independent sources."
        )

    if not next_actions:
        next_actions.append(
            "Continue monitoring for new independent evidence."
        )

    return {
        "conclusion": conclusion,
        "verified_claims": len(verified),
        "uncertain_claims": len(uncertain),
        "insufficient_claims": len(insufficient),
        "rejected_claims": len(rejected),
        "contradictions": contradictions,
        "independent_domains": len(domains),
        "counter_evidence_sources": len(
            counter_sources
        ),
        "next_actions": next_actions,
    }


# ============================================================
# RESEARCH ENGINE
# ============================================================

def research_mission(
    mission_id: str,
    objective: str,
    verify: bool = True,
    remember: bool = True,
    max_queries: int = MAX_RESEARCH_QUERIES,
):

    started = now()

    try:

        update_mission_status(
            mission_id,
            "running",
        )

        db_execute(
            """
            UPDATE missions
            SET started_at = ?
            WHERE id = ?
            """,
            (
                started,
                mission_id,
            ),
        )

        add_event(
            mission_id,
            "mission_started",
            "Autonomous research started.",
            {
                "version": VERSION,
                "build": BUILD,
            },
        )

        # ----------------------------------------------------
        # 1. BUILD QUERIES
        # ----------------------------------------------------

        queries = build_queries(
            objective,
            max_queries,
        )

        add_event(
            mission_id,
            "planning",
            "Research plan generated.",
            {
                "queries": queries
            },
        )

        # ----------------------------------------------------
        # 2. DISCOVERY
        # ----------------------------------------------------

        raw_sources = collect_search_results(
            objective,
            queries,
            mission_id,
        )

        # ----------------------------------------------------
        # 3. EVIDENCE INGESTION
        # ----------------------------------------------------

        sources = fetch_sources(
            raw_sources,
            mission_id,
        )

        # ----------------------------------------------------
        # 4. PERSIST SOURCES
        # ----------------------------------------------------

        for source in sources:
            try:
                save_source(
                    mission_id,
                    source,
                )
            except Exception as exc:
                add_event(
                    mission_id,
                    "persistence_error",
                    "Could not save source.",
                    {
                        "error": str(exc)[:500]
                    },
                )

        # ----------------------------------------------------
        # 5. CLAIM EXTRACTION
        # ----------------------------------------------------

        claims = extract_claims(
            sources
        )

        add_event(
            mission_id,
            "claims",
            "Claims extracted from validated evidence.",
            {
                "claims": len(claims)
            },
        )

        # ----------------------------------------------------
        # 6. VERIFICATION
        # ----------------------------------------------------

        if verify:
            verification = verify_claims(
                claims,
                sources,
            )
        else:
            verification = [
                {
                    "claim": claim.get(
                        "claim"
                    ),
                    "status": "NOT_RUN",
                    "confidence": 0,
                    "supporting_sources": 0,
                    "contradicting_sources": 0,
                    "source_keys": [
                        claim.get(
                            "source_key"
                        )
                    ],
                }
                for claim in claims
            ]

        for result in verification:
            try:
                save_claim(
                    mission_id,
                    result,
                )
            except Exception as exc:
                add_event(
                    mission_id,
                    "persistence_error",
                    "Could not save claim.",
                    {
                        "error": str(exc)[:500]
                    },
                )

        # ----------------------------------------------------
        # 7. COUNTER-EVIDENCE
        # ----------------------------------------------------

        counter_data = {
            "queries": [],
            "sources": [],
        }

        try:
            counter_data = search_counter_evidence(
                objective,
                mission_id,
            )
        except Exception as exc:
            add_event(
                mission_id,
                "counter_evidence_error",
                "Counter-evidence phase failed.",
                {
                    "error": str(exc)[:500]
                },
            )

        counter_sources = counter_data.get(
            "sources",
            [],
        )

        # ----------------------------------------------------
        # 8. EVIDENCE GRAPH
        # ----------------------------------------------------

        graph = build_evidence_graph(
            claims,
            sources,
        )

        # ----------------------------------------------------
        # 9. SYNTHESIS
        # ----------------------------------------------------

        synthesis = synthesize(
            objective,
            sources,
            verification,
            counter_sources,
        )

        # ----------------------------------------------------
        # 10. METRICS
        # ----------------------------------------------------

        domains = sorted(
            {
                get_domain(
                    source.get("url", "")
                )
                for source in sources
                if get_domain(
                    source.get("url", "")
                )
            }
        )

        providers = sorted(
            {
                source.get(
                    "provider"
                )
                for source in sources
                if source.get(
                    "provider"
                )
            }
        )

        verified_count = sum(
            1
            for item in verification
            if item.get("status")
            == "VERIFIED"
        )

        unsupported_count = sum(
            1
            for item in verification
            if item.get("status")
            in {
                "REJECTED",
                "INSUFFICIENT",
            }
        )

        contradiction_count = sum(
            item.get(
                "contradicting_sources",
                0,
            )
            for item in verification
        )

        if sources:
            avg_quality = round(
                sum(
                    source.get(
                        "quality",
                        0,
                    )
                    for source in sources
                )
                / len(sources),
                3,
            )
        else:
            avg_quality = 0.0

        provider_diversity = (
            min(
                1.0,
                len(providers) / 4,
            )
        )

        source_diversity = (
            min(
                1.0,
                len(domains) / 5,
            )
        )

        if verification:
            avg_confidence = round(
                sum(
                    item.get(
                        "confidence",
                        0,
                    )
                    for item in verification
                )
                / len(verification),
                3,
            )
        else:
            avg_confidence = 0.0

        research_strength = round(
            (
                avg_quality
                * 0.35
                + source_diversity
                * 0.25
                + provider_diversity
                * 0.15
                + avg_confidence
                * 0.25
            ),
            3,
        )

        overall_confidence = round(
            min(
                1.0,
                (
                    avg_confidence
                    * 0.65
                    + source_diversity
                    * 0.20
                    + avg_quality
                    * 0.15
                ),
            ),
            3,
        )

        # ----------------------------------------------------
        # 11. FINAL RESULT
        # ----------------------------------------------------

        result = {
            "task_id": mission_id,
            "mission_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "build": BUILD,
            "objective": objective,

            "agent_trace": [
                {
                    "stage": "planning",
                    "status": "completed",
                    "queries": queries,
                },
                {
                    "stage": "discovery",
                    "status": "completed",
                    "sources_discovered": len(
                        raw_sources
                    ),
                },
                {
                    "stage": "evidence_ingestion",
                    "status": "completed",
                    "accepted_sources": len(
                        sources
                    ),
                },
                {
                    "stage": "claim_extraction",
                    "status": "completed",
                    "claims": len(
                        claims
                    ),
                },
                {
                    "stage": "verification",
                    "status": (
                        "completed"
                        if verify
                        else "skipped"
                    ),
                    "verified": verified_count,
                },
                {
                    "stage": "counter_evidence",
                    "status": "completed",
                    "sources": len(
                        counter_sources
                    ),
                },
                {
                    "stage": "synthesis",
                    "status": "completed",
                },
            ],

            "providers": {
                "used": providers,
                "count": len(providers),
            },

            "evidence": {
                "count": len(sources),
                "graph_nodes": len(
                    graph["nodes"]
                ),
                "graph_edges": len(
                    graph["edges"]
                ),
                "independent_domains": len(
                    domains
                ),
                "domains": domains,
                "average_source_quality": avg_quality,
                "source_diversity": round(
                    source_diversity,
                    3,
                ),
                "provider_diversity": round(
                    provider_diversity,
                    3,
                ),
            },

            "claims": verification,

            "verification": {
                "enabled": verify,
                "verified": verified_count,
                "unsupported": unsupported_count,
                "confidence": overall_confidence,
                "research_strength": research_strength,
                "contradictions": contradiction_count,
            },

            "counter_evidence": {
                "queries": counter_data.get(
                    "queries",
                    [],
                ),
                "sources": len(
                    counter_sources
                ),
            },

            "evidence_graph": graph,

            "synthesis": synthesis,

            "next_cycle": {
                "recommended": (
                    synthesis.get(
                        "next_actions",
                        [],
                    )
                )
            },

            "timing": {
                "started_at": started,
                "completed_at": now(),
                "duration_seconds": round(
                    now() - started,
                    2,
                ),
            },
        }

        # ----------------------------------------------------
        # 12. MEMORY
        # ----------------------------------------------------

        if remember:
            try:
                save_memory(
                    mission_id,
                    objective,
                    synthesis.get(
                        "conclusion",
                        "",
                    ),
                )
            except Exception as exc:
                add_event(
                    mission_id,
                    "memory_error",
                    "Memory persistence failed.",
                    {
                        "error": str(exc)[:500]
                    },
                )

        # ----------------------------------------------------
        # 13. SAVE RESULT
        # ----------------------------------------------------

        db_execute(
            """
            UPDATE missions
            SET status = ?,
                completed_at = ?,
                result_json = ?,
                error = NULL
            WHERE id = ?
            """,
            (
                "completed",
                now(),
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                mission_id,
            ),
        )

        add_event(
            mission_id,
            "mission_completed",
            "Mission completed successfully.",
            {
                "sources": len(sources),
                "claims": len(verification),
                "verified": verified_count,
                "confidence": overall_confidence,
            },
        )

    except Exception as exc:

        error = (
            f"{type(exc).__name__}: "
            f"{str(exc)}"
        )

        traceback_text = traceback.format_exc()

        update_mission_status(
            mission_id,
            "failed",
            error,
        )

        db_execute(
            """
            UPDATE missions
            SET completed_at = ?,
                error = ?
            WHERE id = ?
            """,
            (
                now(),
                error,
                mission_id,
            ),
        )

        add_event(
            mission_id,
            "mission_failed",
            "Mission failed.",
            {
                "error": error,
                "traceback": traceback_text[
                    -5000:
                ],
            },
        )


# ============================================================
# MISSION CREATION
# ============================================================

def create_mission(
    objective: str,
    verify: bool = True,
    remember: bool = True,
    max_queries: int = MAX_RESEARCH_QUERIES,
) -> Dict[str, Any]:

    objective = clean_text(
        objective,
        20_000,
    )

    if not objective:
        raise HTTPException(
            status_code=400,
            detail="Objective is required.",
        )

    mission_id = make_id(
        "mission"
    )

    created = now()

    db_execute(
        """
        INSERT INTO missions
        (
            id,
            objective,
            status,
            version,
            build,
            created_at,
            started_at,
            completed_at,
            result_json,
            error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "queued",
            VERSION,
            BUILD,
            created,
            None,
            None,
            None,
            None,
        ),
    )

    add_event(
        mission_id,
        "mission_queued",
        "Mission accepted and queued for background execution.",
        {
            "version": VERSION,
            "build": BUILD,
        },
    )

    # CRITICAL 2050.23 CHANGE:
    # Do NOT perform research inside the HTTP request.
    EXECUTOR.submit(
        research_mission,
        mission_id,
        objective,
        verify,
        remember,
        max_queries,
    )

    return {
        "task_id": mission_id,
        "mission_id": mission_id,
        "status": "queued",
        "version": VERSION,
        "build": BUILD,
        "objective": objective,
        "message": (
            "Mission accepted. "
            "Research is running in the background."
        ),
        "poll": f"/mission/{mission_id}",
    }


# ============================================================
# API ROUTES
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
def dashboard():

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
/>

<title>AI Infinity</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background:
        radial-gradient(
            circle at top,
            #18213c 0,
            #090d18 45%,
            #05070d 100%
        );
    color: #f4f7ff;
    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
    min-height: 100vh;
}

.container {
    width: min(1100px, 94%);
    margin: 0 auto;
    padding: 22px 0 60px;
}

.topbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 35px;
}

.logo {
    font-size: 23px;
    font-weight: 800;
    letter-spacing: -0.7px;
}

.logo span {
    opacity: .65;
}

.status {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 8px 13px;
    border-radius: 999px;
    background: rgba(45, 220, 135, .12);
    border: 1px solid rgba(45, 220, 135, .25);
    color: #6df0aa;
    font-size: 12px;
    font-weight: 700;
}

.dot {
    width: 7px;
    height: 7px;
    background: #53e79a;
    border-radius: 50%;
    box-shadow: 0 0 12px #53e79a;
}

.hero {
    margin-bottom: 24px;
}

h1 {
    font-size: clamp(34px, 7vw, 66px);
    line-height: 1;
    margin: 0 0 17px;
    letter-spacing: -2.5px;
}

.subtitle {
    color: #aab4cb;
    font-size: 17px;
    max-width: 700px;
    line-height: 1.65;
}

.panel {
    background: rgba(12, 17, 31, .78);
    border: 1px solid rgba(255,255,255,.08);
    border-radius: 22px;
    padding: 20px;
    backdrop-filter: blur(18px);
    box-shadow:
        0 20px 70px rgba(0,0,0,.28);
}

textarea {
    width: 100%;
    min-height: 145px;
    resize: vertical;
    border: 1px solid rgba(255,255,255,.09);
    border-radius: 15px;
    background: #080c16;
    color: white;
    padding: 17px;
    font-size: 16px;
    line-height: 1.55;
    outline: none;
}

textarea:focus {
    border-color: rgba(120,150,255,.55);
}

.actions {
    display: flex;
    gap: 12px;
    margin-top: 14px;
    flex-wrap: wrap;
}

button {
    border: 0;
    border-radius: 13px;
    padding: 13px 19px;
    font-size: 15px;
    font-weight: 800;
    cursor: pointer;
    background: #f4f7ff;
    color: #080b12;
}

button:disabled {
    opacity: .55;
    cursor: wait;
}

.secondary {
    background: rgba(255,255,255,.08);
    color: #dce5f7;
    border: 1px solid rgba(255,255,255,.09);
}

.cards {
    display: grid;
    grid-template-columns:
        repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin: 16px 0;
}

.card {
    padding: 17px;
    border-radius: 17px;
    background: rgba(255,255,255,.035);
    border: 1px solid rgba(255,255,255,.07);
}

.card-label {
    color: #8792aa;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 9px;
}

.card-value {
    font-size: 15px;
    font-weight: 800;
}

.ready {
    color: #72e6a6;
}

.progress {
    margin-top: 18px;
    padding: 17px;
    border-radius: 16px;
    background: rgba(255,255,255,.035);
    display: none;
}

.progress.show {
    display: block;
}

.progress-title {
    font-weight: 800;
    margin-bottom: 8px;
}

.progress-bar {
    height: 7px;
    border-radius: 999px;
    background: rgba(255,255,255,.08);
    overflow: hidden;
}

.progress-fill {
    width: 20%;
    height: 100%;
    background: #f4f7ff;
    border-radius: inherit;
    transition: width .5s ease;
}

.result {
    margin-top: 18px;
    display: none;
}

.result.show {
    display: block;
}

.result-title {
    font-weight: 800;
    margin-bottom: 10px;
}

pre {
    margin: 0;
    padding: 17px;
    background: #050810;
    border: 1px solid rgba(255,255,255,.07);
    border-radius: 15px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    color: #cdd8ec;
    font-size: 12px;
    line-height: 1.55;
}

.section {
    margin-top: 17px;
}

.section-title {
    font-size: 13px;
    font-weight: 800;
    margin-bottom: 10px;
    color: #9ca8bf;
    text-transform: uppercase;
    letter-spacing: 1px;
}

.tags {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.tag {
    padding: 8px 10px;
    border-radius: 9px;
    background: rgba(255,255,255,.05);
    color: #aeb9ce;
    font-size: 12px;
}

.footer {
    color: #66728b;
    text-align: center;
    font-size: 12px;
    margin-top: 32px;
}

@media (max-width: 750px) {

    .container {
        width: 92%;
    }

    .cards {
        grid-template-columns:
            repeat(2, minmax(0, 1fr));
    }

    h1 {
        letter-spacing: -1.5px;
    }

}

@media (max-width: 470px) {

    .cards {
        grid-template-columns: 1fr;
    }

    .topbar {
        align-items: flex-start;
    }

    button {
        width: 100%;
    }

}

</style>
</head>

<body>

<div class="container">

    <div class="topbar">

        <div class="logo">
            AI Infinity <span>∞</span>
        </div>

        <div class="status">
            <span class="dot"></span>
            ONLINE
        </div>

    </div>

    <div class="hero">

        <h1>
            Turn an objective into
            verified intelligence.
        </h1>

        <div class="subtitle">
            AI Infinity researches an objective,
            validates evidence, checks contradictions,
            verifies claims and builds an evidence graph.
        </div>

    </div>

    <div class="panel">

        <textarea
            id="objective"
            placeholder="Example:
Research the reliability of autonomous AI agents for real-world task execution. Find independent evidence, verify important claims, identify contradictory evidence, and give the next actions."
        ></textarea>

        <div class="actions">

            <button
                id="runButton"
                onclick="runMission()"
            >
                🚀 Run Mission
            </button>

            <button
                class="secondary"
                onclick="clearMission()"
            >
                Clear
            </button>

        </div>

        <div
            id="progress"
            class="progress"
        >

            <div
                id="progressTitle"
                class="progress-title"
            >
                Mission queued...
            </div>

            <div class="progress-bar">
                <div
                    id="progressFill"
                    class="progress-fill"
                ></div>
            </div>

        </div>

    </div>

    <div class="cards">

        <div class="card">
            <div class="card-label">
                Version
            </div>
            <div
                class="card-value"
                id="version"
            >
                TARGET-2050.23
            </div>
        </div>

        <div class="card">
            <div class="card-label">
                Engine
            </div>
            <div class="card-value ready">
                READY
            </div>
        </div>

        <div class="card">
            <div class="card-label">
                Evidence
            </div>
            <div class="card-value ready">
                ENABLED
            </div>
        </div>

        <div class="card">
            <div class="card-label">
                Verification
            </div>
            <div class="card-value ready">
                ENABLED
            </div>
        </div>

    </div>

    <div class="panel">

        <div class="section">

            <div class="section-title">
                Core capabilities
            </div>

            <div class="tags">

                <div class="tag">
                    Async Missions
                </div>

                <div class="tag">
                    Autonomous Research
                </div>

                <div class="tag">
                    Evidence Integrity
                </div>

                <div class="tag">
                    Source Validation
                </div>

                <div class="tag">
                    Claim Verification
                </div>

                <div class="tag">
                    Counter-Evidence
                </div>

                <div class="tag">
                    Evidence Graph
                </div>

                <div class="tag">
                    Persistent Memory
                </div>

                <div class="tag">
                    SSRF Protection
                </div>

            </div>

        </div>

        <div class="section">

            <div class="section-title">
                API
            </div>

            <div class="tags">

                <div class="tag">
                    /mission
                </div>

                <div class="tag">
                    /missions
                </div>

                <div class="tag">
                    /research
                </div>

                <div class="tag">
                    /run
                </div>

                <div class="tag">
                    /command
                </div>

                <div class="tag">
                    /status
                </div>

                <div class="tag">
                    /capabilities
                </div>

            </div>

        </div>

    </div>

    <div
        id="result"
        class="result"
    >

        <div class="result-title">
            Mission result
        </div>

        <pre id="resultJson"></pre>

    </div>

    <div class="footer">
        AI Infinity · TARGET-2050.23 ·
        Async Autonomous Evidence Core
    </div>

</div>


<script>

let pollTimer = null;

function showProgress(title, percent) {

    const progress =
        document.getElementById("progress");

    const progressTitle =
        document.getElementById("progressTitle");

    const progressFill =
        document.getElementById("progressFill");

    progress.classList.add("show");

    progressTitle.textContent = title;

    progressFill.style.width =
        Math.max(
            5,
            Math.min(
                100,
                percent
            )
        ) + "%";
}


function showResult(data) {

    const result =
        document.getElementById("result");

    const resultJson =
        document.getElementById("resultJson");

    result.classList.add("show");

    resultJson.textContent =
        JSON.stringify(
            data,
            null,
            2
        );
}


function clearMission() {

    if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
    }

    document.getElementById(
        "objective"
    ).value = "";

    document.getElementById(
        "result"
    ).classList.remove("show");

    document.getElementById(
        "progress"
    ).classList.remove("show");

    document.getElementById(
        "resultJson"
    ).textContent = "";

    document.getElementById(
        "runButton"
    ).disabled = false;
}


async function readResponse(response) {

    const text =
        await response.text();

    if (!text) {
        return {
            error: true,
            status: response.status,
            message:
                "Server returned an empty response."
        };
    }

    try {

        return JSON.parse(text);

    } catch (error) {

        return {
            error: true,
            status: response.status,
            message:
                "Server returned a non-JSON response.",
            raw:
                text.substring(
                    0,
                    3000
                )
        };

    }

}


async function runMission() {

    const objective =
        document.getElementById(
            "objective"
        ).value.trim();

    const button =
        document.getElementById(
            "runButton"
        );

    if (!objective) {

        showResult({
            error: true,
            message:
                "Please enter an objective."
        });

        return;
    }

    button.disabled = true;

    document.getElementById(
        "result"
    ).classList.remove("show");

    showProgress(
        "Creating mission...",
        8
    );

    try {

        const response =
            await fetch(
                "/mission",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body:
                        JSON.stringify({
                            objective:
                                objective,
                            research: true,
                            verify: true,
                            remember: true
                        })
                }
            );

        const data =
            await readResponse(
                response
            );

        if (
            data.error
            || !response.ok
        ) {

            showResult(data);

            button.disabled = false;

            showProgress(
                "Mission could not be started.",
                100
            );

            return;
        }

        const missionId =
            data.mission_id
            || data.task_id;

        if (!missionId) {

            showResult({
                error: true,
                message:
                    "Server did not return a mission ID.",
                response:
                    data
            });

            button.disabled = false;

            return;
        }

        showProgress(
            "Mission accepted. Research starting...",
            15
        );

        pollMission(
            missionId
        );

    } catch (error) {

        showResult({
            error: true,
            message:
                error.message
                || String(error)
        });

        button.disabled = false;

        showProgress(
            "Connection failed.",
            100
        );

    }

}


async function pollMission(
    missionId
) {

    try {

        const response =
            await fetch(
                "/mission/"
                + encodeURIComponent(
                    missionId
                ),
                {
                    cache: "no-store"
                }
            );

        const data =
            await readResponse(
                response
            );

        if (
            data.error
            || !response.ok
        ) {

            showResult(data);

            document.getElementById(
                "runButton"
            ).disabled = false;

            return;
        }

        const status =
            data.status;

        if (
            status === "queued"
        ) {

            showProgress(
                "Mission queued...",
                15
            );

        } else if (
            status === "running"
        ) {

            const eventCount =
                Array.isArray(
                    data.events
                )
                ? data.events.length
                : 0;

            let percent = 25;

            if (
                eventCount >= 2
            ) percent = 35;

            if (
                eventCount >= 4
            ) percent = 50;

            if (
                eventCount >= 6
            ) percent = 70;

            if (
                eventCount >= 8
            ) percent = 82;

            showProgress(
                "AI Infinity is researching and verifying...",
                percent
            );

        } else if (
            status === "completed"
        ) {

            showProgress(
                "Mission completed.",
                100
            );

            showResult(
                data.result
                || data
            );

            document.getElementById(
                "runButton"
            ).disabled = false;

            return;

        } else if (
            status === "failed"
        ) {

            showProgress(
                "Mission failed safely.",
                100
            );

            showResult(
                data
            );

            document.getElementById(
                "runButton"
            ).disabled = false;

            return;
        }

        pollTimer =
            setTimeout(
                () => pollMission(
                    missionId
                ),
                2000
            );

    } catch (error) {

        showResult({
            error: true,
            message:
                "Polling failed.",
            detail:
                error.message
        });

        document.getElementById(
            "runButton"
        ).disabled = false;

    }

}

</script>

</body>
</html>
"""

    return HTMLResponse(
        content=html
    )


# ============================================================
# HEALTH / STATUS
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "timestamp": now(),
    }


@app.get("/status")
def status():

    try:
        row = db_execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(
                    CASE
                        WHEN status = 'completed'
                        THEN 1 ELSE 0
                    END
                ) AS completed,
                SUM(
                    CASE
                        WHEN status = 'running'
                        THEN 1 ELSE 0
                    END
                ) AS running,
                SUM(
                    CASE
                        WHEN status = 'queued'
                        THEN 1 ELSE 0
                    END
                ) AS queued,
                SUM(
                    CASE
                        WHEN status = 'failed'
                        THEN 1 ELSE 0
                    END
                ) AS failed
            FROM missions
            """,
            fetch=True,
        )[0]

        return {
            "status": "online",
            "service": APP_NAME,
            "version": VERSION,
            "build": BUILD,
            "database": "online",
            "executor": "online",
            "missions": {
                "total": row["total"] or 0,
                "completed": row["completed"] or 0,
                "running": row["running"] or 0,
                "queued": row["queued"] or 0,
                "failed": row["failed"] or 0,
            },
            "timestamp": now(),
        }

    except Exception as exc:

        return JSONResponse(
            status_code=200,
            content={
                "status": "degraded",
                "version": VERSION,
                "error": str(exc),
            },
        )


@app.get("/capabilities")
def capabilities():

    return {
        "version": VERSION,
        "build": BUILD,

        "builtin_tools": [
            {
                "name": "research",
                "category": "intelligence",
                "permission": "safe",
            },
            {
                "name": "evidence",
                "category": "verification",
                "permission": "safe",
            },
            {
                "name": "verification",
                "category": "verification",
                "permission": "safe",
            },
            {
                "name": "counter_evidence",
                "category": "verification",
                "permission": "safe",
            },
            {
                "name": "evidence_graph",
                "category": "reasoning",
                "permission": "safe",
            },
            {
                "name": "memory",
                "category": "persistence",
                "permission": "safe",
            },
            {
                "name": "async_missions",
                "category": "execution",
                "permission": "safe",
            },
            {
                "name": "source_firewall",
                "category": "security",
                "permission": "safe",
            },
        ],

        "providers": [
            "OpenAlex",
            "Crossref",
            "DuckDuckGo",
            "Wikipedia",
        ],

        "features": [
            "asynchronous mission execution",
            "background workers",
            "source validation",
            "PDF extraction",
            "challenge detection",
            "claim extraction",
            "claim verification",
            "counter-evidence",
            "evidence graph",
            "persistent SQLite memory",
            "SSRF protection",
            "mobile dashboard",
        ],
    }


# ============================================================
# CREATE MISSION
# ============================================================

@app.post("/mission")
def mission_endpoint(
    request: MissionRequest,
):

    objective = (
        request.objective
        or request.command
        or ""
    )

    return JSONResponse(
        status_code=202,
        content=create_mission(
            objective=objective,
            verify=request.verify,
            remember=request.remember,
            max_queries=request.max_queries,
        ),
    )


@app.post("/research")
def research_endpoint(
    request: ResearchRequest,
):

    objective = (
        request.objective
        or request.command
        or ""
    )

    return JSONResponse(
        status_code=202,
        content=create_mission(
            objective=objective,
            verify=request.verify,
            remember=request.remember,
        ),
    )


@app.post("/run")
def run_endpoint(
    request: CommandRequest,
):

    return JSONResponse(
        status_code=202,
        content=create_mission(
            objective=request.command,
            verify=request.verify,
            remember=request.remember,
        ),
    )


@app.post("/command")
def command_endpoint(
    request: CommandRequest,
):

    return JSONResponse(
        status_code=202,
        content=create_mission(
            objective=request.command,
            verify=request.verify,
            remember=request.remember,
        ),
    )


# ============================================================
# GET MISSION
# ============================================================

@app.get("/mission/{mission_id}")
def get_mission(
    mission_id: str,
):

    rows = db_execute(
        """
        SELECT *
        FROM missions
        WHERE id = ?
        """,
        (
            mission_id,
        ),
        fetch=True,
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    row = rows[0]

    events = db_execute(
        """
        SELECT
            event_type,
            message,
            data_json,
            created_at
        FROM events
        WHERE mission_id = ?
        ORDER BY id ASC
        """,
        (
            mission_id,
        ),
        fetch=True,
    )

    event_list = []

    for event in events:

        try:
            event_data = json.loads(
                event["data_json"]
                or "{}"
            )
        except Exception:
            event_data = {}

        event_list.append(
            {
                "event_type": event["event_type"],
                "message": event["message"],
                "data": event_data,
                "created_at": event["created_at"],
            }
        )

    result = None

    if row["result_json"]:

        try:
            result = json.loads(
                row["result_json"]
            )
        except Exception:
            result = {
                "raw": row["result_json"]
            }

    return {
        "task_id": row["id"],
        "mission_id": row["id"],
        "objective": row["objective"],
        "status": row["status"],
        "version": row["version"],
        "build": row["build"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "error": row["error"],
        "result": result,
        "events": event_list,
    }


# ============================================================
# LIST MISSIONS
# ============================================================

@app.get("/missions")
def list_missions(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    )
):

    rows = db_execute(
        """
        SELECT
            id,
            objective,
            status,
            version,
            build,
            created_at,
            started_at,
            completed_at,
            error
        FROM missions
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            limit,
        ),
        fetch=True,
    )

    return {
        "count": len(rows),
        "missions": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# SOURCES
# ============================================================

@app.get(
    "/mission/{mission_id}/sources"
)
def mission_sources(
    mission_id: str,
):

    rows = db_execute(
        """
        SELECT
            id,
            source_key,
            title,
            url,
            domain,
            provider,
            source_type,
            status,
            quality,
            independent,
            content_chars,
            metadata_json,
            created_at
        FROM sources
        WHERE mission_id = ?
        ORDER BY quality DESC
        """,
        (
            mission_id,
        ),
        fetch=True,
    )

    output = []

    for row in rows:

        item = dict(row)

        try:
            item["metadata"] = json.loads(
                item.pop(
                    "metadata_json"
                )
                or "{}"
            )
        except Exception:
            item["metadata"] = {}

        output.append(item)

    return {
        "mission_id": mission_id,
        "count": len(output),
        "sources": output,
    }


# ============================================================
# CLAIMS
# ============================================================

@app.get(
    "/mission/{mission_id}/claims"
)
def mission_claims(
    mission_id: str,
):

    rows = db_execute(
        """
        SELECT
            id,
            claim,
            status,
            confidence,
            supporting_sources,
            contradicting_sources,
            source_keys_json,
            created_at
        FROM claims
        WHERE mission_id = ?
        ORDER BY id ASC
        """,
        (
            mission_id,
        ),
        fetch=True,
    )

    output = []

    for row in rows:

        item = dict(row)

        try:
            item["source_keys"] = json.loads(
                item.pop(
                    "source_keys_json"
                )
                or "[]"
            )
        except Exception:
            item["source_keys"] = []

        output.append(item)

    return {
        "mission_id": mission_id,
        "count": len(output),
        "claims": output,
    }


# ============================================================
# EVENTS
# ============================================================

@app.get(
    "/mission/{mission_id}/events"
)
def mission_events(
    mission_id: str,
):

    rows = db_execute(
        """
        SELECT
            event_type,
            message,
            data_json,
            created_at
        FROM events
        WHERE mission_id = ?
        ORDER BY id ASC
        """,
        (
            mission_id,
        ),
        fetch=True,
    )

    output = []

    for row in rows:

        try:
            data = json.loads(
                row["data_json"]
                or "{}"
            )
        except Exception:
            data = {}

        output.append(
            {
                "event_type": row["event_type"],
                "message": row["message"],
                "data": data,
                "created_at": row["created_at"],
            }
        )

    return {
        "mission_id": mission_id,
        "count": len(output),
        "events": output,
    }


# ============================================================
# SOURCE VALIDATION ENDPOINT
# ============================================================

@app.post("/validate-source")
def validate_source(
    url: str,
):

    result = fetch_url(
        url
    )

    if result.get("ok"):

        return {
            "valid": True,
            "url": result.get(
                "url"
            ),
            "source_type": result.get(
                "source_type"
            ),
            "content_chars": len(
                result.get(
                    "text",
                    "",
                )
            ),
            "quality": source_quality(
                {
                    **result,
                    "provider": "direct",
                    "evidence_text":
                        result.get(
                            "text",
                            "",
                        ),
                }
            ),
        }

    return {
        "valid": False,
        "url": url,
        "reason": result.get(
            "reason"
        ),
        "status": result.get(
            "status"
        ),
    }


# ============================================================
# GLOBAL JSON ERROR HANDLERS
# ============================================================

@app.exception_handler(
    HTTPException
)
async def http_exception_handler(
    request,
    exc: HTTPException,
):

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": True,
            "status": exc.status_code,
            "detail": exc.detail,
            "path": str(
                request.url.path
            ),
        },
    )


@app.exception_handler(
    Exception
)
async def global_exception_handler(
    request,
    exc: Exception,
):

    error = (
        f"{type(exc).__name__}: "
        f"{str(exc)}"
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "status": 500,
            "message":
                "AI Infinity server error.",
            "detail": error[:1000],
            "path": str(
                request.url.path
            ),
            "version": VERSION,
        },
    )


# ============================================================
# STARTUP / SHUTDOWN
# ============================================================

@app.on_event("startup")
def startup_event():

    init_db()

    print(
        f"{APP_NAME} "
        f"{VERSION} "
        f"{BUILD} "
        f"ONLINE"
    )


@app.on_event("shutdown")
def shutdown_event():

    try:
        EXECUTOR.shutdown(
            wait=False,
            cancel_futures=False,
        )
    except Exception:
        pass


# ============================================================
# LOCAL ENTRYPOINT
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
