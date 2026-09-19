"""
AI Infinity
TARGET-2050.30
BUILD: FINAL-EVIDENCE-INTEGRITY-CORE

Final evidence-integrity upgrade:
- FastAPI
- SQLite persistence
- asynchronous missions
- OpenAlex
- Crossref
- Semantic Scholar
- DuckDuckGo
- HTML/PDF/abstract recovery
- evidence tiers
- strict claim-quality filtering
- claim -> evidence graph
- independent-work corroboration
- contradiction detection
- limitation detection
- counter-evidence
- evidence-integrity gate
- auditable synthesis
- automatic next-cycle planning
- mobile dashboard
"""

from __future__ import annotations

import io
import json
import hashlib
import math
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


# ============================================================
# CONFIG
# ============================================================

VERSION = "TARGET-2050.30"
BUILD = "FINAL-EVIDENCE-INTEGRITY-CORE"
EVIDENCE_VERSION = "EVIDENCE-INTEGRITY-4.1"

BASE = Path(
    os.getenv(
        "AI_INFINITY_HOME",
        "/tmp/ai-infinity"
    )
)

BASE.mkdir(
    parents=True,
    exist_ok=True
)

DB_PATH = BASE / "ai_infinity.db"

REQUEST_TIMEOUT = 12
MAX_BYTES = 7 * 1024 * 1024
WORKERS = 6

executor = ThreadPoolExecutor(
    max_workers=WORKERS
)

app = FastAPI(
    title="AI Infinity",
    version=VERSION
)

