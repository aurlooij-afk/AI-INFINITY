"""
AI Infinity
TARGET-2050.21
BUILD: EVIDENCE-INTEGRITY-SOURCE-QUALITY-CORE

Purpose:
    Build a practical autonomous research/verification core.

Key improvements over TARGET-2050.20:
    - Binary/PDF validation
    - PDF extraction when pypdf is available
    - HTML challenge/login/error detection
    - Evidence-text validation
    - Claim sanitization
    - Metadata/evidence separation
    - Underlying-source deduplication
    - Independent-source counting
    - Source-health scoring
    - Claim-level verification
    - Counter-evidence detection
    - Contradiction analysis
    - Explicit verification reasons
    - SQLite persistence
    - Free research providers
    - SSRF/source firewall
    - Safe request limits
    - Backward-compatible practical API routes

Environment:
    PORT
    DATABASE_PATH
    MAX_RESEARCH_SOURCES
    REQUEST_TIMEOUT
    USER_AGENT
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import sqlite3
import time
import traceback
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

VERSION = "TARGET-2050.21"
BUILD = "EVIDENCE-INTEGRITY-SOURCE-QUALITY-CORE"

PORT = int(os.getenv("PORT", "8000"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "/tmp/ai_infinity.db")
MAX_RESEARCH_SOURCES = int(os.getenv("MAX_RESEARCH_SOURCES", "24"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

USER_AGENT = os.getenv(
    "USER_AGENT",
    "AI-Infinity/2050.21 (+https://ai-infinity-ca5e.onrender.com)",
)

APP_TITLE = "AI Infinity"
APP_DESCRIPTION = (
    "Practical autonomous research, evidence integrity, "
    "verification and mission orchestration core."
)

MAX_URL_LENGTH = 2048
MAX_TEXT_LENGTH = 50000
MIN_EVIDENCE_TEXT = 180
MAX_CLAIMS_PER_SOURCE = 8
MAX_CLAIMS_PER_MISSION = 24

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
}

BLOCKED_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
)

CHALLENGE_PATTERNS = [
    r"client challenge",
    r"javascript is disabled",
    r"enable javascript",
    r"checking your browser",
    r"verify you are human",
    r"verify you are a human",
    r"cf-chl",
    r"cloudflare",
    r"attention required",
    r"access denied",
    r"bot detection",
    r"bot detected",
    r"captcha",
    r"security verification",
    r"ddos protection",
    r"ray id",
    r"please wait while we verify",
]

LOGIN_PATTERNS = [
    r"sign in",
    r"log in",
    r"login",
    r"create an account",
]

ERROR_PATTERNS = [
    r"internal server error",
    r"bad gateway",
    r"service unavailable",
    r"gateway timeout",
    r"page not found",
    r"404 not found",
    r"403 forbidden",
    r"request blocked",
]

LOW_VALUE_PATTERNS = [
    r"cookie policy",
    r"privacy policy",
    r"terms of service",
    r"accept cookies",
    r"subscribe to our newsletter",
]


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=VERSION,
)


# ============================================================
# UTILITIES
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def clean_text(value: Any, limit: int = MAX_TEXT_LENGTH) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\x00", " ")
    text = html.unescape(text)

    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()[:limit]


def normalize_title(title: str) -> str:
    title = clean_text(title, 1000).lower()

    title = re.sub(r"https?://\S+", " ", title)
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    title = re.sub(r"\s+", " ", title)

    return title.strip()


def title_fingerprint(title: str) -> str:
    normalized = normalize_title(title)

    if not normalized:
        return ""

    return sha256(normalized)[:24]


def canonicalize_url(url: str) -> str:
    try:
        parsed = urlparse(url)

        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()

        if not scheme or not host:
            return url

        path = parsed.path or "/"

        # Remove tracking parameters.
        ignored = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
            "ref",
        }

        query_items = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in ignored
        ]

        query = urlencode(query_items)

        return urlunparse(
            (
                scheme,
                host,
                path.rstrip("/") or "/",
                "",
                query,
                "",
            )
        )

    except Exception:
        return url


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def source_key(source: Dict[str, Any]) -> str:
    """
    Identifies the underlying evidence object rather than merely
    the provider that returned it.

    DOI is preferred because Crossref/OpenAlex may describe the
    same paper.
    """

    doi = clean_text(source.get("doi", ""), 500).lower()

    if doi:
        return f"doi:{doi}"

    title_fp = title_fingerprint(source.get("title", ""))

    if title_fp:
        return f"title:{title_fp}"

    canonical = canonicalize_url(source.get("url", ""))

    return f"url:{canonical}"


def independent_key(source: Dict[str, Any]) -> str:
    """
    Same underlying work = same independent evidence source.

    Provider diversity does NOT imply source independence.
    """

    return source_key(source)


# ============================================================
# SAFE URL / SSRF FIREWALL
# ============================================================

def validate_public_url(url: str) -> Tuple[bool, str]:
    if not url:
        return False, "empty_url"

    if len(url) > MAX_URL_LENGTH:
        return False, "url_too_long"

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "invalid_url"

    if parsed.scheme not in {"http", "https"}:
        return False, "unsupported_scheme"

    host = (parsed.hostname or "").lower()

    if not host:
        return False, "missing_host"

    if host in BLOCKED_HOSTS:
        return False, "blocked_host"

    if any(host.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        return False, "blocked_private_suffix"

    # Prevent obvious localhost forms.
    if host.startswith("127."):
        return False, "blocked_loopback"

    if host.startswith("10."):
        return False, "blocked_private_network"

    if host.startswith("192.168."):
        return False, "blocked_private_network"

    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
            if 16 <= second <= 31:
                return False, "blocked_private_network"
        except Exception:
            pass

    return True, "ok"


# ============================================================
# HTML PARSER
# ============================================================

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self.skip_depth = 0
        self.skip_tags = {
            "script",
            "style",
            "noscript",
            "svg",
            "canvas",
            "iframe",
            "template",
        }

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.skip_tags:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth == 0:
            text = clean_text(data, 5000)

            if text:
                self.parts.append(text)

    def get_text(self) -> str:
        text = "\n".join(self.parts)
        return clean_text(text, MAX_TEXT_LENGTH)


def html_to_text(content: bytes) -> str:
    try:
        decoded = content.decode("utf-8", errors="replace")
    except Exception:
        decoded = str(content)

    parser = TextExtractor()

    try:
        parser.feed(decoded)
        parser.close()
        return parser.get_text()
    except Exception:
        return clean_text(decoded)


# ============================================================
# CONTENT VALIDATION
# ============================================================

def looks_like_pdf(content: bytes, content_type: str = "") -> bool:
    if content[:5] == b"%PDF-":
        return True

    return "application/pdf" in content_type.lower()


def looks_binary(content: bytes) -> bool:
    if not content:
        return False

    sample = content[:4096]

    nulls = sample.count(b"\x00")

    if nulls > 10:
        return True

    # PDF, ZIP, image and executable signatures.
    signatures = (
        b"\x89PNG",
        b"\xff\xd8\xff",
        b"GIF8",
        b"PK\x03\x04",
        b"MZ",
        b"\x7fELF",
        b"%PDF-",
    )

    return sample.startswith(signatures)


def detect_page_problem(text: str) -> Optional[str]:
    if not text:
        return "empty_content"

    lower = text.lower()

    for pattern in CHALLENGE_PATTERNS:
        if re.search(pattern, lower):
            return "challenge_or_bot_protection"

    for pattern in ERROR_PATTERNS:
        if re.search(pattern, lower):
            return "server_or_http_error_page"

    # Challenge pages are generally very short compared with real
    # research documents.
    if len(text) < 500:
        login_hits = sum(
            1 for pattern in LOGIN_PATTERNS if re.search(pattern, lower)
        )

        if login_hits >= 2:
            return "login_or_authentication_page"

    low_value_hits = sum(
        1 for pattern in LOW_VALUE_PATTERNS if re.search(pattern, lower)
    )

    if len(text) < 1200 and low_value_hits >= 3:
        return "low_value_boilerplate"

    return None


def extract_pdf_text(content: bytes) -> Tuple[str, str]:
    """
    Uses pypdf when installed.

    If unavailable, the PDF is not silently treated as text.
    """

    try:
        from pypdf import PdfReader
    except Exception:
        return "", "pdf_extractor_unavailable"

    try:
        import io

        reader = PdfReader(io.BytesIO(content))

        pages = []

        for page in reader.pages[:30]:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""

            if text:
                pages.append(text)

        result = clean_text("\n".join(pages), MAX_TEXT_LENGTH)

        if len(result) < MIN_EVIDENCE_TEXT:
            return "", "pdf_text_insufficient"

        return result, "ok"

    except Exception:
        return "", "pdf_extraction_failed"


def validate_evidence_text(
    text: str,
    content_type: str = "",
    content: bytes = b"",
) -> Dict[str, Any]:

    result = {
        "valid": False,
        "reason": None,
        "text": "",
        "is_pdf": False,
        "is_binary": False,
        "length": 0,
    }

    if looks_like_pdf(content, content_type):
        result["is_pdf"] = True

        pdf_text, pdf_status = extract_pdf_text(content)

        if pdf_status != "ok":
            result["reason"] = pdf_status
            return result

        text = pdf_text

    elif looks_binary(content):
        result["is_binary"] = True
        result["reason"] = "binary_content"
        return result

    text = clean_text(text, MAX_TEXT_LENGTH)

    result["length"] = len(text)

    if len(text) < MIN_EVIDENCE_TEXT:
        result["reason"] = "insufficient_text"
        return result

    problem = detect_page_problem(text)

    if problem:
        result["reason"] = problem
        return result

    # Reject obvious raw PDF artifacts.
    if text.startswith("%PDF-"):
        result["reason"] = "raw_pdf_bytes"
        return result

    # Require actual alphabetic content.
    alpha = len(re.findall(r"[A-Za-z]", text))

    if alpha < 100:
        result["reason"] = "insufficient_human_readable_text"
        return result

    result["valid"] = True
    result["reason"] = "ok"
    result["text"] = text

    return result


# ============================================================
# DATABASE
# ============================================================

DB_LOCK = None


def db_connection():
    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
        check_same_thread=False,
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_db():
    db = db_connection()

    try:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                version TEXT,
                build TEXT,
                result_json TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                provider TEXT,
                title TEXT,
                url TEXT,
                canonical_url TEXT,
                domain TEXT,
                doi TEXT,
                source_key TEXT,
                source_type TEXT,
                content_type TEXT,
                fetched_status INTEGER,
                content_valid INTEGER,
                rejection_reason TEXT,
                evidence_text TEXT,
                evidence_length INTEGER,
                quality_score REAL,
                health_score REAL,
                independent INTEGER,
                metadata_json TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                source_id TEXT,
                claim_text TEXT,
                claim_hash TEXT,
                status TEXT,
                confidence REAL,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS evidence_links (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                claim_id TEXT,
                source_id TEXT,
                relation TEXT,
                strength REAL,
                explanation TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                event_type TEXT,
                payload_json TEXT,
                created_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_sources_mission
                ON sources(mission_id);

            CREATE INDEX IF NOT EXISTS idx_claims_mission
                ON claims(mission_id);

            CREATE INDEX IF NOT EXISTS idx_claim_hash
                ON claims(claim_hash);
            """
        )

        db.commit()

    finally:
        db.close()


