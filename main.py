"""
AI Infinity
TARGET-2050.26
BUILD: DEEP-EVIDENCE-RECOVERY + CLAIM-GRAPH CORE

Goals:
- Multi-provider scholarly discovery
- OpenAlex abstract recovery
- Semantic Scholar abstract/snippet recovery
- Europe PMC recovery
- Crossref metadata fallback
- arXiv recovery
- Direct open-access PDF recovery
- Strong source filtering
- Supplementary/review/decision artifact rejection
- Claim extraction from real substantive text
- Claim/source graph
- Support / contradiction / limitation detection
- Independent corroboration
- Counter-evidence research
- Evidence-quality telemetry
- Async missions
- Mobile dashboard
- Existing FastAPI style preserved
"""

from __future__ import annotations

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
import xml.etree.ElementTree as ET

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


# ============================================================
# CONFIG
# ============================================================

VERSION = "TARGET-2050.26"
BUILD = "DEEP-EVIDENCE-RECOVERY-CLAIM-GRAPH"

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

WORKERS = int(os.getenv("AI_INFINITY_WORKERS", "3"))
REQUEST_TIMEOUT = int(os.getenv("AI_INFINITY_TIMEOUT", "15"))

USER_AGENT = (
    "AI-Infinity/2050.26 "
    "(research-evidence-engine; contact=ai-infinity)"
)

executor = ThreadPoolExecutor(max_workers=WORKERS)

db_lock = threading.Lock()


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Autonomous evidence research and verification engine",
)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        str(DB_PATH),
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        conn = db()
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            mission_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            created_at REAL,
            updated_at REAL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            kind TEXT,
            content TEXT,
            created_at REAL
        )
        """)

        conn.commit()
        conn.close()


init_db()


def save_mission(
    mission_id: str,
    objective: str,
    status: str,
    result: Optional[dict] = None,
):
    now = time.time()

    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO missions
            (mission_id, objective, status, result, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(mission_id) DO UPDATE SET
                status=excluded.status,
                result=excluded.result,
                updated_at=excluded.updated_at
            """,
            (
                mission_id,
                objective,
                status,
                json.dumps(result, ensure_ascii=False)
                if result is not None
                else None,
                now,
                now,
            ),
        )

        conn.commit()
        conn.close()


def load_mission(mission_id: str):
    with db_lock:
        conn = db()
        row = conn.execute(
            "SELECT * FROM missions WHERE mission_id=?",
            (mission_id,),
        ).fetchone()
        conn.close()

    if not row:
        return None

    result = None

    if row["result"]:
        try:
            result = json.loads(row["result"])
        except Exception:
            result = {"raw": row["result"]}

    return {
        "mission_id": row["mission_id"],
        "objective": row["objective"],
        "status": row["status"],
        "result": result,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ============================================================
# HTTP
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
})


def get_json(
    url: str,
    params: Optional[dict] = None,
    timeout: int = REQUEST_TIMEOUT,
):
    try:
        r = session.get(
            url,
            params=params,
            timeout=timeout,
            allow_redirects=True,
        )

        if r.status_code >= 400:
            return None

        return r.json()

    except Exception:
        return None


def get_text(
    url: str,
    timeout: int = REQUEST_TIMEOUT,
):
    try:
        r = session.get(
            url,
            timeout=timeout,
            allow_redirects=True,
        )

        if r.status_code >= 400:
            return None

        return r.text

    except Exception:
        return None


def get_bytes(
    url: str,
    timeout: int = REQUEST_TIMEOUT,
):
    try:
        r = session.get(
            url,
            timeout=timeout,
            allow_redirects=True,
        )

        if r.status_code >= 400:
            return None

        return r.content

    except Exception:
        return None


# ============================================================
# TEXT UTILITIES
# ============================================================

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from",
    "were", "was", "are", "have", "has", "into", "their",
    "they", "than", "then", "which", "using", "used",
    "based", "such", "these", "those", "about", "between",
    "through", "also", "more", "most", "some", "study",
    "research", "results", "paper", "findings",
}


CLAIM_WORDS = {
    "found",
    "find",
    "showed",
    "shows",
    "demonstrated",
    "observed",
    "reported",
    "identified",
    "increased",
    "decreased",
    "improved",
    "reduced",
    "failed",
    "failure",
    "success",
    "successful",
    "accuracy",
    "performance",
    "reliability",
    "error",
    "errors",
    "rate",
    "rates",
    "percentage",
    "percent",
    "significant",
    "limitation",
    "limitations",
    "intervention",
    "outperformed",
    "underperformed",
    "compared",
    "evaluation",
    "benchmark",
    "experiment",
    "experiments",
    "trial",
    "trials",
    "deployment",
    "deployment",
}