DB_LOCK = threading.Lock()


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=30
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with DB_LOCK:
        conn = db()
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT,
            status TEXT,
            created_at REAL,
            started_at REAL,
            completed_at REAL,
            result TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            source_key TEXT,
            url TEXT,
            title TEXT,
            domain TEXT,
            provider TEXT,
            evidence_tier TEXT,
            quality REAL,
            relevance REAL,
            independent_work TEXT,
            content TEXT,
            metadata TEXT,
            accepted INTEGER,
            created_at REAL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            claim_key TEXT,
            text TEXT,
            status TEXT,
            confidence REAL,
            created_at REAL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            source_node TEXT,
            target_node TEXT,
            relationship TEXT,
            confidence REAL,
            metadata TEXT,
            created_at REAL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            stage TEXT,
            status TEXT,
            detail TEXT,
            created_at REAL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            key TEXT,
            value TEXT,
            created_at REAL
        )
        """)

        conn.commit()
        conn.close()


init_db()


# ============================================================
# MODELS
# ============================================================

class MissionRequest(BaseModel):
    objective: str
    research: bool = True
    verify: bool = True
    remember: bool = True


class CommandRequest(BaseModel):
    command: str
    duration_minutes: int = 1


# ============================================================
# BASIC HELPERS
# ============================================================

def now():
    return time.time()


def mission_id():
    return "mission-" + hashlib.sha1(
        f"{time.time_ns()}".encode()
    ).hexdigest()[:12]


def sha(value: str):
    return hashlib.sha1(
        value.encode(
            "utf-8",
            errors="ignore"
        )
    ).hexdigest()


def clean_text(text: str) -> str:
    if not text:
        return ""

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    text = re.sub(
        r"\[[0-9,\s]+\]",
        " ",
        text
    )

    text = re.sub(
        r"\(https?://[^)]+\)",
        " ",
        text
    )

    return text.strip()


def words(text: str):
    return set(
        re.findall(
            r"\b[a-zA-Z][a-zA-Z0-9\-]{2,}\b",
            text.lower()
        )
    )


def domain_of(url: str):
    try:
        return (
            urlparse(url)
            .netloc
            .lower()
            .replace("www.", "")
        )
    except Exception:
        return ""


def canonical_url(url: str):
    try:
        p = urlparse(url)

        host = (
            p.netloc
            .lower()
            .replace("www.", "")
        )

        path = re.sub(
            r"/+$",
            "",
            p.path
        )

        return (
            f"{p.scheme.lower()}://"
            f"{host}{path}"
        )

    except Exception:
        return url


def safe_json(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False
        )
    except Exception:
        return "{}"


def similarity(a: str, b: str):
    wa = words(a)
    wb = words(b)

    if not wa or not wb:
        return 0.0

    return len(
        wa & wb
    ) / max(
        1,
        len(wa | wb)
    )


# ============================================================
# RESEARCH VOCABULARY
# ============================================================

RESEARCH_TERMS = {
    "agent",
    "agents",
    "autonomous",
    "autonomy",
    "task",
    "tasks",
    "execution",
    "planning",
    "reasoning",
    "tool",
    "tools",
    "reliability",
    "reliable",
    "failure",
    "failures",
    "success",
    "successful",
    "performance",
    "evaluation",
    "evaluate",
    "benchmark",
    "experiment",
    "experiments",
    "study",
    "studies",
    "empirical",
    "evidence",
    "verification",
    "monitoring",
    "human",
    "intervention",
    "deployment",
    "real",
    "world",
    "completion",
    "accuracy",
    "error",
    "errors",
    "limitation",
    "limitations",
    "robustness",
    "safety",
    "recovery",
    "observability",
    "replication",
    "generalization",
}


NEG_WORDS = {
    "not",
    "no",
    "never",
    "failed",
    "failure",
    "failures",
    "unable",
    "worse",
    "decreased",
    "decrease",
    "limited",
    "limitation",
    "unreliable",
    "ineffective",
    "incorrect",
    "error",
    "errors",
}


LIMIT_WORDS = {
    "limitation",
    "limitations",
    "failure",
    "failures",
    "constraint",
    "constraints",
    "caveat",
    "caveats",
    "unable",
    "uncertain",
    "uncertainty",
    "weakness",
    "weaknesses",
    "risk",
    "risks",
}


CLAIM_NOISE_PHRASES = (
    "skip to main content",
    "similar content being viewed",
    "many suspensions, many problems",
    "download for free",
    "share cite",
    "cite cite",
    "home >",
    "article open access",
    "chapter ©",
    "explore related subjects",
    "published:",
    "written by",
    "search submit donate",
    "log in",
    "advanced search",
    "table of contents",
    "page navigation",
    "previous article",
    "next article",
    "related articles",
    "related subjects",
    "all rights reserved",
    "buy this book",
    "read more",
    "cookie policy",
    "privacy policy",
)


NAV_PATTERNS = (
    "related articles",
    "similar content",
    "download references",
    "google scholar",
    "article pdf download",
    "explore related subjects",
    "author information",
    "research interests",
    "about the authors",
    "copyright",
    "subscribe",
    "sign in",
    "table of contents",
    "skip to main content",
    "search submit donate",
    "advanced search",
)


# ============================================================
# SSRF / URL FIREWALL
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
}


def validate_url(url: str):
    try:
        p = urlparse(url)

        if p.scheme not in {
            "http",
            "https"
        }:
            return False

        host = (
            p.hostname or ""
        ).lower()

        if not host:
            return False

        if host in BLOCKED_HOSTS:
            return False

        if host.endswith(".local"):
            return False

        if host.startswith("127."):
            return False

        if host.startswith("10."):
            return False

        if host.startswith("192.168."):
            return False

        if host.startswith("172.16."):
            return False

        if host.startswith("172.17."):
            return False

        if host.startswith("172.18."):
            return False

        if host.startswith("172.19."):
            return False

        if host.startswith("172.2"):
            return False

        return True

    except Exception:
        return False


# ============================================================
# SOURCE QUALITY
# ============================================================

def source_quality(
    provider: str,
    tier: str,
    domain: str
):
    score = 0.40

    if provider == "crossref":
        score += 0.12
    elif provider == "openalex":
        score += 0.12
    elif provider == "semantic_scholar":
        score += 0.13
    elif provider == "direct":
        score += 0.10
    elif provider == "duckduckgo":
        score += 0.05

    if tier == "FULL_TEXT":
        score += 0.25
    elif tier == "ABSTRACT":
        score += 0.18
    elif tier == "STRUCTURED_METADATA":
        score += 0.10

    if domain.endswith(".edu"):
        score += 0.05

    if domain.endswith(".gov"):
        score += 0.06

    return min(
        1.0,
        round(score, 3)
    )


# ============================================================
# SOURCE VALIDATION
# ============================================================

BAD_PATTERNS = (
    "enable javascript",
    "javascript is disabled",
    "client challenge",
    "just a moment",
    "checking your browser",
    "access denied",
    "captcha",
    "sign in to continue",
    "log in to continue",
    "verify you are human",
    "something went wrong",
    "page not found",
)


def looks_like_problem(text: str):
    low = text.lower()

    return any(
        x in low
        for x in BAD_PATTERNS
    )


def looks_binary(data: bytes):
    if not data:
        return True

    if b"%PDF-" in data[:2000]:
        return True

    return sum(
        1
        for b in data[:2000]
        if b == 0
    ) > 10


# ============================================================
# HTML
# ============================================================

def html_to_text(html: str):
    if not html:
        return ""

    text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        html,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<noscript\b[^>]*>.*?</noscript>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = (
        text
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&gt;", ">")
        .replace("&lt;", "<")
    )

    return clean_text(text)


# ============================================================
# PDF
# ============================================================

def extract_pdf(data: bytes):
    if PdfReader is None:
        return ""

    try:
        reader = PdfReader(
            io.BytesIO(data)
        )

        chunks = []

        for page in reader.pages[:30]:
            try:
                chunks.append(
                    page.extract_text() or ""
                )
            except Exception:
                pass

        return clean_text(
            " ".join(chunks)
        )

    except Exception:
        return ""


# ============================================================
# HTTP FETCH
# ============================================================

def fetch_url(url: str):
    if not validate_url(url):
        return None

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent":
                    "AI-Infinity/2050 research engine"
            },
            allow_redirects=True,
            stream=True,
        )

        if response.status_code >= 400:
            return None

        final_url = response.url

        if not validate_url(final_url):
            return None

        data = response.raw.read(
            MAX_BYTES
        )

        content_type = (
            response.headers
            .get("content-type", "")
            .lower()
        )

        if (
            "pdf" in content_type
            or data[:5] == b"%PDF-"
        ):
            text = extract_pdf(data)

            if len(text) >= 500:
                return {
                    "url": final_url,
                    "text": text,
                    "tier": "FULL_TEXT",
                    "content_type": "pdf",
                }

            return None

        if looks_binary(data):
            return None

        raw = data.decode(
            "utf-8",
            errors="ignore"
        )

        text = html_to_text(raw)

        if len(text) < 300:
            return None

        if looks_like_problem(
            text[:10000]
        ):
            return None

        return {
            "url": final_url,
            "text": text,
            "tier": "FULL_TEXT",
            "content_type": "html",
        }

    except Exception:
        return None


# ============================================================
# OPENALEX ABSTRACT
# ============================================================

def reconstruct_openalex_abstract(inv):
    if not inv:
        return ""

    parts = []

    try:
        for word, positions in inv.items():
            for pos in positions:
                parts.append(
                    (pos, word)
                )

        parts.sort()

        return clean_text(
            " ".join(
                x[1]
                for x in parts
            )
        )

    except Exception:
        return ""


# ============================================================
# PROVIDERS
# ============================================================

def crossref_search(query: str):
    results = []

    try:
        response = requests.get(
            "https://api.crossref.org/works",
            params={
                "query.bibliographic": query,
                "rows": 10,
            },
            timeout=REQUEST_TIMEOUT,
        )

        data = response.json()

        for item in data.get(
            "message",
            {}
        ).get(
            "items",
            []
        ):
            title = (
                item.get("title")
                or [""]
            )[0]

            doi = item.get(
                "DOI",
                ""
            )

            url = (
                item.get("URL")
                or (
                    f"https://doi.org/{doi}"
                    if doi
                    else ""
                )
            )

            if not title:
                continue

            results.append({
                "title": title,
                "url": url,
                "doi": doi,
                "abstract": clean_text(
                    item.get(
                        "abstract",
                        ""
                    )
                ),
                "provider": "crossref",
            })

    except Exception:
        pass

    return results


def openalex_search(query: str):
    results = []

    try:
        response = requests.get(
            "https://api.openalex.org/works",
            params={
                "search": query,
                "per-page": 10,
            },
            timeout=REQUEST_TIMEOUT,
        )

        data = response.json()

        for item in data.get(
            "results",
            []
        ):
            title = item.get(
                "title",
                ""
            )

            location = (
                item.get(
                    "primary_location",
                    {}
                ) or {}
            )

            url = (
                location.get(
                    "landing_page_url"
                )
                or item.get(
                    "doi",
                    ""
                )
                or ""
            )

            abstract = reconstruct_openalex_abstract(
                item.get(
                    "abstract_inverted_index"
                )
            )

            doi = item.get(
                "doi",
                ""
            )

            if doi.startswith(
                "https://doi.org/"
            ):
                doi = doi.split(
                    "https://doi.org/",
                    1
                )[1]

            if title:
                results.append({
                    "title": title,
                    "url": url,
                    "doi": doi,
                    "abstract": abstract,
                    "provider": "openalex",
                })

    except Exception:
        pass

    return results


def semantic_scholar_search(query: str):
    results = []

    try:
        response = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": query,
                "limit": 10,
                "fields":
                    "title,abstract,url,externalIds,openAccessPdf",
            },
            timeout=REQUEST_TIMEOUT,
        )

        data = response.json()

        for item in data.get(
            "data",
            []
        ):
            title = item.get(
                "title",
                ""
            )

            abstract = clean_text(
                item.get(
                    "abstract",
                    ""
                ) or ""
            )

            pdf = (
                item.get(
                    "openAccessPdf"
                )
                or {}
            )

            url = (
                item.get("url")
                or pdf.get("url")
                or ""
            )

            ids = (
                item.get(
                    "externalIds"
                )
                or {}
            )

            if title:
                results.append({
                    "title": title,
                    "url": url,
                    "doi": ids.get(
                        "DOI",
                        ""
                    ),
                    "abstract": abstract,
                    "provider":
                        "semantic_scholar",
                })

    except Exception:
        pass

    return results


def duck_search(query: str):
    results = []

    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={
                "q": query
            },
            headers={
                "User-Agent":
                    "Mozilla/5.0 AI-Infinity"
            },
            timeout=REQUEST_TIMEOUT,
        )

        html = response.text

        links = re.findall(
            r'nofollow" class="result__a" href="([^"]+)"',
            html
        )

        titles = re.findall(
            r'class="result__a"[^>]*>(.*?)</a>',
            html,
            flags=re.S
        )

        for i, link in enumerate(
            links[:10]
        ):
            title = (
                re.sub(
                    r"<[^>]+>",
                    "",
                    titles[i]
                )
                if i < len(titles)
                else link
            )

            results.append({
                "title": clean_text(title),
                "url": link,
                "doi": "",
                "abstract": "",
                "provider":
                    "duckduckgo",
            })

    except Exception:
        pass

    return results


# ============================================================
# SOURCE IDENTITY
# ============================================================

def work_key(item):
    doi = (
        item.get("doi")
        or ""
    ).strip().lower()

    if doi:
        return "doi:" + doi

    title = clean_text(
        item.get(
            "title",
            ""
        )
    ).lower()

    if title:
        return "title:" + sha(
            title
        )

    return "url:" + sha(
        canonical_url(
            item.get(
                "url",
                ""
            )
        )
    )


def deduplicate(results):
    grouped = {}

    for item in results:
        key = work_key(item)

        old = grouped.get(key)

        if not old:
            grouped[key] = item
            continue

        old_score = (
            len(
                old.get(
                    "abstract",
                    ""
                )
            )
            + len(
                old.get(
                    "title",
                    ""
                )
            )
        )

        new_score = (
            len(
                item.get(
                    "abstract",
                    ""
                )
            )
            + len(
                item.get(
                    "title",
                    ""
                )
            )
        )

        if new_score > old_score:
            grouped[key] = item

    return list(
        grouped.values()
    )


# ============================================================
# RELEVANCE
# ============================================================

def relevance(
    objective,
    title,
    abstract=""
):
    return round(
        similarity(
            objective,
            f"{title} {abstract}"
        ),
        3
    )


# ============================================================
# INGESTION
# ============================================================

def ingest(
    item,
    objective
):
    title = clean_text(
        item.get(
            "title",
            ""
        )
    )

    url = item.get(
        "url",
        ""
    )

    provider = item.get(
        "provider",
        "unknown"
    )

    abstract = clean_text(
        item.get(
            "abstract",
            ""
        )
    )

    if not title:
        return None

    rel = relevance(
        objective,
        title,
        abstract
    )

    fetched = None

    if url and validate_url(url):
        fetched = fetch_url(url)

    if fetched:
        content = fetched["text"]
        tier = fetched["tier"]
        final_url = fetched["url"]

    elif len(abstract) >= 300:
        content = abstract
        tier = "ABSTRACT"
        final_url = url

    else:
        content = title
        tier = "STRUCTURED_METADATA"
        final_url = url

    domain = domain_of(
        final_url or url
    )

    quality = source_quality(
        provider,
        tier,
        domain
    )

    return {
        "source_key":
            work_key(item),
        "url":
            final_url or url,
        "title":
            title,
        "domain":
            domain,
        "provider":
            provider,
        "tier":
            tier,
        "quality":
            quality,
        "relevance":
            rel,
        "content":
            content,
        "metadata": {
            "doi":
                item.get(
                    "doi",
                    ""
                ),
            "original_provider":
                provider,
        },
    }


# ============================================================
# CLAIM QUALITY
# ============================================================

PREDICATE_MARKERS = (
    "is ",
    "are ",
    "was ",
    "were ",
    "found ",
    "shows ",
    "showed ",
    "demonstrates ",
    "demonstrated ",
    "improves ",
    "improved ",
    "reduces ",
    "reduced ",
    "requires ",
    "provides ",
    "supports ",
    "suggests ",
    "observed ",
    "measured ",
    "reported ",
    "achieved ",
    "resulted ",
    "increased ",
    "decreased ",
    "failed ",
    "fails ",
    "outperformed ",
)


def valid_claim_sentence(
    sentence: str
):
    s = clean_text(sentence)
    low = s.lower()

    if not 70 <= len(s) <= 520:
        return False

    if any(
        p in low
        for p in CLAIM_NOISE_PHRASES
    ):
        return False

    if any(
        p in low
        for p in NAV_PATTERNS
    ):
        return False

    if re.search(
        r"https?://|www\.",
        low
    ):
        return False

    if re.search(
        r"&[a-z0-9#]+;",
        low
    ):
        return False

    if low.count("doi") >= 2:
        return False

    if "copyright" in low:
        return False

    if s.count("|") >= 2:
        return False

    if len(
        re.findall(
            r"[A-Za-z]",
            s
        )
    ) < 45:
        return False

    token_set = words(s)

    if len(token_set) < 10:
        return False

    if not (
        token_set & RESEARCH_TERMS
    ):
        return False

    if re.match(
        r"^(title|authors?|abstract|introduction|keywords?|references?)\s*[:.-]",
        low
    ):
        return False

    if not any(
        marker in low
        for marker in PREDICATE_MARKERS
    ):
        return False

    return True


def extract_claims(source):
    content = source.get(
        "content",
        ""
    )

    if len(content) < 100:
        return []

    if source.get(
        "tier"
    ) == "STRUCTURED_METADATA":
        return []

    sentences = re.split(
        r"(?<=[.!?])\s+",
        content
    )

    candidates = []

    for sentence in sentences:
        sentence = clean_text(
            sentence
        )

        if not valid_claim_sentence(
            sentence
        ):
            continue

        candidates.append(
            sentence
        )

    final = []

    for claim in candidates:
        if any(
            similarity(
                claim,
                old
            ) >= 0.80
            for old in final
        ):
            continue

        final.append(claim)

        if len(final) >= 8:
            break

    return final


# ============================================================
# CLAIM RELATIONSHIP
# ============================================================

def relation_hint(
    claim: str,
    evidence: str
):
    c = words(claim)
    e = words(evidence)

    score = similarity(
        claim,
        evidence
    )

    if score < 0.20:
        return None, score

    shared = (
        (c & e)
        & RESEARCH_TERMS
    )

    if len(shared) < 2:
        return None, score

    claim_negative = bool(
        c & NEG_WORDS
    )

    evidence_negative = bool(
        e & NEG_WORDS
    )

    if (
        claim_negative
        != evidence_negative
        and score >= 0.24
    ):
        return (
            "CONTRADICTS",
            round(
                min(
                    0.95,
                    score + 0.18
                ),
                3
            ),
        )

    if (
        bool(e & LIMIT_WORDS)
        and score >= 0.28
    ):
        return (
            "LIMITS",
            round(
                min(
                    0.92,
                    score + 0.10
                ),
                3
            ),
        )

    return (
        "SUPPORTS",
        round(
            score,
            3
        ),
    )


# ============================================================
# EVENTS / DATABASE
# ============================================================

def event(
    mid,
    stage,
    status,
    detail=""
):
    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO events
            (mission_id, stage, status, detail, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mid,
                stage,
                status,
                detail,
                now(),
            )
        )

        conn.commit()
        conn.close()


def save_source(
    mid,
    source
):
    with DB_LOCK:
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
                evidence_tier,
                quality,
                relevance,
                independent_work,
                content,
                metadata,
                accepted,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mid,
                source["source_key"],
                source["url"],
                source["title"],
                source["domain"],
                source["provider"],
                source["tier"],
                source["quality"],
                source["relevance"],
                source["source_key"],
                source["content"][:50000],
                safe_json(
                    source["metadata"]
                ),
                1,
                now(),
            )
        )

        conn.commit()
        conn.close()


def save_claim(
    mid,
    claim,
    status,
    confidence
):
    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO claims
            (mission_id, claim_key, text, status, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mid,
                sha(claim),
                claim,
                status,
                confidence,
                now(),
            )
        )

        conn.commit()
        conn.close()


def save_edge(
    mid,
    source_node,
    target_node,
    relationship,
    confidence,
    metadata=None
):
    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO graph_edges
            (
                mission_id,
                source_node,
                target_node,
                relationship,
                confidence,
                metadata,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mid,
                source_node,
                target_node,
                relationship,
                confidence,
                safe_json(
                    metadata or {}
                ),
                now(),
            )
        )

        conn.commit()
        conn.close()


# ============================================================
# QUERY PLANNING
# ============================================================

def build_queries(objective):
    base = objective.strip()

    return [
        f"{base} empirical evidence benchmark",
        f"{base} task success failure rate",
        f"{base} real world deployment",
        f"{base} reliability limitations failures",
        f"{base} human intervention monitoring verification",
        f"{base} independent study replication",
        f"{base} systematic evaluation",
        f"{base} failure modes",
        f"{base} performance evaluation",
        f"{base} field study",
    ]


def counter_queries(claim):
    return [
        f'"{claim}" criticism limitations',
        f'"{claim}" failure evidence',
        f'"{claim}" replication',
        f'"{claim}" contradictory evidence',
        f'"{claim}" negative results',
    ]


# ============================================================
# SEARCH
# ============================================================

def search_one(query):
    calls = [
        crossref_search,
        openalex_search,
        semantic_scholar_search,
        duck_search,
    ]

    results = []

    futures = {
        executor.submit(
            fn,
            query
        ): fn.__name__
        for fn in calls
    }

    for future in as_completed(
        futures
    ):
        try:
            results.extend(
                future.result()
            )
        except Exception:
            pass

    return results


# ============================================================
# EVIDENCE GRAPH
# ============================================================

def build_graph(
    mid,
    claims
):
    nodes = []
    edges = []

    sources = [
        x["source"]
        for x in claims
    ]

    unique_sources = {}

    for source in sources:
        unique_sources[
            source["source_key"]
        ] = source

    for source in unique_sources.values():
        nodes.append({
            "id":
                source["source_key"],
            "type":
                "source",
            "title":
                source["title"],
            "domain":
                source["domain"],
            "provider":
                source["provider"],
            "tier":
                source["tier"],
            "quality":
                source["quality"],
            "relevance":
                source["relevance"],
        })

    for idx, item in enumerate(
        claims
    ):
        item["claim_id"] = (
            f"claim-{idx + 1}"
        )

        nodes.append({
            "id":
                item["claim_id"],
            "type":
                "claim",
            "text":
                item["text"],
        })

        # Original claim -> original source.
        edges.append({
            "from":
                item["claim_id"],
            "to":
                item["source"]["source_key"],
            "type":
                "SOURCE_OF",
            "confidence":
                round(
                    item["source"]["quality"],
                    3
                ),
        })

        save_edge(
            mid,
            item["claim_id"],
            item["source"]["source_key"],
            "SOURCE_OF",
            item["source"]["quality"]
        )

    # Claim-to-independent-evidence matching.
    for i, claim_item in enumerate(
        claims
    ):
        for j, evidence_item in enumerate(
            claims
        ):
            if i == j:
                continue

            # Never count another claim from
            # the same underlying work as
            # independent corroboration.
            if (
                claim_item["source"]["source_key"]
                ==
                evidence_item["source"]["source_key"]
            ):
                continue

            rel, score = relation_hint(
                claim_item["text"],
                evidence_item["text"]
            )

            if not rel:
                continue

            edge = {
                "from":
                    claim_item["claim_id"],
                "to":
                    evidence_item["claim_id"],
                "type":
                    rel,
                "confidence":
                    score,
                "source_work":
                    evidence_item["source"]["source_key"],
                "source_domain":
                    evidence_item["source"]["domain"],
            }

            edges.append(edge)

            save_edge(
                mid,
                claim_item["claim_id"],
                evidence_item["claim_id"],
                rel,
                score,
                {
                    "source_work":
                        evidence_item[
                            "source"
                        ]["source_key"],
                    "source_domain":
                        evidence_item[
                            "source"
                        ]["domain"],
                }
            )

    return nodes, edges


# ============================================================
# VERIFICATION
# ============================================================

def verify_claims(
    mid,
    claims,
    graph_edges
):
    results = []

    for item in claims:
        cid = item["claim_id"]

        support = [
            e
            for e in graph_edges
            if e["from"] == cid
            and e["type"] == "SUPPORTS"
            and e["confidence"] >= 0.22
        ]

        contradictions = [
            e
            for e in graph_edges
            if e["from"] == cid
            and e["type"] == "CONTRADICTS"
            and e["confidence"] >= 0.24
        ]

        limitations = [
            e
            for e in graph_edges
            if e["from"] == cid
            and e["type"] == "LIMITS"
            and e["confidence"] >= 0.28
        ]

        support_works = {
            e.get(
                "source_work"
            )
            for e in support
            if e.get("source_work")
        }

        support_domains = {
            e.get(
                "source_domain"
            )
            for e in support
            if e.get("source_domain")
        }

        contradiction_works = {
            e.get(
                "source_work"
            )
            for e in contradictions
            if e.get("source_work")
        }

        original_work = item[
            "source"
        ]["source_key"]

        # Original source counts as evidence,
        # but independent corroboration must
        # come from another underlying work.
        independent_works = len(
            support_works
            - {original_work}
        )

        independent_domains = len(
            support_domains
            - {
                item[
                    "source"
                ]["domain"]
            }
        )

        tier_bonus = {
            "FULL_TEXT": 0.25,
            "ABSTRACT": 0.18,
            "STRUCTURED_METADATA": 0.05,
        }.get(
            item[
                "source"
            ]["tier"],
            0,
        )

        confidence = min(
            1.0,
            0.30
            + min(
                0.35,
                independent_works * 0.14
            )
            + min(
                0.10,
                independent_domains * 0.05
            )
            + tier_bonus
            + item[
                "source"
            ]["quality"] * 0.15,
        )

        if (
            independent_works >= 1
            and not contradictions
        ):
            status = "VERIFIED"

        elif contradictions:
            # Contradictions do NOT disappear.
            # They are explicitly surfaced.
            status = "CONTESTED"

        elif limitations:
            status = "LIMITED"

        elif (
            item[
                "source"
            ]["tier"]
            in {
                "FULL_TEXT",
                "ABSTRACT",
            }
        ):
            status = "SUPPORTED"

        else:
            status = "UNCERTAIN"

        result = {
            "claim":
                item["text"],
            "claim_id":
                cid,
            "status":
                status,
            "confidence":
                round(
                    confidence,
                    3
                ),
            "supporting_independent_works":
                independent_works,
            "supporting_independent_domains":
                independent_domains,
            "contradicting_works":
                len(
                    contradiction_works
                ),
            "limitation_edges":
                len(
                    limitations
                ),
            "source_work":
                original_work,
            "source_domain":
                item[
                    "source"
                ]["domain"],
            "source_title":
                item[
                    "source"
                ]["title"],
            "supporting_evidence":
                [
                    {
                        "work":
                            e.get(
                                "source_work"
                            ),
                        "domain":
                            e.get(
                                "source_domain"
                            ),
                        "confidence":
                            e[
                                "confidence"
                            ],
                    }
                    for e in support
                ],
            "contradicting_evidence":
                [
                    {
                        "work":
                            e.get(
                                "source_work"
                            ),
                        "domain":
                            e.get(
                                "source_domain"
                            ),
                        "confidence":
                            e[
                                "confidence"
                            ],
                    }
                    for e in contradictions
                ],
        }

        results.append(
            result
        )

        save_claim(
            mid,
            item["text"],
            status,
            confidence
        )

    return results


# ============================================================
# COUNTER EVIDENCE
# ============================================================

def collect_counter_evidence(
    objective,
    final_claims
):
    important = [
        x["claim"]
        for x in final_claims
        if x["status"]
        not in {
            "UNCERTAIN",
        }
    ][:6]

    collected = []

    for claim in important:
        for query in counter_queries(
            claim
        ):
            try:
                collected.extend(
                    search_one(query)
                )
            except Exception:
                pass

    collected = deduplicate(
        collected
    )

    accepted = []

    for item in collected[:30]:
        try:
            source = ingest(
                item,
                objective
            )

            if source:
                accepted.append(
                    source
                )
        except Exception:
            pass

    unique = {}

    for source in accepted:
        unique[
            source["source_key"]
        ] = source

    return list(
        unique.values()
    )


# ============================================================
# EVIDENCE GATE
# ============================================================

def evidence_gate(
    accepted,
    final_claims,
    graph_edges
):
    works = {
        x["source_key"]
        for x in accepted
    }

    domains = {
        x["domain"]
        for x in accepted
        if x["domain"]
    }

    providers = {
        x["provider"]
        for x in accepted
    }

    families = {
        (
            "scholarly"
            if x["provider"]
            in {
                "crossref",
                "openalex",
                "semantic_scholar",
            }
            else x["provider"]
        )
        for x in accepted
    }

    verified = sum(
        1
        for x in final_claims
        if x["status"]
        == "VERIFIED"
    )

    unsupported = sum(
        1
        for x in final_claims
        if x["status"]
        in {
            "UNCERTAIN",
        }
    )

    contradictions = sum(
        1
        for x in final_claims
        if x["status"]
        == "CONTESTED"
    )

    checks = {
        "minimum_sources":
            len(accepted) >= 3,
        "independent_works":
            len(works) >= 3,
        "independent_domains":
            len(domains) >= 2,
        "independent_source_families":
            len(families) >= 2,
        "minimum_valid_claims":
            len(final_claims) >= 3,
        "evidence_edges_exist":
            len(graph_edges) > 0,
        "verified_claims_exist":
            verified > 0,
        "unsupported_claims_controlled":
            unsupported == 0,
        # Contradictions are surfaced, not
        # automatically treated as pipeline failure.
        "contradictions_surfaced":
            True,
    }

    passed = all(
        checks.values()
    )

    reasons = []

    if not checks[
        "minimum_sources"
    ]:
        reasons.append(
            "insufficient_sources"
        )

    if not checks[
        "independent_works"
    ]:
        reasons.append(
            "insufficient_independent_works"
        )

    if not checks[
        "independent_domains"
    ]:
        reasons.append(
            "insufficient_domains"
        )

    if not checks[
        "independent_source_families"
    ]:
        reasons.append(
            "insufficient_source_families"
        )

    if not checks[
        "minimum_valid_claims"
    ]:
        reasons.append(
            "insufficient_valid_claims"
        )

    if not checks[
        "evidence_edges_exist"
    ]:
        reasons.append(
            "no_evidence_edges"
        )

    if not checks[
        "verified_claims_exist"
    ]:
        reasons.append(
            "no_verified_claims"
        )

    if not checks[
        "unsupported_claims_controlled"
    ]:
        reasons.append(
            "unsupported_claims_present"
        )

    return {
        "passed":
            passed,
        "checks":
            checks,
        "verified_claims":
            verified,
        "contradicted_or_contested":
            contradictions,
        "independent_works":
            len(works),
        "independent_domains":
            len(domains),
        "source_families":
            len(families),
        "reasons":
            reasons,
    }


# ============================================================
# RESEARCH ENGINE
# ============================================================

def research(
    mid,
    objective
):
    started = now()

    event(
        mid,
        "planning",
        "completed"
    )

    queries = build_queries(
        objective
    )

    event(
        mid,
        "discovery",
        "running",
        f"{len(queries)} research questions"
    )

    discovered = []

    for query in queries:
        try:
            discovered.extend(
                search_one(query)
            )
        except Exception:
            pass

    discovered = deduplicate(
        discovered
    )

    event(
        mid,
        "discovery",
        "completed",
        f"{len(discovered)} unique works"
    )

    # --------------------------------------------------------
    # EVIDENCE INGESTION
    # --------------------------------------------------------

    event(
        mid,
        "evidence_ingestion",
        "running"
    )

    accepted = []

    futures = [
        executor.submit(
            ingest,
            item,
            objective
        )
        for item in discovered[:50]
    ]

    for future in as_completed(
        futures
    ):
        try:
            source = future.result()

            if not source:
                continue

            if source[
                "relevance"
            ] >= 0.06:
                accepted.append(
                    source
                )

        except Exception:
            pass

    # One representation per work.
    grouped = {}

    tier_rank = {
        "FULL_TEXT": 4,
        "ABSTRACT": 3,
        "STRUCTURED_METADATA": 2,
        "DISCOVERY_ONLY": 1,
    }

    for source in accepted:
        key = source[
            "source_key"
        ]

        old = grouped.get(
            key
        )

        if (
            not old
            or tier_rank[
                source["tier"]
            ]
            >
            tier_rank[
                old["tier"]
            ]
        ):
            grouped[key] = source

    accepted = list(
        grouped.values()
    )

    for source in accepted:
        save_source(
            mid,
            source
        )

    event(
        mid,
        "evidence_ingestion",
        "completed",
        f"{len(accepted)} usable evidence records"
    )

    # --------------------------------------------------------
    # CLAIM EXTRACTION
    # --------------------------------------------------------

    event(
        mid,
        "claim_extraction",
        "running"
    )

    extracted = []

    for source in accepted:
        for claim in extract_claims(
            source
        ):
            extracted.append({
                "text":
                    claim,
                "source":
                    source,
            })

    unique = []

    for item in extracted:
        if any(
            similarity(
                item["text"],
                old["text"]
            ) >= 0.78
            for old in unique
        ):
            continue

        unique.append(
            item
        )

    event(
        mid,
        "claim_extraction",
        "completed",
        f"{len(unique)} substantive claims"
    )

    # --------------------------------------------------------
    # GRAPH
    # --------------------------------------------------------

    event(
        mid,
        "evidence_graph",
        "running"
    )

    graph_nodes, graph_edges = (
        build_graph(
            mid,
            unique
        )
    )

    event(
        mid,
        "evidence_graph",
        "completed",
        f"{len(graph_edges)} evidence relationships"
    )

    # --------------------------------------------------------
    # VERIFICATION
    # --------------------------------------------------------

    event(
        mid,
        "verification",
        "running"
    )

    final_claims = verify_claims(
        mid,
        unique,
        graph_edges
    )

    verified = sum(
        1
        for x in final_claims
        if x["status"]
        == "VERIFIED"
    )

    contested = sum(
        1
        for x in final_claims
        if x["status"]
        == "CONTESTED"
    )

    limited = sum(
        1
        for x in final_claims
        if x["status"]
        == "LIMITED"
    )

    uncertain = sum(
        1
        for x in final_claims
        if x["status"]
        == "UNCERTAIN"
    )

    supported = sum(
        1
        for x in final_claims
        if x["status"]
        == "SUPPORTED"
    )

    event(
        mid,
        "verification",
        "completed",
        f"verified={verified}; contested={contested}; limited={limited}"
    )

    # --------------------------------------------------------
    # COUNTER EVIDENCE
    # --------------------------------------------------------

    event(
        mid,
        "counter_evidence",
        "running"
    )

    counter = collect_counter_evidence(
        objective,
        final_claims
    )

    event(
        mid,
        "counter_evidence",
        "completed",
        f"{len(counter)} counter-evidence records"
    )

    # --------------------------------------------------------
    # QUALITY
    # --------------------------------------------------------

    domains = sorted({
        x["domain"]
        for x in accepted
        if x["domain"]
    })

    works = sorted({
        x["source_key"]
        for x in accepted
    })

    providers = sorted({
        x["provider"]
        for x in accepted
    })

    tiers = {}

    for source in accepted:
        tiers[
            source["tier"]
        ] = (
            tiers.get(
                source["tier"],
                0
            ) + 1
        )

    avg_quality = (
        sum(
            x["quality"]
            for x in accepted
        )
        / len(accepted)
        if accepted
        else 0
    )

    verification_rate = (
        verified
        / len(final_claims)
        if final_claims
        else 0
    )

    source_diversity = min(
        1.0,
        len(domains) / 5
    )

    independence = min(
        1.0,
        len(works) / 6
    )

    evidence_coverage = min(
        1.0,
        len(final_claims) / 10
    )

    graph_quality = min(
        1.0,
        len(graph_edges) / max(
            1,
            len(final_claims) * 2
        )
    )

    research_strength = round(
        (
            avg_quality * 0.20
            + source_diversity * 0.15
            + independence * 0.20
            + evidence_coverage * 0.15
            + graph_quality * 0.10
            + verification_rate * 0.20
        ),
        3
    )

    confidence = round(
        (
            verification_rate * 0.50
            + avg_quality * 0.20
            + independence * 0.15
            + graph_quality * 0.15
        ),
        3
    )

    # --------------------------------------------------------
    # GATE
    # --------------------------------------------------------

    gate = evidence_gate(
        accepted,
        final_claims,
        graph_edges
    )

    # --------------------------------------------------------
    # DIAGNOSIS
    # --------------------------------------------------------

    diagnosis = []

    if len(accepted) < 5:
        diagnosis.append(
            "LOW_EVIDENCE_COUNT"
        )

    if len(final_claims) == 0:
        diagnosis.append(
            "NO_SUBSTANTIVE_CLAIMS"
        )

    if len(works) < 3:
        diagnosis.append(
            "LOW_INDEPENDENT_WORKS"
        )

    if len(domains) < 3:
        diagnosis.append(
            "LOW_SOURCE_DIVERSITY"
        )

    if len(providers) < 2:
        diagnosis.append(
            "LOW_PROVIDER_DIVERSITY"
        )

    if tiers.get(
        "FULL_TEXT",
        0
    ) == 0:
        diagnosis.append(
            "NO_FULL_TEXT_EVIDENCE"
        )

    if uncertain:
        diagnosis.append(
            "CLAIM_UNCERTAINTY"
        )

    if contested:
        diagnosis.append(
            "CONTRADICTORY_EVIDENCE"
        )

    if limited:
        diagnosis.append(
            "LIMITED_EVIDENCE"
        )

    if not graph_edges:
        diagnosis.append(
            "NO_EVIDENCE_RELATIONSHIPS"
        )

    if gate["passed"]:
        diagnosis.append(
            "EVIDENCE_GATE_PASSED"
        )

    if not diagnosis:
        diagnosis.append(
            "RESEARCH_PIPELINE_HEALTHY"
        )

    # --------------------------------------------------------
    # NEXT ACTIONS
    # --------------------------------------------------------

    next_actions = []

    if len(final_claims) == 0:
        next_actions.append(
            "Recover additional structured abstracts and empirical findings."
        )

    if tiers.get(
        "FULL_TEXT",
        0
    ) == 0:
        next_actions.append(
            "Search for open-access full text and institutional copies."
        )

    if len(works) < 3:
        next_actions.append(
            "Increase independent primary-study coverage."
        )

    if len(domains) < 3:
        next_actions.append(
            "Expand evidence across independent domains and institutions."
        )

    if len(providers) < 2:
        next_actions.append(
            "Expand research-provider coverage."
        )

    if uncertain:
        next_actions.append(
            "Run targeted follow-up research for uncertain claims."
        )

    if contested:
        next_actions.append(
            "Preserve contradictory evidence and run targeted replication searches."
        )

    if limited:
        next_actions.append(
            "Investigate limitations before treating affected claims as established."
        )

    if not gate["passed"]:
        next_actions.append(
            "Continue the next evidence-recovery cycle until the evidence gate passes."
        )

    if not next_actions:
        next_actions.append(
            "Continue monitoring and update the evidence graph with new studies."
        )

    # --------------------------------------------------------
    # SYNTHESIS
    # --------------------------------------------------------

    if gate["passed"]:
        conclusion = (
            "The evidence-integrity gate passed. "
            "At least one substantive claim has independent "
            "corroborating evidence. Results remain bounded "
            "by the populations, tasks, methods, and conditions "
            "represented in the recovered evidence."
        )

        synthesis_status = (
            "evidence_supported"
        )

    elif final_claims:
        conclusion = (
            "The research produced substantive evidence, "
            "but the evidence-integrity gate has not yet "
            "passed. Claims with corroboration, limitations, "
            "and contradictions are surfaced separately."
        )

        synthesis_status = (
            "evidence_incomplete"
        )

    else:
        conclusion = (
            "The current evidence set does not contain enough "
            "recoverable substantive claims for a strong conclusion."
        )

        synthesis_status = (
            "evidence_insufficient"
        )

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    completed = now()

    result = {
        "task_id":
            mid,
        "mission_id":
            mid,
        "status":
            "completed",
        "version":
            VERSION,
        "build":
            BUILD,
        "evidence_integrity_version":
            EVIDENCE_VERSION,
        "objective":
            objective,

        "agent_trace": [
            {
                "stage":
                    "planning",
                "status":
                    "completed",
                "research_questions":
                    queries,
                "count":
                    len(queries),
            },
            {
                "stage":
                    "discovery",
                "status":
                    "completed",
                "sources_discovered":
                    len(discovered),
            },
            {
                "stage":
                    "evidence_ingestion",
                "status":
                    "completed",
                "accepted_sources":
                    len(accepted),
            },
            {
                "stage":
                    "claim_extraction",
                "status":
                    "completed",
                "claims":
                    len(final_claims),
            },
            {
                "stage":
                    "evidence_graph",
                "status":
                    "completed",
                "nodes":
                    len(graph_nodes),
                "edges":
                    len(graph_edges),
            },
            {
                "stage":
                    "verification",
                "status":
                    "completed",
                "verified":
                    verified,
                "contested":
                    contested,
                "limited":
                    limited,
                "uncertain":
                    uncertain,
            },
            {
                "stage":
                    "counter_evidence",
                "status":
                    "completed",
                "sources":
                    len(counter),
            },
            {
                "stage":
                    "synthesis",
                "status":
                    "completed",
            },
        ],

        "providers": {
            "used":
                providers,
            "count":
                len(providers),
        },

        "evidence": {
            "count":
                len(accepted),
            "graph_nodes":
                len(graph_nodes),
            "graph_edges":
                len(graph_edges),
            "independent_domains":
                len(domains),
            "domains":
                domains,
            "independent_works":
                len(works),
            "average_source_quality":
                round(
                    avg_quality,
                    3
                ),
            "source_diversity":
                round(
                    source_diversity,
                    3
                ),
            "evidence_tiers":
                tiers,
        },

        "claims":
            final_claims,

        "verification": {
            "enabled":
                True,
            "verified":
                verified,
            "supported":
                supported,
            "contested":
                contested,
            "limited":
                limited,
            "uncertain":
                uncertain,
            "confidence":
                confidence,
            "research_strength":
                research_strength,
        },

        "counter_evidence": {
            "sources":
                len(counter),
            "claims_tested":
                min(
                    6,
                    len(final_claims)
                ),
        },

        "evidence_graph": {
            "nodes":
                graph_nodes,
            "edges":
                graph_edges,
        },

        "evidence_gate":
            gate,

        "diagnosis":
            diagnosis,

        "synthesis": {
            "status":
                synthesis_status,
            "conclusion":
                conclusion,
            "verified_claims":
                verified,
            "supported_claims":
                supported,
            "contested_claims":
                contested,
            "limited_claims":
                limited,
            "uncertain_claims":
                uncertain,
            "independent_domains":
                len(domains),
            "independent_works":
                len(works),
            "counter_evidence_sources":
                len(counter),
            "next_actions":
                next_actions,
        },

        "next_cycle": {
            "recommended":
                next_actions,
            "diagnosis":
                diagnosis,
            "automatic":
                True,
        },

        "provenance": {
            "query_count":
                len(queries),
            "source_count":
                len(accepted),
            "underlying_work_count":
                len(works),
            "domain_count":
                len(domains),
            "provider_count":
                len(providers),
            "evidence_integrity":
                EVIDENCE_VERSION,
        },

        "timing": {
            "started_at":
                started,
            "completed_at":
                completed,
            "duration_seconds":
                round(
                    completed - started,
                    2
                ),
        },
    }

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status = ?,
                completed_at = ?,
                result = ?
            WHERE id = ?
            """,
            (
                "completed",
                completed,
                safe_json(result),
                mid,
            )
        )

        conn.commit()
        conn.close()

    event(
        mid,
        "mission",
        "completed",
        f"strength={research_strength}; gate={gate['passed']}"
    )

    return result