init_db()


def db_execute(query: str, params=(), fetch=False, many=False):
    db = db_connection()

    try:
        cur = db.cursor()

        if many:
            cur.executemany(query, params)
        else:
            cur.execute(query, params)

        if fetch:
            rows = cur.fetchall()
            return [dict(row) for row in rows]

        db.commit()
        return cur.lastrowid

    finally:
        db.close()


def save_event(
    mission_id: str,
    event_type: str,
    payload: Dict[str, Any],
):
    db_execute(
        """
        INSERT INTO events
        (id, mission_id, event_type, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            new_id("event"),
            mission_id,
            event_type,
            json.dumps(payload, ensure_ascii=False),
            utc_now(),
        ),
    )


# ============================================================
# HTTP FETCHER
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,"
            "application/pdf;q=0.8,*/*;q=0.5"
        ),
    }
)


def fetch_url(url: str) -> Dict[str, Any]:

    allowed, reason = validate_public_url(url)

    if not allowed:
        return {
            "ok": False,
            "url": url,
            "status": None,
            "content_type": "",
            "content": b"",
            "text": "",
            "reason": reason,
        }

    try:
        response = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )

        final_url = response.url

        allowed_final, final_reason = validate_public_url(final_url)

        if not allowed_final:
            response.close()

            return {
                "ok": False,
                "url": url,
                "final_url": final_url,
                "status": response.status_code,
                "content_type": "",
                "content": b"",
                "text": "",
                "reason": final_reason,
            }

        content = response.content[:5_000_000]

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        status = response.status_code

        response.close()

        if status < 200 or status >= 400:
            return {
                "ok": False,
                "url": url,
                "final_url": final_url,
                "status": status,
                "content_type": content_type,
                "content": content,
                "text": "",
                "reason": f"http_{status}",
            }

        if looks_like_pdf(content, content_type):
            validation = validate_evidence_text(
                "",
                content_type,
                content,
            )

            return {
                "ok": validation["valid"],
                "url": url,
                "final_url": final_url,
                "status": status,
                "content_type": content_type,
                "content": content,
                "text": validation["text"],
                "reason": validation["reason"],
                "is_pdf": True,
            }

        if "text" not in content_type and "html" not in content_type:
            if looks_binary(content):
                return {
                    "ok": False,
                    "url": url,
                    "final_url": final_url,
                    "status": status,
                    "content_type": content_type,
                    "content": content,
                    "text": "",
                    "reason": "unsupported_binary_content",
                }

        text = html_to_text(content)

        validation = validate_evidence_text(
            text,
            content_type,
            content,
        )

        return {
            "ok": validation["valid"],
            "url": url,
            "final_url": final_url,
            "status": status,
            "content_type": content_type,
            "content": content,
            "text": validation["text"],
            "reason": validation["reason"],
            "is_pdf": validation["is_pdf"],
        }

    except requests.Timeout:
        return {
            "ok": False,
            "url": url,
            "status": None,
            "content_type": "",
            "content": b"",
            "text": "",
            "reason": "timeout",
        }

    except requests.RequestException as exc:
        return {
            "ok": False,
            "url": url,
            "status": None,
            "content_type": "",
            "content": b"",
            "text": "",
            "reason": f"request_error:{type(exc).__name__}",
        }

    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "status": None,
            "content_type": "",
            "content": b"",
            "text": "",
            "reason": f"fetch_error:{type(exc).__name__}",
        }


# ============================================================
# SOURCE QUALITY
# ============================================================

def classify_source(
    source: Dict[str, Any],
) -> str:

    provider = source.get("provider", "").lower()
    url = source.get("url", "").lower()
    title = source.get("title", "").lower()

    if "crossref" in provider or "openalex" in provider:
        return "scholarly_metadata"

    if ".edu" in url or ".gov" in url:
        return "institutional"

    if any(
        x in title
        for x in [
            "systematic review",
            "meta-analysis",
            "randomized",
            "benchmark",
            "evaluation",
            "study",
        ]
    ):
        return "research"

    if "wikipedia" in provider:
        return "encyclopedic"

    return "web"


def source_quality_score(
    source: Dict[str, Any],
) -> float:

    score = 0.0

    if source.get("content_valid"):
        score += 0.30

    if source.get("evidence_length", 0) >= 1000:
        score += 0.15
    elif source.get("evidence_length", 0) >= 300:
        score += 0.08

    source_type = source.get("source_type", "")

    if source_type == "research":
        score += 0.20
    elif source_type == "institutional":
        score += 0.18
    elif source_type == "scholarly_metadata":
        score += 0.10
    elif source_type == "encyclopedic":
        score += 0.08
    else:
        score += 0.04

    if source.get("doi"):
        score += 0.10

    if source.get("fetched_status") == 200:
        score += 0.10

    if source.get("independent"):
        score += 0.05

    return round(min(score, 1.0), 3)


def source_health_score(
    source: Dict[str, Any],
) -> float:

    score = 0.0

    if source.get("url"):
        score += 0.10

    if source.get("fetched_status") == 200:
        score += 0.20

    if source.get("content_type"):
        score += 0.05

    if source.get("content_valid"):
        score += 0.30

    if source.get("evidence_length", 0) >= MIN_EVIDENCE_TEXT:
        score += 0.15

    if source.get("title"):
        score += 0.05

    if source.get("domain"):
        score += 0.05

    if not source.get("rejection_reason"):
        score += 0.10

    return round(min(score, 1.0), 3)


# ============================================================
# RESEARCH PROVIDERS
# ============================================================

def provider_openalex(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    url = (
        "https://api.openalex.org/works?"
        + urlencode(
            {
                "search": query,
                "per-page": min(limit, 25),
            }
        )
    )

    result = fetch_url(url)

    if not result["ok"]:
        return []

    try:
        payload = json.loads(result["content"].decode("utf-8"))

        items = []

        for item in payload.get("results", []):
            primary = item.get("primary_location") or {}

            source_url = (
                primary.get("landing_page_url")
                or primary.get("pdf_url")
                or item.get("doi")
                or ""
            )

            items.append(
                {
                    "provider": "openalex",
                    "title": item.get("title") or "",
                    "url": source_url,
                    "doi": item.get("doi") or "",
                    "abstract": reconstruct_openalex_abstract(
                        item.get("abstract_inverted_index")
                    ),
                    "year": item.get("publication_year"),
                    "type": item.get("type"),
                }
            )

        return items

    except Exception:
        return []


def reconstruct_openalex_abstract(
    inverted: Optional[Dict[str, List[int]]],
) -> str:

    if not inverted:
        return ""

    words = []

    try:
        positions = []

        for word, indexes in inverted.items():
            for index in indexes:
                positions.append((index, word))

        positions.sort()

        words = [word for _, word in positions]

    except Exception:
        return ""

    return clean_text(" ".join(words), 10000)


def provider_crossref(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    url = (
        "https://api.crossref.org/works?"
        + urlencode(
            {
                "query.bibliographic": query,
                "rows": min(limit, 20),
                "select": (
                    "DOI,title,URL,published,"
                    "container-title,type,author"
                ),
            }
        )
    )

    result = fetch_url(url)

    if not result["ok"]:
        return []

    try:
        payload = json.loads(result["content"].decode("utf-8"))

        items = []

        for item in payload.get("message", {}).get("items", []):
            title_list = item.get("title") or []

            title = title_list[0] if title_list else ""

            items.append(
                {
                    "provider": "crossref",
                    "title": title,
                    "url": item.get("URL") or "",
                    "doi": item.get("DOI") or "",
                    "abstract": "",
                    "year": (
                        (
                            item.get("published", {})
                            .get("date-parts", [[None]])[0][0]
                        )
                    ),
                    "type": item.get("type"),
                }
            )

        return items

    except Exception:
        return []


def provider_wikipedia(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    api = (
        "https://en.wikipedia.org/w/api.php?"
        + urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "utf8": 1,
                "srlimit": min(limit, 10),
            }
        )
    )

    result = fetch_url(api)

    if not result["ok"]:
        return []

    try:
        payload = json.loads(result["content"].decode("utf-8"))

        items = []

        for item in payload.get("query", {}).get("search", []):
            title = item.get("title") or ""

            page_url = (
                "https://en.wikipedia.org/wiki/"
                + title.replace(" ", "_")
            )

            items.append(
                {
                    "provider": "wikipedia",
                    "title": title,
                    "url": page_url,
                    "doi": "",
                    "abstract": clean_text(
                        item.get("snippet", ""),
                        2000,
                    ),
                    "year": None,
                    "type": "encyclopedia",
                }
            )

        return items

    except Exception:
        return []


def provider_duckduckgo(
    query: str,
    limit: int = 8,
) -> List[Dict[str, Any]]:

    url = (
        "https://html.duckduckgo.com/html/?"
        + urlencode({"q": query})
    )

    result = fetch_url(url)

    if not result["ok"]:
        return []

    raw = result["content"].decode(
        "utf-8",
        errors="replace",
    )

    items = []

    # Extract result links and titles without depending on
    # BeautifulSoup.
    pattern = re.compile(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.I | re.S,
    )

    for match in pattern.finditer(raw):
        if len(items) >= limit:
            break

        link = html.unescape(match.group(1))

        title = re.sub(
            r"<[^>]+>",
            " ",
            match.group(2),
        )

        title = clean_text(title, 500)

        if not link.startswith("http"):
            continue

        items.append(
            {
                "provider": "duckduckgo",
                "title": title,
                "url": link,
                "doi": "",
                "abstract": "",
                "year": None,
                "type": "web",
            }
        )

    return items


def research_providers(query: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    providers = [
        provider_openalex,
        provider_crossref,
        provider_duckduckgo,
        provider_wikipedia,
    ]

    for provider in providers:
        try:
            results.extend(
                provider(
                    query,
                    max(3, MAX_RESEARCH_SOURCES // len(providers)),
                )
            )
        except Exception:
            continue

    return results


# ============================================================
# SOURCE INGESTION
# ============================================================

def hydrate_source(
    candidate: Dict[str, Any],
) -> Dict[str, Any]:

    source = {
        "provider": candidate.get("provider", "unknown"),
        "title": clean_text(candidate.get("title", ""), 1000),
        "url": candidate.get("url", ""),
        "doi": clean_text(candidate.get("doi", ""), 500),
        "abstract": clean_text(candidate.get("abstract", ""), 10000),
        "year": candidate.get("year"),
        "type": candidate.get("type"),
    }

    source["canonical_url"] = canonicalize_url(source["url"])
    source["domain"] = domain_of(source["url"])
    source["source_key"] = source_key(source)

    fetched = fetch_url(source["url"])

    source["fetched_status"] = fetched.get("status")
    source["content_type"] = fetched.get("content_type", "")
    source["final_url"] = fetched.get("final_url", source["url"])

    if fetched.get("ok"):
        source["evidence_text"] = fetched.get("text", "")
        source["content_valid"] = True
        source["rejection_reason"] = None
    else:
        # Metadata is retained, but metadata is NOT evidence.
        source["evidence_text"] = ""
        source["content_valid"] = False
        source["rejection_reason"] = fetched.get(
            "reason",
            "unusable_source",
        )

    # If the provider gave an abstract, it is useful metadata/evidence
    # only when it is genuine text. It cannot override a corrupted page.
    if (
        not source["evidence_text"]
        and len(source["abstract"]) >= MIN_EVIDENCE_TEXT
    ):
        source["evidence_text"] = source["abstract"]
        source["content_valid"] = True
        source["rejection_reason"] = None
        source["evidence_origin"] = "provider_abstract"
    else:
        source["evidence_origin"] = "fetched_document"

    source["evidence_length"] = len(
        source.get("evidence_text", "")
    )

    source["source_type"] = classify_source(source)

    source["independent"] = True

    source["health_score"] = source_health_score(source)

    source["quality_score"] = source_quality_score(source)

    return source


def deduplicate_sources(
    sources: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    grouped: Dict[str, Dict[str, Any]] = {}

    for source in sources:
        key = independent_key(source)

        if not key:
            continue

        existing = grouped.get(key)

        if existing is None:
            grouped[key] = source
            continue

        # Prefer the version with actual readable evidence.
        existing_valid = existing.get("content_valid", False)
        current_valid = source.get("content_valid", False)

        if current_valid and not existing_valid:
            grouped[key] = source
            continue

        # Otherwise prefer higher quality.
        if (
            source.get("quality_score", 0)
            > existing.get("quality_score", 0)
        ):
            grouped[key] = source

    result = list(grouped.values())

    for source in result:
        source["independent"] = True

    return result


# ============================================================
# CLAIM EXTRACTION
# ============================================================

def sentence_split(text: str) -> List[str]:

    text = clean_text(text)

    if not text:
        return []

    text = re.sub(
        r"(?<=[.!?])\s+",
        "\n",
        text,
    )

    return [
        clean_text(x, 1500)
        for x in text.split("\n")
        if clean_text(x)
    ]


def valid_claim(sentence: str) -> bool:

    sentence = clean_text(sentence)

    if len(sentence) < 60:
        return False

    if len(sentence) > 1200:
        return False

    # Never allow document-format garbage into the claim graph.
    bad_fragments = [
        "%pdf-",
        "client challenge",
        "javascript is disabled",
        "enable javascript",
        "cloudflare",
        "captcha",
        "cookie policy",
        "privacy policy",
        "terms of service",
        "sign in",
        "log in",
        "ray id",
        "doctype html",
        "<html",
        "binary",
    ]

    lower = sentence.lower()

    if any(fragment in lower for fragment in bad_fragments):
        return False

    alpha = len(re.findall(r"[A-Za-z]", sentence))

    if alpha < 40:
        return False

    # Claims should contain at least one assertion-like signal.
    assertion_terms = [
        "found",
        "showed",
        "shows",
        "suggest",
        "suggests",
        "demonstrate",
        "demonstrates",
        "reported",
        "observed",
        "increased",
        "decreased",
        "improved",
        "failed",
        "failure",
        "effective",
        "ineffective",
        "accuracy",
        "performance",
        "reliability",
        "limitation",
        "limitations",
        "risk",
        "error",
        "result",
        "results",
        "evidence",
        "study",
        "experiment",
        "evaluation",
        "benchmark",
        "agents",
        "autonomous",
    ]

    return any(term in lower for term in assertion_terms)


def extract_claims(
    source: Dict[str, Any],
) -> List[str]:

    if not source.get("content_valid"):
        return []

    text = source.get("evidence_text", "")

    if len(text) < MIN_EVIDENCE_TEXT:
        return []

    sentences = sentence_split(text)

    claims = []

    for sentence in sentences:

        if not valid_claim(sentence):
            continue

        normalized = re.sub(
            r"\s+",
            " ",
            sentence,
        ).strip()

        claim_hash = sha256(normalized.lower())

        if any(
            sha256(existing.lower()) == claim_hash
            for existing in claims
        ):
            continue

        claims.append(normalized)

        if len(claims) >= MAX_CLAIMS_PER_SOURCE:
            break

    return claims


# ============================================================
# CLAIM RELEVANCE
# ============================================================

def tokenize(text: str) -> set:
    return set(
        x
        for x in re.findall(
            r"[a-zA-Z][a-zA-Z0-9_-]{2,}",
            text.lower(),
        )
    )


def relevance_score(
    objective: str,
    claim: str,
) -> float:

    objective_tokens = tokenize(objective)
    claim_tokens = tokenize(claim)

    if not objective_tokens or not claim_tokens:
        return 0.0

    intersection = objective_tokens.intersection(
        claim_tokens
    )

    score = len(intersection) / math.sqrt(
        len(objective_tokens) * len(claim_tokens)
    )

    return round(min(score * 2.5, 1.0), 3)


# ============================================================
# CONTRADICTION / SUPPORT
# ============================================================

NEGATION_TERMS = {
    "not",
    "no",
    "never",
    "failed",
    "failure",
    "unable",
    "cannot",
    "insufficient",
    "ineffective",
    "unreliable",
    "limitation",
    "limitations",
    "weak",
    "poor",
    "error",
    "errors",
    "risk",
    "risks",
}


def claim_relation(
    claim_a: str,
    claim_b: str,
) -> Tuple[str, float]:

    a = tokenize(claim_a)
    b = tokenize(claim_b)

    if not a or not b:
        return "unrelated", 0.0

    overlap = len(a.intersection(b)) / max(
        1,
        min(len(a), len(b)),
    )

    if overlap < 0.20:
        return "unrelated", round(overlap, 3)

    neg_a = len(a.intersection(NEGATION_TERMS))
    neg_b = len(b.intersection(NEGATION_TERMS))

    if abs(neg_a - neg_b) >= 1 and overlap >= 0.30:
        return "contradicts", round(overlap, 3)

    return "supports", round(overlap, 3)


# ============================================================
# MISSION MODELS
# ============================================================

class MissionRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=10000)
    research: bool = True
    verify: bool = True
    remember: bool = True


class ResearchRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=10000)
    verify: bool = True


class CommandRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=10000)


# ============================================================
# MISSION ENGINE
# ============================================================

def create_mission(objective: str) -> str:

    mission_id = new_id("mission")

    now = utc_now()

    db_execute(
        """
        INSERT INTO missions
        (id, objective, status, version, build, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "running",
            VERSION,
            BUILD,
            now,
            now,
        ),
    )

    return mission_id


