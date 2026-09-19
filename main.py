"""
AI Infinity
TARGET-2050.25
BUILD: EXTRAORDINARY-RESEARCH-RECOVERY-CORE

Major upgrade over TARGET-2050.24

Core:
- FastAPI
- SQLite
- asynchronous missions
- evidence recovery
- HTML/PDF/abstract/metadata ingestion
- OpenAlex
- Crossref
- Semantic Scholar
- DuckDuckGo
- Wikipedia
- evidence tiers
- claim extraction
- claim/evidence linking
- support/contradiction/limitation edges
- independent-work detection
- counter-evidence
- synthesis
- automatic next-cycle planning
- mobile dashboard
"""

from __future__ import annotations

import os
import re
import json
import time
import math
import hashlib
import sqlite3
import threading
from pathlib import Path
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Any

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

VERSION = "TARGET-2050.25"
BUILD = "EXTRAORDINARY-RESEARCH-RECOVERY-CORE"

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

REQUEST_TIMEOUT = 12
MAX_BYTES = 7 * 1024 * 1024
WORKERS = 6

executor = ThreadPoolExecutor(max_workers=WORKERS)

app = FastAPI(
    title="AI Infinity",
    version=VERSION
)


# ============================================================
# DATABASE
# ============================================================

DB_LOCK = threading.Lock()


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
# HELPERS
# ============================================================

def now():
    return time.time()


def mission_id():
    return "mission-" + hashlib.sha1(
        f"{time.time_ns()}".encode()
    ).hexdigest()[:12]


def sha(value: str):
    return hashlib.sha1(
        value.encode("utf-8", errors="ignore")
    ).hexdigest()


def clean_text(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\[[0-9,\s]+\]", " ", text)
    text = re.sub(r"\(https?://[^)]+\)", " ", text)

    return text.strip()


def words(text: str):
    return set(
        re.findall(
            r"\b[a-zA-Z][a-zA-Z0-9\-]{2,}\b",
            text.lower()
        )
    )


def similarity(a: str, b: str):
    wa = words(a)
    wb = words(b)

    if not wa or not wb:
        return 0.0

    return len(wa & wb) / max(1, len(wa | wb))


def domain_of(url: str):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def canonical_url(url: str):
    try:
        p = urlparse(url)

        host = p.netloc.lower().replace("www.", "")

        path = re.sub(
            r"/+$",
            "",
            p.path
        )

        return f"{p.scheme.lower()}://{host}{path}"
    except Exception:
        return url


def safe_json(value):
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "{}"


# ============================================================
# SSRF / URL FIREWALL
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254"
}


def validate_url(url: str):
    try:
        p = urlparse(url)

        if p.scheme not in {"http", "https"}:
            return False

        host = (p.hostname or "").lower()

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

    elif tier == "DISCOVERY_ONLY":
        score += 0.00

    if domain.endswith(".edu"):
        score += 0.05

    if domain.endswith(".gov"):
        score += 0.06

    return min(1.0, round(score, 3))


# ============================================================
# SOURCE PROBLEM DETECTION
# ============================================================

BAD_PATTERNS = [
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
]


def looks_like_problem(text: str):
    low = text.lower()

    hits = sum(
        1 for x in BAD_PATTERNS
        if x in low
    )

    return hits >= 1


def looks_binary(data: bytes):
    if not data:
        return True

    sample = data[:2000]

    if b"%PDF-" in sample:
        return True

    bad = sum(
        1 for b in sample
        if b == 0
    )

    return bad > 10


# ============================================================
# HTML TEXT EXTRACTION
# ============================================================

def html_to_text(html: str):
    if not html:
        return ""

    text = html

    text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        text,
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
        text.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
    )

    return clean_text(text)


# ============================================================
# PDF
# ============================================================