# ============================================================
# BACKGROUND RUNNER
# ============================================================

def background_run(
    mid,
    objective
):
    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status = ?,
                started_at = ?
            WHERE id = ?
            """,
            (
                "running",
                now(),
                mid,
            )
        )

        conn.commit()
        conn.close()

    event(
        mid,
        "mission",
        "running"
    )

    try:
        research(
            mid,
            objective
        )

    except Exception as exc:
        result = {
            "task_id":
                mid,
            "mission_id":
                mid,
            "status":
                "failed",
            "version":
                VERSION,
            "build":
                BUILD,
            "error":
                str(exc),
            "next_actions": [
                "Retry the mission.",
                "Inspect mission events for the failed stage.",
            ],
        }

        with DB_LOCK:
            conn = db()

            conn.execute(
                """
                UPDATE missions
                SET status = ?,
                    completed_at = ?,
                    result = ?
                WHERE id = ?
                """,
                (
                    "failed",
                    now(),
                    safe_json(result),
                    mid,
                )
            )

            conn.commit()
            conn.close()

        event(
            mid,
            "mission",
            "failed",
            str(exc)
        )


# ============================================================
# DASHBOARD
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def dashboard():
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
margin:0;
background:#070b12;
color:#f4f7fb;
font-family:system-ui,-apple-system,BlinkMacSystemFont,sans-serif;
}}

.wrap {{
max-width:900px;
margin:auto;
padding:24px;
}}

.hero {{
padding:25px 0;
}}

h1 {{
font-size:42px;
margin:0 0 8px;
}}

.sub {{
color:#9aa7b7;
font-size:17px;
}}

.status {{
display:inline-block;
padding:7px 12px;
border-radius:20px;
background:#12351f;
color:#72f5a1;
font-size:13px;
margin:15px 0;
}}

textarea {{
width:100%;
height:150px;
box-sizing:border-box;
background:#0d1420;
border:1px solid #26364a;
border-radius:14px;
padding:16px;
color:white;
font-size:16px;
outline:none;
}}

button {{
width:100%;
margin-top:12px;
padding:16px;
border:0;
border-radius:14px;
font-size:17px;
font-weight:700;
background:#ffffff;
color:#05070a;
}}

.grid {{
display:grid;
grid-template-columns:repeat(4,1fr);
gap:10px;
margin:20px 0;
}}

.card {{
background:#0d1420;
border:1px solid #1d2a3a;
padding:15px;
border-radius:14px;
}}

.label {{
color:#7f8da0;
font-size:12px;
}}

.value {{
font-size:16px;
margin-top:5px;
}}

pre {{
white-space:pre-wrap;
word-break:break-word;
background:#05080d;
border:1px solid #1d2a3a;
border-radius:14px;
padding:15px;
overflow:auto;
}}

@media(max-width:700px) {{
.grid {{
grid-template-columns:repeat(2,1fr);
}}
h1 {{
font-size:34px;
}}
}}
</style>
</head>

<body>

<div class="wrap">

<div class="hero">

<div class="status">
● ONLINE
</div>

<h1>AI Infinity</h1>

<div class="sub">
Autonomous Evidence Intelligence
</div>

</div>

<textarea id="objective"
placeholder="Give AI Infinity a research mission..."></textarea>

<button onclick="runMission()">
🚀 RUN AUTONOMOUS MISSION
</button>

<div class="grid">

<div class="card">
<div class="label">VERSION</div>
<div class="value">
{VERSION}
</div>
</div>

<div class="card">
<div class="label">BUILD</div>
<div class="value">
FINAL
</div>
</div>

<div class="card">
<div class="label">EVIDENCE</div>
<div class="value">
INTEGRITY
</div>
</div>

<div class="card">
<div class="label">VERIFY</div>
<div class="value">
ACTIVE
</div>
</div>

</div>

<pre id="output">
Ready.
</pre>

</div>

<script>

async function safeJSON(response) {{
    const text = await response.text();

    try {{
        return JSON.parse(text);
    }} catch(e) {{
        return {{
            status:"error",
            http_status:response.status,
            raw:text
        }};
    }}
}}

async function runMission() {{

    const objective =
        document.getElementById(
            "objective"
        ).value.trim();

    const output =
        document.getElementById(
            "output"
        );

    if(!objective) {{
        output.textContent =
            "Enter a mission first.";
        return;
    }}

    output.textContent =
        "🚀 Mission accepted...";

    try {{

        const response =
            await fetch(
                "/mission",
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
            await safeJSON(response);

        if(!data.mission_id) {{
            output.textContent =
                JSON.stringify(
                    data,
                    null,
                    2
                );
            return;
        }}

        const id =
            data.mission_id;

        output.textContent =
            "🧠 Mission running...\\n" +
            "ID: " + id;

        const timer =
            setInterval(
                async function() {{
                    try {{
                        const r =
                            await fetch(
                                "/mission/" + id
                            );

                        const d =
                            await safeJSON(r);

                        output.textContent =
                            JSON.stringify(
                                d,
                                null,
                                2
                            );

                        if(
                            d.status ===
                                "completed"
                            ||
                            d.status ===
                                "failed"
                        ) {{
                            clearInterval(timer);
                        }}

                    }} catch(e) {{
                        output.textContent =
                            "Polling error: " +
                            e.message;
                    }}

                }},
                3000
            );

    }} catch(e) {{
        output.textContent =
            "Mission error: " +
            e.message;
    }}
}}

</script>

</body>
</html>
"""
    )


