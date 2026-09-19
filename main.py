"""
AI Infinity
TARGET-2050.24
BUILD: EXTRAORDINARY-EVIDENCE-INTELLIGENCE-CORE

Major upgrade from TARGET-2050.23:

- Async mission execution
- Research-question decomposition
- Relevance-first source selection
- Evidence passage extraction
- Aggressive webpage boilerplate removal
- Claim quality filtering
- Claim/source relationship graph
- Support / contradiction / limitation edges
- Underlying-work deduplication
- Claim-level verification
- Claim-specific counter-evidence
- Source independence analysis
- Evidence quality scoring
- Structured synthesis
- SQLite persistence
- Existing API compatibility
- Mobile control center
- Fail-closed verification
- No paid APIs required
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
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
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


# ============================================================
# CONFIGURATION
# ============================================================

VERSION = "TARGET-2050.24"
BUILD = "EXTRAORDINARY-EVIDENCE-INTELLIGENCE-CORE"

BASE = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity"))
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "12"))
MAX_BODY_BYTES = 6 * 1024 * 1024
MAX_TEXT_CHARS = 60000

WORKERS = int(os.getenv("MISSION_WORKERS", "3"))

USER_AGENT = (
    "AI-Infinity/2050.24 "
    "(evidence-research-engine; respectful automated retrieval)"
)

executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=WORKERS,
    thread_name_prefix="ai-infinity"
)

db_lock = threading.Lock()


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Autonomous evidence intelligence engine",
)


# ============================================================
# REQUEST MODELS
# ============================================================

class MissionRequest(BaseModel):
    objective: str = Field(..., min_length=5, max_length=12000)
    research: bool = True
    verify: bool = True
    remember: bool = True


class ResearchRequest(BaseModel):
    objective: str = Field(..., min_length=5, max_length=12000)


class CommandRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=12000)
    duration_minutes: int = Field(default=1, ge=1, le=120)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        conn = db()

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                version TEXT NOT NULL,
                build TEXT NOT NULL,
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                result_json TEXT,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                source_key TEXT NOT NULL,
                url TEXT,
                title TEXT,
                domain TEXT,
                provider TEXT,
                doi TEXT,
                work_key TEXT,
                relevance REAL DEFAULT 0,
                quality REAL DEFAULT 0,
                evidence_score REAL DEFAULT 0,
                accepted INTEGER DEFAULT 0,
                rejection_reason TEXT,
                content_chars INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                claim_key TEXT NOT NULL,
                claim TEXT NOT NULL,
                status TEXT,
                confidence REAL DEFAULT 0,
                support_count INTEGER DEFAULT 0,
                contradiction_count INTEGER DEFAULT 0,
                limitation_count INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )

        conn.commit()
        conn.close()


init_db()


# ============================================================
# DATABASE HELPERS
# ============================================================

def save_event(
    mission_id: str,
    stage: str,
    status: str,
    payload: Optional[Dict[str, Any]] = None,
):
    with db_lock:
        conn = db()
        conn.execute(
            """
            INSERT INTO events
            (mission_id, stage, status, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                stage,
                status,
                json.dumps(payload or {}, ensure_ascii=False),
                time.time(),
            ),
        )
        conn.commit()
        conn.close()


def create_mission_record(
    mission_id: str,
    objective: str,
):
    with db_lock:
        conn = db()
        conn.execute(
            """
            INSERT INTO missions
            (id, objective, status, version, build, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                objective,
                "queued",
                VERSION,
                BUILD,
                time.time(),
            ),
        )
        conn.commit()
        conn.close()


def update_mission(
    mission_id: str,
    status: str,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
):
    with db_lock:
        conn = db()

        if status == "running":
            conn.execute(
                """
                UPDATE missions
                SET status=?, started_at=?
                WHERE id=?
                """,
                (status, time.time(), mission_id),
            )
        elif status in {"completed", "failed"}:
            conn.execute(
                """
                UPDATE missions
                SET status=?, completed_at=?,
                    result_json=?, error=?
                WHERE id=?
                """,
                (
                    status,
                    time.time(),
                    json.dumps(result or {}, ensure_ascii=False),
                    error,
                    mission_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE missions
                SET status=?
                WHERE id=?
                """,
                (status, mission_id),
            )

        conn.commit()
        conn.close()


def get_mission_record(mission_id: str):
    conn = db()
    row = conn.execute(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,),
    ).fetchone()
    conn.close()
    return row


def save_source(mission_id: str, source: Dict[str, Any]):
    with db_lock:
        conn = db()
        conn.execute(
            """
            INSERT INTO sources
            (
                mission_id,
                source_key,
                url,
                title,
                domain,
                provider,
                doi,
                work_key,
                relevance,
                quality,
                evidence_score,
                accepted,
                rejection_reason,
                content_chars,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                source.get("source_key", ""),
                source.get("url", ""),
                source.get("title", ""),
                source.get("domain", ""),
                source.get("provider", ""),
                source.get("doi", ""),
                source.get("work_key", ""),
                source.get("relevance", 0),
                source.get("quality", 0),
                source.get("evidence_score", 0),
                int(bool(source.get("accepted"))),
                source.get("rejection_reason"),
                len(source.get("text", "")),
                time.time(),
            ),
        )
        conn.commit()
        conn.close()


def save_claim(mission_id: str, claim: Dict[str, Any]):
    with db_lock:
        conn = db()
        conn.execute(
            """
            INSERT INTO claims
            (
                mission_id,
                claim_key,
                claim,
                status,
                confidence,
                support_count,
                contradiction_count,
                limitation_count,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                claim.get("claim_key", ""),
                claim.get("claim", ""),
                claim.get("status", ""),
                claim.get("confidence", 0),
                claim.get("supporting_sources", 0),
                claim.get("contradicting_sources", 0),
                claim.get("limitation_sources", 0),
                time.time(),
            ),
        )
        conn.commit()
        conn.close()


# ============================================================
# BASIC TEXT UTILITIES
# ============================================================

STOPWORDS = {
    "the", "and", "or", "of", "to", "a", "an", "in", "on", "for",
    "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "as", "at", "it", "its",
    "their", "they", "them", "we", "our", "you", "your", "can",
    "may", "might", "could", "should", "would", "into", "than",
    "using", "use", "used", "research", "study", "paper", "results",
}


def normalize_space(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\x00", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def words(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", text.lower())
    return [
        t for t in tokens
        if t not in STOPWORDS
        and not t.isdigit()
    ]


def token_set(text: str) -> set:
    return set(words(text))


def similarity(a: str, b: str) -> float:
    aa = token_set(a)
    bb = token_set(b)

    if not aa or not bb:
        return 0.0

    inter = len(aa & bb)
    union = len(aa | bb)

    return inter / max(union, 1)


def contains_number(text: str) -> bool:
    return bool(
        re.search(
            r"\b\d+(?:\.\d+)?\s*(?:%|percent|times|x)?\b",
            text,
            re.I,
        )
    )


# ============================================================
# HTML CLEANING
# ============================================================

class CleanHTMLParser(HTMLParser):
    """
    Lightweight HTML-to-text parser.
    No BeautifulSoup dependency required.
    """

    BLOCK_TAGS = {
        "p", "div", "section", "article", "main", "header", "footer",
        "li", "ul", "ol", "br", "tr", "td", "th", "h1", "h2", "h3",
        "h4", "h5", "h6", "blockquote", "pre",
    }

    IGNORE_TAGS = {
        "script", "style", "noscript", "svg", "nav", "form", "button",
        "footer",
    }

    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignore_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag in self.IGNORE_TAGS:
            self.ignore_depth += 1

        if tag in self.BLOCK_TAGS and self.ignore_depth == 0:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in self.IGNORE_TAGS and self.ignore_depth:
            self.ignore_depth -= 1

        if tag in self.BLOCK_TAGS and self.ignore_depth == 0:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.ignore_depth == 0:
            self.parts.append(data)

    def text(self):
        return normalize_space("\n".join(self.parts))


def html_to_text(raw: str) -> str:
    parser = CleanHTMLParser()
    try:
        parser.feed(raw)
        return parser.text()
    except Exception:
        return normalize_space(raw)


# ============================================================
# SSRF / URL SECURITY
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
}


def is_private_ip(host: str) -> bool:
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)

        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except Exception:
        return False