def update_mission(
    mission_id: str,
    status: str,
    result: Optional[Dict[str, Any]] = None,
):

    db_execute(
        """
        UPDATE missions
        SET status = ?,
            result_json = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            json.dumps(result, ensure_ascii=False)
            if result is not None
            else None,
            utc_now(),
            mission_id,
        ),
    )


def save_source(
    mission_id: str,
    source: Dict[str, Any],
) -> str:

    source_id = new_id("source")

    db_execute(
        """
        INSERT INTO sources (
            id,
            mission_id,
            provider,
            title,
            url,
            canonical_url,
            domain,
            doi,
            source_key,
            source_type,
            content_type,
            fetched_status,
            content_valid,
            rejection_reason,
            evidence_text,
            evidence_length,
            quality_score,
            health_score,
            independent,
            metadata_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            mission_id,
            source.get("provider"),
            source.get("title"),
            source.get("url"),
            source.get("canonical_url"),
            source.get("domain"),
            source.get("doi"),
            source.get("source_key"),
            source.get("source_type"),
            source.get("content_type"),
            source.get("fetched_status"),
            int(bool(source.get("content_valid"))),
            source.get("rejection_reason"),
            source.get("evidence_text", "")[:MAX_TEXT_LENGTH],
            source.get("evidence_length", 0),
            source.get("quality_score", 0),
            source.get("health_score", 0),
            int(bool(source.get("independent"))),
            json.dumps(
                {
                    "year": source.get("year"),
                    "type": source.get("type"),
                    "final_url": source.get("final_url"),
                    "evidence_origin": source.get(
                        "evidence_origin"
                    ),
                },
                ensure_ascii=False,
            ),
            utc_now(),
        ),
    )

    return source_id