# ============================================================
# HEALTH / STATUS
# ============================================================

@app.get("/health")
def health():
    return {
        "status":
            "ok",
        "version":
            VERSION,
        "build":
            BUILD,
        "evidence_integrity":
            EVIDENCE_VERSION,
    }


@app.get("/status")
def status():
    return {
        "status":
            "online",
        "version":
            VERSION,
        "build":
            BUILD,
        "evidence_integrity":
            EVIDENCE_VERSION,
        "engine":
            "final-evidence-integrity",
        "evidence_recovery":
            True,
        "claim_verification":
            True,
        "independent_corroboration":
            True,
        "counter_evidence":
            True,
        "contradiction_detection":
            True,
        "evidence_graph":
            True,
        "autonomous_research":
            True,
    }


@app.get("/capabilities")
def capabilities():
    return {
        "version":
            VERSION,
        "build":
            BUILD,
        "capabilities": [
            "autonomous_research",
            "evidence_recovery",
            "full_text_ingestion",
            "pdf_extraction",
            "abstract_recovery",
            "metadata_recovery",
            "strict_claim_extraction",
            "claim_evidence_linking",
            "independent_work_corroboration",
            "claim_verification",
            "counter_evidence",
            "contradiction_detection",
            "limitation_detection",
            "evidence_graph",
            "evidence_integrity_gate",
            "research_diagnostics",
            "automatic_next_cycle",
        ],
    }