BAD_TEXT = {
    "cookie",
    "privacy policy",
    "terms of use",
    "accept cookies",
    "javascript required",
    "sign in",
    "log in",
    "subscribe",
    "copyright",
    "all rights reserved",
    "captcha",
    "access denied",
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = html.unescape(text)

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

    text = re.sub(r"<[^>]+>", " ", text)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def tokens(text: str) -> set:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", text.lower())

    return {
        w
        for w in words
        if w not in STOPWORDS
    }


def relevance(objective: str, text: str) -> float:
    a = tokens(objective)
    b = tokens(text)

    if not a or not b:
        return 0.0

    overlap = len(a & b)

    return round(
        min(1.0, overlap / max(6, min(len(a), 25))),
        3,
    )


def sentence_split(text: str) -> List[str]:
    text = clean_text(text)

    return [
        s.strip()
        for s in re.split(
            r"(?<=[.!?])\s+(?=[A-Z0-9])",
            text,
        )
        if 35 <= len(s.strip()) <= 900
    ]


def looks_like_claim(sentence: str) -> bool:
    s = sentence.lower()

    if len(s) < 45:
        return False

    if any(x in s for x in BAD_TEXT):
        return False

    words = set(re.findall(r"[a-z]+", s))

    signal = len(words & CLAIM_WORDS)

    has_number = bool(
        re.search(
            r"\b\d+(?:\.\d+)?\s*(?:%|percent|times|fold)?\b",
            s,
            flags=re.I,
        )
    )

    has_comparison = bool(
        re.search(
            r"\b(compared|versus|vs\.?|higher|lower|better|worse|less|more)\b",
            s,
            flags=re.I,
        )
    )

    return signal >= 1 or has_number or has_comparison


def normalize_claim(sentence: str) -> str:
    sentence = clean_text(sentence)

    sentence = re.sub(
        r"\[[0-9,\-\s]+\]",
        "",
        sentence,
    )

    sentence = re.sub(
        r"\s+",
        " ",
        sentence,
    )

    return sentence.strip()


def similarity(a: str, b: str) -> float:
    ta = tokens(a)
    tb = tokens(b)

    if not ta or not tb:
        return 0.0

    return len(ta & tb) / max(1, len(ta | tb))


# ============================================================
# SOURCE IDENTIFICATION
# ============================================================

def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        return host
    except Exception:
        return ""


def canonical_doi(doi: str) -> str:
    doi = (doi or "").strip()

    doi = re.sub(
        r"^https?://(dx\.)?doi\.org/",
        "",
        doi,
        flags=re.I,
    )

    doi = doi.rstrip(".,; ")

    return doi.lower()


def source_id(source: dict) -> str:
    doi = canonical_doi(source.get("doi", ""))

    if doi:
        return "doi:" + doi

    pid = source.get("provider_id")

    if pid:
        return f"{source.get('provider')}:{pid}"

    url = source.get("url", "")

    return "url:" + hashlib.sha256(
        url.encode()
    ).hexdigest()[:24]


def is_artifact(source: dict) -> bool:
    title = (source.get("title") or "").lower()
    doi = (source.get("doi") or "").lower()

    bad_title = [
        "supplementary",
        "supplemental",
        "supporting information",
        "data availability",
        "graphical abstract",
        "cover image",
        "reviewer",
        "review report",
        "editor decision",
        "decision letter",
        "response to reviewers",
        "peer review",
        "correction",
        "erratum",
        "retraction notice",
    ]

    if any(x in title for x in bad_title):
        return True

    if re.search(
        r"/(?:s00\d+|review\d+|decision\d+)",
        doi,
    ):
        return True

    return False


def likely_research_source(source: dict) -> bool:
    if is_artifact(source):
        return False

    title = clean_text(source.get("title", ""))

    text = clean_text(
        " ".join(
            [
                title,
                source.get("abstract", ""),
                source.get("text", ""),
            ]
        )
    )

    if len(title) < 8:
        return False

    if len(text) < 80:
        return False

    return True


# ============================================================
# SOURCE QUALITY
# ============================================================

def quality_score(source: dict) -> float:
    score = 0.35

    provider = source.get("provider")

    if provider == "semantic_scholar":
        score += 0.12

    elif provider == "openalex":
        score += 0.10

    elif provider == "europe_pmc":
        score += 0.12

    elif provider == "arxiv":
        score += 0.08

    elif provider == "crossref":
        score += 0.05

    tier = source.get("tier")

    if tier == "FULL_TEXT":
        score += 0.28

    elif tier == "ABSTRACT":
        score += 0.20

    elif tier == "SNIPPET":
        score += 0.14

    elif tier == "STRUCTURED_METADATA":
        score += 0.04

    if source.get("doi"):
        score += 0.06

    if source.get("open_access"):
        score += 0.06

    publication_type = str(
        source.get("publication_type", "")
    ).lower()

    if publication_type in {
        "journal article",
        "journalarticle",
        "research article",
        "study",
        "clinical trial",
        "conference",
    }:
        score += 0.05

    return round(min(1.0, score), 3)


# ============================================================
# OPENALEX
# ============================================================

def openalex_abstract(inv: Optional[dict]) -> str:
    if not inv:
        return ""

    positions = []

    for word, indexes in inv.items():
        for index in indexes:
            positions.append((index, word))

    positions.sort()

    return " ".join(
        word
        for _, word in positions
    )


def search_openalex(query: str, limit: int = 10) -> List[dict]:
    data = get_json(
        "https://api.openalex.org/works",
        params={
            "search": query,
            "per-page": min(limit, 25),
        },
    )

    if not data:
        return []

    output = []

    for item in data.get("results", []):
        abstract = openalex_abstract(
            item.get("abstract_inverted_index")
        )

        primary = item.get("primary_location") or {}

        source = primary.get("source") or {}

        doi = canonical_doi(
            item.get("doi") or ""
        )

        url = (
            primary.get("landing_page_url")
            or item.get("id")
            or ""
        )

        output.append({
            "provider": "openalex",
            "provider_id": item.get("id"),
            "title": clean_text(item.get("title")),
            "abstract": clean_text(abstract),
            "text": "",
            "doi": doi,
            "url": url,
            "domain": domain_of(url),
            "year": item.get("publication_year"),
            "venue": source.get("display_name"),
            "open_access": bool(
                (item.get("open_access") or {}).get("is_oa")
            ),
            "publication_type": item.get("type"),
            "tier": "ABSTRACT" if abstract else "STRUCTURED_METADATA",
        })

    return output


# ============================================================
# SEMANTIC SCHOLAR
# ============================================================

def search_semantic_scholar(
    query: str,
    limit: int = 10,
) -> List[dict]:

    data = get_json(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={
            "query": query,
            "limit": min(limit, 20),
            "fields": (
                "title,abstract,url,year,authors,"
                "venue,publicationTypes,openAccessPdf,"
                "externalIds,citationCount"
            ),
        },
    )

    if not data:
        return []

    output = []

    for item in data.get("data", []):
        external = item.get("externalIds") or {}

        doi = canonical_doi(
            external.get("DOI") or ""
        )

        url = item.get("url") or ""

        pdf = item.get("openAccessPdf") or {}

        if pdf.get("url"):
            url = pdf["url"]

        abstract = clean_text(
            item.get("abstract")
        )

        types = item.get("publicationTypes") or []

        output.append({
            "provider": "semantic_scholar",
            "provider_id": item.get("paperId"),
            "title": clean_text(item.get("title")),
            "abstract": abstract,
            "text": "",
            "doi": doi,
            "url": url,
            "domain": domain_of(url),
            "year": item.get("year"),
            "venue": clean_text(item.get("venue")),
            "open_access": bool(pdf.get("url")),
            "publication_type": (
                types[0] if types else ""
            ),
            "citation_count": item.get("citationCount", 0),
            "tier": "ABSTRACT" if abstract else "STRUCTURED_METADATA",
        })

    return output


# ============================================================
# EUROPE PMC
# ============================================================

def search_europe_pmc(
    query: str,
    limit: int = 10,
) -> List[dict]:

    data = get_json(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        params={
            "query": query,
            "format": "json",
            "pageSize": min(limit, 25),
            "resultType": "core",
        },
    )

    if not data:
        return []

    output = []

    for item in data.get("resultList", {}).get(
        "result",
        [],
    ):

        abstract = clean_text(
            item.get("abstractText")
        )

        pmid = item.get("pmid")

        url = ""

        if pmid:
            url = (
                "https://europepmc.org/article/MED/"
                + str(pmid)
            )

        doi = canonical_doi(
            item.get("doi") or ""
        )

        output.append({
            "provider": "europe_pmc",
            "provider_id": pmid or item.get("id"),
            "title": clean_text(item.get("title")),
            "abstract": abstract,
            "text": "",
            "doi": doi,
            "url": url,
            "domain": "europepmc.org",
            "year": item.get("pubYear"),
            "venue": item.get("journalTitle"),
            "open_access": bool(
                item.get("isOpenAccess")
            ),
            "publication_type": item.get("pubType"),
            "tier": "ABSTRACT" if abstract else "STRUCTURED_METADATA",
        })

    return output


# ============================================================
# CROSSREF
# ============================================================

def search_crossref(
    query: str,
    limit: int = 10,
) -> List[dict]:

    data = get_json(
        "https://api.crossref.org/works",
        params={
            "query.bibliographic": query,
            "rows": min(limit, 20),
        },
    )

    if not data:
        return []

    output = []

    for item in data.get(
        "message",
        {},
    ).get("items", []):

        doi = canonical_doi(
            item.get("DOI") or ""
        )

        url = (
            item.get("URL")
            or (
                "https://doi.org/" + doi
                if doi
                else ""
            )
        )

        abstract = clean_text(
            item.get("abstract")
        )

        output.append({
            "provider": "crossref",
            "provider_id": doi,
            "title": clean_text(
                " ".join(item.get("title") or [])
            ),
            "abstract": abstract,
            "text": "",
            "doi": doi,
            "url": url,
            "domain": domain_of(url),
            "year": (
                (item.get("published-print") or {})
                .get("date-parts", [[None]])[0][0]
                or
                (item.get("published-online") or {})
                .get("date-parts", [[None]])[0][0]
            ),
            "venue": clean_text(
                " ".join(item.get("container-title") or [])
            ),
            "open_access": False,
            "publication_type": item.get("type"),
            "tier": "ABSTRACT"
            if abstract
            else "STRUCTURED_METADATA",
        })

    return output


# ============================================================
# ARXIV
# ============================================================

def search_arxiv(
    query: str,
    limit: int = 8,
) -> List[dict]:

    url = (
        "https://export.arxiv.org/api/query"
    )

    text = get_text(
        url + "?"
        + requests.compat.urlencode({
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": min(limit, 15),
        })
    )

    if not text:
        return []

    try:
        root = ET.fromstring(text)
    except Exception:
        return []

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
    }

    output = []

    for entry in root.findall(
        "atom:entry",
        ns,
    ):

        title = clean_text(
            entry.findtext(
                "atom:title",
                "",
                ns,
            )
        )

        abstract = clean_text(
            entry.findtext(
                "atom:summary",
                "",
                ns,
            )
        )

        url = clean_text(
            entry.findtext(
                "atom:id",
                "",
                ns,
            )
        )

        output.append({
            "provider": "arxiv",
            "provider_id": url,
            "title": title,
            "abstract": abstract,
            "text": "",
            "doi": "",
            "url": url,
            "domain": "arxiv.org",
            "year": None,
            "venue": "arXiv",
            "open_access": True,
            "publication_type": "preprint",
            "tier": "ABSTRACT" if abstract else "STRUCTURED_METADATA",
        })

    return output