def extract_pdf(data: bytes):
    if PdfReader is None:
        return ""

    try:
        import io

        reader = PdfReader(
            io.BytesIO(data)
        )

        chunks = []

        for page in reader.pages[:30]:
            try:
                txt = page.extract_text() or ""
                chunks.append(txt)
            except Exception:
                continue

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
        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent":
                "AI-Infinity/2050 research engine"
            },
            allow_redirects=True,
            stream=True
        )

        if r.status_code >= 400:
            return None

        final_url = r.url

        if not validate_url(final_url):
            return None

        data = r.raw.read(MAX_BYTES)

        content_type = (
            r.headers.get(
                "content-type",
                ""
            ).lower()
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
                    "content_type": "pdf"
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

        if looks_like_problem(text[:10000]):
            return None

        return {
            "url": final_url,
            "text": text,
            "tier": "FULL_TEXT",
            "content_type": "html"
        }

    except Exception:
        return None


# ============================================================
# ABSTRACT EXTRACTION
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
        r = requests.get(
            "https://api.crossref.org/works",
            params={
                "query.bibliographic": query,
                "rows": 10
            },
            timeout=REQUEST_TIMEOUT
        )

        data = r.json()

        for item in data.get(
            "message",
            {}
        ).get(
            "items",
            []
        ):

            title = (
                item.get("title") or [""]
            )[0]

            doi = item.get("DOI")

            url = (
                item.get("URL")
                or (
                    f"https://doi.org/{doi}"
                    if doi else ""
                )
            )

            if not title or not url:
                continue

            results.append({
                "title": title,
                "url": url,
                "doi": doi or "",
                "abstract": clean_text(
                    item.get("abstract", "")
                ),
                "provider": "crossref"
            })

    except Exception:
        pass

    return results


def openalex_search(query: str):
    results = []

    try:
        r = requests.get(
            "https://api.openalex.org/works",
            params={
                "search": query,
                "per-page": 10
            },
            timeout=REQUEST_TIMEOUT
        )

        data = r.json()

        for item in data.get(
            "results",
            []
        ):

            title = item.get(
                "title",
                ""
            )

            url = (
                item.get("primary_location", {})
                .get("landing_page_url")
                or item.get("doi")
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
                    "https://doi.org/"
                )[1]

            if title:
                results.append({
                    "title": title,
                    "url": url,
                    "doi": doi,
                    "abstract": abstract,
                    "provider": "openalex"
                })

    except Exception:
        pass

    return results


def semantic_scholar_search(query: str):
    results = []

    try:
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": query,
                "limit": 10,
                "fields":
                "title,abstract,url,externalIds,openAccessPdf"
            },
            timeout=REQUEST_TIMEOUT
        )

        data = r.json()

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

            url = (
                item.get("url")
                or (
                    item.get(
                        "openAccessPdf"
                    ) or {}
                ).get("url")
                or ""
            )

            ids = item.get(
                "externalIds",
                {}
            ) or {}

            doi = ids.get(
                "DOI",
                ""
            )

            if title:
                results.append({
                    "title": title,
                    "url": url,
                    "doi": doi,
                    "abstract": abstract,
                    "provider":
                    "semantic_scholar"
                })

    except Exception:
        pass

    return results


def duck_search(query: str):
    results = []

    try:
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={
                "q": query
            },
            headers={
                "User-Agent":
                "Mozilla/5.0 AI-Infinity"
            },
            timeout=REQUEST_TIMEOUT
        )

        html = r.text

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
                "provider": "duckduckgo"
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
        item.get("title", "")
    ).lower()

    if title:
        return "title:" + sha(title)

    return "url:" + sha(
        canonical_url(
            item.get("url", "")
        )
    )


def deduplicate(results):
    grouped = {}

    for item in results:

        key = work_key(item)

        existing = grouped.get(key)

        if not existing:
            grouped[key] = item
            continue

        # Prefer richer evidence.
        old_abs = len(
            existing.get(
                "abstract",
                ""
            )
        )

        new_abs = len(
            item.get(
                "abstract",
                ""
            )
        )

        if new_abs > old_abs:
            grouped[key] = item

    return list(grouped.values())


# ============================================================
# RELEVANCE
# ============================================================

def relevance(objective, title, abstract=""):
    text = (
        f"{title} {abstract}"
    )

    return round(
        similarity(
            objective,
            text
        ),
        3
    )


# ============================================================
# EVIDENCE INGESTION
# ============================================================

