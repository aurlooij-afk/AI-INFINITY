"""
AI Infinity
TARGET-2050.31
BUILD: AUTONOMOUS-EVIDENCE-PROOF-CORE

Evidence-first autonomous research engine.
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


VERSION = "TARGET-2050.31"
BUILD = "AUTONOMOUS-EVIDENCE-PROOF-CORE"

BASE = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity"))
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

MAX_BYTES = 7 * 1024 * 1024
MAX_TEXT = 120_000

WORKERS = int(os.getenv("AI_INFINITY_WORKERS", "6"))
TIMEOUT = int(os.getenv("AI_INFINITY_HTTP_TIMEOUT", "20"))

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
)

EXECUTOR = ThreadPoolExecutor(
    max_workers=WORKERS
)

DB_LOCK = threading.RLock()


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
    "monitoring",
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

    return selected or set(list(t)[:12])


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


def safe_url(
    url: str,
) -> bool:
    try:
        p = urlparse(url)

        if (
            p.scheme not in ("http", "https")
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

        octets = ip.split(".")

        if len(octets) == 4:
            a = int(octets[0])
            b = int(octets[1])

            if a in {10, 127, 169}:
                return False

            if a == 192 and b == 168:
                return False

            if a == 172 and 16 <= b <= 31:
                return False

        return True

    except Exception:
        return False


def clean(
    text: str,
) -> str:
    return re.sub(
        r"\s+",
        " ",
        text or "",
    ).strip()


def domain(
    url: str,
) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


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
                    "AI-Infinity/2050.31"
            },
            stream=True,
            allow_redirects=True,
        )

        r.raise_for_status()

        ct = (
            r.headers.get("content-type")
            or ""
        ).lower()

        data = bytearray()

        for chunk in r.iter_content(
            65536
        ):
            data.extend(chunk)

            if len(data) > MAX_BYTES:
                break

        if (
            "pdf" in ct
            or url.lower().endswith(".pdf")
        ):
            if PdfReader is None:
                return "", "pdf"

            import io

            reader = PdfReader(
                io.BytesIO(
                    bytes(data)
                )
            )

            text = "\n".join(
                (
                    p.extract_text()
                    or ""
                )
                for p in reader.pages
            )

            return (
                clean(text)[:MAX_TEXT],
                "pdf",
            )

        text = bytes(data).decode(
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


def api_json(
    url: str,
    params=None,
) -> dict:

    try:
        r = requests.get(
            url,
            params=params,
            timeout=TIMEOUT,
            headers={
                "User-Agent":
                    "AI-Infinity/2050.31"
            },
        )

        r.raise_for_status()

        return r.json()

    except Exception:
        return {}


def crossref(
    q: str,
) -> list[dict]:

    data = api_json(
        "https://api.crossref.org/works",
        {
            "query.bibliographic": q,
            "rows": 10,
        },
    )

    out = []

    for x in (
        data
        .get("message", {})
        .get("items", [])
    ):

        title = (
            x.get("title")
            or [""]
        )[0]

        doi = x.get(
            "DOI",
            "",
        )

        out.append(
            {
                "title": title,
                "abstract": clean(
                    x.get(
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

    return out


def openalex(
    q: str,
) -> list[dict]:

    data = api_json(
        "https://api.openalex.org/works",
        {
            "search": q,
            "per-page": 10,
        },
    )

    out = []

    for x in data.get(
        "results",
        [],
    ):

        loc = (
            x.get(
                "primary_location"
            )
            or {}
        )

        out.append(
            {
                "title": x.get(
                    "title",
                    "",
                ),
                "abstract": "",
                "url": (
                    loc.get(
                        "landing_page_url"
                    )
                    or x.get(
                        "id",
                        "",
                    )
                ),
                "doi": (
                    x.get(
                        "doi"
                    )
                    or ""
                ).replace(
                    "https://doi.org/",
                    "",
                ),
                "provider": "openalex",
                "open_pdf": loc.get(
                    "pdf_url",
                    "",
                ),
            }
        )

    return out


def semantic(
    q: str,
) -> list[dict]:

    data = api_json(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        {
            "query": q,
            "limit": 10,
            "fields": (
                "title,abstract,url,"
                "openAccessPdf,externalIds"
            ),
        },
    )

    out = []

    for x in data.get(
        "data",
        [],
    ):

        ext = (
            x.get(
                "externalIds"
            )
            or {}
        )

        pdf = (
            x.get(
                "openAccessPdf"
            )
            or {}
        ).get(
            "url",
            "",
        )

        out.append(
            {
                "title": x.get(
                    "title",
                    "",
                ),
                "abstract": clean(
                    x.get(
                        "abstract",
                        "",
                    )
                ),
                "url": (
                    pdf
                    or x.get(
                        "url",
                        "",
                    )
                ),
                "doi": ext.get(
                    "DOI",
                    "",
                ),
                "provider":
                    "semantic_scholar",
                "open_pdf": pdf,
            }
        )

    return out


def duck(
    q: str,
) -> list[dict]:

    data = api_json(
        "https://api.duckduckgo.com/",
        {
            "q": q,
            "format": "json",
            "no_html": 1,
        },
    )

    out = []

    def walk(items):

        for x in items or []:

            if x.get("FirstURL"):

                out.append(
                    {
                        "title":
                            x.get(
                                "Text",
                                "",
                            ),
                        "abstract":
                            x.get(
                                "Text",
                                "",
                            ),
                        "url":
                            x["FirstURL"],
                        "doi": "",
                        "provider":
                            "duckduckgo",
                    }
                )

            walk(
                x.get(
                    "Topics"
                )
            )

    walk(
        data.get(
            "RelatedTopics"
        )
    )

    return out[:10]


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
    ) as ex:

        futures = [
            ex.submit(
                provider,
                query,
            )
            for query in queries[:2]
            for provider in (
                crossref,
                openalex,
                semantic,
                duck,
            )
        ]

        for f in as_completed(
            futures
        ):
            try:
                results.extend(
                    f.result()
                )
            except Exception:
                pass

    seen = set()
    out = []

    for x in results:

        key = work_key(
            x.get(
                "title",
                "",
            ),
            x.get(
                "doi",
                "",
            ),
        )

        if key in seen:
            continue

        seen.add(key)
        out.append(x)

    return out[:40]


def relevant(
    objective: str,
    item: dict,
) -> bool:

    title = item.get(
        "title",
        "",
    )

    abstract = item.get(
        "abstract",
        "",
    )

    return max(
        similarity(
            objective,
            title,
        ),
        similarity(
            objective,
            abstract,
        ),
    ) >= 0.025


def ingest(
    mid: str,
    objective: str,
    item: dict,
):

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

    url = (
        item.get(
            "open_pdf"
        )
        or item.get(
            "url",
            "",
        )
    )

    text = ""
    kind = ""

    if url:
        text, kind = fetch(
            url
        )

    if (
        len(text) < 500
        and abstract
    ):
        text = abstract

    if (
        len(text) < 250
        and item.get("url")
        and item["url"] != url
    ):

        recovered, recovered_kind = fetch(
            item["url"]
        )

        if len(recovered) > len(text):
            text = recovered
            kind = recovered_kind

    full = (
        (
            len(text) >= 500
            and kind != "html"
        )
        or len(text) >= 1200
    )

    if (
        not title
        or len(text) < 180
    ):
        return None

    provider = item.get(
        "provider",
        "unknown",
    )

    dom = domain(url)

    quality = source_quality(
        provider,
        url,
        text if full else "",
        abstract,
    )

    relevance_score = max(
        similarity(
            objective,
            title,
        ),
        similarity(
            objective,
            text[:10000],
        ),
    )

    wk = work_key(
        title,
        item.get(
            "doi",
            "",
        ),
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
                wk,
                title,
                abstract,
                url,
                item.get(
                    "doi",
                    "",
                ),
                provider,
                provider,
                dom,
                quality,
                relevance_score,
                int(full),
                now(),
            ),
        )

        row = c.execute(
            """
            SELECT id
            FROM works
            WHERE mission_id=?
              AND work_key=?
            """,
            (
                mid,
                wk,
            ),
        ).fetchone()

        source_id = row["id"]

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
                wk,
                source_id,
                text[:5000],
                (
                    "full_text"
                    if full
                    else "abstract"
                ),
                "",
                quality,
                relevance_score,
                provider,
                dom,
                now(),
            ),
        )

        c.commit()
        c.close()

    return wk


def split_sentences(
    text: str,
) -> list[str]:

    return [
        clean(x)
        for x in re.split(
            r"(?<=[.!?])\s+",
            text or "",
        )
        if 40 <= len(
            clean(x)
        ) <= 700
    ]


def claim_ok(
    sentence: str,
    terms: set[str],
) -> bool:

    lo = sentence.lower()

    if not 70 <= len(
        sentence
    ) <= 520:
        return False

    if any(
        p in lo
        for p in NOISE_PHRASES
    ):
        return False

    if (
        "http://" in lo
        or "https://" in lo
        or "&gt;" in lo
        or "&amp;" in lo
    ):
        return False

    if sentence.count("|") > 2:
        return False

    if lo.count("doi") > 2:
        return False

    if len(
        re.findall(
            r"[A-Za-z]",
            sentence,
        )
    ) < 45:
        return False

    if len(
        sentence.split()
    ) < 10:
        return False

    if not any(
        t in lo
        for t in terms
        if len(t) > 3
    ):
        return False

    if not any(
        re.search(
            r"\b"
            + re.escape(p)
            + r"\b",
            lo,
        )
        for p in PREDICATES
    ):
        return False

    if re.match(
        r"^(title|authors?|abstract|"
        r"introduction|keywords?|references?)"
        r"\s*[:\-]",
        lo,
    ):
        return False

    return True


def extract_claims(
    mid: str,
    objective: str,
) -> int:

    terms = research_terms(
        objective
    )

    with DB_LOCK:

        c = db()

        evidence_rows = c.execute(
            """
            SELECT *
            FROM evidence
            WHERE mission_id=?
            ORDER BY relevance DESC,
                     quality DESC
            """,
            (mid,),
        ).fetchall()

        seen = set()
        extracted = []

        for evidence in evidence_rows:

            for sentence in split_sentences(
                evidence["excerpt"]
            ):

                if not claim_ok(
                    sentence,
                    terms,
                ):
                    continue

                fingerprint = hashlib.sha256(
                    re.sub(
                        r"\W+",
                        " ",
                        sentence.lower(),
                    ).encode()
                ).hexdigest()

                if fingerprint in seen:
                    continue

                seen.add(
                    fingerprint
                )

                extracted.append(
                    (
                        sentence,
                        fingerprint,
                    )
                )

                if len(
                    extracted
                ) >= 30:
                    break

            if len(
                extracted
            ) >= 30:
                break

        for sentence, fingerprint in extracted:

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
                    fingerprint,
                    "UNASSESSED",
                    0.0,
                    now(),
                ),
            )

        c.commit()
        c.close()

    return len(extracted)


def relation(
    a: str,
    b: str,
    objective: str,
):

    sim = similarity(
        a,
        b,
    )

    shared = (
        tokens(a)
        & tokens(b)
        & research_terms(objective)
    )

    if (
        sim < 0.20
        or len(shared) < 2
    ):
        return (
            None,
            sim,
            "insufficient semantic overlap",
        )

    neg_a = bool(
        tokens(a)
        & NEGATION
    )

    neg_b = bool(
        tokens(b)
        & NEGATION
    )

    if (
        neg_a != neg_b
        and sim >= 0.24
    ):
        return (
            "contradicts",
            sim,
            (
                "opposing negation with "
                "shared research terms"
            ),
        )

    if (
        tokens(a)
        & LIMITATION
        or tokens(b)
        & LIMITATION
    ):

        if sim >= 0.28:
            return (
                "limits",
                sim,
                (
                    "limitation language with "
                    "shared research terms"
                ),
            )

    return (
        "supports",
        sim,
        (
            "shared research terms and "
            "semantic overlap"
        ),
    )


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

        evidence_rows = c.execute(
            """
            SELECT *
            FROM evidence
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchall()

        for evidence in evidence_rows:

            for claim in claims:

                sim = similarity(
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
                    sim >= 0.18
                    and len(shared) >= 2
                ):

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
                            "supported_by",
                            sim,
                            (
                                "claim-to-evidence "
                                "semantic/lexical overlap"
                            ),
                            now(),
                        ),
                    )

        for i, a in enumerate(
            claims
        ):

            for b in claims[i + 1:]:

                rel, score, reason = relation(
                    a["claim"],
                    b["claim"],
                    objective,
                )

                if rel:

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
                                a["id"]
                            ),
                            "claim",
                            str(
                                b["id"]
                            ),
                            rel,
                            score,
                            reason,
                            now(),
                        ),
                    )

        c.commit()
        c.close()


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

            domains = set()
            families = set()
            work_ids = set()

            for evidence in evidence_rows:

                sim = similarity(
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
                    sim >= 0.20
                    and len(shared) >= 2
                ):

                    support.append(
                        evidence
                    )

                    domains.add(
                        evidence["domain"]
                    )

                    families.add(
                        evidence["source_family"]
                    )

                    work_ids.add(
                        evidence["work_key"]
                    )

            for evidence in evidence_rows:

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
                    similarity(
                        claim["claim"],
                        evidence["excerpt"],
                    ) >= 0.24
                    and len(shared) >= 2
                ):

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

                    if neg1 != neg2:
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
                    "id":
                        claim["id"],
                    "status":
                        status,
                    "confidence":
                        round(
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
                        len(contradictions),
                }
            )

        c.commit()
        c.close()

    return output


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
        verified
        and usable
        and domains >= 2
        and families >= 2
        and len(edges) > 0
    )

    actually_working = round(
        100
        * sum(
            stages.get(s)
            == "completed"
            for s in [
                "discovery",
                "evidence_ingestion",
                "claims",
                "evidence_graph",
            ]
        )
        / 4,
        1,
    )

    end_to_end = round(
        100
        * sum(
            stages.get(s)
            == "completed"
            for s in [
                "discovery",
                "evidence_ingestion",
                "claims",
                "verification",
                "counter_evidence",
                "mission",
            ]
        )
        / 6,
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

    return {
        "version":
            VERSION,

        "build":
            BUILD,

        "mission_id":
            mid,

        "objective":
            objective,

        "current_reality": {
            "status":
                (
                    "research_pipeline_operational_"
                    "but_verification_incomplete"
                ),

            "works":
                independent_works,

            "usable_evidence":
                usable,

            "claims":
                total,

            "verified_claims":
                verified,

            "supported_claims":
                supported,

            "domains":
                domains,

            "source_families":
                families,

            "graph_edges":
                len(edges),

            "evidence_gate":
                gate,
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
                    (
                        "completed discovery, "
                        "evidence ingestion, claims, "
                        "and graph stages"
                    ),
                "denominator":
                    4,
                "definition":
                    (
                        "pipeline stages completed "
                        "successfully"
                    ),
            },

            "independently_verified_percent": {
                "numerator":
                    verified,
                "denominator":
                    total,
                "definition":
                    (
                        "claims reaching VERIFIED "
                        "under independent-work/"
                        "domain/family rules"
                    ),
            },

            "end_to_end_demonstrated_percent": {
                "numerator":
                    (
                        "completed required "
                        "audit pipeline stages"
                    ),
                "denominator":
                    6,
                "definition":
                    (
                        "completed discovery, ingestion, "
                        "claims, verification, "
                        "counter-evidence, and mission stages"
                    ),
            },

            "evidence_integrity_percent": {
                "numerator":
                    verified,
                "denominator":
                    total,
                "definition":
                    (
                        "verified claims divided by "
                        "extracted claims; this is a "
                        "narrow integrity metric, not "
                        "overall intelligence"
                    ),
            },
        },

        "gate_definition": {
            "requires":
                (
                    "at least 1 verified claim supported "
                    "by >=2 independent works across >=2 "
                    "domains and >=2 source families, "
                    "plus usable evidence and provenance edges"
                ),

            "passed":
                gate,
        },

        "verified_capabilities": [
            "mission creation and persistence",
            "multi-provider source discovery",
            "safe URL retrieval",
            "evidence ingestion",
            "candidate claim extraction",
            "claim-to-evidence provenance",
            "independent-work/domain/family verification",
            "contradiction detection",
            "persistent mission events",
            "evidence gate",
            "reality audit metrics",
        ],

        "not_yet_demonstrated": [
            "reliable independent corroboration across repeated unseen tasks",
            "high-precision autonomous verification",
            "autonomous self-correction",
            "reliable real-world tool execution",
            "production-grade autonomy",
            "continuous learning/self-improvement",
        ],

        "next_test":
            (
                "Run a fresh unseen objective and require "
                "at least one independently corroborated "
                "claim to reach VERIFIED without manual intervention."
            ),

        "events": [
            dict(x)
            for x in events
        ],
    }