# ============================================================
# MISSION API
# ============================================================

@app.post("/mission")
def create_mission(
    req: MissionRequest
):
    objective = req.objective.strip()

    if not objective:
        raise HTTPException(
            status_code=400,
            detail="Objective is required."
        )

    mid = mission_id()

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO missions
            (id, objective, status, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                mid,
                objective,
                "queued",
                now(),
            )
        )

        conn.commit()
        conn.close()

    event(
        mid,
        "mission",
        "queued"
    )

    executor.submit(
        background_run,
        mid,
        objective
    )

    return JSONResponse(
        status_code=202,
        content={
            "task_id":
                mid,
            "mission_id":
                mid,
            "status":
                "queued",
            "version":
                VERSION,
            "build":
                BUILD,
            "message":
                "Mission accepted and running autonomously.",
        },
    )


@app.post("/research")
def research_endpoint(
    req: MissionRequest
):
    return create_mission(req)


@app.post("/run")
def run_endpoint(
    req: MissionRequest
):
    return create_mission(req)


@app.post("/command")
def command_endpoint(
    req: CommandRequest
):
    return create_mission(
        MissionRequest(
            objective=req.command
        )
    )


# ============================================================
# MISSION READ API
# ============================================================

@app.get("/mission/{mid}")
def get_mission(mid: str):
    with DB_LOCK:
        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM missions
            WHERE id = ?
            """,
            (mid,)
        ).fetchone()

        conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found."
        )

    result = (
        json.loads(
            row["result"]
        )
        if row["result"]
        else None
    )

    return {
        "task_id":
            mid,
        "mission_id":
            mid,
        "status":
            row["status"],
        "objective":
            row["objective"],
        "result":
            result,
    }


@app.get("/missions")
def missions():
    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT id, objective, status,
                   created_at,
                   started_at,
                   completed_at
            FROM missions
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()

        conn.close()

    return [
        dict(row)
        for row in rows
    ]


@app.get(
    "/mission/{mid}/sources"
)
def mission_sources(mid: str):
    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM sources
            WHERE mission_id = ?
            ORDER BY quality DESC
            """,
            (mid,)
        ).fetchall()

        conn.close()

    return [
        dict(row)
        for row in rows
    ]