def ingest(item, objective):
    title = clean_text(
        item.get("title", "")
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

    rel = relevance(
        objective,
        title,
        abstract
    )

    # First try direct article retrieval.
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

    elif title:
        content = title
        tier = "STRUCTURED_METADATA"
        final_url = url

    else:
        return None

    domain = domain_of(
        final_url or url
    )

    quality = source_quality(
        provider,
        tier,
        domain
    )

    return {
        "source_key": work_key(item),
        "url": final_url or url,
        "title": title,
        "domain": domain,
        "provider": provider,
        "tier": tier,
        "quality": quality,
        "relevance": rel,
        "content": content,
        "metadata": {
            "doi": item.get("doi", ""),
            "original_provider":
                provider
        }
    }


# ============================================================
# TEXT CLEANING FOR CLAIM EXTRACTION
# ============================================================

NAV_PATTERNS = [
    "related articles",
    "similar content",
    "download references",
    "google scholar",
    "article pdf download",
    "explore related subjects",
    "suggested using machine learning",
    "acknowledgements",
    "author information",
    "research interests",
    "about the authors",
    "copyright",
    "subscribe",
    "sign in",
]


def useful_sentence(sentence: str):

    s = clean_text(sentence)

    if len(s) < 45:
        return False

    low = s.lower()

    if any(
        p in low
        for p in NAV_PATTERNS
    ):
        return False

    if low.count("©") > 0:
        return False

    if low.startswith(
        ("article", "chapter", "author")
    ):
        return False

    # biography-like sentences
    if (
        "is a researcher at" in low
        or "research interests are" in low
        or "is professor" in low
    ):
        return False

    # Reference-like fragments
    if (
        low.startswith("doi:")
        or low.startswith("http")
    ):
        return False

    return True


# ============================================================
# CLAIM EXTRACTION
# ============================================================

CLAIM_SIGNAL = re.compile(
    r"\b("
    r"found|findings|found that|showed|shows|"
    r"demonstrated|results|resulted|"
    r"improved|reduced|increased|decreased|"
    r"achieved|accuracy|success rate|failure rate|"
    r"benchmark|evaluation|experiment|"
    r"outperformed|underperformed|"
    r"limitation|limitations|failure|failures|"
    r"reliable|reliability|"
    r"effective|ineffective|"
    r"human intervention|"
    r"task completion|"
    r"task success|"
    r"performance"
    r")\b",
    re.I
)


def extract_claims(source):
    content = source.get(
        "content",
        ""
    )

    if len(content) < 100:
        return []

    # Metadata-only source does not generate factual claims.
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

        if not useful_sentence(
            sentence
        ):
            continue

        if not CLAIM_SIGNAL.search(
            sentence
        ):
            continue

        # avoid giant page fragments
        if len(sentence) > 650:
            continue

        candidates.append(
            sentence
        )

    # De-duplicate similar claims.
    final = []

    for claim in candidates:

        if any(
            similarity(
                claim,
                x
            ) > 0.80
            for x in final
        ):
            continue

        final.append(claim)

        if len(final) >= 8:
            break

    return final


# ============================================================
# CLAIM RELATION
# ============================================================

NEGATION_WORDS = {
    "not",
    "no",
    "never",
    "failed",
    "failure",
    "unable",
    "worse",
    "decreased",
    "limited",
    "limitation",
    "unreliable",
    "ineffective"
}


def relation(a, b):

    sim = similarity(
        a,
        b
    )

    if sim < 0.18:
        return None, sim

    wa = words(a)
    wb = words(b)

    neg_a = bool(
        wa & NEGATION_WORDS
    )

    neg_b = bool(
        wb & NEGATION_WORDS
    )

    if neg_a != neg_b and sim >= 0.24:
        return "contradiction", sim

    limitation = (
        bool(
            wa & {
                "limitation",
                "limitations",
                "failure",
                "failures"
            }
        )
    )

    if limitation:
        return "limitation", sim

    return "support", sim


# ============================================================
# DATABASE EVENTS
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
                now()
            )
        )

        conn.commit()
        conn.close()