# ============================================================
# DIRECT TEXT/PDF RECOVERY
# ============================================================

def extract_html_text(raw: str) -> str:
    if not raw:
        return ""

    text = raw

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
        r"<nav\b[^>]*>.*?</nav>",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = re.sub(
        r"<footer\b[^>]*>.*?</footer>",
        " ",
        text,
        flags=re.I | re.S,
    )

    matches = re.findall(
        r"<(?:article|main)\b[^>]*>(.*?)</(?:article|main)>",
        text,
        flags=re.I | re.S,
    )

    if matches:
        text = " ".join(matches)

    text = clean_text(text)

    return text


def extract_pdf_text(data: bytes) -> str:
    if not data or not PdfReader:
        return ""

    try:
        import io

        reader = PdfReader(
            io.BytesIO(data)
        )

        chunks = []

        for page in reader.pages[:40]:
            try:
                value = page.extract_text() or ""

                if value:
                    chunks.append(value)
            except Exception:
                continue

        return clean_text(
            "\n".join(chunks)
        )

    except Exception:
        return ""


def recover_full_text(source: dict) -> dict:
    url = source.get("url") or ""

    if not url:
        return source

    if not url.startswith("http"):
        return source

    raw = get_bytes(url)

    if not raw:
        return source

    content_type = ""

    try:
        r = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            stream=True,
        )

        content_type = (
            r.headers.get(
                "content-type",
                "",
            )
            .lower()
        )

        r.close()

    except Exception:
        pass

    if (
        "pdf" in content_type
        or raw[:4] == b"%PDF"
    ):
        text = extract_pdf_text(raw)

        if len(text) >= 500:
            source["text"] = text
            source["tier"] = "FULL_TEXT"

    else:
        try:
            decoded = raw.decode(
                "utf-8",
                errors="ignore",
            )

            text = extract_html_text(
                decoded
            )

            if len(text) >= 800:
                source["text"] = text
                source["tier"] = "FULL_TEXT"

        except Exception:
            pass

    return source