def save_claim(
    mission_id: str,
    source_id: str,
    claim_text: str,
) -> str:

    claim_id = new_id("claim")

    db_execute(
        """
        INSERT INTO claims (
            id,
            mission_id,
            source_id,
            claim_text,
            claim_hash,
            status,
            confidence,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            claim_id,
            mission_id,
            source_id,
            claim_text,
            sha256(claim_text.lower()),
            "unverified",
            0.0,
            utc_now(),
        ),
    )

    return claim_id


def save_evidence_link(
    mission_id: str,
    claim_id: str,
    source_id: str,
    relation: str,
    strength: float,
    explanation: str,
):

    db_execute(
        """
        INSERT INTO evidence_links (
            id,
            mission_id,
            claim_id,
            source_id,
            relation,
            strength,
            explanation,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("link"),
            mission_id,
            claim_id,
            source_id,
            relation,
            strength,
            explanation,
            utc_now(),
        ),
    )


# ============================================================
# RESEARCH QUERY GENERATION
# ============================================================

def build_queries(objective: str) -> List[str]:

    objective = clean_text(objective, 3000)

    queries = [
        objective,
        f"{objective} evidence study",
        f"{objective} benchmark evaluation",
        f"{objective} limitations failure",
        f"{objective} reliability independent study",
    ]

    result = []

    seen = set()

    for query in queries:
        normalized = query.lower().strip()

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(query)

    return result


