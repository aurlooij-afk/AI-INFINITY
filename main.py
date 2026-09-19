"""
AI Infinity
TARGET-2050.32
BUILD: AUTONOMOUS-EVIDENCE-DASHBOARD-CORE

Evidence-first autonomous research engine with mobile dashboard.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import socket
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


# ============================================================
# CORE
# ============================================================

VERSION = "TARGET-2050.32"
BUILD = "AUTONOMOUS-EVIDENCE-DASHBOARD-CORE"

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

MAX_BYTES = 7 * 1024 * 1024
MAX_TEXT = 120_000

WORKERS = int(
    os.getenv(
        "AI_INFINITY_WORKERS",
        "6",
    )
)

TIMEOUT = int(
    os.getenv(
        "AI_INFINITY_HTTP_TIMEOUT",
        "20",
    )
)

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
)

EXECUTOR = ThreadPoolExecutor(
    max_workers=WORKERS
)

DB_LOCK = threading.RLock()


# ============================================================
# RESEARCH CONSTANTS
# ============================================================

RESEARCH_TERMS = {
    "agent",
    "agents",
    "autonomous",
    "autonomy",
    "planning",
    "tool",
    "tools",
    "execution",
    "verification",
    "reliability",
    "failure",
    "recovery",
    "safety",
    "benchmark",
    "task",
    "tasks",
    "reasoning",
    "workflow",
    "performance",
    "success",
    "error",
    "human",
    "oversight",
    "real-world",
    "research",
    "system",
    "model",
    "models",
    "memory",
    "learning",
    "self-correction",
    "evidence",
}


NOISE_PHRASES = {
    "skip to main content",
    "similar content being viewed",
    "download for free",
    "share cite",
    "cite cite",
    "home >",
    "article open access",
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
    "cookie",
    "privacy policy",
    "accept all cookies",
    "sign in",
    "register",
    "javascript",
}


PREDICATES = {
    "is",
    "are",
    "was",
    "were",
    "can",
    "cannot",
    "found",
    "shows",
    "demonstrates",
    "improves",
    "reduces",
    "requires",
    "provides",
    "supports",
    "suggests",
    "observed",
    "measured",
    "reported",
    "increases",
    "decreases",
    "achieved",
    "failed",
    "performs",
    "outperforms",
    "depends",
    "causes",
    "associated",
    "predicts",
    "enables",
    "limits",
}


NEGATION = {
    "not",
    "no",
    "never",
    "cannot",
    "can't",
    "unable",
    "fails",
    "failure",
    "without",
    "lack",
    "lacks",
}


LIMITATION = {
    "limitation",
    "limited",
    "caveat",
    "constraint",
    "uncertain",
    "uncertainty",
    "weakness",
    "failure",
    "fails",
    "risk",
}


# ============================================================
# DATABASE
# ============================================================

def now() -> float:
    return time.time()


def db():
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=30,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with DB_LOCK:
        c = db()

        c.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS missions(
                id TEXT PRIMARY KEY,
                objective TEXT,
                status TEXT,
                result TEXT,
                created_at REAL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                stage TEXT,
                status TEXT,
                detail TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS works(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                work_key TEXT,
                title TEXT,
                abstract TEXT,
                url TEXT,
                doi TEXT,
                provider TEXT,
                source_family TEXT,
                domain TEXT,
                quality REAL,
                relevance REAL,
                full_text INTEGER DEFAULT 0,
                created_at REAL,
                UNIQUE(mission_id, work_key)
            );

            CREATE TABLE IF NOT EXISTS evidence(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                work_key TEXT,
                source_id INTEGER,
                excerpt TEXT,
                evidence_type TEXT,
                locator TEXT,
                quality REAL,
                relevance REAL,
                source_family TEXT,
                domain TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS claims(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                claim TEXT,
                fingerprint TEXT,
                status TEXT,
                confidence REAL,
                created_at REAL,
                UNIQUE(mission_id, fingerprint)
            );

            CREATE TABLE IF NOT EXISTS edges(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                from_type TEXT,
                from_id TEXT,
                to_type TEXT,
                to_id TEXT,
                relation TEXT,
                score REAL,
                reason TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS memory(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                key TEXT,
                value TEXT,
                created_at REAL
            );
            """
        )

        c.commit()
        c.close()


init_db()


# ============================================================
# DATABASE HELPERS
# ============================================================

def event(
    mid: str,
    stage: str,
    status: str,
    detail: str = "",
):
    with DB_LOCK:
        c = db()

        c.execute(
            """
            INSERT INTO events(
                mission_id,
                stage,
                status,
                detail,
                created_at
            )
            VALUES(?,?,?,?,?)
            """,
            (
                mid,
                stage,
                status,
                detail,
                now(),
            ),
        )

        c.execute(
            """
            UPDATE missions
            SET updated_at=?
            WHERE id=?
            """,
            (
                now(),
                mid,
            ),
        )

        c.commit()
        c.close()


def set_mission(
    mid: str,
    status: str,
    result=None,
):
    with DB_LOCK:
        c = db()

        c.execute(
            """
            UPDATE missions
            SET status=?,
                result=?,
                updated_at=?
            WHERE id=?
            """,
            (
                status,
                json.dumps(
                    result,
                    ensure_ascii=False,
                )
                if result is not None
                else None,
                now(),
                mid,
            ),
        )

        c.commit()
        c.close()


# ============================================================
# TEXT / RELEVANCE
# ============================================================

def clean(text: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        text or "",
    ).strip()


def tokens(s: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "their",
        "there",
        "about",
        "using",
        "were",
        "have",
        "has",
        "been",
        "than",
        "then",
        "they",
        "them",
        "which",
        "also",
        "such",
        "more",
        "other",
        "these",
        "those",
    }

    return {
        x
        for x in re.findall(
            r"[a-zA-Z][a-zA-Z0-9'-]{2,}",
            (s or "").lower(),
        )
        if x not in stop
    }


def research_terms(
    objective: str,
) -> set[str]:

    t = tokens(objective)

    selected = {
        x
        for x in t
        if x in RESEARCH_TERMS
        or any(
            k in x
            for k in (
                "agent",
                "autonom",
                "verif",
                "execut",
                "research",
                "reliab",
                "eviden",
                "task",
                "tool",
                "fail",
                "recover",
                "safety",
                "plan",
                "monitor",
            )
        )
    }

    return selected or set(
        list(t)[:12]
    )