# ============================================================
# SEMANTIC SCHOLAR SNIPPET RECOVERY
# ============================================================

def semantic_snippets(
    query: str,
    limit: int = 6,
) -> List[dict]:

    data = get_json(
        "https://api.semanticscholar.org/graph/v1/snippet/search",
        params={
            "query": query,
            "limit": min(limit, 10),
        },
    )

    if not data:
        return []

    output = []

    for item in data.get("data", []):
        paper = item.get("paper") or {}

        text = clean_text(
            item.get("text")
        )

        if len(text) < 100:
            continue

        output.append({
            "provider": "semantic_scholar_snippet",
            "provider_id": paper.get("corpusId"),
            "title": clean_text(
                paper.get("title")
            ),
            "abstract": "",
            "text": text,
            "doi": "",
            "url": "",
            "domain": "semanticscholar.org",
            "year": None,
            "venue": "",
            "open_access": False,
            "publication_type": "",
            "tier": "SNIPPET",
        })

    return output


# ============================================================
# DISCOVERY
# ============================================================

def build_queries(objective: str) -> List[str]:
    base = clean_text(objective)

    return [
        base + " empirical study",
        base + " benchmark evaluation",
        base + " task success failure rate",
        base + " real world deployment",
        base + " reliability limitations failures",
        base + " human intervention monitoring",
        base + " independent study replication",
        base + " systematic evaluation",
        base + " failure modes",
        base + " performance evaluation",
    ]


def discover_sources(
    objective: str,
) -> tuple[List[dict], List[str]]:

    queries = build_queries(objective)

    sources = []

    provider_used = set()

    # --------------------------------------------------------
    # Parallel-ish provider fanout.
    # Each provider is independent.
    # --------------------------------------------------------

    for query in queries[:6]:

        found = search_openalex(
            query,
            limit=8,
        )

        if found:
            provider_used.add("openalex")

        sources.extend(found)

        found = search_semantic_scholar(
            query,
            limit=8,
        )

        if found:
            provider_used.add("semantic_scholar")

        sources.extend(found)

    # Targeted providers
    for query in queries[:3]:

        found = search_crossref(
            query,
            limit=8,
        )

        if found:
            provider_used.add("crossref")

        sources.extend(found)

        found = search_arxiv(
            query,
            limit=6,
        )

        if found:
            provider_used.add("arxiv")

        sources.extend(found)

    # Europe PMC is especially useful for biomedical
    # and empirical literature.
    for query in queries[:2]:

        found = search_europe_pmc(
            query,
            limit=8,
        )

        if found:
            provider_used.add("europe_pmc")

        sources.extend(found)

    # --------------------------------------------------------
    # Semantic snippets are an additional evidence route.
    # --------------------------------------------------------

    snippet_query = clean_text(
        objective
    )

    snippets = semantic_snippets(
        snippet_query,
        limit=8,
    )

    if snippets:
        provider_used.add(
            "semantic_scholar_snippet"
        )

    sources.extend(snippets)

    return sources, sorted(provider_used)


# ============================================================
# SOURCE NORMALIZATION
# ============================================================

def deduplicate_sources(
    sources: List[dict],
) -> List[dict]:

    by_key = {}

    for source in sources:

        if is_artifact(source):
            continue

        sid = source_id(source)

        existing = by_key.get(sid)

        if existing is None:
            by_key[sid] = source
            continue

        # Prefer better evidence tier.
        tier_rank = {
            "FULL_TEXT": 4,
            "ABSTRACT": 3,
            "SNIPPET": 2,
            "STRUCTURED_METADATA": 1,
        }

        old_rank = tier_rank.get(
            existing.get("tier"),
            0,
        )

        new_rank = tier_rank.get(
            source.get("tier"),
            0,
        )

        if new_rank > old_rank:
            by_key[sid] = source

        elif (
            len(source.get("abstract", ""))
            > len(existing.get("abstract", ""))
        ):
            by_key[sid] = source

    return list(by_key.values())


def enrich_sources(
    objective: str,
    sources: List[dict],
) -> List[dict]:

    result = []

    for source in sources:

        content = " ".join([
            source.get("title", ""),
            source.get("abstract", ""),
            source.get("text", ""),
        ])

        rel = relevance(
            objective,
            content,
        )

        source["relevance"] = rel

        # Hard reject clearly irrelevant material.
        if rel < 0.08:
            continue

        # Recover full text only for promising sources.
        if (
            source.get("tier")
            in {
                "ABSTRACT",
                "STRUCTURED_METADATA",
            }
            and rel >= 0.15
            and source.get("url")
        ):
            source = recover_full_text(
                source
            )

        source["quality"] = quality_score(
            source
        )

        # Relevance is deliberately part of final quality.
        source["evidence_score"] = round(
            (
                source["quality"] * 0.55
                + source["relevance"] * 0.45
            ),
            3,
        )

        # Do not call title-only metadata evidence.
        usable = bool(
            len(
                source.get("text", "")
            ) >= 500
            or len(
                source.get("abstract", "")
            ) >= 120
        )

        if not usable:
            continue

        result.append(source)

    result.sort(
        key=lambda x: x.get(
            "evidence_score",
            0,
        ),
        reverse=True,
    )

    return result[:40]


# ============================================================
# CLAIM EXTRACTION
# ============================================================

def extract_claims(
    objective: str,
    sources: List[dict],
) -> List[dict]:

    claims = []

    seen = []

    for source in sources:

        body = clean_text(
            source.get("text")
            or source.get("abstract")
            or ""
        )

        if not body:
            continue

        sentences = sentence_split(
            body
        )

        # Limit per source to avoid graph pollution.
        selected = []

        for sentence in sentences:

            if not looks_like_claim(
                sentence
            ):
                continue

            score = relevance(
                objective,
                sentence,
            )

            if score < 0.08:
                continue

            sentence = normalize_claim(
                sentence
            )

            if any(
                similarity(
                    sentence,
                    old,
                ) >= 0.82
                for old in selected
            ):
                continue

            selected.append(sentence)

            if len(selected) >= 6:
                break

        for sentence in selected:

            claim = {
                "claim_id": (
                    "claim-"
                    + hashlib.sha256(
                        (
                            source_id(source)
                            + sentence
                        ).encode()
                    ).hexdigest()[:16]
                ),
                "text": sentence,
                "source_id": source_id(source),
                "provider": source.get(
                    "provider"
                ),
                "domain": source.get(
                    "domain"
                ),
                "quality": source.get(
                    "quality",
                    0,
                ),
                "relevance": relevance(
                    objective,
                    sentence,
                ),
                "status": "UNCERTAIN",
                "supporting_sources": [],
                "contradicting_sources": [],
                "limiting_sources": [],
            }

            # Global duplicate detection.
            duplicate = False

            for old in claims:

                if similarity(
                    sentence,
                    old["text"],
                ) >= 0.82:
                    duplicate = True
                    break

            if not duplicate:
                claims.append(
                    claim
                )

    return claims[:100]