def collect_candidates(
    objective: str,
) -> List[Dict[str, Any]]:

    candidates = []

    for query in build_queries(objective):

        try:
            batch = research_providers(query)

            candidates.extend(batch)

        except Exception:
            continue

        if len(candidates) >= MAX_RESEARCH_SOURCES * 2:
            break

    return candidates


# ============================================================
# EVIDENCE GRAPH
# ============================================================

def build_evidence_graph(
    objective: str,
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:

    nodes = []

    for source in sources:

        if not source.get("content_valid"):
            continue

        claims = extract_claims(source)

        for claim in claims:

            relevance = relevance_score(
                objective,
                claim,
            )

            if relevance < 0.05:
                continue

            nodes.append(
                {
                    "source_key": source["source_key"],
                    "domain": source["domain"],
                    "provider": source["provider"],
                    "claim": claim,
                    "relevance": relevance,
                    "quality": source["quality_score"],
                }
            )

    edges = []

    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):

            relation, strength = claim_relation(
                nodes[i]["claim"],
                nodes[j]["claim"],
            )

            if relation == "unrelated":
                continue

            edges.append(
                {
                    "from": i,
                    "to": j,
                    "relation": relation,
                    "strength": strength,
                }
            )

    independent_sources = {
        node["source_key"]
        for node in nodes
    }

    domains = {
        node["domain"]
        for node in nodes
        if node["domain"]
    }

    providers = {
        node["provider"]
        for node in nodes
    }

    support_edges = [
        edge
        for edge in edges
        if edge["relation"] == "supports"
    ]

    contradiction_edges = [
        edge
        for edge in edges
        if edge["relation"] == "contradicts"
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "independent_sources": len(independent_sources),
        "independent_domains": len(domains),
        "provider_diversity": len(providers),
        "support_edges": len(support_edges),
        "contradiction_edges": len(contradiction_edges),
    }


# ============================================================
# CLAIM VERIFICATION
# ============================================================