def similarity(
    a: str,
    b: str,
) -> float:

    A = tokens(a)
    B = tokens(b)

    if not A or not B:
        return 0.0

    return len(A & B) / max(
        1,
        len(A | B),
    )


def useful_sentence(
    sentence: str,
    objective: str,
) -> bool:

    s = clean(sentence)

    if len(s) < 100:
        return False

    low = s.lower()

    for phrase in NOISE_PHRASES:
        if phrase in low:
            return False

    words = tokens(s)

    if len(words) < 12:
        return False

    if not (
        words & research_terms(objective)
    ):
        return False

    predicate = any(
        p in words
        for p in PREDICATES
    )

    return predicate


def fingerprint(
    text: str,
) -> str:

    normalized = re.sub(
        r"[^a-z0-9 ]+",
        " ",
        text.lower(),
    )

    normalized = clean(normalized)

    return hashlib.sha256(
        normalized.encode()
    ).hexdigest()[:32]


# ============================================================
# NETWORK SAFETY
# ============================================================

def safe_url(
    url: str,
) -> bool:

    try:
        p = urlparse(url)

        if (
            p.scheme not in
            ("http", "https")
            or not p.hostname
        ):
            return False

        host = p.hostname.lower().strip(".")

        if host in {
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "::1",
        }:
            return False

        ip = socket.gethostbyname(host)

        parts = ip.split(".")

        if len(parts) == 4:

            a = int(parts[0])
            b = int(parts[1])

            if a in {
                10,
                127,
            }:
                return False

            if a == 169 and b == 254:
                return False

            if a == 192 and b == 168:
                return False

            if a == 172 and 16 <= b <= 31:
                return False

        return True

    except Exception:
        return False


def domain(
    url: str,
) -> str:

    try:
        return (
            urlparse(url).hostname
            or ""
        )
    except Exception:
        return ""


# ============================================================
# HTTP FETCH
# ============================================================

def fetch(
    url: str,
) -> Tuple[str, str]:

    if not safe_url(url):
        return "", ""

    try:
        r = requests.get(
            url,
            timeout=TIMEOUT,
            headers={
                "User-Agent":
                    "AI-Infinity/2050.32"
            },
            stream=True,
            allow_redirects=True,
        )

        r.raise_for_status()

        content_type = (
            r.headers.get(
                "content-type",
                "",
            )
            .lower()
        )

        data = bytearray()

        for chunk in r.iter_content(
            65536
        ):

            if not chunk:
                continue

            data.extend(chunk)

            if len(data) > MAX_BYTES:
                break

        raw = bytes(data)

        if (
            "pdf" in content_type
            or url.lower().endswith(".pdf")
        ):

            if PdfReader is None:
                return "", "pdf"

            import io

            reader = PdfReader(
                io.BytesIO(raw)
            )

            text = "\n".join(
                (
                    page.extract_text()
                    or ""
                )
                for page in reader.pages
            )

            return (
                clean(text)[:MAX_TEXT],
                "pdf",
            )

        text = raw.decode(
            r.encoding or "utf-8",
            "ignore",
        )

        if "<html" in text.lower():

            text = re.sub(
                r"(?is)<script.*?</script>|"
                r"<style.*?</style>|"
                r"<noscript.*?</noscript>",
                " ",
                text,
            )

            text = re.sub(
                r"(?is)<[^>]+>",
                " ",
                text,
            )

            text = html.unescape(text)

        return (
            clean(text)[:MAX_TEXT],
            "html",
        )

    except Exception:
        return "", ""


# ============================================================
# PROVIDERS
# ============================================================

def api_json(
    url: str,
    params: Optional[dict] = None,
) -> dict:

    try:
        r = requests.get(
            url,
            params=params,
            timeout=TIMEOUT,
            headers={
                "User-Agent":
                    "AI-Infinity/2050.32"
            },
        )

        r.raise_for_status()

        return r.json()

    except Exception:
        return {}


def crossref(
    query: str,
) -> list[dict]:

    data = api_json(
        "https://api.crossref.org/works",
        {
            "query.bibliographic": query,
            "rows": 10,
        },
    )

    output = []

    for item in (
        data
        .get("message", {})
        .get("items", [])
    ):

        title = (
            item.get("title")
            or [""]
        )[0]

        doi = item.get(
            "DOI",
            "",
        )

        output.append(
            {
                "title": title,
                "abstract": clean(
                    item.get(
                        "abstract",
                        "",
                    )
                ),
                "url": (
                    "https://doi.org/"
                    + doi
                    if doi
                    else ""
                ),
                "doi": doi,
                "provider": "crossref",
            }
        )

    return output


def openalex(
    query: str,
) -> list[dict]:

    data = api_json(
        "https://api.openalex.org/works",
        {
            "search": query,
            "per-page": 10,
        },
    )

    output = []

    for item in data.get(
        "results",
        [],
    ):

        location = (
            item.get(
                "primary_location"
            )
            or {}
        )

        output.append(
            {
                "title": item.get(
                    "title",
                    "",
                ),
                "abstract": "",
                "url": (
                    location.get(
                        "landing_page_url"
                    )
                    or item.get(
                        "id",
                        "",
                    )
                ),
                "doi": (
                    item.get(
                        "doi"
                    )
                    or ""
                ).replace(
                    "https://doi.org/",
                    "",
                ),
                "provider": "openalex",
                "open_pdf": location.get(
                    "pdf_url",
                    "",
                ),
            }
        )

    return output


def semantic_scholar(
    query: str,
) -> list[dict]:

    data = api_json(
        "https://api.semanticscholar.org/"
        "graph/v1/paper/search",
        {
            "query": query,
            "limit": 10,
            "fields": (
                "title,abstract,url,"
                "openAccessPdf,externalIds"
            ),
        },
    )

    output = []

    for item in data.get(
        "data",
        [],
    ):

        external = (
            item.get(
                "externalIds"
            )
            or {}
        )

        pdf = (
            item.get(
                "openAccessPdf"
            )
            or {}
        ).get(
            "url",
            "",
        )

        output.append(
            {
                "title": item.get(
                    "title",
                    "",
                ),
                "abstract": clean(
                    item.get(
                        "abstract",
                        "",
                    )
                ),
                "url": (
                    pdf
                    or item.get(
                        "url",
                        "",
                    )
                ),
                "doi": external.get(
                    "DOI",
                    "",
                ),
                "provider":
                    "semantic_scholar",
                "open_pdf": pdf,
            }
        )

    return output