# ============================================================
# CLAIM STANCE
# ============================================================

POSITIVE_MARKERS = {
    "improve",
    "improved",
    "improves",
    "increase",
    "increased",
    "higher",
    "better",
    "successful",
    "success",
    "effective",
    "reliable",
    "accurate",
    "outperformed",
    "benefit",
}

NEGATIVE_MARKERS = {
    "fail",
    "failed",
    "failure",
    "decrease",
    "decreased",
    "lower",
    "worse",
    "error",
    "errors",
    "unreliable",
    "limitation",
    "limitations",
    "underperformed",
    "harm",
    "problem",
}

LIMITATION_MARKERS = {
    "however",
    "limitation",
    "limitations",
    "caution",
    "caveat",
    "may not",
    "cannot",
    "unable",
    "restricted",
    "only when",
    "depends on",
}


def stance(text: str) -> str:
    words = set(
        re.findall(
            r"[a-z]+",
            text.lower(),
        )
    )

    positive = len(
        words & POSITIVE_MARKERS
    )

    negative = len(
        words & NEGATIVE_MARKERS
    )

    limitation = len(
        words & LIMITATION_MARKERS
    )

    if limitation:
        return "limitation"

    if negative > positive:
        return "negative"

    if positive > negative:
        return "positive"

    return "neutral"


# ============================================================
# EVIDENCE GRAPH
# ============================================================