def verify_claims(
    objective: str,
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:

    claim_records = []

    all_claims = []

    for source in sources:

        if not source.get("content_valid"):
            continue

        claims = extract_claims(source)

        for claim in claims:

            relevance = relevance_score(
                objective,
                claim,
            )

            if relevance < 0.05:
                continue

            all_claims.append(
                {
                    "claim": claim,
                    "source": source,
                    "relevance": relevance,
                }
            )

    # Deduplicate exact claims.
    deduped = {}

    for item in all_claims:
        key = sha256(
            re.sub(
                r"\s+",
                " ",
                item["claim"].lower(),
            )
        )

        current = deduped.get(key)

        if current is None:
            deduped[key] = item
            continue

        if (
            item["source"]["quality_score"]
            > current["source"]["quality_score"]
        ):
            deduped[key] = item

    all_claims = list(deduped.values())

    for item in all_claims[:MAX_CLAIMS_PER_MISSION]:

        claim = item["claim"]
        source = item["source"]

        supporting = []
        contradicting = []

        for other in sources:

            if other is source:
                continue

            if not other.get("content_valid"):
                continue

            other_claims = extract_claims(other)

            for other_claim in other_claims:

                relation, strength = claim_relation(
                    claim,
                    other_claim,
                )

                if strength < 0.30:
                    continue

                record = {
                    "source_key": other["source_key"],
                    "domain": other["domain"],
                    "provider": other["provider"],
                    "claim": other_claim,
                    "strength": strength,
                    "quality": other["quality_score"],
                }

                if relation == "supports":
                    supporting.append(record)

                elif relation == "contradicts":
                    contradicting.append(record)

        independent_support = {
            x["source_key"]
            for x in supporting
            if x["source_key"] != source["source_key"]
        }

        independent_contradiction = {
            x["source_key"]
            for x in contradicting
            if x["source_key"] != source["source_key"]
        }

        if (
            len(independent_support) >= 2
            and not independent_contradiction
        ):
            status = "verified"
            confidence = min(
                0.95,
                0.55
                + 0.12 * len(independent_support)
                + 0.15 * source["quality_score"],
            )

        elif (
            len(independent_support) >= 1
            and not independent_contradiction
        ):
            status = "uncertain"
            confidence = min(
                0.75,
                0.40
                + 0.15 * len(independent_support)
                + 0.10 * source["quality_score"],
            )

        elif independent_contradiction:
            status = "insufficient"
            confidence = 0.35

        else:
            status = "insufficient"
            confidence = 0.25

        claim_records.append(
            {
                "claim": claim,
                "status": status,
                "confidence": round(confidence, 3),
                "primary_source": {
                    "source_key": source["source_key"],
                    "title": source["title"],
                    "domain": source["domain"],
                    "provider": source["provider"],
                    "quality": source["quality_score"],
                },
                "supporting_sources": supporting[:6],
                "contradicting_sources": contradicting[:6],
                "independent_support": len(
                    independent_support
                ),
                "independent_contradiction": len(
                    independent_contradiction
                ),
            }
        )

    verified = [
        x for x in claim_records
        if x["status"] == "verified"
    ]

    uncertain = [
        x for x in claim_records
        if x["status"] == "uncertain"
    ]

    insufficient = [
        x for x in claim_records
        if x["status"] == "insufficient"
    ]

    return {
        "claims": claim_records,
        "total": len(claim_records),
        "verified": len(verified),
        "uncertain": len(uncertain),
        "insufficient": len(insufficient),
        "confidence": round(
            (
                sum(x["confidence"] for x in claim_records)
                / len(claim_records)
            )
            if claim_records
            else 0.0,
            3,
        ),
    }


# ============================================================
# COUNTER-EVIDENCE
# ============================================================

def counter_queries(objective: str) -> List[str]:

    return [
        f"{objective} failure",
        f"{objective} limitations",
        f"{objective} criticism",
        f"{objective} negative results",
        f"{objective} benchmark weaknesses",
        f"{objective} replication failure",
        f"{objective} real world failures",
    ]


def run_countercheck(
    objective: str,
    existing_sources: List[Dict[str, Any]],
) -> Dict[str, Any]:

    candidates = []

    for query in counter_queries(objective):

        try:
            candidates.extend(
                research_providers(query)
            )
        except Exception:
            continue

        if len(candidates) >= 20:
            break

    hydrated = []

    for candidate in candidates:

        try:
            source = hydrate_source(candidate)

            if source.get("content_valid"):
                hydrated.append(source)

        except Exception:
            continue

    merged = deduplicate_sources(
        existing_sources + hydrated
    )

    counter_sources = [
        source
        for source in merged
        if any(
            word in (
                source.get("evidence_text", "")
                .lower()
            )
            for word in [
                "failure",
                "limitation",
                "error",
                "risk",
                "unreliable",
                "weakness",
                "negative",
                "cannot",
                "unable",
            ]
        )
    ]

    return {
        "completed": True,
        "candidate_count": len(candidates),
        "usable_counter_sources": len(counter_sources),
        "sources": counter_sources,
    }


# ============================================================
# SYNTHESIS
# ============================================================

def calculate_research_strength(
    sources: List[Dict[str, Any]],
    verification: Dict[str, Any],
    graph: Dict[str, Any],
) -> float:

    usable = [
        source
        for source in sources
        if source.get("content_valid")
    ]

    if not usable:
        return 0.0

    avg_quality = sum(
        source.get("quality_score", 0)
        for source in usable
    ) / len(usable)

    independent = graph["independent_sources"]

    domain_factor = min(
        independent / 4,
        1.0,
    )

    verified_factor = (
        verification["verified"]
        / max(1, verification["total"])
    )

    contradiction_penalty = min(
        graph["contradiction_edges"] * 0.03,
        0.25,
    )

    strength = (
        0.30 * avg_quality
        + 0.25 * domain_factor
        + 0.30 * verified_factor
        + 0.15 * verification["confidence"]
        - contradiction_penalty
    )

    return round(
        max(0.0, min(1.0, strength)),
        3,
    )


def verification_decision(
    sources: List[Dict[str, Any]],
    verification: Dict[str, Any],
    graph: Dict[str, Any],
) -> Tuple[str, str]:

    usable = [
        source
        for source in sources
        if source.get("content_valid")
    ]

    if not usable:
        return (
            "REJECTED",
            "no_usable_evidence_sources",
        )

    if graph["independent_sources"] < 2:
        return (
            "INSUFFICIENT",
            "insufficient_independent_evidence",
        )

    if verification["verified"] == 0:
        if verification["uncertain"] > 0:
            return (
                "UNCERTAIN",
                "claims_have_partial_support_but_do_not_meet_verification_threshold",
            )

        return (
            "INSUFFICIENT",
            "no_claim_reached_verification_threshold",
        )

    if graph["contradiction_edges"] > 5:
        return (
            "UNCERTAIN",
            "substantial_contradictory_evidence",
        )

    return (
        "VERIFIED",
        "multiple_independent_sources_support_key_claims",
    )


# ============================================================
# FULL MISSION
# ============================================================

def execute_mission(
    objective: str,
    verify: bool = True,
) -> Dict[str, Any]:

    mission_id = create_mission(objective)

    started = time.time()

    save_event(
        mission_id,
        "mission_started",
        {
            "objective": objective,
            "version": VERSION,
            "build": BUILD,
        },
    )

    try:

        # ----------------------------------------------------
        # UNDERSTAND
        # ----------------------------------------------------

        save_event(
            mission_id,
            "understand",
            {
                "objective": objective,
            },
        )

        # ----------------------------------------------------
        # RESEARCH
        # ----------------------------------------------------

        candidates = collect_candidates(objective)

        save_event(
            mission_id,
            "research_candidates",
            {
                "count": len(candidates),
            },
        )

        # ----------------------------------------------------
        # SOURCE INGESTION
        # ----------------------------------------------------

        hydrated = []

        rejected_sources = []

        seen_candidate_keys = set()

        for candidate in candidates:

            # Build temporary identity before fetching.
            temp = {
                "title": candidate.get("title", ""),
                "url": candidate.get("url", ""),
                "doi": candidate.get("doi", ""),
            }

            key = source_key(temp)

            if key in seen_candidate_keys:
                continue

            seen_candidate_keys.add(key)

            source = hydrate_source(candidate)

            if source.get("content_valid"):
                hydrated.append(source)
            else:
                rejected_sources.append(
                    {
                        "provider": source.get("provider"),
                        "title": source.get("title"),
                        "url": source.get("url"),
                        "reason": source.get(
                            "rejection_reason"
                        ),
                    }
                )

            if len(hydrated) >= MAX_RESEARCH_SOURCES:
                break

        # ----------------------------------------------------
        # DEDUPLICATION
        # ----------------------------------------------------

        sources = deduplicate_sources(hydrated)

        # Recalculate independence.
        source_keys = {
            source["source_key"]
            for source in sources
        }

        for source in sources:
            source["independent"] = (
                source["source_key"] in source_keys
            )
            source["quality_score"] = source_quality_score(
                source
            )
            source["health_score"] = source_health_score(
                source
            )

        # ----------------------------------------------------
        # SAVE SOURCES
        # ----------------------------------------------------

        source_ids = {}

        for source in sources:
            source_ids[source["source_key"]] = save_source(
                mission_id,
                source,
            )

        # ----------------------------------------------------
        # EVIDENCE GRAPH
        # ----------------------------------------------------

        graph = build_evidence_graph(
            objective,
            sources,
        )

        save_event(
            mission_id,
            "evidence_graph",
            {
                "node_count": graph["node_count"],
                "edge_count": graph["edge_count"],
                "independent_sources": graph[
                    "independent_sources"
                ],
                "independent_domains": graph[
                    "independent_domains"
                ],
                "provider_diversity": graph[
                    "provider_diversity"
                ],
            },
        )

        # ----------------------------------------------------
        # CLAIMS
        # ----------------------------------------------------

        total_claim_count = 0

        for source in sources:

            source_id = source_ids.get(
                source["source_key"]
            )

            if not source_id:
                continue

            claims = extract_claims(source)

            for claim in claims[:MAX_CLAIMS_PER_SOURCE]:

                if total_claim_count >= MAX_CLAIMS_PER_MISSION:
                    break

                save_claim(
                    mission_id,
                    source_id,
                    claim,
                )

                total_claim_count += 1

        # ----------------------------------------------------
        # VERIFICATION
        # ----------------------------------------------------

        if verify:
            verification = verify_claims(
                objective,
                sources,
            )
        else:
            verification = {
                "claims": [],
                "total": 0,
                "verified": 0,
                "uncertain": 0,
                "insufficient": 0,
                "confidence": 0,
            }

        # ----------------------------------------------------
        # COUNTERCHECK
        # ----------------------------------------------------

        countercheck = run_countercheck(
            objective,
            sources,
        )

        # ----------------------------------------------------
        # FINAL GRAPH AFTER COUNTERCHECK
        # ----------------------------------------------------

        combined_sources = deduplicate_sources(
            sources + countercheck.get("sources", [])
        )

        final_graph = build_evidence_graph(
            objective,
            combined_sources,
        )

        # ----------------------------------------------------
        # FINAL VERIFICATION
        # ----------------------------------------------------

        final_verification = verify_claims(
            objective,
            combined_sources,
        )

        # ----------------------------------------------------
        # DECISION
        # ----------------------------------------------------

        decision, reason = verification_decision(
            combined_sources,
            final_verification,
            final_graph,
        )

        research_strength = calculate_research_strength(
            combined_sources,
            final_verification,
            final_graph,
        )

        usable_sources = [
            source
            for source in combined_sources
            if source.get("content_valid")
        ]

        corrupted_sources = [
            item
            for item in rejected_sources
            if item.get("reason")
        ]

        # ----------------------------------------------------
        # CRITIQUE
        # ----------------------------------------------------

        critique = []

        if final_graph["independent_sources"] < 2:
            critique.append(
                "Evidence comes from fewer than two independent underlying sources."
            )

        if final_graph["independent_domains"] < 2:
            critique.append(
                "Evidence diversity across independent domains is limited."
            )

        if final_verification["verified"] == 0:
            critique.append(
                "No claim reached the strict verification threshold."
            )

        if final_graph["contradiction_edges"] > 0:
            critique.append(
                "Contradictory or potentially conflicting evidence was detected."
            )

        if corrupted_sources:
            critique.append(
                f"{len(corrupted_sources)} discovered sources were rejected before claim extraction."
            )

        if not critique:
            critique.append(
                "No major evidence-integrity failure detected."
            )

        # ----------------------------------------------------
        # NEXT ACTION
        # ----------------------------------------------------

        if decision == "VERIFIED":
            next_action = (
                "Proceed using only claims linked to verified independent evidence; "
                "retain contradictory evidence in the audit trail."
            )

        elif reason == "insufficient_independent_evidence":
            next_action = (
                "Collect additional independent primary or high-quality secondary "
                "sources before relying on the affected claims."
            )

        elif final_graph["contradiction_edges"] > 0:
            next_action = (
                "Investigate contradictory evidence and re-run claim-level verification."
            )

        else:
            next_action = (
                "Collect stronger primary evidence and repeat verification."
            )

        # ----------------------------------------------------
        # SOURCE REPORT
        # ----------------------------------------------------

        source_report = []

        for source in combined_sources[:MAX_RESEARCH_SOURCES]:

            source_report.append(
                {
                    "provider": source.get("provider"),
                    "title": source.get("title"),
                    "url": source.get("url"),
                    "domain": source.get("domain"),
                    "doi": source.get("doi"),
                    "source_key": source.get("source_key"),
                    "source_type": source.get("source_type"),
                    "content_valid": bool(
                        source.get("content_valid")
                    ),
                    "evidence_origin": source.get(
                        "evidence_origin"
                    ),
                    "evidence_length": source.get(
                        "evidence_length",
                        0,
                    ),
                    "health_score": source.get(
                        "health_score",
                        0,
                    ),
                    "quality_score": source.get(
                        "quality_score",
                        0,
                    ),
                    "rejection_reason": source.get(
                        "rejection_reason"
                    ),
                }
            )

        duration = round(
            time.time() - started,
            3,
        )

        result = {
            "task_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "build": BUILD,
            "objective": objective,

            "pipeline": [
                "understand",
                "research",
                "source_validation",
                "deduplication",
                "evidence_graph",
                "claim_extraction",
                "verification",
                "countercheck",
                "contradiction_analysis",
                "critique",
                "synthesis",
                "next_cycle",
            ],

            "research": {
                "candidate_sources": len(candidates),
                "usable_sources": len(usable_sources),
                "rejected_sources": len(rejected_sources),
                "corrupted_or_unusable_sources": len(
                    corrupted_sources
                ),
                "provider_diversity": final_graph[
                    "provider_diversity"
                ],
                "independent_underlying_sources": final_graph[
                    "independent_sources"
                ],
                "independent_domains": final_graph[
                    "independent_domains"
                ],
            },

            "source_quality": {
                "average_quality": round(
                    (
                        sum(
                            x.get(
                                "quality_score",
                                0,
                            )
                            for x in usable_sources
                        )
                        / len(usable_sources)
                    )
                    if usable_sources
                    else 0,
                    3,
                ),
                "average_health": round(
                    (
                        sum(
                            x.get(
                                "health_score",
                                0,
                            )
                            for x in usable_sources
                        )
                        / len(usable_sources)
                    )
                    if usable_sources
                    else 0,
                    3,
                ),
                "independence_rule": (
                    "DOI/title underlying-work identity; "
                    "provider diversity does not equal source independence"
                ),
            },

            "evidence_graph": {
                "nodes": final_graph["node_count"],
                "edges": final_graph["edge_count"],
                "support_edges": final_graph[
                    "support_edges"
                ],
                "contradiction_edges": final_graph[
                    "contradiction_edges"
                ],
            },

            "claims": {
                "extracted": final_verification["total"],
                "verified": final_verification["verified"],
                "uncertain": final_verification["uncertain"],
                "insufficient": final_verification[
                    "insufficient"
                ],
            },

            "verification": {
                "decision": decision,
                "verified": (
                    decision == "VERIFIED"
                ),
                "reason": reason,
                "confidence": final_verification[
                    "confidence"
                ],
                "threshold": {
                    "minimum_independent_support": 2,
                    "contradiction_awareness": True,
                    "source_quality_required": True,
                },
            },

            "countercheck": {
                "completed": countercheck[
                    "completed"
                ],
                "candidate_count": countercheck[
                    "candidate_count"
                ],
                "usable_counter_sources": countercheck[
                    "usable_counter_sources"
                ],
            },

            "research_strength": research_strength,

            "critique": critique,

            "synthesis": {
                "conclusion": (
                    "Evidence supports the conclusion."
                    if decision == "VERIFIED"
                    else (
                        "Evidence is not sufficient "
                        "for a strong conclusion."
                    )
                ),
                "mission_score": round(
                    research_strength
                    * final_verification["confidence"],
                    3,
                ),
                "verified_claims": final_verification[
                    "verified"
                ],
                "unsupported_claims": final_verification[
                    "insufficient"
                ],
                "uncertain_claims": final_verification[
                    "uncertain"
                ],
                "evidence_count": len(usable_sources),
                "independent_sources": final_graph[
                    "independent_sources"
                ],
                "independent_domains": final_graph[
                    "independent_domains"
                ],
                "contradictions": final_graph[
                    "contradiction_edges"
                ],
            },

            "next_cycle": {
                "action": next_action,
                "automatic_research_allowed": True,
                "requires_stronger_evidence": (
                    decision != "VERIFIED"
                ),
            },

            "sources": source_report,

            "rejected_sources": rejected_sources[
                :30
            ],

            "claims_detail": final_verification[
                "claims"
            ][
                :MAX_CLAIMS_PER_MISSION
            ],

            "performance": {
                "duration_seconds": duration,
            },
        }

        # ----------------------------------------------------
        # SAVE FINAL RESULT
        # ----------------------------------------------------

        update_mission(
            mission_id,
            "completed",
            result,
        )

        save_event(
            mission_id,
            "mission_completed",
            {
                "decision": decision,
                "research_strength": research_strength,
                "duration_seconds": duration,
            },
        )

        return result

    except Exception as exc:

        error = {
            "task_id": mission_id,
            "status": "failed",
            "version": VERSION,
            "build": BUILD,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc()[-5000:],
        }

        update_mission(
            mission_id,
            "failed",
            error,
        )

        save_event(
            mission_id,
            "mission_failed",
            error,
        )

        return error


# ============================================================
# API ROUTES
# ============================================================

@app.get("/")
def root():

    return {
        "name": APP_TITLE,
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "mission_engine": "ready",
        "evidence_integrity": "enabled",
        "source_validation": "enabled",
        "claim_verification": "enabled",
        "countercheck": "enabled",
        "database": "sqlite",
    }


@app.get("/health")
def health():

    try:
        db = db_connection()

        try:
            db.execute("SELECT 1")
        finally:
            db.close()

        database = "ok"

    except Exception:
        database = "error"

    return {
        "status": "healthy"
        if database == "ok"
        else "degraded",
        "version": VERSION,
        "build": BUILD,
        "database": database,
        "timestamp": utc_now(),
    }


@app.get("/status")
def status():

    try:
        missions = db_execute(
            "SELECT COUNT(*) AS count FROM missions",
            fetch=True,
        )[0]["count"]

        sources = db_execute(
            "SELECT COUNT(*) AS count FROM sources",
            fetch=True,
        )[0]["count"]

        claims = db_execute(
            "SELECT COUNT(*) AS count FROM claims",
            fetch=True,
        )[0]["count"]

    except Exception:
        missions = 0
        sources = 0
        claims = 0

    return {
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "research",
            "source_firewall",
            "content_validation",
            "pdf_detection",
            "pdf_text_extraction",
            "challenge_detection",
            "metadata_evidence_separation",
            "source_deduplication",
            "independent_source_analysis",
            "evidence_graph",
            "claim_extraction",
            "claim_verification",
            "counter_evidence",
            "contradiction_analysis",
            "mission_persistence",
            "sqlite_memory",
        ],
        "database_counts": {
            "missions": missions,
            "sources": sources,
            "claims": claims,
        },
    }