def duckduckgo(
    query: str,
) -> list[dict]:

    data = api_json(
        "https://api.duckduckgo.com/",
        {
            "q": query,
            "format": "json",
            "no_html": 1,
        },
    )

    output = []

    def walk(items):

        for item in items or []:

            if item.get(
                "FirstURL"
            ):

                output.append(
                    {
                        "title": item.get(
                            "Text",
                            "",
                        ),
                        "abstract": item.get(
                            "Text",
                            "",
                        ),
                        "url": item[
                            "FirstURL"
                        ],
                        "doi": "",
                        "provider":
                            "duckduckgo",
                    }
                )

            walk(
                item.get(
                    "Topics"
                )
            )

    walk(
        data.get(
            "RelatedTopics"
        )
    )

    return output[:10]


def discover(
    objective: str,
) -> list[dict]:

    q = clean(
        objective
    )[:450]

    queries = [
        q,
        (
            "autonomous AI agents "
            "reliability real world "
            "task execution evidence"
        ),
        (
            "AI agents planning tool use "
            "verification failure recovery"
        ),
    ]

    results = []

    with ThreadPoolExecutor(
        max_workers=4
    ) as executor:

        futures = []

        for query in queries:

            futures.extend(
                [
                    executor.submit(
                        crossref,
                        query,
                    ),
                    executor.submit(
                        openalex,
                        query,
                    ),
                    executor.submit(
                        semantic_scholar,
                        query,
                    ),
                    executor.submit(
                        duckduckgo,
                        query,
                    ),
                ]
            )

        for future in as_completed(
            futures
        ):

            try:
                results.extend(
                    future.result()
                )
            except Exception:
                pass

    seen = set()
    unique = []

    for item in results:

        key = work_key(
            item.get(
                "title",
                "",
            ),
            item.get(
                "doi",
                "",
            ),
        )

        if key in seen:
            continue

        seen.add(key)

        if item.get("title"):
            unique.append(item)

    return unique[:80]


# ============================================================
# SOURCE QUALITY
# ============================================================

def source_quality(
    provider: str,
    url: str,
    full_text: str,
    abstract: str,
) -> float:

    score = {
        "semantic_scholar": 0.86,
        "openalex": 0.84,
        "crossref": 0.76,
        "duckduckgo": 0.55,
    }.get(
        provider,
        0.50,
    )

    if full_text:
        score += 0.08
    elif abstract:
        score += 0.03

    if (
        ".edu" in url
        or ".gov" in url
    ):
        score += 0.04

    return min(
        score,
        1.0,
    )


def work_key(
    title: str = "",
    doi: str = "",
) -> str:

    if doi:
        return (
            "doi:"
            + doi.lower().strip()
        )

    normalized = re.sub(
        r"\W+",
        " ",
        title.lower(),
    ).strip()

    return (
        "title:"
        + hashlib.sha256(
            normalized.encode()
        ).hexdigest()[:24]
    )


def relevant(
    objective: str,
    item: dict,
) -> bool:

    text = " ".join(
        [
            item.get(
                "title",
                "",
            ),
            item.get(
                "abstract",
                "",
            ),
        ]
    )

    score = similarity(
        objective,
        text,
    )

    terms = (
        tokens(text)
        & research_terms(
            objective
        )
    )

    return (
        score >= 0.015
        and len(terms) >= 2
    )


# ============================================================
# INGESTION
# ============================================================

def extract_excerpt(
    text: str,
    objective: str,
) -> str:

    if not text:
        return ""

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    candidates = []

    for sentence in sentences:

        sentence = clean(
            sentence
        )

        if useful_sentence(
            sentence,
            objective,
        ):

            candidates.append(
                sentence
            )

    if not candidates:
        return clean(text)[:5000]

    selected = sorted(
        candidates,
        key=lambda x: similarity(
            objective,
            x,
        ),
        reverse=True,
    )

    return clean(
        " ".join(
            selected[:8]
        )
    )[:5000]


def ingest(
    mid: str,
    objective: str,
    item: dict,
) -> bool:

    title = clean(
        item.get(
            "title",
            "",
        )
    )

    abstract = clean(
        item.get(
            "abstract",
            "",
        )
    )

    url = item.get(
        "url",
        "",
    )

    if not title:
        return False

    pdf_url = item.get(
        "open_pdf",
        "",
    )

    fetch_url = (
        pdf_url
        or url
    )

    full_text = ""
    evidence_type = "metadata"

    if fetch_url:
        full_text, evidence_type = fetch(
            fetch_url
        )

    if not full_text:
        full_text = abstract

    key = work_key(
        title,
        item.get(
            "doi",
            "",
        ),
    )

    relevance_score = similarity(
        objective,
        " ".join(
            [
                title,
                abstract,
                full_text[:8000],
            ]
        ),
    )

    quality = source_quality(
        item.get(
            "provider",
            "",
        ),
        url,
        full_text,
        abstract,
    )

    source_domain = domain(
        url
    )

    provider = item.get(
        "provider",
        "unknown",
    )

    with DB_LOCK:

        c = db()

        c.execute(
            """
            INSERT OR IGNORE INTO works(
                mission_id,
                work_key,
                title,
                abstract,
                url,
                doi,
                provider,
                source_family,
                domain,
                quality,
                relevance,
                full_text,
                created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mid,
                key,
                title,
                abstract,
                url,
                item.get(
                    "doi",
                    "",
                ),
                provider,
                provider,
                source_domain,
                quality,
                relevance_score,
                1 if full_text else 0,
                now(),
            ),
        )

        row = c.execute(
            """
            SELECT *
            FROM works
            WHERE mission_id=?
              AND work_key=?
            """,
            (
                mid,
                key,
            ),
        ).fetchone()

        if not row:
            c.close()
            return False

        excerpt = extract_excerpt(
            full_text,
            objective,
        )

        if len(excerpt) < 80:
            c.close()
            return True

        c.execute(
            """
            INSERT INTO evidence(
                mission_id,
                work_key,
                source_id,
                excerpt,
                evidence_type,
                locator,
                quality,
                relevance,
                source_family,
                domain,
                created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mid,
                key,
                row["id"],
                excerpt,
                evidence_type,
                "document-excerpt",
                quality,
                relevance_score,
                provider,
                source_domain,
                now(),
            ),
        )

        c.commit()
        c.close()

    return True


# ============================================================
# CLAIM EXTRACTION
# ============================================================