def run_mission(
    mid: str,
    objective: str,
):

    try:

        event(
            mid,
            "mission",
            "running",
        )

        event(
            mid,
            "discovery",
            "running",
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
        )

        claim_count = extract_claims(
            mid,
            objective,
        )

        event(
            mid,
            "claims",
            "completed",
            f"{claim_count} substantive claims",
        )

        event(
            mid,
            "evidence_graph",
            "running",
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
                f"{edge_count} "
                "provenance/relationship edges"
            ),
        )

        event(
            mid,
            "verification",
            "running",
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
            (
                "counter-evidence represented "
                "through contradiction analysis"
            ),
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
                f"gate="
                f"{result['gate_definition']['passed']}"
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
                "error":
                    repr(exc),

                "version":
                    VERSION,

                "build":
                    BUILD,
            },
        )


class MissionIn(
    BaseModel
):

    objective: str = Field(
        min_length=10,
        max_length=12000,
    )


@app.get(
    "/",
    response_class=HTMLResponse,
)
def root():

    return (
        f"<h1>AI Infinity</h1>"
        f"<p>{VERSION} — {BUILD}</p>"
        f"<p>Evidence-first autonomous research.</p>"
    )


@app.get("/health")
def health():

    return {
        "status":
            "ok",

        "version":
            VERSION,

        "build":
            BUILD,
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
        "status":
            "online",

        "missions":
            count,

        "version":
            VERSION,

        "build":
            BUILD,
    }