@app.get("/capabilities")
def capabilities():

    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            {
                "name": "research",
                "status": "enabled",
            },
            {
                "name": "source_validation",
                "status": "enabled",
            },
            {
                "name": "binary_rejection",
                "status": "enabled",
            },
            {
                "name": "pdf_extraction",
                "status": "optional_pypdf",
            },
            {
                "name": "challenge_detection",
                "status": "enabled",
            },
            {
                "name": "claim_sanitization",
                "status": "enabled",
            },
            {
                "name": "evidence_graph",
                "status": "enabled",
            },
            {
                "name": "counter_evidence",
                "status": "enabled",
            },
            {
                "name": "verification",
                "status": "enabled",
            },
            {
                "name": "sqlite_persistence",
                "status": "enabled",
            },
        ],
    }


@app.post("/mission")
def mission(request: MissionRequest):

    return execute_mission(
        request.objective,
        verify=request.verify,
    )


@app.post("/missions")
def missions(request: MissionRequest):

    return execute_mission(
        request.objective,
        verify=request.verify,
    )


@app.post("/research")
def research(request: ResearchRequest):

    return execute_mission(
        request.objective,
        verify=request.verify,
    )


@app.post("/run")
def run(request: CommandRequest):

    return execute_mission(
        request.command,
        verify=True,
    )