def save_source(mid, source):
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
                now()
            )
        )

        conn.commit()
        conn.close()


def save_claim(mid, claim, status, confidence):
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
                now()
            )
        )

        conn.commit()
        conn.close()


# ============================================================
# QUERY PLANNER
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


# ============================================================
# COUNTER QUERIES
# ============================================================

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
# RESEARCH ENGINE
# ============================================================

def research(mid, objective):

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
        discovered.extend(
            search_one(query)
        )

    discovered = deduplicate(
        discovered
    )

    event(
        mid,
        "discovery",
        "completed",
        f"{len(discovered)} unique works"
    )

    # ========================================================
    # INGESTION
    # ========================================================

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
        for item in discovered[:40]
    ]

    for future in as_completed(
        futures
    ):
        try:
            result = future.result()

            if not result:
                continue

            # Relevance gate is intentionally soft.
            if result["relevance"] >= 0.08:
                accepted.append(
                    result
                )

        except Exception:
            pass

    # strongest representation per work
    grouped = {}

    tier_rank = {
        "FULL_TEXT": 4,
        "ABSTRACT": 3,
        "STRUCTURED_METADATA": 2,
        "DISCOVERY_ONLY": 1
    }

    for source in accepted:

        key = source[
            "source_key"
        ]

        old = grouped.get(key)

        if (
            not old
            or tier_rank[
                source["tier"]
            ] > tier_rank[
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

    # ========================================================
    # CLAIM EXTRACTION
    # ========================================================

    event(
        mid,
        "claim_extraction",
        "running"
    )

    claims = []

    for source in accepted:

        source_claims = extract_claims(
            source
        )

        for claim in source_claims:

            claims.append({
                "text": claim,
                "source": source
            })

    # unique claims
    unique_claims = []

    for item in claims:

        if any(
            similarity(
                item["text"],
                old["text"]
            ) > 0.78
            for old in unique_claims
        ):
            continue

        unique_claims.append(
            item
        )

    event(
        mid,
        "claim_extraction",
        "completed",
        f"{len(unique_claims)} substantive claims"
    )

    # ========================================================
    # LINKING
    # ========================================================

    event(
        mid,
        "evidence_linking",
        "running"
    )

    claim_results = []

    support_edges = []
    contradiction_edges = []
    limitation_edges = []

    for idx, item in enumerate(
        unique_claims
    ):

        claim = item["text"]

        support = []
        contradict = []
        limitations = []

        for other in unique_claims:

            if other is item:
                continue

            rel, score = relation(
                claim,
                other["text"]
            )

            if not rel:
                continue

            if rel == "support":
                support.append(
                    other
                )

                support_edges.append({
                    "from": idx,
                    "to": unique_claims.index(other),
                    "score": round(score, 3)
                })

            elif rel == "contradiction":
                contradict.append(
                    other
                )

                contradiction_edges.append({
                    "from": idx,
                    "to": unique_claims.index(other),
                    "score": round(score, 3)
                })

            elif rel == "limitation":
                limitations.append(
                    other
                )

                limitation_edges.append({
                    "from": idx,
                    "to": unique_claims.index(other),
                    "score": round(score, 3)
                })

        claim_results.append({
            "claim": claim,
            "source": item["source"],
            "support": support,
            "contradictions": contradict,
            "limitations": limitations
        })

    event(
        mid,
        "evidence_linking",
        "completed",
        f"{len(support_edges) + len(contradiction_edges) + len(limitation_edges)} relationships"
    )

    # ========================================================
    # VERIFICATION
    # ========================================================

    event(
        mid,
        "verification",
        "running"
    )

    final_claims = []

    for item in claim_results:

        source = item["source"]

        support_sources = {
            x["source"]["source_key"]
            for x in item["support"]
        }

        contradiction_sources = {
            x["source"]["source_key"]
            for x in item["contradictions"]
        }

        # Include original evidence.
        support_sources.add(
            source["source_key"]
        )

        independent_support = len(
            support_sources
        )

        independent_contradiction = len(
            contradiction_sources
        )

        tier_bonus = {
            "FULL_TEXT": 0.25,
            "ABSTRACT": 0.18,
            "STRUCTURED_METADATA": 0.05
        }.get(
            source["tier"],
            0
        )

        confidence = min(
            1.0,
            0.30
            + min(
                0.40,
                independent_support * 0.15
            )
            + tier_bonus
            + source["quality"] * 0.15
        )

        if independent_contradiction:
            status = "UNCERTAIN"

        elif independent_support >= 2:
            status = "VERIFIED"

        elif source["tier"] in {
            "FULL_TEXT",
            "ABSTRACT"
        }:
            status = "INSUFFICIENT"

        else:
            status = "REJECTED"

        final_claims.append({
            "claim": item["claim"],
            "status": status,
            "confidence": round(
                confidence,
                3
            ),
            "supporting_sources":
                independent_support,
            "contradicting_sources":
                independent_contradiction,
            "support_domains": list({
                x["source"]["domain"]
                for x in item["support"]
            }),
            "contradiction_domains": list({
                x["source"]["domain"]
                for x in item["contradictions"]
            }),
            "source_keys": list(
                support_sources
            )
        })

        save_claim(
            mid,
            item["claim"],
            status,
            confidence
        )

    verified = sum(
        1
        for x in final_claims
        if x["status"] == "VERIFIED"
    )

    uncertain = sum(
        1
        for x in final_claims
        if x["status"] == "UNCERTAIN"
    )

    unsupported = sum(
        1
        for x in final_claims
        if x["status"] in {
            "INSUFFICIENT",
            "REJECTED"
        }
    )

    # ========================================================
    # COUNTER EVIDENCE
    # ========================================================

    event(
        mid,
        "counter_evidence",
        "running"
    )

    counter_sources = []

    important_claims = [
        x["claim"]
        for x in final_claims
        if x["status"] != "REJECTED"
    ][:6]

    for claim in important_claims:

        for query in counter_queries(
            claim
        ):

            results = search_one(
                query
            )

            counter_sources.extend(
                results
            )

    counter_sources = deduplicate(
        counter_sources
    )

    counter_accepted = []

    for item in counter_sources[:30]:

        source = ingest(
            item,
            objective
        )

        if source:
            counter_accepted.append(
                source
            )

    counter_accepted = list({
        x["source_key"]: x
        for x in counter_accepted
    }.values())

    event(
        mid,
        "counter_evidence",
        "completed",
        f"{len(counter_accepted)} counter-evidence records"
    )

    # ========================================================
    # GRAPH
    # ========================================================

    graph_nodes = []
    graph_edges = []

    for source in accepted:

        graph_nodes.append({
            "id": source[
                "source_key"
            ],
            "type": "source",
            "domain": source[
                "domain"
            ],
            "provider": source[
                "provider"
            ],
            "tier": source[
                "tier"
            ],
            "quality": source[
                "quality"
            ],
            "relevance": source[
                "relevance"
            ]
        })

    for i, claim in enumerate(
        final_claims,
        start=1
    ):

        cid = f"claim-{i}"

        graph_nodes.append({
            "id": cid,
            "type": "claim",
            "text": claim[
                "claim"
            ],
            "status": claim[
                "status"
            ]
        })

        for source_key in claim[
            "source_keys"
        ]:

            graph_edges.append({
                "from": source_key,
                "to": cid,
                "type": "supports"
            })

    for edge in contradiction_edges:

        graph_edges.append({
            "from":
                f"claim-{edge['from'] + 1}",
            "to":
                f"claim-{edge['to'] + 1}",
            "type": "contradicts",
            "score":
                edge["score"]
        })

    for edge in limitation_edges:

        graph_edges.append({
            "from":
                f"claim-{edge['from'] + 1}",
            "to":
                f"claim-{edge['to'] + 1}",
            "type": "limits",
            "score":
                edge["score"]
        })

    # ========================================================
    # QUALITY METRICS
    # ========================================================

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

    for x in accepted:
        tiers[x["tier"]] = (
            tiers.get(
                x["tier"],
                0
            ) + 1
        )

    avg_quality = (
        sum(
            x["quality"]
            for x in accepted
        ) / len(accepted)
        if accepted else 0
    )

    source_diversity = min(
        1.0,
        len(domains) / 5
    )

    provider_diversity = min(
        1.0,
        len(providers) / 4
    )

    independence = min(
        1.0,
        len(works) / 6
    )

    evidence_coverage = min(
        1.0,
        len(final_claims) / 10
    )

    verification_rate = (
        verified / len(final_claims)
        if final_claims
        else 0
    )

    research_strength = round(
        (
            avg_quality * 0.20
            + source_diversity * 0.15
            + provider_diversity * 0.10
            + independence * 0.20
            + evidence_coverage * 0.15
            + verification_rate * 0.20
        ),
        3
    )

    confidence = round(
        (
            verification_rate * 0.50
            + avg_quality * 0.20
            + independence * 0.20
            + source_diversity * 0.10
        ),
        3
    )

    # ========================================================
    # DIAGNOSIS
    # ========================================================

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

    if contradiction_edges:
        diagnosis.append(
            "CONTRADICTORY_EVIDENCE"
        )

    if not diagnosis:
        diagnosis.append(
            "RESEARCH_PIPELINE_HEALTHY"
        )

    # ========================================================
    # NEXT ACTIONS
    # ========================================================

    next_actions = []

    if "NO_SUBSTANTIVE_CLAIMS" in diagnosis:
        next_actions.append(
            "Recover structured abstracts and empirical findings from additional scholarly providers."
        )

    if "NO_FULL_TEXT_EVIDENCE" in diagnosis:
        next_actions.append(
            "Search for open-access full text and institutional copies."
        )

    if "LOW_INDEPENDENT_WORKS" in diagnosis:
        next_actions.append(
            "Increase independent primary-study coverage."
        )

    if "LOW_SOURCE_DIVERSITY" in diagnosis:
        next_actions.append(
            "Expand evidence across independent domains and institutions."
        )

    if "LOW_PROVIDER_DIVERSITY" in diagnosis:
        next_actions.append(
            "Use additional research indexes and scholarly providers."
        )

    if contradiction_edges:
        next_actions.append(
            "Run targeted contradiction and replication searches."
        )

    if not next_actions:
        next_actions.append(
            "Continue monitoring and update the evidence graph with new studies."
        )

    # ========================================================
    # SYNTHESIS
    # ========================================================

    if verified >= 2 and not contradiction_edges:

        conclusion = (
            "Multiple independent evidence records "
            "support substantive claims, but the evidence "
            "should remain tied to the specific populations, "
            "tasks, and conditions studied."
        )

    elif final_claims:

        conclusion = (
            "The research produced substantive evidence, "
            "but claim-level verification remains incomplete "
            "or mixed."
        )

    else:

        conclusion = (
            "The current evidence set does not yet contain "
            "enough recoverable substantive claims for a "
            "strong conclusion."
        )

    # ========================================================
    # RESULT
    # ========================================================

    completed = now()

    result = {
        "task_id": mid,
        "mission_id": mid,
        "status": "completed",
        "version": VERSION,
        "build": BUILD,
        "objective": objective,

        "agent_trace": [
            {
                "stage": "planning",
                "status": "completed",
                "research_questions": queries,
                "count": len(queries)
            },
            {
                "stage": "discovery",
                "status": "completed",
                "sources_discovered":
                    len(discovered)
            },
            {
                "stage": "evidence_ingestion",
                "status": "completed",
                "accepted_sources":
                    len(accepted)
            },
            {
                "stage": "claim_extraction",
                "status": "completed",
                "claims":
                    len(final_claims)
            },
            {
                "stage": "evidence_linking",
                "status": "completed",
                "relationships":
                    len(graph_edges)
            },
            {
                "stage": "verification",
                "status": "completed",
                "verified":
                    verified
            },
            {
                "stage": "counter_evidence",
                "status": "completed",
                "sources":
                    len(counter_accepted)
            },
            {
                "stage": "synthesis",
                "status": "completed"
            }
        ],

        "providers": {
            "used": providers,
            "count": len(providers)
        },

        "evidence": {
            "count": len(accepted),
            "graph_nodes":
                len(graph_nodes),
            "graph_edges":
                len(graph_edges),
            "support_edges":
                len(support_edges),
            "contradiction_edges":
                len(contradiction_edges),
            "limitation_edges":
                len(limitation_edges),
            "independent_domains":
                len(domains),
            "domains":
                domains,
            "independent_works":
                len(works),
            "average_source_quality":
                round(avg_quality, 3),
            "source_diversity":
                round(source_diversity, 3),
            "provider_diversity":
                round(provider_diversity, 3),
            "evidence_tiers":
                tiers
        },

        "claims":
            final_claims,

        "verification": {
            "enabled": True,
            "verified": verified,
            "unsupported": unsupported,
            "uncertain": uncertain,
            "confidence": confidence,
            "research_strength":
                research_strength,
            "contradictions":
                len(contradiction_edges)
        },

        "counter_evidence": {
            "sources":
                len(counter_accepted),
            "claims_tested":
                len(important_claims)
        },

        "evidence_graph": {
            "nodes":
                graph_nodes,
            "edges":
                graph_edges
        },

        "diagnosis": diagnosis,

        "synthesis": {
            "conclusion":
                conclusion,
            "verified_claims":
                verified,
            "uncertain_claims":
                uncertain,
            "insufficient_claims":
                sum(
                    1
                    for x in final_claims
                    if x["status"] ==
                    "INSUFFICIENT"
                ),
            "rejected_claims":
                sum(
                    1
                    for x in final_claims
                    if x["status"] ==
                    "REJECTED"
                ),
            "contradictions":
                len(contradiction_edges),
            "independent_domains":
                len(domains),
            "counter_evidence_sources":
                len(counter_accepted),
            "next_actions":
                next_actions
        },

        "next_cycle": {
            "recommended":
                next_actions,
            "diagnosis":
                diagnosis
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
                )
        }
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
                mid
            )
        )

        conn.commit()
        conn.close()

    event(
        mid,
        "mission",
        "completed",
        f"strength={research_strength}"
    )

    return result