@app.get("/capabilities")
def capabilities():

    return {
        "version":
            VERSION,

        "build":
            BUILD,

        "capabilities": [
            "multi-provider discovery",
            "safe URL retrieval",
            "evidence excerpts",
            "claim extraction",
            "claim-to-evidence provenance",
            "independent work/domain/family verification",
            "contradiction detection",
            "persistent missions/events",
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
    )

    EXECUTOR.submit(
        run_mission,
        mid,
        req.objective,
    )

    return {
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
    }


@app.post("/command")
def command(
    req: MissionIn,
):

    return create_mission(
        req
    )


@app.get("/mission/{mid}")
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

    out = dict(row)

    out["result"] = (
        json.loads(
            out["result"]
        )
        if out["result"]
        else None
    )

    return out


@app.get("/missions")
def missions():

    with DB_LOCK:

        c = db()

        rows = [
            dict(x)
            for x in c.execute(
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


@app.get("/mission/{mid}/events")
def mission_events(
    mid: str,
):

    with DB_LOCK:

        c = db()

        rows = [
            dict(x)
            for x in c.execute(
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


@app.get("/mission/{mid}/sources")
def mission_sources(
    mid: str,
):

    with DB_LOCK:

        c = db()

        rows = [
            dict(x)
            for x in c.execute(
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


@app.get("/mission/{mid}/claims")
def mission_claims(
    mid: str,
):

    with DB_LOCK:

        c = db()

        rows = [
            dict(x)
            for x in c.execute(
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


@app.get("/mission/{mid}/evidence")
def mission_evidence(
    mid: str,
):

    with DB_LOCK:

        c = db()

        rows = [
            dict(x)
            for x in c.execute(
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


@app.get("/mission/{mid}/graph")
def mission_graph(
    mid: str,
):

    with DB_LOCK:

        c = db()

        rows = [
            dict(x)
            for x in c.execute(
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


@app.get("/mission/{mid}/audit")
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

            "result":
                None,
        }

    return mission["result"]


@app.get("/validate-source")
def validate_source(
    url: str,
):

    return {
        "url":
            url,

        "safe":
            safe_url(url),
    }


@app.on_event("shutdown")
def shutdown():

    EXECUTOR.shutdown(
        wait=False,
        cancel_futures=True,
    )