def build_evidence_graph(
    claims: List[dict],
    sources: List[dict],
) -> dict:

    source_map = {
        source_id(s): s
        for s in sources
    }

    edges = []

    # --------------------------------------------------------
    # Compare claims across independent works.
    # --------------------------------------------------------

    for i, a in enumerate(claims):

        for b in claims[i + 1:]:

            if (
                a["source_id"]
                == b["source_id"]
            ):
                continue

            sim = similarity(
                a["text"],
                b["text"],
            )

            if sim < 0.20:
                continue

            sa = stance(a["text"])
            sb = stance(b["text"])

            same_domain = (
                a.get("domain")
                == b.get("domain")
            )

            if (
                sa == sb
                and sa in {
                    "positive",
                    "negative",
                }
            ):
                relation = "support"

            elif (
                {
                    sa,
                    sb,
                }
                == {
                    "positive",
                    "negative",
                }
            ):
                relation = "contradiction"

            elif (
                sa == "limitation"
                or sb == "limitation"
            ):
                relation = "limitation"

            else:
                continue

            edges.append({
                "from": a["claim_id"],
                "to": b["claim_id"],
                "type": relation,
                "similarity": round(sim, 3),
                "independent_domain": not same_domain,
            })

    # --------------------------------------------------------
    # Populate source lists.
    # --------------------------------------------------------

    claim_map = {
        c["claim_id"]: c
        for c in claims
    }

    for edge in edges:

        target = claim_map.get(
            edge["to"]
        )

        source = claim_map.get(
            edge["from"]
        )

        if not target or not source:
            continue

        source_id_value = source[
            "source_id"
        ]

        if edge["type"] == "support":
            target[
                "supporting_sources"
            ].append(
                source_id_value
            )

        elif edge["type"] == "contradiction":
            target[
                "contradicting_sources"
            ].append(
                source_id_value
            )

        elif edge["type"] == "limitation":
            target[
                "limiting_sources"
            ].append(
                source_id_value
            )

    nodes = []

    for source in sources:

        nodes.append({
            "id": source_id(source),
            "type": "source",
            "domain": source.get(
                "domain"
            ),
            "provider": source.get(
                "provider"
            ),
            "tier": source.get(
                "tier"
            ),
            "quality": source.get(
                "quality"
            ),
            "relevance": source.get(
                "relevance"
            ),
        })

    for claim in claims:

        nodes.append({
            "id": claim["claim_id"],
            "type": "claim",
            "text": claim["text"],
            "status": claim["status"],
        })

    return {
        "nodes": nodes,
        "edges": edges,
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_claims(
    claims: List[dict],
    sources: List[dict],
) -> dict:

    source_map = {
        source_id(s): s
        for s in sources
    }

    verified = 0
    uncertain = 0
    contradicted = 0

    for claim in claims:

        supporting = set(
            claim.get(
                "supporting_sources",
                [],
            )
        )

        contradicting = set(
            claim.get(
                "contradicting_sources",
                [],
            )
        )

        limiting = set(
            claim.get(
                "limiting_sources",
                [],
            )
        )

        domains = {
            source_map[sid].get(
                "domain"
            )
            for sid in supporting
            if sid in source_map
        }

        # Strong corroboration requires more than
        # one independent source.
        if (
            len(supporting) >= 2
            and len(domains) >= 2
            and len(contradicting) == 0
        ):
            claim["status"] = "VERIFIED"
            verified += 1

        elif (
            len(contradicting) >= 1
            and len(contradicting)
            >= len(supporting)
        ):
            claim["status"] = "CONTRADICTED"
            contradicted += 1

        elif (
            len(supporting) >= 1
            or len(limiting) >= 1
        ):
            claim["status"] = "UNCERTAIN"
            uncertain += 1

        else:
            claim["status"] = "UNSUPPORTED"
            uncertain += 1

    total = len(claims)

    confidence = (
        (
            verified * 1.0
            + uncertain * 0.45
            + contradicted * 0.15
        )
        / total
        if total
        else 0
    )

    return {
        "enabled": True,
        "verified": verified,
        "uncertain": uncertain,
        "contradicted": contradicted,
        "unsupported": max(
            0,
            total
            - verified
            - uncertain
            - contradicted,
        ),
        "confidence": round(
            confidence,
            3,
        ),
    }


# ============================================================
# COUNTER-EVIDENCE
# ============================================================

def counter_queries(
    claim: str,
) -> List[str]:

    base = clean_text(
        claim
    )

    return [
        base + " limitations",
        base + " failure",
        base + " contradictory evidence",
        base + " replication",
        base + " independent study",
    ]


def collect_counter_evidence(
    claims: List[dict],
    existing_sources: List[dict],
) -> tuple[List[dict], int]:

    important = [
        c
        for c in claims
        if c.get("status")
        in {
            "VERIFIED",
            "UNCERTAIN",
        }
    ][:8]

    found = []
    tested = 0

    existing_ids = {
        source_id(s)
        for s in existing_sources
    }

    for claim in important:

        tested += 1

        for query in counter_queries(
            claim["text"]
        )[:3]:

            # Two diverse providers per counter query.
            for provider in (
                "semantic_scholar",
                "openalex",
            ):

                if provider == "semantic_scholar":
                    results = search_semantic_scholar(
                        query,
                        limit=4,
                    )
                else:
                    results = search_openalex(
                        query,
                        limit=4,
                    )

                for source in results:

                    if is_artifact(source):
                        continue

                    content = " ".join([
                        source.get(
                            "title",
                            "",
                        ),
                        source.get(
                            "abstract",
                            "",
                        ),
                    ])

                    rel = relevance(
                        claim["text"],
                        content,
                    )

                    if rel < 0.12:
                        continue

                    source["relevance"] = rel

                    if source_id(
                        source
                    ) in existing_ids:
                        continue

                    source = recover_full_text(
                        source
                    )

                    source["quality"] = quality_score(
                        source
                    )

                    source["evidence_score"] = round(
                        (
                            source["quality"] * 0.55
                            + rel * 0.45
                        ),
                        3,
                    )

                    found.append(
                        source
                    )

                    existing_ids.add(
                        source_id(source)
                    )

    return (
        found[:30],
        tested,
    )


# ============================================================
# SYNTHESIS
# ============================================================

def synthesize(
    claims: List[dict],
    sources: List[dict],
    verification: dict,
    counter_sources: List[dict],
) -> dict:

    verified = [
        c for c in claims
        if c.get("status") == "VERIFIED"
    ]

    uncertain = [
        c for c in claims
        if c.get("status") == "UNCERTAIN"
    ]

    contradicted = [
        c for c in claims
        if c.get("status") == "CONTRADICTED"
    ]

    domains = sorted({
        s.get("domain")
        for s in sources
        if s.get("domain")
    })

    providers = sorted({
        s.get("provider")
        for s in sources
        if s.get("provider")
    })

    if verified and not contradicted:
        conclusion = (
            "The recovered evidence contains "
            "claims with independent corroboration. "
            "The verified claims are supported by "
            "multiple sources, while remaining "
            "claims should be treated according "
            "to their individual status."
        )

    elif contradicted:
        conclusion = (
            "The evidence contains conflicting findings. "
            "The system identified claims with both "
            "supporting and contradictory evidence, so "
            "a single unconditional conclusion is not "
            "justified."
        )

    elif claims:
        conclusion = (
            "The evidence contains substantive claims, "
            "but independent corroboration remains "
            "insufficient for a strong conclusion."
        )

    else:
        conclusion = (
            "The current evidence set still does not "
            "contain enough recoverable substantive claims."
        )

    next_actions = []

    if not claims:
        next_actions.append(
            "Recover more abstract/full-text research."
        )

    if len(domains) < 3:
        next_actions.append(
            "Increase independent domain diversity."
        )

    if len(providers) < 2:
        next_actions.append(
            "Increase scholarly provider diversity."
        )

    if not verified and claims:
        next_actions.append(
            "Seek independent corroboration for major claims."
        )

    if contradicted:
        next_actions.append(
            "Investigate contradictory findings individually."
        )

    if counter_sources:
        next_actions.append(
            "Review counter-evidence before finalizing conclusions."
        )

    if not next_actions:
        next_actions.append(
            "Run another independent evidence cycle."
        )

    return {
        "conclusion": conclusion,
        "verified_claims": len(verified),
        "uncertain_claims": len(uncertain),
        "contradicted_claims": len(
            contradicted
        ),
        "independent_domains": len(
            domains
        ),
        "providers": providers,
        "counter_evidence_sources": len(
            counter_sources
        ),
        "next_actions": next_actions,
    }


# ============================================================
# MISSION ENGINE
# ============================================================

def research_mission(
    mission_id: str,
    objective: str,
):

    started = time.time()

    try:

        # ----------------------------------------------------
        # 1. Planning
        # ----------------------------------------------------

        queries = build_queries(
            objective
        )

        trace = [
            {
                "stage": "planning",
                "status": "completed",
                "research_questions": queries,
                "count": len(queries),
            }
        ]

        # ----------------------------------------------------
        # 2. Discovery
        # ----------------------------------------------------

        discovered, providers = (
            discover_sources(
                objective
            )
        )

        trace.append({
            "stage": "discovery",
            "status": "completed",
            "sources_discovered": len(
                discovered
            ),
            "providers_attempted": providers,
        })

        # ----------------------------------------------------
        # 3. Deduplication
        # ----------------------------------------------------

        candidates = deduplicate_sources(
            discovered
        )

        trace.append({
            "stage": "source_filter",
            "status": "completed",
            "candidate_sources": len(
                candidates
            ),
        })

        # ----------------------------------------------------
        # 4. Evidence recovery
        # ----------------------------------------------------

        sources = enrich_sources(
            objective,
            candidates,
        )

        trace.append({
            "stage": "evidence_recovery",
            "status": "completed",
            "accepted_sources": len(
                sources
            ),
        })

        # ----------------------------------------------------
        # 5. Claims
        # ----------------------------------------------------

        claims = extract_claims(
            objective,
            sources,
        )

        trace.append({
            "stage": "claim_extraction",
            "status": "completed",
            "claims": len(
                claims
            ),
        })

        # ----------------------------------------------------
        # 6. Graph
        # ----------------------------------------------------

        graph = build_evidence_graph(
            claims,
            sources,
        )

        trace.append({
            "stage": "evidence_linking",
            "status": "completed",
            "relationships": len(
                graph["edges"]
            ),
        })

        # ----------------------------------------------------
        # 7. Verification
        # ----------------------------------------------------

        verification = verify_claims(
            claims,
            sources,
        )

        trace.append({
            "stage": "verification",
            "status": "completed",
            "verified": verification[
                "verified"
            ],
            "uncertain": verification[
                "uncertain"
            ],
            "contradicted": verification[
                "contradicted"
            ],
        })

        # ----------------------------------------------------
        # 8. Counter evidence
        # ----------------------------------------------------

        counter_sources, tested = (
            collect_counter_evidence(
                claims,
                sources,
            )
        )

        trace.append({
            "stage": "counter_evidence",
            "status": "completed",
            "sources": len(
                counter_sources
            ),
            "claims_tested": tested,
        })

        # ----------------------------------------------------
        # 9. Final synthesis
        # ----------------------------------------------------

        synthesis = synthesize(
            claims,
            sources,
            verification,
            counter_sources,
        )

        trace.append({
            "stage": "synthesis",
            "status": "completed",
        })

        # ----------------------------------------------------
        # Evidence telemetry
        # ----------------------------------------------------

        tiers = {}

        for source in sources:
            tier = source.get(
                "tier",
                "UNKNOWN",
            )

            tiers[tier] = (
                tiers.get(tier, 0)
                + 1
            )

        domains = sorted({
            s.get("domain")
            for s in sources
            if s.get("domain")
        })

        independent_works = len({
            source_id(s)
            for s in sources
        })

        average_quality = (
            sum(
                s.get(
                    "quality",
                    0,
                )
                for s in sources
            )
            / len(sources)
            if sources
            else 0
        )

        support_edges = len([
            e for e in graph["edges"]
            if e["type"] == "support"
        ])

        contradiction_edges = len([
            e for e in graph["edges"]
            if e["type"] == "contradiction"
        ])

        limitation_edges = len([
            e for e in graph["edges"]
            if e["type"] == "limitation"
        ])

        evidence_strength = round(
            min(
                1.0,
                (
                    len(sources) / 20 * 0.20
                    + len(claims) / 20 * 0.25
                    + verification["verified"]
                    / max(1, len(claims))
                    * 0.30
                    + len(domains) / 5 * 0.15
                    + len(providers) / 5 * 0.10
                ),
            ),
            3,
        )

        result = {
            "task_id": mission_id,
            "mission_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "build": BUILD,
            "objective": objective,

            "agent_trace": trace,

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
                "support_edges": support_edges,
                "contradiction_edges": (
                    contradiction_edges
                ),
                "limitation_edges": limitation_edges,
                "independent_domains": len(
                    domains
                ),
                "domains": domains,
                "independent_works": independent_works,
                "average_source_quality": round(
                    average_quality,
                    3,
                ),
                "evidence_tiers": tiers,
            },

            "claims": claims,

            "verification": {
                **verification,
                "research_strength": evidence_strength,
            },

            "counter_evidence": {
                "sources": len(
                    counter_sources
                ),
                "claims_tested": tested,
                "items": [
                    {
                        "id": source_id(s),
                        "title": s.get(
                            "title"
                        ),
                        "provider": s.get(
                            "provider"
                        ),
                        "domain": s.get(
                            "domain"
                        ),
                        "tier": s.get(
                            "tier"
                        ),
                        "relevance": s.get(
                            "relevance"
                        ),
                    }
                    for s in counter_sources
                ],
            },

            "evidence_graph": graph,

            "source_index": [
                {
                    "id": source_id(s),
                    "title": s.get(
                        "title"
                    ),
                    "provider": s.get(
                        "provider"
                    ),
                    "domain": s.get(
                        "domain"
                    ),
                    "tier": s.get(
                        "tier"
                    ),
                    "quality": s.get(
                        "quality"
                    ),
                    "relevance": s.get(
                        "relevance"
                    ),
                    "evidence_score": s.get(
                        "evidence_score"
                    ),
                    "doi": s.get(
                        "doi"
                    ),
                    "url": s.get(
                        "url"
                    ),
                }
                for s in sources
            ],

            "synthesis": synthesis,

            "next_cycle": {
                "recommended": synthesis[
                    "next_actions"
                ]
            },

            "timing": {
                "started_at": started,
                "completed_at": time.time(),
                "duration_seconds": round(
                    time.time() - started,
                    2,
                ),
            },
        }

        save_mission(
            mission_id,
            objective,
            "completed",
            result,
        )

        return result

    except Exception as exc:

        result = {
            "task_id": mission_id,
            "mission_id": mission_id,
            "status": "failed",
            "version": VERSION,
            "build": BUILD,
            "error": str(exc),
            "timing": {
                "started_at": started,
                "completed_at": time.time(),
            },
        }

        save_mission(
            mission_id,
            objective,
            "failed",
            result,
        )

        return result