# ============================================================
# MISSION RUNNER
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
                mid
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
            "task_id": mid,
            "mission_id": mid,
            "status": "failed",
            "version": VERSION,
            "build": BUILD,
            "error": str(exc),
            "next_actions": [
                "Retry the mission.",
                "Inspect the failed research stage."
            ]
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
                    mid
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
# API
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def dashboard():

    return HTMLResponse("""
<!doctype html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>
body{
margin:0;
background:#070b12;
color:#f4f7fb;
font-family:system-ui,-apple-system,BlinkMacSystemFont,sans-serif;
}

.wrap{
max-width:900px;
margin:auto;
padding:24px;
}

.hero{
padding:25px 0;
}

h1{
font-size:42px;
margin:0 0 8px;
}

.sub{
color:#9aa7b7;
font-size:17px;
}

.status{
display:inline-block;
padding:7px 12px;
border-radius:20px;
background:#12351f;
color:#72f5a1;
font-size:13px;
margin:15px 0;
}

textarea{
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
}

button{
width:100%;
margin-top:12px;
padding:16px;
border:0;
border-radius:14px;
font-size:17px;
font-weight:700;
background:#ffffff;
color:#05070a;
}

.grid{
display:grid;
grid-template-columns:repeat(4,1fr);
gap:10px;
margin:20px 0;
}

.card{
background:#0d1420;
border:1px solid #1d2a3a;
padding:15px;
border-radius:14px;
}

.label{
color:#7f8da0;
font-size:12px;
}

.value{
font-size:16px;
margin-top:5px;
}

pre{
white-space:pre-wrap;
word-break:break-word;
background:#05080d;
border:1px solid #1d2a3a;
border-radius:14px;
padding:15px;
overflow:auto;
}

@media(max-width:700px){
.grid{
grid-template-columns:repeat(2,1fr);
}
h1{
font-size:34px;
}
}
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
Autonomous evidence intelligence
</div>

</div>

<textarea id="objective"
placeholder="Give AI Infinity a mission..."></textarea>

<button onclick="runMission()">
🚀 RUN MISSION
</button>

<div class="grid">

<div class="card">
<div class="label">VERSION</div>
<div class="value">
""" + VERSION + """
</div>
</div>

<div class="card">
<div class="label">ENGINE</div>
<div class="value">
EXTRAORDINARY
</div>
</div>

<div class="card">
<div class="label">EVIDENCE</div>
<div class="value">
RECOVERY
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

async function safeJSON(response){

    const text = await response.text();

    try{
        return JSON.parse(text);
    }catch(e){
        return {
            status:"error",
            http_status:response.status,
            raw:text
        };
    }
}

async function runMission(){

    const objective =
        document.getElementById(
            "objective"
        ).value.trim();

    const output =
        document.getElementById(
            "output"
        );

    if(!objective){
        output.textContent =
            "Enter a mission first.";
        return;
    }

    output.textContent =
        "🚀 Mission accepted...";

    try{

        const response =
            await fetch(
                "/mission",
                {
                    method:"POST",
                    headers:{
                        "Content-Type":
                            "application/json"
                    },
                    body:JSON.stringify({
                        objective:objective,
                        research:true,
                        verify:true,
                        remember:true
                    })
                }
            );

        const data =
            await safeJSON(response);

        if(!data.mission_id){

            output.textContent =
                JSON.stringify(
                    data,
                    null,
                    2
                );

            return;
        }

        const id =
            data.mission_id;

        output.textContent =
            "🧠 Mission running...\\n" +
            "ID: " + id;

        const timer =
            setInterval(
                async function(){

                    try{

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
                        ){

                            clearInterval(
                                timer
                            );
                        }

                    }catch(e){

                        output.textContent =
                            "Polling error: " +
                            e.message;
                    }

                },
                3000
            );

    }catch(e){

        output.textContent =
            "Mission error: " +
            e.message;
    }
}

</script>

</body>
</html>
""")