def validate_public_url(url: str) -> Tuple[bool, str]:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return False, "unsupported_scheme"

        host = (parsed.hostname or "").lower().strip(".")

        if not host:
            return False, "missing_host"

        if host in BLOCKED_HOSTS:
            return False, "blocked_host"

        if is_private_ip(host):
            return False, "private_ip"

        if host.endswith(".local"):
            return False, "local_domain"

        return True, ""
    except Exception:
        return False, "invalid_url"


# ============================================================
# URL / SOURCE NORMALIZATION
# ============================================================

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "ref",
}


def canonical_url(url: str) -> str:
    try:
        p = urlparse(url)

        query = [
            (k, v)
            for k, v in parse_qsl(
                p.query,
                keep_blank_values=True,
            )
            if k.lower() not in TRACKING_PARAMS
        ]

        path = p.path.rstrip("/") or "/"

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


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def extract_doi(text: str) -> str:
    if not text:
        return ""

    match = re.search(
        r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
        text,
        re.I,
    )

    if not match:
        return ""

    doi = match.group(1)
    doi = re.sub(r"[)\].,;]+$", "", doi)

    return doi.lower()


def source_work_key(
    title: str = "",
    doi: str = "",
    url: str = "",
) -> str:

    if doi:
        return "doi:" + doi.lower().strip()

    normalized_title = normalize_space(title).lower()

    if normalized_title:
        normalized_title = re.sub(
            r"[^a-z0-9 ]+",
            " ",
            normalized_title,
        )
        normalized_title = re.sub(
            r"\s+",
            " ",
            normalized_title,
        ).strip()

        if len(normalized_title) >= 20:
            return "title:" + normalized_title[:300]

    return "url:" + canonical_url(url)


def source_key(source: Dict[str, Any]) -> str:
    return source_work_key(
        source.get("title", ""),
        source.get("doi", ""),
        source.get("url", ""),
    )


# ============================================================
# PAGE QUALITY / BOILERPLATE DETECTION
# ============================================================

BAD_PAGE_MARKERS = [
    "enable javascript",
    "javascript is disabled",
    "client challenge",
    "just a moment",
    "checking your browser",
    "access denied",
    "captcha",
    "verify you are human",
    "request unsuccessful",
    "something went wrong",
    "page not found",
    "internal server error",
    "sign in to continue",
    "login to continue",
]


BOILERPLATE_PATTERNS = [
    r"article\s+pdf\s+download",
    r"download\s+references",
    r"google\s+scholar",
    r"similar\s+content\s+being\s+viewed",
    r"explore\s+related\s+subjects",
    r"discover\s+the\s+latest\s+articles",
    r"suggested\s+using\s+machine\s+learning",
    r"acknowledgements",
    r"copyright\s+©",
]


def page_problem(text: str) -> Optional[str]:
    low = text.lower()

    for marker in BAD_PAGE_MARKERS:
        if marker in low:
            return "page_challenge_or_error"

    return None


def boilerplate_ratio(text: str) -> float:
    if not text:
        return 1.0

    hits = 0

    low = text.lower()

    for pattern in BOILERPLATE_PATTERNS:
        if re.search(pattern, low):
            hits += 1

    lines = [
        normalize_space(x)
        for x in text.splitlines()
        if normalize_space(x)
    ]

    if not lines:
        return 1.0

    short_lines = sum(
        1 for line in lines
        if len(line) < 35
    )

    ratio = hits / max(len(BOILERPLATE_PATTERNS), 1)

    if short_lines / len(lines) > 0.8:
        ratio += 0.25

    return min(ratio, 1.0)