# ============================================================
# API MODELS
# ============================================================

class MissionRequest(BaseModel):
    command: Optional[str] = Field(
        default=None,
        max_length=10000,
    )

    objective: Optional[str] = Field(
        default=None,
        max_length=10000,
    )

    research: bool = True
    verify: bool = True
    remember: bool = True


# ============================================================
# API
# ============================================================

@app.get(
    "/health",
)
def health():

    return {
        "status": "ok",
        "online": True,
        "version": VERSION,
        "build": BUILD,
    }


@app.get(
    "/status",
)
def status():

    return {
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "engine": "DEEP-EVIDENCE-RECOVERY",
        "workers": WORKERS,
        "database": str(DB_PATH),
        "providers": [
            "openalex",
            "semantic_scholar",
            "europe_pmc",
            "crossref",
            "arxiv",
        ],
        "capabilities": [
            "research",
            "evidence_recovery",
            "claim_extraction",
            "evidence_graph",
            "verification",
            "counter_evidence",
            "synthesis",
        ],
    }


@app.get(
    "/",
    response_class=HTMLResponse,
)
def dashboard():

    return """
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity</title>

<style>
*{
    box-sizing:border-box;
}

body{
    margin:0;
    background:#070b12;
    color:#eaf2ff;
    font-family:
      Inter,
      system-ui,
      -apple-system,
      BlinkMacSystemFont,
      sans-serif;
}

.wrap{
    max-width:1000px;
    margin:auto;
    padding:22px;
}

.header{
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:15px;
    margin-bottom:22px;
}

.logo{
    font-size:28px;
    font-weight:800;
}

.badge{
    border:1px solid #2b9f70;
    padding:7px 12px;
    border-radius:20px;
    font-size:12px;
}

.card{
    background:#0d1420;
    border:1px solid #1d2b3e;
    border-radius:18px;
    padding:18px;
    margin-bottom:16px;
}

textarea{
    width:100%;
    min-height:150px;
    resize:vertical;
    background:#070b12;
    color:#fff;
    border:1px solid #26384e;
    border-radius:14px;
    padding:15px;
    font-size:16px;
    outline:none;
}

button{
    width:100%;
    margin-top:12px;
    border:0;
    border-radius:14px;
    padding:15px;
    font-size:16px;
    font-weight:700;
    cursor:pointer;
    background:#ffffff;
    color:#05070a;
}

.grid{
    display:grid;
    grid-template-columns:
      repeat(4,minmax(0,1fr));
    gap:10px;
}

.stat{
    background:#080e17;
    border:1px solid #1c2a3c;
    border-radius:14px;
    padding:14px;
}

.stat b{
    display:block;
    font-size:21px;
    margin-top:4px;
}

pre{
    white-space:pre-wrap;
    word-break:break-word;
    background:#05080d;
    border:1px solid #1b2939;
    border-radius:14px;
    padding:15px;
    overflow:auto;
    max-height:600px;
}

.small{
    color:#8ea0b7;
    font-size:13px;
}

@media(max-width:700px){
    .grid{
        grid-template-columns:
          repeat(2,minmax(0,1fr));
    }
}
</style>
</head>

<body>

<div class="wrap">

<div class="header">
    <div class="logo">∞ AI Infinity</div>
    <div class="badge">● ONLINE</div>
</div>

<div class="card">

<div class="small">
TARGET-2050.26 · DEEP EVIDENCE RECOVERY
</div>

<h2>Research Mission</h2>

<textarea id="objective"
placeholder="Describe what AI Infinity should investigate..."></textarea>

<button onclick="runMission()">
START AUTONOMOUS RESEARCH
</button>

</div>

<div class="card">

<div class="grid">

<div class="stat">
Sources
<b id="sources">—</b>
</div>

<div class="stat">
Claims
<b id="claims">—</b>
</div>

<div class="stat">
Verified
<b id="verified">—</b>
</div>

<div class="stat">
Edges
<b id="edges">—</b>
</div>

</div>

</div>

<div class="card">

<div class="small">
MISSION OUTPUT
</div>

<pre id="output">
Ready.
</pre>

</div>

</div>

<script>

let activeMission = null;

async function runMission(){

    const objective =
        document.getElementById(
            "objective"
        ).value.trim();

    if(!objective){
        alert("Enter a research objective.");
        return;
    }

    document.getElementById(
        "output"
    ).textContent =
        "Starting autonomous evidence research...";

    const response = await fetch(
        "/mission",
        {
            method:"POST",
            headers:{
                "Content-Type":
                    "application/json"
            },
            body:JSON.stringify({
                command:objective,
                research:true,
                verify:true,
                remember:true
            })
        }
    );

    const text =
        await response.text();

    let data;

    try{
        data = JSON.parse(text);
    }catch(e){
        document.getElementById(
            "output"
        ).textContent = text;
        return;
    }

    activeMission =
        data.mission_id;

    poll();
}

async function poll(){

    if(!activeMission)
        return;

    const response =
        await fetch(
            "/mission/" +
            activeMission
        );

    const text =
        await response.text();

    let data;

    try{
        data = JSON.parse(text);
    }catch(e){
        document.getElementById(
            "output"
        ).textContent = text;

        setTimeout(
            poll,
            2500
        );

        return;
    }

    document.getElementById(
        "output"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );

    const result =
        data.result || {};

    const evidence =
        result.evidence || {};

    const verification =
        result.verification || {};

    document.getElementById(
        "sources"
    ).textContent =
        evidence.count ?? "—";

    document.getElementById(
        "claims"
    ).textContent =
        result.claims
        ? result.claims.length
        : "—";

    document.getElementById(
        "verified"
    ).textContent =
        verification.verified ?? "—";

    document.getElementById(
        "edges"
    ).textContent =
        evidence.graph_edges ?? "—";

    if(
        data.status === "completed"
        ||
        data.status === "failed"
    ){
        return;
    }

    setTimeout(
        poll,
        2500
    );
}

</script>

</body>
</html>
"""