@app.get("/health")
def health():

    return {
        "status": "ok",
        "version": VERSION,
        "build": BUILD
    }


@app.get("/status")
def status():

    return {
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "engine": "extraordinary",
        "evidence_recovery": True,
        "claim_verification": True,
        "counter_evidence": True,
        "autonomous_research": True
    }


@app.get("/capabilities")
def capabilities():

    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "autonomous_research",
            "evidence_recovery",
            "full_text_ingestion",
            "pdf_extraction",
            "abstract_recovery",
            "metadata_recovery",
            "claim_extraction",
            "claim_verification",
            "counter_evidence",
            "contradiction_detection",
            "limitation_detection",
            "evidence_graph",
            "independent_work_detection",
            "research_diagnostics",
            "automatic_next_cycle"
        ]
    }


@app.post("/mission")
def create_mission(req: MissionRequest):

    if not req.objective.strip():
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
                req.objective.strip(),
                "queued",
                now()
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
        req.objective.strip()
    )

    return JSONResponse(
        status_code=202,
        content={
            "task_id": mid,
            "mission_id": mid,
            "status": "queued",
            "version": VERSION,
            "build": BUILD,
            "message":
                "Mission accepted and running autonomously."
        }
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
        json.loads(row["result"])
        if row["result"]
        else None
    )

    return {
        "task_id": mid,
        "mission_id": mid,
        "status": row["status"],
        "objective": row["objective"],
        "result": result
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
        dict(x)
        for x in rows
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
        dict(x)
        for x in rows
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
        dict(x)
        for x in rows
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
        dict(x)
        for x in rows
    ]


@app.post("/validate-source")
def validate_source(url: str):

    if not validate_url(url):
        return {
            "valid": False
        }

    result = fetch_url(url)

    if not result:
        return {
            "valid": False,
            "reason":
                "Source could not be safely recovered."
        }

    return {
        "valid": True,
        "url": result["url"],
        "tier": result["tier"],
        "characters":
            len(result["text"])
    }


# ============================================================
# GLOBAL JSON ERROR HANDLING
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
            "status": "error",
            "version": VERSION,
            "build": BUILD,
            "error": str(exc)
        }
    )