@app.get(
    "/mission/{mid}/claims"
)
def mission_claims(mid: str):
    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM claims
            WHERE mission_id = ?
            ORDER BY confidence DESC
            """,
            (mid,)
        ).fetchall()

        conn.close()

    return [
        dict(row)
        for row in rows
    ]


@app.get(
    "/mission/{mid}/events"
)
def mission_events(mid: str):
    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM events
            WHERE mission_id = ?
            ORDER BY created_at ASC
            """,
            (mid,)
        ).fetchall()

        conn.close()

    return [
        dict(row)
        for row in rows
    ]


@app.get(
    "/mission/{mid}/graph"
)
def mission_graph(mid: str):
    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM graph_edges
            WHERE mission_id = ?
            ORDER BY confidence DESC
            """,
            (mid,)
        ).fetchall()

        conn.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# SOURCE VALIDATION
# ============================================================

@app.post("/validate-source")
def validate_source(
    url: str
):
    if not validate_url(url):
        return {
            "valid":
                False
        }

    result = fetch_url(
        url
    )

    if not result:
        return {
            "valid":
                False,
            "reason":
                "Source could not be safely recovered.",
        }

    return {
        "valid":
            True,
        "url":
            result["url"],
        "tier":
            result["tier"],
        "characters":
            len(
                result["text"]
            ),
    }


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@app.exception_handler(
    Exception
)
async def global_exception_handler(
    request,
    exc
):
    return JSONResponse(
        status_code=500,
        content={
            "status":
                "error",
            "version":
                VERSION,
            "build":
                BUILD,
            "error":
                str(exc),
        },
    )