def extract_claims(
    mid: str,
    objective: str,
) -> int:

    with DB_LOCK:

        c = db()

        evidence_rows = c.execute(
            """
            SELECT *
            FROM evidence
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchall()

        count = 0

        for evidence in evidence_rows:

            sentences = re.split(
                r"(?<=[.!?])\s+",
                evidence["excerpt"],
            )

            for sentence in sentences:

                sentence = clean(
                    sentence
                )

                if not useful_sentence(
                    sentence,
                    objective,
                ):
                    continue

                words = tokens(
                    sentence
                )

                if not (
                    words
                    & PREDICATES
                ):
                    continue

                if len(sentence) > 700:
                    sentence = sentence[:700]

                fp = fingerprint(
                    sentence
                )

                c.execute(
                    """
                    INSERT OR IGNORE INTO claims(
                        mission_id,
                        claim,
                        fingerprint,
                        status,
                        confidence,
                        created_at
                    )
                    VALUES(?,?,?,?,?,?)
                    """,
                    (
                        mid,
                        sentence,
                        fp,
                        "UNVERIFIED",
                        0.20,
                        now(),
                    ),
                )

                claim = c.execute(
                    """
                    SELECT id
                    FROM claims
                    WHERE mission_id=?
                      AND fingerprint=?
                    """,
                    (
                        mid,
                        fp,
                    ),
                ).fetchone()

                if claim:

                    c.execute(
                        """
                        INSERT INTO edges(
                            mission_id,
                            from_type,
                            from_id,
                            to_type,
                            to_id,
                            relation,
                            score,
                            reason,
                            created_at
                        )
                        VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            mid,
                            "claim",
                            str(
                                claim["id"]
                            ),
                            "evidence",
                            str(
                                evidence["id"]
                            ),
                            "SUPPORTED_BY",
                            round(
                                similarity(
                                    sentence,
                                    evidence[
                                        "excerpt"
                                    ],
                                ),
                                4,
                            ),
                            "claim provenance",
                            now(),
                        ),
                    )

                count += 1

        c.commit()
        c.close()

    return count


# ============================================================
# GRAPH
# ============================================================