@app.post("/command")
def command(request: CommandRequest):

    return execute_mission(
        request.command,
        verify=True,
    )


@app.get("/mission/{mission_id}")
def get_mission(mission_id: str):

    rows = db_execute(
        """
        SELECT *
        FROM missions
        WHERE id = ?
        """,
        (mission_id,),
        fetch=True,
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    mission_data = rows[0]

    result = None

    if mission_data.get("result_json"):
        try:
            result = json.loads(
                mission_data["result_json"]
            )
        except Exception:
            result = mission_data["result_json"]

    return {
        "id": mission_data["id"],
        "objective": mission_data["objective"],
        "status": mission_data["status"],
        "version": mission_data["version"],
        "build": mission_data["build"],
        "created_at": mission_data["created_at"],
        "updated_at": mission_data["updated_at"],
        "result": result,
    }


@app.get("/missions")
def list_missions(limit: int = 20):

    limit = max(1, min(limit, 100))

    rows = db_execute(
        """
        SELECT
            id,
            objective,
            status,
            version,
            build,
            created_at,
            updated_at
        FROM missions
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
        fetch=True,
    )

    return {
        "count": len(rows),
        "missions": rows,
    }


@app.get("/mission/{mission_id}/sources")
def mission_sources(mission_id: str):

    rows = db_execute(
        """
        SELECT
            id,
            provider,
            title,
            url,
            canonical_url,
            domain,
            doi,
            source_key,
            source_type,
            fetched_status,
            content_valid,
            rejection_reason,
            evidence_length,
            quality_score,
            health_score,
            independent,
            created_at
        FROM sources
        WHERE mission_id = ?
        ORDER BY quality_score DESC
        """,
        (mission_id,),
        fetch=True,
    )

    return {
        "mission_id": mission_id,
        "count": len(rows),
        "sources": rows,
    }


@app.get("/mission/{mission_id}/claims")
def mission_claims(mission_id: str):

    rows = db_execute(
        """
        SELECT
            id,
            source_id,
            claim_text,
            claim_hash,
            status,
            confidence,
            created_at
        FROM claims
        WHERE mission_id = ?
        ORDER BY created_at
        """,
        (mission_id,),
        fetch=True,
    )

    return {
        "mission_id": mission_id,
        "count": len(rows),
        "claims": rows,
    }


@app.get("/mission/{mission_id}/events")
def mission_events(mission_id: str):

    rows = db_execute(
        """
        SELECT
            id,
            event_type,
            payload_json,
            created_at
        FROM events
        WHERE mission_id = ?
        ORDER BY created_at
        """,
        (mission_id,),
        fetch=True,
    )

    events = []

    for row in rows:

        payload = row["payload_json"]

        try:
            payload = json.loads(payload)
        except Exception:
            pass

        events.append(
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "payload": payload,
                "created_at": row["created_at"],
            }
        )

    return {
        "mission_id": mission_id,
        "count": len(events),
        "events": events,
    }


# ============================================================
# OPTIONAL DIRECT URL VALIDATION
# ============================================================

class URLCheckRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)


@app.post("/validate-source")
def validate_source(request: URLCheckRequest):

    result = fetch_url(request.url)

    return {
        "url": request.url,
        "valid": result.get("ok", False),
        "status": result.get("status"),
        "content_type": result.get(
            "content_type"
        ),
        "reason": result.get("reason"),
        "evidence_length": len(
            result.get("text", "")
        ),
        "is_pdf": result.get(
            "is_pdf",
            False,
        ),
    }


# ============================================================
# ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):

    return {
        "status": "error",
        "version": VERSION,
        "error": str(exc),
        "error_type": type(exc).__name__,
    }


# ============================================================
# LOCAL ENTRYPOINT
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
    )