def remove_boilerplate(text: str) -> str:
    """
    Remove common publisher/interface contamination.
    """

    if not text:
        return ""

    lines = [
        normalize_space(line)
        for line in text.splitlines()
    ]

    output = []

    for line in lines:
        if not line:
            continue

        low = line.lower()

        if any(
            re.search(pattern, low)
            for pattern in BOILERPLATE_PATTERNS
        ):
            continue

        if low in {
            "download",
            "pdf",
            "references",
            "acknowledgements",
            "google scholar",
            "similar content",
            "related subjects",
            "login",
            "sign in",
        }:
            continue

        output.append(line)

    text = "\n".join(output)

    # Remove repeated interface blocks.
    text = re.sub(
        r"(similar content being viewed by others).*?"
        r"(explore related subjects)",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = normalize_space(text)

    return text[:MAX_TEXT_CHARS]


# ============================================================
# PDF
# ============================================================

def extract_pdf_text(data: bytes) -> str:
    if PdfReader is None:
        return ""

    try:
        import io

        reader = PdfReader(io.BytesIO(data))

        pages = []

        for page in reader.pages[:30]:
            try:
                text = page.extract_text() or ""
                if text:
                    pages.append(text)
            except Exception:
                continue

        return remove_boilerplate(
            "\n".join(pages)
        )[:MAX_TEXT_CHARS]

    except Exception:
        return ""


# ============================================================
# EVIDENCE VALIDATION
# ============================================================

def valid_evidence_text(text: str) -> Tuple[bool, str]:
    text = normalize_space(text)

    if len(text) < 300:
        return False, "too_little_text"

    if text.startswith("%PDF-"):
        return False, "raw_pdf"

    if page_problem(text):
        return False, "challenge_or_error_page"

    if boilerplate_ratio(text) > 0.7:
        return False, "boilerplate_dominant"

    letters = sum(c.isalpha() for c in text)

    if letters < 200:
        return False, "insufficient_readable_text"

    unique_words = len(set(words(text)))

    if unique_words < 50:
        return False, "low_text_diversity"

    return True, ""


# ============================================================
# FETCH
# ============================================================

def fetch_url(url: str) -> Dict[str, Any]:
    ok, reason = validate_public_url(url)

    if not ok:
        return {
            "ok": False,
            "reason": reason,
            "url": url,
        }

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/pdf;q=0.9,*/*;q=0.8"
                ),
            },
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )

        final_url = response.url

        ok, reason = validate_public_url(final_url)

        if not ok:
            response.close()

            return {
                "ok": False,
                "reason": "unsafe_redirect",
                "url": url,
            }

        chunks = []
        total = 0

        for chunk in response.iter_content(65536):
            if not chunk:
                continue

            total += len(chunk)

            if total > MAX_BODY_BYTES:
                break

            chunks.append(chunk)

        data = b"".join(chunks)

        content_type = (
            response.headers.get(
                "content-type",
                "",
            ).lower()
        )

        response.close()

        if response.status_code >= 400:
            return {
                "ok": False,
                "reason": f"http_{response.status_code}",
                "url": final_url,
            }

        if (
            "application/pdf" in content_type
            or data[:5] == b"%PDF-"
        ):
            text = extract_pdf_text(data)

            if not text:
                return {
                    "ok": False,
                    "reason": "pdf_extraction_failed",
                    "url": final_url,
                }
        else:
            try:
                raw = data.decode(
                    response.encoding or "utf-8",
                    errors="ignore",
                )
            except Exception:
                raw = data.decode(
                    "utf-8",
                    errors="ignore",
                )

            text = html_to_text(raw)
            text = remove_boilerplate(text)

        valid, why = valid_evidence_text(text)

        if not valid:
            return {
                "ok": False,
                "reason": why,
                "url": final_url,
            }

        return {
            "ok": True,
            "url": final_url,
            "text": text[:MAX_TEXT_CHARS],
            "content_type": content_type,
        }

    except Exception as exc:
        return {
            "ok": False,
            "reason": str(exc)[:300],
            "url": url,
        }


# ============================================================
# PROVIDERS
# ============================================================

def provider_openalex(query: str) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://api.openalex.org/works",
            params={
                "search": query,
                "per-page": 10,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        data = response.json()

        results = []

        for item in data.get("results", []):
            title = item.get("display_name") or ""

            doi = extract_doi(
                item.get("doi") or ""
            )

            primary = item.get(
                "primary_location"
            ) or {}

            landing = (
                primary.get("landing_page_url")
                or ""
            )

            pdf = (
                primary.get("pdf_url")
                or ""
            )

            url = pdf or landing

            if not url:
                continue

            results.append(
                {
                    "provider": "openalex",
                    "title": title,
                    "url": url,
                    "doi": doi,
                    "abstract": reconstruct_openalex_abstract(item),
                }
            )

        return results

    except Exception:
        return []


def reconstruct_openalex_abstract(item: Dict[str, Any]) -> str:
    inverted = item.get(
        "abstract_inverted_index"
    )

    if not inverted:
        return ""

    pairs = []

    try:
        for word, positions in inverted.items():
            for pos in positions:
                pairs.append((pos, word))

        pairs.sort(key=lambda x: x[0])

        return " ".join(
            word for _, word in pairs
        )
    except Exception:
        return ""


def provider_crossref(query: str) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://api.crossref.org/works",
            params={
                "query.bibliographic": query,
                "rows": 10,
            },
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
            title_list = item.get("title") or []

            title = (
                title_list[0]
                if title_list
                else ""
            )

            doi = (
                item.get("DOI")
                or ""
            ).lower()

            url = ""

            links = item.get("link") or []

            if links:
                url = links[0].get(
                    "URL",
                    "",
                )

            if not url and doi:
                url = (
                    "https://doi.org/"
                    + doi
                )

            if not url:
                continue

            results.append(
                {
                    "provider": "crossref",
                    "title": title,
                    "url": url,
                    "doi": doi,
                    "abstract": (
                        item.get("abstract")
                        or ""
                    ),
                }
            )

        return results

    except Exception:
        return []


def provider_duckduckgo(query: str) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "User-Agent": USER_AGENT
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        raw = response.text

        results = []

        # Lightweight extraction of result links/titles.
        links = re.findall(
            r'class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            raw,
            flags=re.I | re.S,
        )

        for url, title_html in links[:10]:
            title = html_to_text(
                title_html
            )

            if url.startswith("//"):
                url = "https:" + url

            if not url.startswith("http"):
                continue

            results.append(
                {
                    "provider": "duckduckgo",
                    "title": title,
                    "url": url,
                    "doi": extract_doi(url),
                    "abstract": "",
                }
            )

        return results

    except Exception:
        return []


def provider_wikipedia(query: str) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": 5,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        data = response.json()

        results = []

        for item in data.get(
            "query", {}
        ).get("search", []):

            title = item.get(
                "title",
                "",
            )

            if not title:
                continue

            url = (
                "https://en.wikipedia.org/wiki/"
                + title.replace(" ", "_")
            )

            results.append(
                {
                    "provider": "wikipedia",
                    "title": title,
                    "url": url,
                    "doi": "",
                    "abstract": (
                        item.get("snippet")
                        or ""
                    ),
                }
            )

        return results

    except Exception:
        return []


PROVIDERS = {
    "openalex": provider_openalex,
    "crossref": provider_crossref,
    "duckduckgo": provider_duckduckgo,
    "wikipedia": provider_wikipedia,
}


# ============================================================
# MISSION DECOMPOSITION
# ============================================================

def build_research_questions(
    objective: str,
) -> List[str]:

    base = normalize_space(objective)

    questions = [
        f"{base} empirical evidence benchmark evaluation",
        f"{base} task success failure rate autonomous agents",
        f"{base} real world deployment autonomous AI agents",
        f"{base} reliability limitations failures autonomous agents",
        f"{base} human intervention monitoring verification agents",
        f"{base} independent study replication autonomous agents",
    ]

    # Domain-aware expansion.
    low = base.lower()

    if "reliab" in low or "agent" in low:
        questions.extend(
            [
                "autonomous AI agents benchmark success failure tool use",
                "LLM agents real world task completion reliability",
                "AI agent failure modes empirical evaluation",
                "autonomous agents human intervention empirical study",
            ]
        )

    # Deduplicate.
    seen = set()
    output = []

    for q in questions:
        q = normalize_space(q)

        if q.lower() not in seen:
            seen.add(q.lower())
            output.append(q)

    return output[:10]


# ============================================================
# SOURCE RELEVANCE
# ============================================================

IMPORTANT_TERMS = {
    "autonomous",
    "agent",
    "agents",
    "reliability",
    "failure",
    "failures",
    "task",
    "tasks",
    "real",
    "world",
    "evaluation",
    "benchmark",
    "performance",
    "verification",
    "tool",
    "planning",
    "execution",
    "human",
    "intervention",
    "safety",
    "limitation",
}


def relevance_score(
    objective: str,
    query: str,
    source: Dict[str, Any],
) -> float:

    source_text = " ".join(
        [
            source.get("title", ""),
            source.get("abstract", ""),
            source.get("url", ""),
        ]
    )

    source_tokens = token_set(source_text)
    objective_tokens = token_set(
        objective + " " + query
    )

    if not source_tokens:
        return 0.0

    overlap = len(
        source_tokens & objective_tokens
    ) / max(
        len(objective_tokens),
        1,
    )

    important = len(
        source_tokens & IMPORTANT_TERMS
    ) / max(
        len(IMPORTANT_TERMS),
        1,
    )

    title_score = similarity(
        source.get("title", ""),
        objective + " " + query,
    )

    score = (
        0.45 * overlap
        + 0.25 * important
        + 0.30 * title_score
    )

    return min(
        max(score * 2.0, 0.0),
        1.0,
    )


# ============================================================
# SOURCE QUALITY
# ============================================================

ACADEMIC_DOMAINS = {
    "arxiv.org",
    "doi.org",
    "nature.com",
    "sciencedirect.com",
    "springer.com",
    "link.springer.com",
    "acm.org",
    "ieee.org",
    "dl.acm.org",
    "journals.sagepub.com",
    "oup.com",
    "cambridge.org",
    "wiley.com",
    "plos.org",
    "nih.gov",
    "ncbi.nlm.nih.gov",
    "openreview.net",
}


def source_quality(source: Dict[str, Any]) -> float:
    domain = domain_of(
        source.get("url", "")
    )

    provider = source.get(
        "provider",
        "",
    )

    score = 0.40

    if domain in ACADEMIC_DOMAINS:
        score += 0.25

    if source.get("doi"):
        score += 0.12

    if provider in {
        "openalex",
        "crossref",
    }:
        score += 0.08

    if len(
        source.get("abstract", "")
    ) > 300:
        score += 0.05

    if len(
        source.get("text", "")
    ) > 2000:
        score += 0.05

    if provider == "wikipedia":
        score -= 0.10

    return min(
        max(score, 0.0),
        1.0,
    )


# ============================================================
# SOURCE DEDUPLICATION
# ============================================================

def deduplicate_sources(
    sources: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    grouped: Dict[str, Dict[str, Any]] = {}

    for source in sources:
        key = source_work_key(
            source.get("title", ""),
            source.get("doi", ""),
            source.get("url", ""),
        )

        source["work_key"] = key

        existing = grouped.get(key)

        if existing is None:
            grouped[key] = source
            continue

        # Preserve best representation.
        existing_score = (
            len(existing.get("abstract", ""))
            + len(existing.get("text", ""))
            + existing.get("quality", 0) * 1000
        )

        current_score = (
            len(source.get("abstract", ""))
            + len(source.get("text", ""))
            + source.get("quality", 0) * 1000
        )

        if current_score > existing_score:
            grouped[key] = source

    return list(grouped.values())


# ============================================================
# EVIDENCE PASSAGES
# ============================================================

ASSERTION_PATTERNS = [
    r"\bwe\s+(found|find|show|demonstrate|observe|report|evaluated)\b",
    r"\bresults?\s+(show|indicate|suggest|demonstrate)\b",
    r"\bour\s+(results|experiments|evaluation)\b",
    r"\bthe\s+(study|experiment|evaluation|benchmark)\b",
    r"\bperformance\b",
    r"\baccuracy\b",
    r"\bsuccess\s+rate\b",
    r"\bfailure\s+rate\b",
    r"\bfailed\b",
    r"\bfails?\b",
    r"\bimprov(?:e|ed|es|ement)\b",
    r"\bdegrad(?:e|ed|es|ation)\b",
    r"\boutperform(?:ed|s)?\b",
    r"\bunderperform(?:ed|s)?\b",
    r"\blimitations?\b",
    r"\bhowever\b",
    r"\bdespite\b",
    r"\bcompared\s+with\b",
    r"\bstatistically\b",
]


def sentence_split(text: str) -> List[str]:
    text = normalize_space(text)

    chunks = re.split(
        r"(?<=[.!?])\s+(?=[A-Z0-9])",
        text,
    )

    return [
        normalize_space(x)
        for x in chunks
        if len(normalize_space(x)) >= 45
    ]


def extract_evidence_passages(
    objective: str,
    source: Dict[str, Any],
) -> List[str]:

    text = source.get("text", "")

    if not text:
        text = source.get(
            "abstract",
            "",
        )

    sentences = sentence_split(text)

    if not sentences:
        return []

    objective_tokens = token_set(objective)

    scored = []

    for sentence in sentences:
        low = sentence.lower()

        assertion = any(
            re.search(
                pattern,
                low,
                re.I,
            )
            for pattern in ASSERTION_PATTERNS
        )

        relevance = len(
            token_set(sentence)
            & objective_tokens
        ) / max(
            len(objective_tokens),
            1,
        )

        numeric = 0.15 if contains_number(
            sentence
        ) else 0.0

        score = (
            0.50 * relevance
            + 0.35 * (1.0 if assertion else 0.0)
            + numeric
        )

        # Reject obvious metadata.
        if any(
            re.search(
                pattern,
                low,
                re.I,
            )
            for pattern in BOILERPLATE_PATTERNS
        ):
            continue

        if len(sentence) > 1500:
            sentence = sentence[:1500]

        scored.append(
            (
                min(score, 1.0),
                sentence,
            )
        )

    scored.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    passages = []

    for score, sentence in scored[:12]:
        if score >= 0.20:
            passages.append(sentence)

    return passages


# ============================================================
# CLAIM EXTRACTION
# ============================================================

def looks_like_real_claim(
    sentence: str,
    objective: str,
) -> bool:

    s = normalize_space(sentence)

    if len(s) < 60:
        return False

    if len(s) > 1200:
        return False

    low = s.lower()

    # Explicit metadata rejection.
    reject_phrases = [
        "research interests",
        "is a researcher",
        "chapter ©",
        "article pdf",
        "google scholar",
        "download references",
        "similar content",
        "explore related subjects",
        "acknowledgements",
        "national natural science foundation",
    ]

    if any(
        phrase in low
        for phrase in reject_phrases
    ):
        return False

    assertion = any(
        re.search(
            pattern,
            low,
            re.I,
        )
        for pattern in ASSERTION_PATTERNS
    )

    relevance = similarity(
        s,
        objective,
    )

    if not assertion and relevance < 0.20:
        return False

    # Reject pure title/reference-like strings.
    if (
        s.endswith(".")
        and len(words(s)) >= 8
    ):
        return True

    return assertion and relevance >= 0.10


def claim_key(claim: str) -> str:
    normalized = normalize_space(
        claim
    ).lower()

    return hashlib.sha256(
        normalized.encode(
            "utf-8"
        )
    ).hexdigest()[:16]


def extract_claims_from_source(
    objective: str,
    source: Dict[str, Any],
) -> List[Dict[str, Any]]:

    passages = source.get(
        "evidence_passages",
        [],
    )

    claims = []

    for passage in passages:
        sentences = sentence_split(
            passage
        )

        for sentence in sentences:
            sentence = normalize_space(
                sentence
            )

            if not looks_like_real_claim(
                sentence,
                objective,
            ):
                continue

            # Strip leading citation-like artifacts.
            sentence = re.sub(
                r"^\[[0-9,\-\s]+\]\s*",
                "",
                sentence,
            )

            if len(sentence) < 60:
                continue

            claims.append(
                {
                    "claim": sentence,
                    "claim_key": claim_key(
                        sentence
                    ),
                    "source_key": source[
                        "source_key"
                    ],
                    "work_key": source[
                        "work_key"
                    ],
                    "domain": source.get(
                        "domain",
                        "",
                    ),
                }
            )

    return claims[:8]


# ============================================================
# CLAIM RELATIONSHIP
# ============================================================

NEGATION_WORDS = {
    "not",
    "no",
    "never",
    "failed",
    "failure",
    "fails",
    "unable",
    "without",
    "worse",
    "decreased",
    "decrease",
    "declined",
    "limitation",
    "limited",
    "weak",
    "unstable",
    "unreliable",
}


def relation_between(
    claim: str,
    evidence: str,
) -> Tuple[str, float]:

    sim = similarity(
        claim,
        evidence,
    )

    if sim < 0.08:
        return "unrelated", sim

    claim_neg = bool(
        token_set(claim)
        & NEGATION_WORDS
    )

    evidence_neg = bool(
        token_set(evidence)
        & NEGATION_WORDS
    )

    if claim_neg != evidence_neg:
        if sim >= 0.14:
            return "contradicts", sim

    limitation_words = {
        "limitation",
        "limited",
        "however",
        "despite",
        "caveat",
        "constraint",
    }

    if token_set(evidence) & limitation_words:
        return "limits", sim

    return "supports", sim


# ============================================================
# GRAPH
# ============================================================

def build_evidence_graph(
    sources: List[Dict[str, Any]],
    claims: List[Dict[str, Any]],
) -> Dict[str, Any]:

    nodes = []
    edges = []

    for source in sources:
        nodes.append(
            {
                "id": source[
                    "source_key"
                ],
                "type": "source",
                "domain": source.get(
                    "domain",
                    "",
                ),
                "provider": source.get(
                    "provider",
                    "",
                ),
                "quality": round(
                    source.get(
                        "quality",
                        0,
                    ),
                    3,
                ),
                "relevance": round(
                    source.get(
                        "relevance",
                        0,
                    ),
                    3,
                ),
            }
        )

    for claim in claims:
        nodes.append(
            {
                "id": claim[
                    "claim_key"
                ],
                "type": "claim",
                "text": claim[
                    "claim"
                ],
            }
        )

        for evidence in claim.get(
            "evidence",
            [],
        ):
            relation = evidence.get(
                "relation",
            )

            if relation not in {
                "supports",
                "contradicts",
                "limits",
            }:
                continue

            edges.append(
                {
                    "from": evidence[
                        "source_key"
                    ],
                    "to": claim[
                        "claim_key"
                    ],
                    "relation": relation,
                    "strength": round(
                        evidence.get(
                            "strength",
                            0,
                        ),
                        3,
                    ),
                    "domain": evidence.get(
                        "domain",
                        "",
                    ),
                }
            )

    return {
        "nodes": nodes,
        "edges": edges,
    }


# ============================================================
# INDEPENDENCE
# ============================================================

def independent_domains(
    evidence: List[Dict[str, Any]],
) -> List[str]:

    domains = []

    for item in evidence:
        domain = item.get(
            "domain",
            "",
        )

        if domain and domain not in domains:
            domains.append(domain)

    return domains


def independent_work_count(
    evidence: List[Dict[str, Any]],
) -> int:

    return len(
        {
            item.get(
                "work_key",
                "",
            )
            for item in evidence
            if item.get("work_key")
        }
    )


# ============================================================
# CLAIM VERIFICATION
# ============================================================

def verify_claim(
    claim: Dict[str, Any],
) -> Dict[str, Any]:

    evidence = claim.get(
        "evidence",
        [],
    )

    supporting = [
        e for e in evidence
        if e.get("relation") == "supports"
    ]

    contradicting = [
        e for e in evidence
        if e.get("relation") == "contradicts"
    ]

    limitations = [
        e for e in evidence
        if e.get("relation") == "limits"
    ]

    support_domains = {
        e.get("domain")
        for e in supporting
        if e.get("domain")
    }

    contradiction_domains = {
        e.get("domain")
        for e in contradicting
        if e.get("domain")
    }

    support_works = {
        e.get("work_key")
        for e in supporting
        if e.get("work_key")
    }

    confidence = 0.0

    if supporting:
        confidence += min(
            len(supporting) * 0.18,
            0.50,
        )

    confidence += min(
        len(support_domains) * 0.10,
        0.25,
    )

    confidence += min(
        len(support_works) * 0.08,
        0.20,
    )

    if contradicting:
        confidence -= min(
            len(contradicting) * 0.15,
            0.45,
        )

    if limitations:
        confidence -= min(
            len(limitations) * 0.04,
            0.15,
        )

    confidence = min(
        max(confidence, 0.0),
        0.99,
    )

    # Fail closed.
    if (
        len(support_domains) >= 2
        and len(support_works) >= 2
        and len(contradicting) == 0
        and confidence >= 0.62
    ):
        status = "VERIFIED"

    elif (
        supporting
        and confidence >= 0.30
    ):
        status = "UNCERTAIN"

    elif (
        contradicting
        and not supporting
    ):
        status = "REJECTED"

    else:
        status = "INSUFFICIENT"

    claim.update(
        {
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
            "limitation_sources": len(
                limitations
            ),
            "support_domains": sorted(
                support_domains
            ),
            "contradiction_domains": sorted(
                contradiction_domains
            ),
        }
    )

    return claim


# ============================================================
# COUNTER-EVIDENCE
# ============================================================

def counter_queries_for_claim(
    claim: str,
) -> List[str]:

    return [
        f'"{claim}" limitation failure',
        f'"{claim}" criticism negative result',
        f'"{claim}" replication failure',
        f'"{claim}" benchmark weakness',
    ]


# ============================================================
# SYNTHESIS
# ============================================================

def synthesize(
    objective: str,
    claims: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    graph: Dict[str, Any],
    counter_sources: int,
) -> Dict[str, Any]:

    verified = [
        c for c in claims
        if c.get("status") == "VERIFIED"
    ]

    uncertain = [
        c for c in claims
        if c.get("status") == "UNCERTAIN"
    ]

    insufficient = [
        c for c in claims
        if c.get("status") == "INSUFFICIENT"
    ]

    rejected = [
        c for c in claims
        if c.get("status") == "REJECTED"
    ]

    domains = sorted(
        {
            s.get("domain")
            for s in sources
            if s.get("domain")
        }
    )

    edges = graph.get(
        "edges",
        [],
    )

    contradictions = [
        e for e in edges
        if e.get("relation") == "contradicts"
    ]

    if verified:
        conclusion = (
            "The available evidence supports "
            "specific claims, but conclusions "
            "should remain limited to the "
            "verified scope."
        )
    elif uncertain:
        conclusion = (
            "The research found relevant evidence, "
            "but the available independent support "
            "is not strong enough to verify the "
            "main claims conclusively."
        )
    else:
        conclusion = (
            "The current evidence set does not "
            "provide sufficient claim-level support "
            "for a strong conclusion."
        )

    next_actions = []

    if not verified:
        next_actions.append(
            "Collect additional independent primary studies."
        )

    if contradictions:
        next_actions.append(
            "Review contradictory evidence claim by claim."
        )

    if len(domains) < 3:
        next_actions.append(
            "Increase independent source diversity."
        )

    if counter_sources:
        next_actions.append(
            "Reconcile counter-evidence with the strongest "
            "supporting evidence."
        )

    if not next_actions:
        next_actions.append(
            "Continue monitoring for new independent evidence."
        )

    return {
        "conclusion": conclusion,
        "verified_claims": len(verified),
        "uncertain_claims": len(uncertain),
        "insufficient_claims": len(
            insufficient
        ),
        "rejected_claims": len(rejected),
        "contradictions": len(
            contradictions
        ),
        "independent_domains": len(
            domains
        ),
        "counter_evidence_sources": counter_sources,
        "next_actions": next_actions,
    }


# ============================================================
# MAIN RESEARCH ENGINE
# ============================================================

def research_mission(
    mission_id: str,
    objective: str,
    verify_enabled: bool = True,
) -> Dict[str, Any]:

    started = time.time()

    update_mission(
        mission_id,
        "running",
    )

    save_event(
        mission_id,
        "planning",
        "running",
    )

    questions = build_research_questions(
        objective
    )

    save_event(
        mission_id,
        "planning",
        "completed",
        {
            "questions": questions,
            "count": len(questions),
        },
    )

    # --------------------------------------------------------
    # DISCOVERY
    # --------------------------------------------------------

    save_event(
        mission_id,
        "discovery",
        "running",
    )

    discovered = []

    # Search providers in parallel.
    jobs = []

    for question in questions:
        for provider_name, provider_fn in PROVIDERS.items():
            jobs.append(
                (
                    provider_name,
                    question,
                    provider_fn,
                )
            )

    def run_provider(job):
        provider_name, query, fn = job

        try:
            return provider_name, query, fn(query)
        except Exception:
            return provider_name, query, []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(12, len(jobs))
    ) as pool:

        futures = [
            pool.submit(
                run_provider,
                job,
            )
            for job in jobs
        ]

        for future in concurrent.futures.as_completed(
            futures
        ):
            try:
                provider_name, query, results = (
                    future.result()
                )

                for result in results:
                    result["query"] = query
                    discovered.append(result)

            except Exception:
                continue

    # --------------------------------------------------------
    # DEDUPLICATE
    # --------------------------------------------------------

    for source in discovered:
        source["url"] = canonical_url(
            source.get("url", "")
        )

        source["domain"] = domain_of(
            source.get("url", "")
        )

        source["source_key"] = source_work_key(
            source.get("title", ""),
            source.get("doi", ""),
            source.get("url", ""),
        )

        source["relevance"] = relevance_score(
            objective,
            source.get("query", ""),
            source,
        )

        source["quality"] = source_quality(
            source
        )

    discovered = deduplicate_sources(
        discovered
    )

    # Keep sources that have genuine mission relevance.
    discovered.sort(
        key=lambda x: (
            x.get("relevance", 0)
            * 0.65
            + x.get("quality", 0)
            * 0.35
        ),
        reverse=True,
    )

    discovered = discovered[:30]

    save_event(
        mission_id,
        "discovery",
        "completed",
        {
            "sources_discovered": len(
                discovered
            )
        },
    )

    # --------------------------------------------------------
    # INGESTION
    # --------------------------------------------------------

    save_event(
        mission_id,
        "evidence_ingestion",
        "running",
    )

    accepted_sources = []

    def ingest(source):
        result = fetch_url(
            source.get("url", "")
        )

        source = dict(source)

        if not result.get("ok"):
            source["accepted"] = False
            source["rejection_reason"] = (
                result.get(
                    "reason",
                    "fetch_failed",
                )
            )
            return source

        text = result.get(
            "text",
            "",
        )

        source["text"] = text

        # Recalculate quality with actual evidence.
        source["quality"] = source_quality(
            source
        )

        source["evidence_passages"] = (
            extract_evidence_passages(
                objective,
                source,
            )
        )

        if not source[
            "evidence_passages"
        ]:
            source["accepted"] = False
            source["rejection_reason"] = (
                "no_relevant_evidence_passages"
            )
            return source

        source["evidence_score"] = min(
            1.0,
            0.50 * source["relevance"]
            + 0.30 * source["quality"]
            + 0.20 * min(
                len(
                    source[
                        "evidence_passages"
                    ]
                ) / 5,
                1.0,
            ),
        )

        if source["evidence_score"] < 0.25:
            source["accepted"] = False
            source["rejection_reason"] = (
                "low_evidence_score"
            )
            return source

        source["accepted"] = True

        return source

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=8
    ) as pool:

        futures = [
            pool.submit(
                ingest,
                source,
            )
            for source in discovered
        ]

        for future in concurrent.futures.as_completed(
            futures
        ):
            try:
                source = future.result()

                save_source(
                    mission_id,
                    source,
                )

                if source.get(
                    "accepted"
                ):
                    accepted_sources.append(
                        source
                    )
            except Exception:
                continue

    # Prefer genuine evidence diversity.
    accepted_sources.sort(
        key=lambda x: (
            x.get("evidence_score", 0)
        ),
        reverse=True,
    )

    save_event(
        mission_id,
        "evidence_ingestion",
        "completed",
        {
            "accepted_sources": len(
                accepted_sources
            ),
        },
    )

    # --------------------------------------------------------
    # CLAIM EXTRACTION
    # --------------------------------------------------------

    save_event(
        mission_id,
        "claim_extraction",
        "running",
    )

    raw_claims = []

    for source in accepted_sources:
        raw_claims.extend(
            extract_claims_from_source(
                objective,
                source,
            )
        )

    # Global claim deduplication.
    unique_claims = {}

    for claim in raw_claims:
        key = claim[
            "claim_key"
        ]

        existing = unique_claims.get(
            key
        )

        if existing is None:
            claim["evidence"] = []
            unique_claims[key] = claim
        else:
            pass

    claims = list(
        unique_claims.values()
    )

    # Keep claims relevant to objective.
    claims = [
        claim for claim in claims
        if similarity(
            claim["claim"],
            objective,
        ) >= 0.10
    ]

    claims = claims[:40]

    save_event(
        mission_id,
        "claim_extraction",
        "completed",
        {
            "claims": len(claims),
        },
    )

    # --------------------------------------------------------
    # CLAIM ↔ EVIDENCE LINKING
    # --------------------------------------------------------

    save_event(
        mission_id,
        "evidence_linking",
        "running",
    )

    for claim in claims:

        for source in accepted_sources:

            passages = source.get(
                "evidence_passages",
                [],
            )

            for passage in passages:

                relation, strength = (
                    relation_between(
                        claim["claim"],
                        passage,
                    )
                )

                if relation == "unrelated":
                    continue

                if strength < 0.10:
                    continue

                claim["evidence"].append(
                    {
                        "source_key": source[
                            "source_key"
                        ],
                        "work_key": source[
                            "work_key"
                        ],
                        "domain": source[
                            "domain"
                        ],
                        "provider": source[
                            "provider"
                        ],
                        "relation": relation,
                        "strength": strength,
                        "passage": passage[
                            :1200
                        ],
                    }
                )

        # Avoid duplicated same-source evidence.
        best_by_source = {}

        for evidence in claim[
            "evidence"
        ]:
            key = evidence[
                "source_key"
            ]

            old = best_by_source.get(
                key
            )

            if old is None or (
                evidence["strength"]
                > old["strength"]
            ):
                best_by_source[key] = (
                    evidence
                )

        claim["evidence"] = list(
            best_by_source.values()
        )

    save_event(
        mission_id,
        "evidence_linking",
        "completed",
        {
            "claims": len(claims),
            "relationships": sum(
                len(c.get("evidence", []))
                for c in claims
            ),
        },
    )

    # --------------------------------------------------------
    # VERIFICATION
    # --------------------------------------------------------

    save_event(
        mission_id,
        "verification",
        "running",
    )

    if verify_enabled:
        for claim in claims:
            verify_claim(
                claim
            )
    else:
        for claim in claims:
            claim["status"] = "UNVERIFIED"
            claim["confidence"] = 0.0

    for claim in claims:
        save_claim(
            mission_id,
            claim,
        )

    save_event(
        mission_id,
        "verification",
        "completed",
        {
            "verified": sum(
                c.get("status")
                == "VERIFIED"
                for c in claims
            ),
            "uncertain": sum(
                c.get("status")
                == "UNCERTAIN"
                for c in claims
            ),
        },
    )

    # --------------------------------------------------------
    # COUNTER-EVIDENCE
    # --------------------------------------------------------

    save_event(
        mission_id,
        "counter_evidence",
        "running",
    )

    counter_sources = []

    # Only attack claims that have enough relevance to matter.
    target_claims = [
        c for c in claims
        if c.get("status")
        in {
            "VERIFIED",
            "UNCERTAIN",
        }
    ]

    target_claims = sorted(
        target_claims,
        key=lambda c: c.get(
            "confidence",
            0,
        ),
        reverse=True,
    )[:10]

    counter_jobs = []

    for claim in target_claims:
        for query in counter_queries_for_claim(
            claim["claim"]
        ):
            counter_jobs.append(
                (
                    claim,
                    query,
                )
            )

    def run_counter(job):
        claim, query = job

        local_results = []

        # Use academic providers first.
        for provider_name in [
            "openalex",
            "crossref",
            "duckduckgo",
        ]:
            try:
                results = PROVIDERS[
                    provider_name
                ](query)

                for result in results[:4]:
                    result["query"] = query
                    result["target_claim"] = (
                        claim["claim_key"]
                    )
                    result["provider"] = (
                        provider_name
                    )

                    result["url"] = canonical_url(
                        result.get(
                            "url",
                            "",
                        )
                    )

                    result["domain"] = domain_of(
                        result.get(
                            "url",
                            "",
                        )
                    )

                    result["work_key"] = (
                        source_work_key(
                            result.get(
                                "title",
                                "",
                            ),
                            result.get(
                                "doi",
                                "",
                            ),
                            result.get(
                                "url",
                                "",
                            ),
                        )
                    )

                    result["relevance"] = (
                        relevance_score(
                            objective,
                            query,
                            result,
                        )
                    )

                    local_results.append(
                        result
                    )

            except Exception:
                continue

        return local_results

    if counter_jobs:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=8
        ) as pool:

            futures = [
                pool.submit(
                    run_counter,
                    job,
                )
                for job in counter_jobs
            ]

            for future in concurrent.futures.as_completed(
                futures
            ):
                try:
                    counter_sources.extend(
                        future.result()
                    )
                except Exception:
                    continue

    # Deduplicate counter sources.
    counter_unique = {}

    for source in counter_sources:
        key = source.get(
            "work_key"
        ) or source.get(
            "url"
        )

        if key and key not in counter_unique:
            counter_unique[key] = source

    counter_sources = list(
        counter_unique.values()
    )[:30]

    # Fetch and connect counter evidence.
    for source in counter_sources:

        fetched = fetch_url(
            source.get(
                "url",
                "",
            )
        )

        if not fetched.get("ok"):
            continue

        source["text"] = fetched.get(
            "text",
            "",
        )

        source["evidence_passages"] = (
            extract_evidence_passages(
                objective,
                source,
            )
        )

        for claim in claims:
            if claim[
                "claim_key"
            ] != source.get(
                "target_claim"
            ):
                continue

            for passage in source.get(
                "evidence_passages",
                [],
            ):

                relation, strength = (
                    relation_between(
                        claim["claim"],
                        passage,
                    )
                )

                if relation in {
                    "contradicts",
                    "limits",
                } and strength >= 0.10:

                    claim["evidence"].append(
                        {
                            "source_key": source.get(
                                "work_key"
                            ),
                            "work_key": source.get(
                                "work_key"
                            ),
                            "domain": source.get(
                                "domain"
                            ),
                            "provider": source.get(
                                "provider"
                            ),
                            "relation": relation,
                            "strength": strength,
                            "counter_evidence": True,
                            "passage": passage[
                                :1200
                            ],
                        }
                    )

            verify_claim(
                claim
            )

    save_event(
        mission_id,
        "counter_evidence",
        "completed",
        {
            "sources": len(
                counter_sources
            ),
        },
    )

    # --------------------------------------------------------
    # FINAL GRAPH
    # --------------------------------------------------------

    save_event(
        mission_id,
        "evidence_graph",
        "running",
    )

    graph = build_evidence_graph(
        accepted_sources,
        claims,
    )

    save_event(
        mission_id,
        "evidence_graph",
        "completed",
        {
            "nodes": len(
                graph["nodes"]
            ),
            "edges": len(
                graph["edges"]
            ),
        },
    )

    # --------------------------------------------------------
    # SYNTHESIS
    # --------------------------------------------------------

    save_event(
        mission_id,
        "synthesis",
        "running",
    )

    synthesis = synthesize(
        objective,
        claims,
        accepted_sources,
        graph,
        len(counter_sources),
    )

    save_event(
        mission_id,
        "synthesis",
        "completed",
    )

    # --------------------------------------------------------
    # METRICS
    # --------------------------------------------------------

    domains = independent_domains(
        accepted_sources
    )

    providers_used = sorted(
        {
            source.get(
                "provider"
            )
            for source in accepted_sources
            if source.get("provider")
        }
    )

    supporting_edges = sum(
        1
        for e in graph["edges"]
        if e["relation"] == "supports"
    )

    contradiction_edges = sum(
        1
        for e in graph["edges"]
        if e["relation"] == "contradicts"
    )

    limitation_edges = sum(
        1
        for e in graph["edges"]
        if e["relation"] == "limits"
    )

    verified_count = sum(
        c.get("status")
        == "VERIFIED"
        for c in claims
    )

    usable_claims = sum(
        c.get("status")
        in {
            "VERIFIED",
            "UNCERTAIN",
        }
        for c in claims
    )

    average_quality = (
        sum(
            s.get(
                "quality",
                0,
            )
            for s in accepted_sources
        )
        / max(
            len(accepted_sources),
            1,
        )
    )

    research_strength = min(
        1.0,
        0.25 * min(
            len(accepted_sources) / 10,
            1,
        )
        + 0.20 * min(
            len(domains) / 4,
            1,
        )
        + 0.20 * min(
            len(graph["edges"]) / 15,
            1,
        )
        + 0.20 * min(
            usable_claims / 8,
            1,
        )
        + 0.15 * average_quality,
    )

    # Confidence across meaningful claims.
    meaningful_confidences = [
        c.get(
            "confidence",
            0,
        )
        for c in claims
        if c.get(
            "status"
        ) != "REJECTED"
    ]

    overall_confidence = (
        sum(
            meaningful_confidences
        )
        / max(
            len(meaningful_confidences),
            1,
        )
    )

    # --------------------------------------------------------
    # FINAL RESULT
    # --------------------------------------------------------

    duration = time.time() - started

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
                "research_questions": questions,
                "count": len(questions),
            },
            {
                "stage": "discovery",
                "status": "completed",
                "sources_discovered": len(
                    discovered
                ),
            },
            {
                "stage": "evidence_ingestion",
                "status": "completed",
                "accepted_sources": len(
                    accepted_sources
                ),
            },
            {
                "stage": "claim_extraction",
                "status": "completed",
                "claims": len(claims),
            },
            {
                "stage": "evidence_linking",
                "status": "completed",
                "relationships": len(
                    graph["edges"]
                ),
            },
            {
                "stage": "verification",
                "status": "completed",
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
            "used": providers_used,
            "count": len(
                providers_used
            ),
        },

        "evidence": {
            "count": len(
                accepted_sources
            ),
            "graph_nodes": len(
                graph["nodes"]
            ),
            "graph_edges": len(
                graph["edges"]
            ),
            "support_edges": supporting_edges,
            "contradiction_edges": contradiction_edges,
            "limitation_edges": limitation_edges,
            "independent_domains": len(
                domains
            ),
            "domains": domains,
            "independent_works": independent_work_count(
                accepted_sources
            ),
            "average_source_quality": round(
                average_quality,
                3,
            ),
            "source_diversity": round(
                min(
                    len(domains) / 5,
                    1,
                ),
                3,
            ),
            "provider_diversity": round(
                min(
                    len(providers_used) / 4,
                    1,
                ),
                3,
            ),
        },

        "claims": claims,

        "verification": {
            "enabled": verify_enabled,
            "verified": verified_count,
            "unsupported": sum(
                c.get("status")
                in {
                    "INSUFFICIENT",
                    "REJECTED",
                }
                for c in claims
            ),
            "uncertain": sum(
                c.get("status")
                == "UNCERTAIN"
                for c in claims
            ),
            "confidence": round(
                overall_confidence,
                3,
            ),
            "research_strength": round(
                research_strength,
                3,
            ),
            "contradictions": contradiction_edges,
        },

        "counter_evidence": {
            "sources": len(
                counter_sources
            ),
            "claims_tested": len(
                target_claims
            ),
        },

        "evidence_graph": graph,

        "synthesis": synthesis,

        "next_cycle": {
            "recommended": synthesis[
                "next_actions"
            ],
        },

        "timing": {
            "started_at": started,
            "completed_at": time.time(),
            "duration_seconds": round(
                duration,
                2,
            ),
        },
    }

    update_mission(
        mission_id,
        "completed",
        result=result,
    )

    return result


# ============================================================
# BACKGROUND MISSION
# ============================================================

def background_run(
    mission_id: str,
    objective: str,
    verify: bool,
):
    try:
        research_mission(
            mission_id,
            objective,
            verify,
        )

    except Exception as exc:

        error = (
            f"{type(exc).__name__}: "
            f"{str(exc)}"
        )

        save_event(
            mission_id,
            "runtime",
            "failed",
            {
                "error": error
            },
        )

        update_mission(
            mission_id,
            "failed",
            error=error,
        )


# ============================================================
# API
# ============================================================

@app.get("/")
def root():
    return HTMLResponse(
        DASHBOARD_HTML
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "online": True,
        "version": VERSION,
        "build": BUILD,
        "time": time.time(),
    }


@app.get("/status")
def status():
    return {
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "engine": "ready",
        "evidence": True,
        "verification": True,
        "async": True,
        "research_intelligence": True,
        "evidence_graph": True,
    }


@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "autonomous_missions",
            "research_question_decomposition",
            "multi_provider_discovery",
            "source_relevance_scoring",
            "source_quality_scoring",
            "boilerplate_rejection",
            "pdf_extraction",
            "evidence_passage_extraction",
            "claim_extraction",
            "claim_evidence_linking",
            "support_detection",
            "contradiction_detection",
            "limitation_detection",
            "underlying_work_deduplication",
            "independent_source_analysis",
            "claim_level_verification",
            "counter_evidence",
            "evidence_graph",
            "structured_synthesis",
            "persistent_missions",
            "async_execution",
            "ssrf_protection",
            "mobile_control_center",
        ],
        "providers": [
            "openalex",
            "crossref",
            "duckduckgo",
            "wikipedia",
        ],
    }


@app.post("/mission")
def create_mission(req: MissionRequest):

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:12]
    )

    create_mission_record(
        mission_id,
        req.objective,
    )

    save_event(
        mission_id,
        "mission",
        "queued",
        {
            "objective": req.objective
        },
    )

    executor.submit(
        background_run,
        mission_id,
        req.objective,
        req.verify,
    )

    return JSONResponse(
        status_code=202,
        content={
            "task_id": mission_id,
            "mission_id": mission_id,
            "status": "queued",
            "version": VERSION,
            "build": BUILD,
            "message": (
                "Mission accepted and "
                "running asynchronously."
            ),
        },
    )


@app.post("/research")
def research(req: ResearchRequest):

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:12]
    )

    create_mission_record(
        mission_id,
        req.objective,
    )

    executor.submit(
        background_run,
        mission_id,
        req.objective,
        True,
    )

    return JSONResponse(
        status_code=202,
        content={
            "task_id": mission_id,
            "mission_id": mission_id,
            "status": "queued",
            "version": VERSION,
        },
    )


@app.post("/run")
def run(req: CommandRequest):

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:12]
    )

    create_mission_record(
        mission_id,
        req.command,
    )

    executor.submit(
        background_run,
        mission_id,
        req.command,
        True,
    )

    return JSONResponse(
        status_code=202,
        content={
            "task_id": mission_id,
            "mission_id": mission_id,
            "status": "queued",
            "version": VERSION,
        },
    )


@app.post("/command")
def command(req: CommandRequest):
    return run(req)


@app.get("/mission/{mission_id}")
def mission(mission_id: str):

    row = get_mission_record(
        mission_id
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    result = None

    if row["result_json"]:
        try:
            result = json.loads(
                row["result_json"]
            )
        except Exception:
            result = None

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
    }


@app.get("/missions")
def missions():

    conn = db()

    rows = conn.execute(
        """
        SELECT id, objective, status,
               version, build,
               created_at, started_at,
               completed_at, error
        FROM missions
        ORDER BY created_at DESC
        LIMIT 50
        """
    ).fetchall()

    conn.close()

    return {
        "missions": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/mission/{mission_id}/sources")
def mission_sources(
    mission_id: str,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM sources
        WHERE mission_id=?
        ORDER BY evidence_score DESC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "sources": [
            dict(row)
            for row in rows
        ],
    }


@app.get("/mission/{mission_id}/claims")
def mission_claims(
    mission_id: str,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM claims
        WHERE mission_id=?
        ORDER BY confidence DESC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "claims": [
            dict(row)
            for row in rows
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
        FROM events
        WHERE mission_id=?
        ORDER BY created_at ASC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    events = []

    for row in rows:
        item = dict(row)

        try:
            item["payload"] = json.loads(
                item.pop(
                    "payload_json",
                    "{}",
                )
            )
        except Exception:
            item["payload"] = {}

        events.append(item)

    return {
        "mission_id": mission_id,
        "events": events,
    }


@app.post("/validate-source")
def validate_source(url: str):

    result = fetch_url(url)

    return {
        "url": url,
        "valid": bool(
            result.get("ok")
        ),
        "reason": result.get(
            "reason"
        ),
        "content_chars": len(
            result.get(
                "text",
                "",
            )
        ),
    }


# ============================================================
# GLOBAL JSON ERROR HANDLING
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request,
    exc,
):
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "error": type(exc).__name__,
            "message": str(exc)[:1000],
            "version": VERSION,
        },
    )


# ============================================================
# DASHBOARD
# ============================================================

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta
 name="viewport"
 content="width=device-width,initial-scale=1"
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
      #172554 0,
      #070b16 38%,
      #03050a 100%
    );
  color: #eef2ff;
  font-family:
    Inter,
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    sans-serif;
}

.container {
  width: min(1100px, 94%);
  margin: auto;
  padding: 28px 0 50px;
}

.top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 15px;
}

.brand {
  font-size: 25px;
  font-weight: 900;
  letter-spacing: -0.7px;
}

.infinity {
  opacity: .65;
}

.badge {
  padding: 7px 11px;
  border-radius: 999px;
  background: rgba(34,197,94,.13);
  border: 1px solid rgba(34,197,94,.35);
  color: #86efac;
  font-size: 12px;
  font-weight: 800;
}

.hero {
  padding: 55px 0 30px;
}

.hero h1 {
  max-width: 850px;
  font-size: clamp(36px, 7vw, 68px);
  line-height: .98;
  margin: 0 0 18px;
  letter-spacing: -3px;
}

.hero p {
  max-width: 750px;
  color: #aeb9d6;
  font-size: 17px;
  line-height: 1.65;
}

.panel {
  background: rgba(9,14,28,.78);
  border: 1px solid rgba(148,163,184,.16);
  border-radius: 22px;
  padding: 20px;
  box-shadow: 0 25px 80px rgba(0,0,0,.35);
  backdrop-filter: blur(15px);
}

textarea {
  width: 100%;
  min-height: 150px;
  resize: vertical;
  border: 1px solid rgba(148,163,184,.2);
  border-radius: 15px;
  padding: 16px;
  color: white;
  background: #050914;
  outline: none;
  font: inherit;
  line-height: 1.55;
}

textarea:focus {
  border-color: #60a5fa;
}

button {
  margin-top: 14px;
  width: 100%;
  border: 0;
  border-radius: 14px;
  padding: 15px;
  font-size: 16px;
  font-weight: 900;
  cursor: pointer;
  color: white;
  background:
    linear-gradient(
      135deg,
      #2563eb,
      #7c3aed
    );
}

button:disabled {
  opacity: .5;
  cursor: wait;
}

.grid {
  display: grid;
  grid-template-columns:
    repeat(4, 1fr);
  gap: 12px;
  margin: 18px 0;
}

.card {
  background: rgba(10,15,30,.75);
  border: 1px solid rgba(148,163,184,.13);
  border-radius: 16px;
  padding: 16px;
}

.label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: #8491af;
}

.value {
  margin-top: 7px;
  font-size: 17px;
  font-weight: 850;
}

.progress {
  height: 8px;
  background: #111827;
  border-radius: 999px;
  overflow: hidden;
  margin-top: 15px;
}

.bar {
  height: 100%;
  width: 0%;
  background:
    linear-gradient(
      90deg,
      #38bdf8,
      #8b5cf6,
      #22c55e
    );
  transition: width .4s;
}

pre {
  white-space: pre-wrap;
  word-break: break-word;
  color: #cbd5e1;
  background: #030712;
  border-radius: 15px;
  padding: 15px;
  max-height: 650px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.55;
}

.small {
  color: #8190ad;
  font-size: 12px;
}

@media(max-width:760px) {
  .grid {
    grid-template-columns:
      repeat(2, 1fr);
  }

  .hero {
    padding-top: 35px;
  }

  .hero h1 {
    letter-spacing: -2px;
  }
}

@media(max-width:480px) {
  .grid {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
</head>

<body>
<div class="container">

  <div class="top">
    <div class="brand">
      AI Infinity <span class="infinity">∞</span>
    </div>
    <div class="badge">
      ● ONLINE
    </div>
  </div>

  <section class="hero">
    <h1>
      Turn an objective into
      verified intelligence.
    </h1>

    <p>
      AI Infinity decomposes missions,
      researches multiple sources,
      filters evidence, extracts claims,
      links evidence to claims, tests
      counter-evidence, and builds a
      traceable evidence graph.
    </p>
  </section>

  <div class="panel">

    <textarea id="objective"
      placeholder="Give AI Infinity a mission..."></textarea>

    <button id="run"
      onclick="runMission()">
      🚀 RUN MISSION
    </button>

    <div class="progress">
      <div id="bar"
        class="bar"></div>
    </div>

    <div id="progressText"
      class="small"
      style="margin-top:8px;">
      Ready.
    </div>

  </div>

  <div class="grid">

    <div class="card">
      <div class="label">Version</div>
      <div id="version"
        class="value">
        TARGET-2050.24
      </div>
    </div>

    <div class="card">
      <div class="label">Engine</div>
      <div class="value">
        EXTRAORDINARY
      </div>
    </div>

    <div class="card">
      <div class="label">Evidence</div>
      <div class="value">
        INTELLIGENT
      </div>
    </div>

    <div class="card">
      <div class="label">Verification</div>
      <div class="value">
        FAIL-CLOSED
      </div>
    </div>

  </div>

  <div class="panel">

    <div class="label">
      MISSION RESULT
    </div>

    <pre id="result">
No mission executed yet.
    </pre>

  </div>

</div>

<script>
let pollTimer = null;

function setProgress(value, text) {
  document.getElementById("bar")
    .style.width = value + "%";

  document.getElementById(
    "progressText"
  ).textContent = text;
}

async function safeJSON(response) {

  const raw = await response.text();

  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new Error(
      "Server returned non-JSON response: "
      + raw.slice(0, 500)
    );
  }
}

async function runMission() {

  const objective =
    document.getElementById(
      "objective"
    ).value.trim();

  if (!objective) {
    alert(
      "Enter a mission objective first."
    );
    return;
  }

  const button =
    document.getElementById("run");

  button.disabled = true;

  document.getElementById(
    "result"
  ).textContent =
    "Submitting mission...";

  setProgress(
    5,
    "Mission accepted..."
  );

  try {

    const response = await fetch(
      "/mission",
      {
        method: "POST",
        headers: {
          "Content-Type":
            "application/json"
        },
        body: JSON.stringify({
          objective:
            objective,
          research: true,
          verify: true,
          remember: true
        })
      }
    );

    const data =
      await safeJSON(response);

    if (!response.ok) {
      throw new Error(
        data.message ||
        data.detail ||
        "Mission submission failed."
      );
    }

    const missionId =
      data.mission_id ||
      data.task_id;

    if (!missionId) {
      throw new Error(
        "Server did not return a mission ID."
      );
    }

    setProgress(
      12,
      "Mission queued..."
    );

    pollMission(
      missionId
    );

  } catch (error) {

    document.getElementById(
      "result"
    ).textContent =
      "Mission request failed:\n\n"
      + error.message;

    setProgress(
      0,
      "Mission failed."
    );

    button.disabled = false;
  }
}

async function pollMission(
  missionId
) {

  clearInterval(
    pollTimer
  );

  let attempts = 0;

  pollTimer = setInterval(
    async () => {

      attempts++;

      try {

        const response =
          await fetch(
            "/mission/"
            + encodeURIComponent(
              missionId
            )
          );

        const data =
          await safeJSON(
            response
          );

        if (!response.ok) {
          throw new Error(
            data.message ||
            data.detail ||
            "Status request failed."
          );
        }

        const status =
          data.status;

        if (status === "queued") {

          setProgress(
            15,
            "Mission queued..."
          );

        } else if (
          status === "running"
        ) {

          const estimated =
            Math.min(
              90,
              20 + attempts * 4
            );

          setProgress(
            estimated,
            "AI Infinity is researching..."
          );

        } else if (
          status === "completed"
        ) {

          clearInterval(
            pollTimer
          );

          setProgress(
            100,
            "Mission completed."
          );

          document.getElementById(
            "result"
          ).textContent =
            JSON.stringify(
              data.result,
              null,
              2
            );

          document.getElementById(
            "run"
          ).disabled = false;

        } else if (
          status === "failed"
        ) {

          clearInterval(
            pollTimer
          );

          setProgress(
            0,
            "Mission failed."
          );

          document.getElementById(
            "result"
          ).textContent =
            JSON.stringify(
              data,
              null,
              2
            );

          document.getElementById(
            "run"
          ).disabled = false;
        }

        if (attempts > 180) {

          clearInterval(
            pollTimer
          );

          setProgress(
            0,
            "Polling timeout. Mission may still be running."
          );

          document.getElementById(
            "run"
          ).disabled = false;
        }

      } catch (error) {

        clearInterval(
          pollTimer
        );

        document.getElementById(
          "result"
        ).textContent =
          "Polling failed:\n\n"
          + error.message;

        setProgress(
          0,
          "Connection error."
        );

        document.getElementById(
          "run"
        ).disabled = false;
      }

    },
    2500
  );
}
</script>

</body>
</html>
"""


# ============================================================
# STARTUP / SHUTDOWN
# ============================================================

@app.on_event("startup")
def startup():
    init_db()


@app.on_event("shutdown")
def shutdown():
    try:
        executor.shutdown(
            wait=False,
            cancel_futures=False,
        )
    except Exception:
        pass