@app.post(
    "/mission",
    status_code=202,
)
def create_mission(
    request: MissionRequest,
):

    objective = (
        request.objective
        or request.command
        or ""
    ).strip()

    if not objective:
        raise HTTPException(
            status_code=400,
            detail="command or objective is required",
        )

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:12]
    )

    save_mission(
        mission_id,
        objective,
        "running",
    )

    executor.submit(
        research_mission,
        mission_id,
        objective,
    )

    return {
        "task_id": mission_id,
        "mission_id": mission_id,
        "status": "running",
        "version": VERSION,
        "build": BUILD,
        "message": (
            "Mission accepted. "
            "Deep evidence research is running."
        ),
    }


@app.get(
    "/mission/{mission_id}",
)
def get_mission(
    mission_id: str,
):

    mission = load_mission(
        mission_id
    )

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return mission


@app.get(
    "/missions",
)
def list_missions():

    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT mission_id,
                   objective,
                   status,
                   created_at,
                   updated_at
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


# ============================================================
# GLOBAL JSON ERROR HANDLERS
# ============================================================

@app.exception_handler(
    Exception
)
async def global_exception_handler(
    request,
    exc,
):
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "version": VERSION,
            "error": str(exc),
        },
    )


# ============================================================
# LOCAL RUN
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