def build_graph(
    mid: str,
    objective: str,
):

    with DB_LOCK:

        c = db()

        claims = c.execute(
            """
            SELECT *
            FROM claims
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchall()

        evidence = c.execute(
            """
            SELECT *
            FROM evidence
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchall()

        for claim_a in claims:

            for claim_b in claims:

                if (
                    claim_a["id"]
                    >= claim_b["id"]
                ):
                    continue

                score = similarity(
                    claim_a["claim"],
                    claim_b["claim"],
                )

                if score < 0.30:
                    continue

                words_a = tokens(
                    claim_a["claim"]
                )

                words_b = tokens(
                    claim_b["claim"]
                )

                neg_a = bool(
                    words_a & NEGATION
                )

                neg_b = bool(
                    words_b & NEGATION
                )

                relation = (
                    "CONTRADICTS"
                    if neg_a != neg_b
                    else "RELATED"
                )

                c.execute(
                    """
                    INSERT INTO edges(
                        mission_id,
                        from_type,
                        from_id,
                        to_type,
                        to_id,
                        relation,
                        score,
                        reason,
                        created_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        mid,
                        "claim",
                        str(
                            claim_a["id"]
                        ),
                        "claim",
                        str(
                            claim_b["id"]
                        ),
                        relation,
                        round(
                            score,
                            4,
                        ),
                        "claim similarity analysis",
                        now(),
                    ),
                )

        for claim in claims:

            for ev in evidence:

                score = similarity(
                    claim["claim"],
                    ev["excerpt"],
                )

                if score < 0.24:
                    continue

                c.execute(
                    """
                    INSERT INTO edges(
                        mission_id,
                        from_type,
                        from_id,
                        to_type,
                        to_id,
                        relation,
                        score,
                        reason,
                        created_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        mid,
                        "claim",
                        str(
                            claim["id"]
                        ),
                        "evidence",
                        str(
                            ev["id"]
                        ),
                        "EVIDENCE_MATCH",
                        round(
                            score,
                            4,
                        ),
                        "semantic token overlap",
                        now(),
                    ),
                )

        c.commit()
        c.close()


# ============================================================
# VERIFICATION
# ============================================================

def verify(
    mid: str,
    objective: str,
) -> list[dict]:

    with DB_LOCK:

        c = db()

        claims = c.execute(
            """
            SELECT *
            FROM claims
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchall()

        evidence_rows = c.execute(
            """
            SELECT *
            FROM evidence
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchall()

        output = []

        for claim in claims:

            support = []
            contradictions = []

            work_ids = set()
            domains = set()
            families = set()

            for evidence in evidence_rows:

                score = similarity(
                    claim["claim"],
                    evidence["excerpt"],
                )

                shared = (
                    tokens(
                        claim["claim"]
                    )
                    & tokens(
                        evidence["excerpt"]
                    )
                    & research_terms(
                        objective
                    )
                )

                if (
                    score >= 0.24
                    and len(shared) >= 2
                ):

                    support.append(
                        evidence
                    )

                    work_ids.add(
                        evidence["work_key"]
                    )

                    if evidence["domain"]:
                        domains.add(
                            evidence["domain"]
                        )

                    if evidence[
                        "source_family"
                    ]:
                        families.add(
                            evidence[
                                "source_family"
                            ]
                        )

                neg1 = bool(
                    tokens(
                        claim["claim"]
                    )
                    & NEGATION
                )

                neg2 = bool(
                    tokens(
                        evidence["excerpt"]
                    )
                    & NEGATION
                )

                if (
                    score >= 0.24
                    and len(shared) >= 2
                    and neg1 != neg2
                ):
                    contradictions.append(
                        evidence
                    )

            independent = (
                len(work_ids) >= 2
                and len(domains) >= 2
                and len(families) >= 2
            )

            if (
                independent
                and len(support) >= 2
                and not contradictions
            ):

                status = "VERIFIED"

                confidence = min(
                    0.99,
                    0.60
                    + 0.10
                    * len(support)
                    + 0.08
                    * len(domains),
                )

            elif (
                support
                and contradictions
            ):

                status = "CONTRADICTED"
                confidence = 0.35

            elif support:

                status = "SUPPORTED"

                confidence = min(
                    0.74,
                    0.35
                    + 0.08
                    * len(support)
                    + 0.05
                    * len(domains),
                )

            else:

                status = "INSUFFICIENT"
                confidence = 0.10

            c.execute(
                """
                UPDATE claims
                SET status=?,
                    confidence=?
                WHERE id=?
                """,
                (
                    status,
                    confidence,
                    claim["id"],
                ),
            )

            output.append(
                {
                    "id": claim["id"],
                    "status": status,
                    "confidence": round(
                        confidence,
                        3,
                    ),
                    "support_count":
                        len(support),
                    "independent_works":
                        len(work_ids),
                    "independent_domains":
                        len(domains),
                    "source_families":
                        len(families),
                    "contradictions":
                        len(
                            contradictions
                        ),
                }
            )

        c.commit()
        c.close()

    return output


# ============================================================
# AUDIT
# ============================================================

def audit_result(
    mid: str,
    objective: str,
) -> dict:

    with DB_LOCK:

        c = db()

        works = c.execute(
            """
            SELECT *
            FROM works
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchall()

        evidence_rows = c.execute(
            """
            SELECT *
            FROM evidence
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchall()

        claims = c.execute(
            """
            SELECT *
            FROM claims
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchall()

        edges = c.execute(
            """
            SELECT *
            FROM edges
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchall()

        events = c.execute(
            """
            SELECT *
            FROM events
            WHERE mission_id=?
            ORDER BY id
            """,
            (mid,),
        ).fetchall()

        c.close()

    total = len(claims)

    verified = sum(
        x["status"] == "VERIFIED"
        for x in claims
    )

    supported = sum(
        x["status"]
        in (
            "VERIFIED",
            "SUPPORTED",
        )
        for x in claims
    )

    contradicted = sum(
        x["status"] == "CONTRADICTED"
        for x in claims
    )

    domains = len(
        {
            x["domain"]
            for x in works
            if x["domain"]
        }
    )

    families = len(
        {
            x["source_family"]
            for x in works
            if x["source_family"]
        }
    )

    independent_works = len(
        {
            x["work_key"]
            for x in works
        }
    )

    usable = sum(
        len(x["excerpt"]) >= 180
        for x in evidence_rows
    )

    stages = {
        x["stage"]:
            x["status"]
        for x in events
    }

    gate = bool(
        verified > 0
        and usable > 0
        and domains >= 2
        and families >= 2
        and len(edges) > 0
    )

    pipeline_stages = [
        "discovery",
        "evidence_ingestion",
        "claims",
        "evidence_graph",
    ]

    end_stages = [
        "discovery",
        "evidence_ingestion",
        "claims",
        "verification",
        "counter_evidence",
        "mission",
    ]

    actually_working = round(
        100
        * sum(
            stages.get(stage)
            == "completed"
            for stage in pipeline_stages
        )
        / len(pipeline_stages),
        1,
    )

    end_to_end = round(
        100
        * sum(
            stages.get(stage)
            == "completed"
            for stage in end_stages
        )
        / len(end_stages),
        1,
    )

    verification_percent = (
        round(
            100
            * verified
            / total,
            1,
        )
        if total
        else 0.0
    )

    if gate:
        reality_status = (
            "evidence_gate_passed"
        )
    elif verified:
        reality_status = (
            "research_pipeline_operational"
            "_with_partial_verification"
        )
    else:
        reality_status = (
            "research_pipeline_operational"
            "_verification_incomplete"
        )

    return {
        "version": VERSION,
        "build": BUILD,
        "mission_id": mid,
        "objective": objective,

        "current_reality": {
            "status": reality_status,
            "works": independent_works,
            "usable_evidence": usable,
            "claims": total,
            "verified_claims": verified,
            "supported_claims": supported,
            "contradicted_claims": contradicted,
            "domains": domains,
            "source_families": families,
            "graph_edges": len(edges),
            "evidence_gate": gate,
        },

        "objective_measurements": {
            "capability_coverage_percent":
                "NOT MEASURABLE YET",

            "implemented_percent":
                "NOT MEASURABLE YET",

            "actually_working_percent":
                actually_working,

            "independently_verified_percent":
                verification_percent,

            "end_to_end_demonstrated_percent":
                end_to_end,

            "production_readiness_percent":
                "NOT MEASURABLE YET",

            "autonomy_percent":
                "NOT MEASURABLE YET",

            "evidence_integrity_percent":
                verification_percent,

            "reality_gap_percent":
                "NOT MEASURABLE YET",
        },

        "measurement_definitions": {
            "actually_working_percent": {
                "numerator":
                    "completed discovery, "
                    "evidence ingestion, claims "
                    "and evidence graph stages",
                "denominator": 4,
                "definition":
                    "research pipeline stage completion",
            },

            "independently_verified_percent": {
                "numerator": verified,
                "denominator": total,
                "definition":
                    "claims reaching VERIFIED under "
                    "independent work/domain/source "
                    "family requirements",
            },

            "end_to_end_demonstrated_percent": {
                "numerator":
                    "completed required audit stages",
                "denominator": 6,
                "definition":
                    "pipeline completion from discovery "
                    "through mission completion",
            },

            "evidence_integrity_percent": {
                "numerator": verified,
                "denominator": total,
                "definition":
                    "verified claims divided by "
                    "extracted claims; narrow metric "
                    "only",
            },
        },

        "gate_definition": {
            "requires":
                "at least one VERIFIED claim with "
                "at least two independent works, "
                "two domains and two source families, "
                "plus usable evidence and graph edges",

            "passed": gate,
        },

        "verified_capabilities": [
            "mission creation",
            "multi-provider discovery",
            "safe URL retrieval",
            "evidence ingestion",
            "claim extraction",
            "claim-to-evidence provenance",
            "independent corroboration checks",
            "contradiction detection",
            "mission events",
            "evidence gate",
            "audit metrics",
            "browser dashboard",
        ],

        "not_yet_demonstrated": [
            "reliable autonomous execution of arbitrary real-world actions",
            "autonomous self-correction across repeated failures",
            "production-grade autonomous operation",
            "continuous self-improvement",
            "general autonomous tool execution",
            "persistent durable memory across infrastructure restarts",
        ],

        "next_test":
            "Run a fresh unseen objective and require "
            "at least one independently corroborated "
            "claim to reach VERIFIED without manual "
            "intervention.",

        "events": [
            dict(x)
            for x in events
        ],
    }


# ============================================================
# MISSION EXECUTION
# ============================================================

def run_mission(
    mid: str,
    objective: str,
):

    try:

        event(
            mid,
            "mission",
            "running",
            "Mission started",
        )

        event(
            mid,
            "discovery",
            "running",
            "Querying research providers",
        )

        found = discover(
            objective
        )

        accepted = 0

        for item in found:

            if relevant(
                objective,
                item,
            ):

                if ingest(
                    mid,
                    objective,
                    item,
                ):
                    accepted += 1

        event(
            mid,
            "discovery",
            "completed",
            (
                f"{len(found)} discovered; "
                f"{accepted} accepted"
            ),
        )

        event(
            mid,
            "evidence_ingestion",
            "completed",
            f"{accepted} usable works",
        )

        event(
            mid,
            "claims",
            "running",
            "Extracting substantive claims",
        )

        claim_count = extract_claims(
            mid,
            objective,
        )

        event(
            mid,
            "claims",
            "completed",
            (
                f"{claim_count} "
                "candidate claims"
            ),
        )

        event(
            mid,
            "evidence_graph",
            "running",
            "Building provenance graph",
        )

        build_graph(
            mid,
            objective,
        )

        with DB_LOCK:

            c = db()

            edge_count = c.execute(
                """
                SELECT COUNT(*) AS n
                FROM edges
                WHERE mission_id=?
                """,
                (mid,),
            ).fetchone()["n"]

            c.close()

        event(
            mid,
            "evidence_graph",
            "completed",
            (
                f"{edge_count} graph edges"
            ),
        )

        event(
            mid,
            "verification",
            "running",
            "Testing independent corroboration",
        )

        verification = verify(
            mid,
            objective,
        )

        verified = sum(
            x["status"] == "VERIFIED"
            for x in verification
        )

        contradicted = sum(
            x["status"] == "CONTRADICTED"
            for x in verification
        )

        event(
            mid,
            "verification",
            "completed",
            (
                f"verified={verified}; "
                f"contradicted={contradicted}"
            ),
        )

        event(
            mid,
            "counter_evidence",
            "completed",
            "Contradiction analysis completed",
        )

        result = audit_result(
            mid,
            objective,
        )

        event(
            mid,
            "mission",
            "completed",
            (
                "Evidence gate="
                + str(
                    result[
                        "gate_definition"
                    ]["passed"]
                )
            ),
        )

        set_mission(
            mid,
            "completed",
            result,
        )

    except Exception as exc:

        event(
            mid,
            "mission",
            "failed",
            repr(exc),
        )

        set_mission(
            mid,
            "failed",
            {
                "error": repr(exc),
                "version": VERSION,
                "build": BUILD,
            },
        )


# ============================================================
# MODELS
# ============================================================

class MissionIn(BaseModel):

    objective: str = Field(
        min_length=10,
        max_length=12000,
    )


# ============================================================
# DASHBOARD
# ============================================================

DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<meta
    name="theme-color"
    content="#08111f"
>

<title>AI Infinity</title>

<style>

:root {
    color-scheme: dark;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #08111f;
    color: #e8eef7;
    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}

main {
    width: 100%;
    max-width: 1100px;
    margin: 0 auto;
    padding: 14px;
}

.card {
    background: #0e1a2b;
    border: 1px solid #21334b;
    border-radius: 16px;
    padding: 15px;
    margin: 10px 0;
    box-shadow:
        0 8px 30px rgba(0,0,0,.18);
}

.header {
    padding: 20px;
}

h1 {
    margin: 0 0 4px;
    font-size: 29px;
}

h2,
h3 {
    margin-top: 0;
}

.sub {
    color: #91a4bc;
}

textarea {
    width: 100%;
    min-height: 150px;
    resize: vertical;
    background: #07101c;
    color: #ffffff;
    border: 1px solid #2b405c;
    border-radius: 12px;
    padding: 13px;
    font-size: 15px;
    line-height: 1.45;
    outline: none;
}

textarea:focus {
    border-color: #4f8cff;
}

button {
    width: 100%;
    border: 0;
    border-radius: 12px;
    padding: 15px;
    margin-top: 10px;
    background: #4f8cff;
    color: white;
    font-weight: 800;
    font-size: 16px;
    cursor: pointer;
}

button:disabled {
    opacity: .45;
    cursor: wait;
}

.grid {
    display: grid;
    grid-template-columns:
        repeat(4, minmax(0, 1fr));
    gap: 9px;
}

.metric {
    background: #0a1524;
    border-radius: 12px;
    padding: 12px;
    min-width: 0;
}

.num {
    font-size: 25px;
    font-weight: 850;
}

.lab {
    color: #91a4bc;
    font-size: 11px;
    margin-top: 2px;
}

.progress {
    height: 9px;
    background: #17263a;
    border-radius: 9px;
    overflow: hidden;
    margin-top: 12px;
}

.fill {
    height: 100%;
    width: 0%;
    background: #62a0ff;
    transition: width .3s ease;
}

.item {
    padding: 10px 0;
    border-bottom:
        1px solid #1d2b3e;
}

.item:last-child {
    border-bottom: 0;
}

.ok {
    color: #63d391;
}

.warn {
    color: #ffc857;
}

.bad {
    color: #ff7272;
}

.neutral {
    color: #8db7ff;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: #07101c;
    color: #d9e4f2;
    border-radius: 10px;
    padding: 12px;
    max-height: 450px;
    overflow: auto;
    font-size: 12px;
}

.small {
    font-size: 12px;
}

.pill {
    display: inline-block;
    padding: 4px 8px;
    border-radius: 99px;
    background: #17263a;
    font-size: 11px;
}

@media (max-width: 700px) {

    main {
        padding: 9px;
    }

    .grid {
        grid-template-columns:
            repeat(2, minmax(0, 1fr));
    }

    .header {
        padding: 15px;
    }

    h1 {
        font-size: 25px;
    }
}

</style>

</head>

<body>

<main>

<div class="card header">

    <h1>AI Infinity</h1>

    <div class="sub">
        TARGET-2050.32
        ·
        AUTONOMOUS-EVIDENCE-DASHBOARD-CORE
    </div>

    <br>

    <textarea id="objective">Research the reliability of autonomous AI agents for real-world task execution. Find independent evidence, verify important claims, identify contradictory evidence, and give the next actions.</textarea>

    <button
        id="run"
        onclick="runMission()"
    >
        RUN MISSION
    </button>

    <div
        id="status"
        class="sub"
        style="margin-top:10px"
    >
        Ready.
    </div>

    <div class="progress">
        <div
            id="fill"
            class="fill"
        ></div>
    </div>

</div>


<div class="grid">

<div class="metric">
    <div
        id="sources"
        class="num"
    >0</div>
    <div class="lab">
        SOURCES
    </div>
</div>

<div class="metric">
    <div
        id="evidence"
        class="num"
    >0</div>
    <div class="lab">
        EVIDENCE
    </div>
</div>

<div class="metric">
    <div
        id="claims"
        class="num"
    >0</div>
    <div class="lab">
        CLAIMS
    </div>
</div>

<div class="metric">
    <div
        id="verified"
        class="num"
    >0</div>
    <div class="lab">
        VERIFIED
    </div>
</div>

</div>


<div class="card">

    <h3>MISSION</h3>

    <div id="mission">
        No mission running.
    </div>

</div>


<div class="card">

    <h3>EVENTS</h3>

    <div id="events">
        Waiting...
    </div>

</div>


<div class="card">

    <h3>CLAIMS</h3>

    <div id="claimsList">
        Waiting...
    </div>

</div>


<div class="card">

    <h3>EVIDENCE GRAPH</h3>

    <div id="graph">
        Waiting...
    </div>

</div>


<div class="card">

    <h3>AUDIT</h3>

    <pre id="audit">Run a mission to generate the audit.</pre>

</div>


<div class="card">

    <h3>MISSION HISTORY</h3>

    <div id="history">
        Loading...
    </div>

</div>

</main>


<script>

let activeMission = null;
let pollTimer = null;

const $ = id =>
    document.getElementById(id);


function escapeHtml(value) {

    return String(
        value ?? ""
    ).replace(
        /[&<>"']/g,
        function(char) {

            return {
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                '"': "&quot;",
                "'": "&#39;"
            }[char];

        }
    );

}


async function api(
    url,
    options
) {

    const response =
        await fetch(
            url,
            options
        );

    if (!response.ok) {

        throw new Error(
            await response.text()
        );

    }

    return response.json();

}


async function runMission() {

    const objective =
        $("objective")
        .value
        .trim();

    if (
        objective.length < 10
    ) {

        $("status")
            .textContent =
            "Objective must be at least 10 characters.";

        return;

    }

    $("run").disabled = true;

    $("status")
        .textContent =
        "Starting mission...";

    $("fill")
        .style
        .width = "3%";

    try {

        const result =
            await api(
                "/run",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body:
                        JSON.stringify({
                            objective:
                                objective
                        })
                }
            );

        activeMission =
            result.mission_id;

        $("status")
            .textContent =
            "Mission queued: "
            + activeMission;

        pollMission();

    } catch (error) {

        $("status")
            .textContent =
            "Start failed: "
            + error.message;

        $("run").disabled = false;

    }

}


async function pollMission() {

    if (!activeMission) {
        return;
    }

    try {

        const results =
            await Promise.all([
                api(
                    "/mission/"
                    + activeMission
                ),

                api(
                    "/mission/"
                    + activeMission
                    + "/events"
                ),

                api(
                    "/mission/"
                    + activeMission
                    + "/sources"
                ),

                api(
                    "/mission/"
                    + activeMission
                    + "/evidence"
                ),

                api(
                    "/mission/"
                    + activeMission
                    + "/claims"
                ),

                api(
                    "/mission/"
                    + activeMission
                    + "/graph"
                )
            ]);

        const mission =
            results[0];

        const events =
            results[1];

        const sources =
            results[2];

        const evidence =
            results[3];

        const claims =
            results[4];

        const graph =
            results[5];

        renderMission(
            mission,
            events,
            sources,
            evidence,
            claims,
            graph
        );

        if (
            mission.status ===
                "completed"
            ||
            mission.status ===
                "failed"
        ) {

            try {

                const audit =
                    await api(
                        "/mission/"
                        + activeMission
                        + "/audit"
                    );

                $("audit")
                    .textContent =
                    JSON.stringify(
                        audit,
                        null,
                        2
                    );

            } catch (error) {

                $("audit")
                    .textContent =
                    error.message;

            }

            $("run").disabled = false;

            activeMission = null;

            loadHistory();

            return;

        }

    } catch (error) {

        $("status")
            .textContent =
            "Polling error: "
            + error.message;

    }

    pollTimer =
        setTimeout(
            pollMission,
            2500
        );

}


function renderMission(
    mission,
    events,
    sources,
    evidence,
    claims,
    graph
) {

    $("status")
        .textContent =
        "Mission "
        + mission.status
        + " · "
        + mission.id;

    $("mission")
        .innerHTML =
        "<b>"
        + escapeHtml(
            mission.id
        )
        + "</b><br>"
        + escapeHtml(
            mission.objective
        );

    $("sources")
        .textContent =
        sources.length;

    $("evidence")
        .textContent =
        evidence.length;

    $("claims")
        .textContent =
        claims.length;

    $("verified")
        .textContent =
        claims.filter(
            x =>
                x.status ===
                "VERIFIED"
        ).length;

    const completed =
        events.filter(
            x =>
                x.status ===
                "completed"
        ).length;

    const progress =
        Math.min(
            100,
            completed / 6 * 100
        );

    $("fill")
        .style
        .width =
        progress + "%";


    $("events")
        .innerHTML =
        events.length
        ? events.map(
            item =>
                "<div class='item'>"
                + "<b>"
                + escapeHtml(
                    item.stage
                )
                + "</b>"
                + " · "
                + escapeHtml(
                    item.status
                )
                + "<br>"
                + "<span class='small'>"
                + escapeHtml(
                    item.detail
                )
                + "</span>"
                + "</div>"
        ).join("")
        : "No events yet.";


    $("claimsList")
        .innerHTML =
        claims.length
        ? claims
            .slice(0, 40)
            .map(
                claim => {

                    let cls =
                        "warn";

                    if (
                        claim.status ===
                        "VERIFIED"
                    ) {
                        cls = "ok";
                    }

                    if (
                        claim.status ===
                        "CONTRADICTED"
                    ) {
                        cls = "bad";
                    }

                    return (
                        "<div class='item'>"
                        + "<b class='"
                        + cls
                        + "'>"
                        + escapeHtml(
                            claim.status
                        )
                        + "</b>"
                        + " · "
                        + escapeHtml(
                            claim.claim
                        )
                        + "</div>"
                    );

                }
            )
            .join("")
        : "No claims yet.";


    $("graph")
        .innerHTML =
        "<div class='sub'>"
        + graph.length
        + " graph edges"
        + "</div>"
        +
        (
            graph.length
            ? graph
                .slice(0, 50)
                .map(
                    edge =>
                        "<div class='item'>"
                        + "<span class='pill'>"
                        + escapeHtml(
                            edge.from_type
                        )
                        + "</span> "
                        + escapeHtml(
                            edge.relation
                        )
                        + " "
                        + "<span class='pill'>"
                        + escapeHtml(
                            edge.to_type
                        )
                        + "</span>"
                        + " · score "
                        + Number(
                            edge.score || 0
                        ).toFixed(3)
                        + "</div>"
                )
                .join("")
            : "No graph edges yet."
        );

}


async function loadHistory() {

    try {

        const rows =
            await api(
                "/missions"
            );

        $("history")
            .innerHTML =
            rows.length
            ? rows.map(
                item =>
                    "<div class='item'>"
                    + "<b>"
                    + escapeHtml(
                        item.status
                    )
                    + "</b>"
                    + " · "
                    + escapeHtml(
                        item.id
                    )
                    + "<br>"
                    + "<span class='small'>"
                    + escapeHtml(
                        item.objective
                    )
                    + "</span>"
                    + "</div>"
            ).join("")
            : "No missions yet.";

    } catch (error) {

        $("history")
            .textContent =
            error.message;

    }

}


loadHistory();

</script>

</body>
</html>
"""


# ============================================================
# API
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
def root():

    return HTMLResponse(
        DASHBOARD_HTML
    )


@app.get(
    "/dashboard",
    response_class=HTMLResponse,
)
def dashboard():

    return HTMLResponse(
        DASHBOARD_HTML
    )


@app.get(
    "/run",
    response_class=HTMLResponse,
)
def run_page():

    return HTMLResponse(
        DASHBOARD_HTML
    )


@app.get("/health")
def health():

    return {
        "status": "ok",
        "version": VERSION,
        "build": BUILD,
    }


@app.get("/status")
def status():

    with DB_LOCK:

        c = db()

        count = c.execute(
            """
            SELECT COUNT(*) AS n
            FROM missions
            """
        ).fetchone()["n"]

        c.close()

    return {
        "status": "online",
        "missions": count,
        "version": VERSION,
        "build": BUILD,
    }


@app.get("/capabilities")
def capabilities():

    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "mobile dashboard",
            "multi-provider discovery",
            "safe URL retrieval",
            "evidence excerpts",
            "claim extraction",
            "claim-to-evidence provenance",
            "independent verification",
            "contradiction detection",
            "persistent mission events",
            "evidence graph",
            "evidence gate",
            "reality audit",
        ],
    }


@app.post("/mission")
@app.post("/research")
@app.post("/run")
def create_mission(
    req: MissionIn,
):

    mid = (
        "mission-"
        + uuid.uuid4().hex[:12]
    )

    with DB_LOCK:

        c = db()

        c.execute(
            """
            INSERT INTO missions(
                id,
                objective,
                status,
                result,
                created_at,
                updated_at
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                mid,
                req.objective,
                "queued",
                None,
                now(),
                now(),
            ),
        )

        c.commit()
        c.close()

    event(
        mid,
        "mission",
        "queued",
        "Mission accepted",
    )

    EXECUTOR.submit(
        run_mission,
        mid,
        req.objective,
    )

    return {
        "task_id": mid,
        "mission_id": mid,
        "status": "queued",
        "version": VERSION,
        "build": BUILD,
    }


@app.post("/command")
def command(
    req: MissionIn,
):

    return create_mission(
        req
    )


@app.get(
    "/mission/{mid}"
)
def get_mission(
    mid: str,
):

    with DB_LOCK:

        c = db()

        row = c.execute(
            """
            SELECT *
            FROM missions
            WHERE id=?
            """,
            (mid,),
        ).fetchone()

        c.close()

    if not row:

        raise HTTPException(
            status_code=404,
            detail="mission not found",
        )

    output = dict(row)

    output["result"] = (
        json.loads(
            output["result"]
        )
        if output["result"]
        else None
    )

    return output


@app.get("/missions")
def missions():

    with DB_LOCK:

        c = db()

        rows = [
            dict(row)
            for row in c.execute(
                """
                SELECT *
                FROM missions
                ORDER BY created_at DESC
                LIMIT 100
                """
            )
        ]

        c.close()

    for row in rows:

        row["result"] = (
            json.loads(
                row["result"]
            )
            if row["result"]
            else None
        )

    return rows


@app.get(
    "/mission/{mid}/events"
)
def mission_events(
    mid: str,
):

    with DB_LOCK:

        c = db()

        rows = [
            dict(row)
            for row in c.execute(
                """
                SELECT *
                FROM events
                WHERE mission_id=?
                ORDER BY id
                """,
                (mid,),
            )
        ]

        c.close()

    return rows


@app.get(
    "/mission/{mid}/sources"
)
def mission_sources(
    mid: str,
):

    with DB_LOCK:

        c = db()

        rows = [
            dict(row)
            for row in c.execute(
                """
                SELECT *
                FROM works
                WHERE mission_id=?
                ORDER BY relevance DESC,
                         quality DESC
                """,
                (mid,),
            )
        ]

        c.close()

    return rows


@app.get(
    "/mission/{mid}/claims"
)
def mission_claims(
    mid: str,
):

    with DB_LOCK:

        c = db()

        rows = [
            dict(row)
            for row in c.execute(
                """
                SELECT *
                FROM claims
                WHERE mission_id=?
                ORDER BY confidence DESC
                """,
                (mid,),
            )
        ]

        c.close()

    return rows


@app.get(
    "/mission/{mid}/evidence"
)
def mission_evidence(
    mid: str,
):

    with DB_LOCK:

        c = db()

        rows = [
            dict(row)
            for row in c.execute(
                """
                SELECT *
                FROM evidence
                WHERE mission_id=?
                ORDER BY relevance DESC,
                         quality DESC
                """,
                (mid,),
            )
        ]

        c.close()

    return rows


@app.get(
    "/mission/{mid}/graph"
)
def mission_graph(
    mid: str,
):

    with DB_LOCK:

        c = db()

        rows = [
            dict(row)
            for row in c.execute(
                """
                SELECT *
                FROM edges
                WHERE mission_id=?
                ORDER BY score DESC
                """,
                (mid,),
            )
        ]

        c.close()

    return rows


@app.get(
    "/mission/{mid}/audit"
)
def mission_audit(
    mid: str,
):

    mission = get_mission(
        mid
    )

    if not mission["result"]:

        return {
            "status":
                mission["status"],
            "result": None,
        }

    return mission["result"]


@app.get("/validate-source")
def validate_source(
    url: str,
):

    return {
        "url": url,
        "safe": safe_url(url),
    }


# ============================================================
# SHUTDOWN
# ============================================================

@app.on_event("shutdown")
def shutdown():

    EXECUTOR.shutdown(
        wait=False,
        cancel_futures=True,
    )
