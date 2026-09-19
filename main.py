"""
AI Infinity
TARGET-2050.27
BUILD: AUTONOMOUS-EVIDENCE-REASONING-CORE

Pipeline:

Research Objective
    -> Query Planner
    -> Multi-Provider Discovery
    -> Source Identity / Deduplication
    -> Source Filtering
    -> Abstract / Full-Text Recovery
    -> Real Substantive Text
    -> Claim Extraction
    -> Claim Normalization
    -> Evidence Graph
    -> Support / Contradiction / Limitation
    -> Independent Corroboration
    -> Verification
    -> Counter-Evidence
    -> Synthesis

No LLM/API key is required.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import sqlite3
import threading
import time
import uuid
import xml.etree.ElementTree as ET

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import (
    quote,
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
# CONFIG
# ============================================================

VERSION = "TARGET-2050.27"
BUILD = "AUTONOMOUS-EVIDENCE-REASONING-CORE"

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

WORKERS = max(
    1,
    int(os.getenv("AI_INFINITY_WORKERS", "3")),
)

REQUEST_TIMEOUT = max(
    5,
    int(os.getenv("AI_INFINITY_TIMEOUT", "15")),
)

MAX_SOURCES = 50
MAX_CLAIMS = 100
MAX_COUNTER_SOURCES = 40

USER_AGENT = (
    "AI-Infinity/2050.27 "
    "(autonomous-evidence-research; "
    "https://ai-infinity-ca5e.onrender.com)"
)

executor = ThreadPoolExecutor(
    max_workers=WORKERS
)

db_lock = threading.Lock()

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
})


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "Autonomous evidence discovery, reasoning, "
        "verification and counter-evidence engine."
    ),
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

        conn.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            mission_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            created_at REAL,
            updated_at REAL
        )
        """)

        conn.execute("""
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
            (mission_id, objective, status, result,
             created_at, updated_at)
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
                json.dumps(
                    result,
                    ensure_ascii=False,
                )
                if result is not None
                else None,
                now,
                now,
            ),
        )

        conn.commit()
        conn.close()


def load_mission(
    mission_id: str,
):
    with db_lock:
        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM missions
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        conn.close()

    if not row:
        return None

    result = None

    if row["result"]:
        try:
            result = json.loads(
                row["result"]
            )
        except Exception:
            result = {
                "raw": row["result"]
            }

    return {
        "mission_id": row["mission_id"],
        "objective": row["objective"],
        "status": row["status"],
        "result": result,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ============================================================
# NETWORK SAFETY
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata.google",
}


def host_is_private(host: str) -> bool:
    if not host:
        return True

    host = host.lower().strip()

    if host in BLOCKED_HOSTS:
        return True

    if host.endswith(".local"):
        return True

    try:
        ip = ipaddress.ip_address(host)

        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )

    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(
            host,
            None,
        )

        for info in infos:
            addr = info[4][0]

            try:
                ip = ipaddress.ip_address(addr)

                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                ):
                    return True

            except ValueError:
                continue

    except Exception:
        return True

    return False


def safe_url(
    url: str,
) -> bool:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {
            "http",
            "https",
        }:
            return False

        if not parsed.hostname:
            return False

        return not host_is_private(
            parsed.hostname
        )

    except Exception:
        return False


# ============================================================
# HTTP
# ============================================================

def get_response(
    url: str,
    params: Optional[dict] = None,
    timeout: int = REQUEST_TIMEOUT,
):
    if not safe_url(url):
        return None

    try:
        response = session.get(
            url,
            params=params,
            timeout=timeout,
            allow_redirects=True,
        )

        if not safe_url(
            response.url
        ):
            return None

        if response.status_code >= 400:
            return None

        return response

    except Exception:
        return None


def get_json(
    url: str,
    params: Optional[dict] = None,
):
    response = get_response(
        url,
        params=params,
    )

    if not response:
        return None

    try:
        return response.json()
    except Exception:
        return None


def get_text(
    url: str,
):
    response = get_response(url)

    if not response:
        return None

    return response.text


def get_bytes(
    url: str,
):
    response = get_response(url)

    if not response:
        return None

    return response.content


# ============================================================
# TEXT
# ============================================================

STOPWORDS = {
    "the", "and", "for", "with", "that", "this",
    "from", "were", "was", "are", "have", "has",
    "into", "their", "they", "than", "then",
    "which", "using", "used", "based", "such",
    "these", "those", "about", "between", "through",
    "also", "more", "most", "some", "study",
    "studies", "research", "results", "result",
    "paper", "papers", "findings", "finding",
    "our", "we", "in", "of", "to", "a", "an",
    "on", "by", "as", "is", "it", "be", "or",
    "at", "can", "may", "could", "would",
}

BAD_PHRASES = {
    "view pdf",
    "view html",
    "cite as",
    "submission history",
    "subject classification",
    "subjects:",
    "download pdf",
    "download source",
    "submit to",
    "sign in",
    "log in",
    "cookie policy",
    "privacy policy",
    "terms of use",
    "accept cookies",
    "javascript required",
    "captcha",
    "access denied",
    "all rights reserved",
    "copyright",
    "table of contents",
    "references",
    "bibliography",
    "supplementary material",
    "supporting information",
}

CLAIM_MARKERS = {
    "found",
    "find",
    "showed",
    "shows",
    "demonstrated",
    "observed",
    "reported",
    "identified",
    "measured",
    "evaluated",
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
    "replication",
    "replicated",
    "generalization",
    "generalisation",
    "robust",
    "robustness",
    "failure",
    "failures",
    "null",
    "effect",
    "effects",
}


def clean_text(
    value: Any,
) -> str:
    if value is None:
        return ""

    text = html.unescape(
        str(value)
    )

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
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def normalize_word(
    word: str,
) -> str:
    word = word.lower()

    replacements = {
        "agents": "agent",
        "agentic": "agent",
        "failures": "failure",
        "failed": "failure",
        "failing": "failure",
        "reliable": "reliability",
        "reliably": "reliability",
        "evaluations": "evaluation",
        "evaluated": "evaluation",
        "benchmarks": "benchmark",
        "interventions": "intervention",
        "limitations": "limitation",
        "replications": "replication",
        "successes": "success",
    }

    if word in replacements:
        return replacements[word]

    if len(word) > 6:
        for suffix in (
            "ingly",
            "edly",
            "ation",
            "ments",
            "ment",
            "ness",
            "ing",
            "ers",
            "ies",
            "ed",
            "es",
            "s",
        ):
            if word.endswith(suffix):
                candidate = word[:-len(suffix)]

                if len(candidate) >= 4:
                    return candidate

    return word


def tokens(
    text: str,
) -> set:
    words = re.findall(
        r"[A-Za-z][A-Za-z0-9'-]{2,}",
        text.lower(),
    )

    return {
        normalize_word(w)
        for w in words
        if w not in STOPWORDS
    }


def similarity(
    a: str,
    b: str,
) -> float:
    ta = tokens(a)
    tb = tokens(b)

    if not ta or not tb:
        return 0.0

    return len(ta & tb) / max(
        1,
        len(ta | tb),
    )


def relevance(
    objective: str,
    text: str,
) -> float:
    a = tokens(objective)
    b = tokens(text)

    if not a or not b:
        return 0.0

    overlap = len(a & b)

    return round(
        min(
            1.0,
            overlap
            / max(
                5,
                min(
                    len(a),
                    30,
                ),
            ),
        ),
        3,
    )


def sentence_split(
    text: str,
) -> List[str]:
    text = clean_text(text)

    parts = re.split(
        r"(?<=[.!?])\s+(?=[A-Z0-9])",
        text,
    )

    return [
        p.strip()
        for p in parts
        if 70 <= len(p.strip()) <= 600
    ]


def looks_like_metadata(
    sentence: str,
) -> bool:
    s = sentence.lower()

    if any(
        phrase in s
        for phrase in BAD_PHRASES
    ):
        return True

    if "http://" in s or "https://" in s:
        return True

    if re.search(
        r"\b(arxiv|doi):\s*[\w./-]+",
        s,
        flags=re.I,
    ):
        return True

    # Navigation-like fragments.
    if re.match(
        r"^(view|download|cite|submit|search|"
        r"browse|login|sign in|share)\b",
        s,
    ):
        return True

    # Excessive metadata separators.
    if s.count("|") >= 2:
        return True

    return False


def looks_like_claim(
    sentence: str,
) -> bool:
    s = sentence.lower()

    if len(s) < 70:
        return False

    if looks_like_metadata(
        sentence
    ):
        return False

    words = set(
        re.findall(
            r"[a-z]+",
            s,
        )
    )

    marker_score = len(
        words & CLAIM_MARKERS
    )

    number = bool(
        re.search(
            r"\b\d+(?:\.\d+)?\s*"
            r"(?:%|percent|percentage|times|fold)?\b",
            s,
            flags=re.I,
        )
    )

    comparison = bool(
        re.search(
            r"\b("
            r"compared|versus|vs\.?|"
            r"higher|lower|better|worse|"
            r"less|more|outperformed|"
            r"underperformed"
            r")\b",
            s,
            flags=re.I,
        )
    )

    empirical = bool(
        re.search(
            r"\b("
            r"experiment|study|trial|"
            r"benchmark|evaluation|dataset|"
            r"participants|sample|"
            r"measured|observed|"
            r"tested|tested on"
            r")\b",
            s,
            flags=re.I,
        )
    )

    return (
        marker_score >= 1
        or (
            number
            and (
                comparison
                or empirical
            )
        )
    )


# ============================================================
# DOMAIN / IDENTITY
# ============================================================

RESOLVER_DOMAINS = {
    "doi.org",
    "dx.doi.org",
}

NON_INDEPENDENT_DOMAINS = {
    "doi.org",
    "dx.doi.org",
    "api.openalex.org",
    "semanticscholar.org",
    "api.semanticscholar.org",
    "api.crossref.org",
}


def domain_of(
    url: str,
) -> str:
    try:
        host = (
            urlparse(url)
            .netloc
            .lower()
            .split("@")[-1]
            .split(":")[0]
        )

        if host.startswith("www."):
            host = host[4:]

        return host

    except Exception:
        return ""


def independent_domain(
    url: str,
) -> str:
    host = domain_of(url)

    if not host:
        return ""

    if host in NON_INDEPENDENT_DOMAINS:
        return ""

    parts = host.split(".")

    if len(parts) <= 2:
        return host

    # Simple handling for common country-code domains.
    if (
        len(parts) >= 3
        and parts[-2] in {
            "co",
            "org",
            "ac",
            "gov",
            "edu",
        }
    ):
        return ".".join(
            parts[-3:]
        )

    return ".".join(
        parts[-2:]
    )


def canonical_doi(
    doi: str,
) -> str:
    value = (
        doi or ""
    ).strip()

    value = re.sub(
        r"^https?://"
        r"(?:dx\.)?doi\.org/",
        "",
        value,
        flags=re.I,
    )

    value = value.rstrip(
        ".,; "
    )

    return value.lower()


def canonical_arxiv(
    value: str,
) -> str:
    value = (
        value or ""
    ).strip()

    match = re.search(
        r"arxiv\.org/(?:abs|pdf)/"
        r"([^?#/]+)",
        value,
        flags=re.I,
    )

    if match:
        return match.group(1)

    if value.lower().startswith(
        "arxiv:"
    ):
        return value.split(
            ":",
            1,
        )[1]

    return value


def normalize_title(
    title: str,
) -> str:
    value = clean_text(
        title
    ).lower()

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return " ".join(
        value.split()
    )


def source_id(
    source: dict,
) -> str:
    doi = canonical_doi(
        source.get("doi", "")
    )

    if doi:
        return "doi:" + doi

    arxiv_id = canonical_arxiv(
        source.get("arxiv_id", "")
        or source.get("provider_id", "")
        if source.get("provider")
        == "arxiv"
        else ""
    )

    if arxiv_id:
        return "arxiv:" + arxiv_id

    pmid = str(
        source.get("pmid", "")
        or ""
    ).strip()

    if pmid:
        return "pmid:" + pmid

    title = normalize_title(
        source.get("title", "")
    )

    if title:
        return "title:" + hashlib.sha256(
            title.encode()
        ).hexdigest()[:24]

    url = source.get(
        "url",
        "",
    )

    return "url:" + hashlib.sha256(
        url.encode()
    ).hexdigest()[:24]


def work_key(
    source: dict,
) -> str:
    return source_id(source)


# ============================================================
# FILTERING
# ============================================================

ARTIFACT_PHRASES = (
    "supplementary",
    "supplemental",
    "supporting information",
    "data availability",
    "graphical abstract",
    "cover image",
    "reviewer report",
    "review report",
    "editor decision",
    "decision letter",
    "response to reviewers",
    "peer review",
    "correction",
    "erratum",
    "retraction notice",
    "publisher correction",
    "author response",
)


def is_artifact(
    source: dict,
) -> bool:
    title = clean_text(
        source.get("title", "")
    ).lower()

    doi = canonical_doi(
        source.get("doi", "")
    ).lower()

    if any(
        x in title
        for x in ARTIFACT_PHRASES
    ):
        return True

    if re.search(
        r"(?:/|-|_)(?:s00\d+|"
        r"supp|supplement|review\d+|"
        r"decision\d+)(?:[./_-]|$)",
        doi,
    ):
        return True

    publication_type = clean_text(
        source.get(
            "publication_type",
            "",
        )
    ).lower()

    if publication_type in {
        "correction",
        "erratum",
        "retraction",
        "peer review",
        "editorial",
    }:
        return True

    return False


def is_review_like(
    source: dict,
) -> bool:
    title = clean_text(
        source.get(
            "title",
            "",
        )
    ).lower()

    return bool(
        re.search(
            r"\b("
            r"systematic review|"
            r"literature review|"
            r"scoping review|"
            r"narrative review|"
            r"meta-analysis"
            r")\b",
            title,
        )
    )


def likely_research_source(
    source: dict,
) -> bool:
    if is_artifact(source):
        return False

    title = clean_text(
        source.get(
            "title",
            "",
        )
    )

    body = clean_text(
        source.get(
            "text",
            "",
        )
        or source.get(
            "abstract",
            "",
        )
    )

    return (
        len(title) >= 8
        and len(body) >= 100
    )


# ============================================================
# QUALITY
# ============================================================

TIER_RANK = {
    "FULL_TEXT": 5,
    "ABSTRACT": 4,
    "SNIPPET": 3,
    "STRUCTURED_METADATA": 1,
}


def quality_score(
    source: dict,
) -> float:
    score = 0.30

    provider = source.get(
        "provider"
    )

    provider_bonus = {
        "openalex": 0.10,
        "semantic_scholar": 0.11,
        "europe_pmc": 0.12,
        "crossref": 0.05,
        "arxiv": 0.09,
        "semantic_scholar_snippet": 0.05,
    }

    score += provider_bonus.get(
        provider,
        0,
    )

    score += {
        "FULL_TEXT": 0.30,
        "ABSTRACT": 0.20,
        "SNIPPET": 0.12,
        "STRUCTURED_METADATA": 0.02,
    }.get(
        source.get("tier"),
        0,
    )

    if source.get("doi"):
        score += 0.05

    if source.get(
        "open_access"
    ):
        score += 0.05

    if source.get(
        "citation_count",
        0,
    ) >= 10:
        score += 0.03

    return round(
        min(1.0, score),
        3,
    )


# ============================================================
# OPENALEX
# ============================================================

def openalex_abstract(
    inverted: Optional[dict],
) -> str:
    if not inverted:
        return ""

    words = []

    for word, positions in (
        inverted.items()
    ):
        for position in positions:
            words.append(
                (
                    position,
                    word,
                )
            )

    words.sort(
        key=lambda x: x[0]
    )

    return " ".join(
        word
        for _, word in words
    )


def search_openalex(
    query: str,
    limit: int = 8,
) -> List[dict]:
    data = get_json(
        "https://api.openalex.org/works",
        params={
            "search": query,
            "per-page": min(
                limit,
                25,
            ),
        },
    )

    if not data:
        return []

    output = []

    for item in data.get(
        "results",
        [],
    ):
        abstract = clean_text(
            openalex_abstract(
                item.get(
                    "abstract_inverted_index"
                )
            )
        )

        primary = (
            item.get(
                "primary_location"
            )
            or {}
        )

        source = (
            primary.get(
                "source"
            )
            or {}
        )

        landing = (
            primary.get(
                "landing_page_url"
            )
            or ""
        )

        pdf = (
            primary.get(
                "pdf_url"
            )
            or ""
        )

        doi = canonical_doi(
            item.get(
                "doi"
            )
            or ""
        )

        url = (
            landing
            or pdf
            or (
                "https://doi.org/"
                + doi
                if doi
                else ""
            )
        )

        output.append({
            "provider": "openalex",
            "provider_id": item.get(
                "id"
            ),
            "title": clean_text(
                item.get(
                    "title"
                )
            ),
            "abstract": abstract,
            "text": "",
            "doi": doi,
            "url": url,
            "pdf_url": pdf,
            "domain": domain_of(
                url
            ),
            "independent_domain":
                independent_domain(
                    url
                ),
            "year": item.get(
                "publication_year"
            ),
            "venue": clean_text(
                source.get(
                    "display_name"
                )
            ),
            "open_access": bool(
                (
                    item.get(
                        "open_access"
                    )
                    or {}
                ).get(
                    "is_oa"
                )
            ),
            "publication_type":
                item.get(
                    "type"
                ),
            "tier": (
                "ABSTRACT"
                if abstract
                else
                "STRUCTURED_METADATA"
            ),
        })

    return output


# ============================================================
# SEMANTIC SCHOLAR
# ============================================================

def search_semantic_scholar(
    query: str,
    limit: int = 8,
) -> List[dict]:
    data = get_json(
        "https://api.semanticscholar.org/"
        "graph/v1/paper/search",
        params={
            "query": query,
            "limit": min(
                limit,
                20,
            ),
            "fields": (
                "title,abstract,url,year,"
                "authors,venue,publicationTypes,"
                "openAccessPdf,externalIds,"
                "citationCount"
            ),
        },
    )

    if not data:
        return []

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

        doi = canonical_doi(
            external.get(
                "DOI"
            )
            or ""
        )

        url = (
            item.get(
                "url"
            )
            or ""
        )

        pdf = (
            item.get(
                "openAccessPdf"
            )
            or {}
        )

        pdf_url = (
            pdf.get(
                "url"
            )
            or ""
        )

        abstract = clean_text(
            item.get(
                "abstract"
            )
        )

        publication_types = (
            item.get(
                "publicationTypes"
            )
            or []
        )

        output.append({
            "provider":
                "semantic_scholar",
            "provider_id":
                item.get(
                    "paperId"
                ),
            "title":
                clean_text(
                    item.get(
                        "title"
                    )
                ),
            "abstract":
                abstract,
            "text": "",
            "doi": doi,
            "url": (
                pdf_url
                or url
                or (
                    "https://doi.org/"
                    + doi
                    if doi
                    else ""
                )
            ),
            "pdf_url": pdf_url,
            "domain":
                domain_of(
                    pdf_url or url
                ),
            "independent_domain":
                independent_domain(
                    pdf_url or url
                ),
            "year":
                item.get(
                    "year"
                ),
            "venue":
                clean_text(
                    item.get(
                        "venue"
                    )
                ),
            "open_access":
                bool(pdf_url),
            "publication_type":
                (
                    publication_types[0]
                    if publication_types
                    else ""
                ),
            "citation_count":
                item.get(
                    "citationCount",
                    0,
                ),
            "tier": (
                "ABSTRACT"
                if abstract
                else
                "STRUCTURED_METADATA"
            ),
        })

    return output


# ============================================================
# EUROPE PMC
# ============================================================

def search_europe_pmc(
    query: str,
    limit: int = 8,
) -> List[dict]:
    data = get_json(
        "https://www.ebi.ac.uk/"
        "europepmc/webservices/rest/search",
        params={
            "query": query,
            "format": "json",
            "pageSize": min(
                limit,
                20,
            ),
            "resultType": "core",
        },
    )

    if not data:
        return []

    output = []

    for item in (
        data.get(
            "resultList",
            {},
        ).get(
            "result",
            [],
        )
    ):
        abstract = clean_text(
            item.get(
                "abstractText"
            )
        )

        pmid = str(
            item.get(
                "pmid"
            )
            or ""
        )

        doi = canonical_doi(
            item.get(
                "doi"
            )
            or ""
        )

        url = (
            "https://europepmc.org/article/"
            "MED/"
            + pmid
            if pmid
            else ""
        )

        output.append({
            "provider":
                "europe_pmc",
            "provider_id":
                pmid or item.get(
                    "id"
                ),
            "pmid": pmid,
            "title":
                clean_text(
                    item.get(
                        "title"
                    )
                ),
            "abstract":
                abstract,
            "text": "",
            "doi": doi,
            "url": url,
            "domain":
                "europepmc.org",
            "independent_domain":
                "europepmc.org",
            "year":
                item.get(
                    "pubYear"
                ),
            "venue":
                clean_text(
                    item.get(
                        "journalTitle"
                    )
                ),
            "open_access":
                bool(
                    item.get(
                        "isOpenAccess"
                    )
                ),
            "publication_type":
                item.get(
                    "pubType"
                ),
            "tier": (
                "ABSTRACT"
                if abstract
                else
                "STRUCTURED_METADATA"
            ),
        })

    return output


# ============================================================
# CROSSREF
# ============================================================

def search_crossref(
    query: str,
    limit: int = 8,
) -> List[dict]:
    data = get_json(
        "https://api.crossref.org/works",
        params={
            "query.bibliographic": query,
            "rows": min(
                limit,
                20,
            ),
        },
    )

    if not data:
        return []

    output = []

    for item in (
        data.get(
            "message",
            {},
        ).get(
            "items",
            [],
        )
    ):
        doi = canonical_doi(
            item.get(
                "DOI"
            )
            or ""
        )

        url = (
            item.get(
                "URL"
            )
            or (
                "https://doi.org/"
                + doi
                if doi
                else ""
            )
        )

        abstract = clean_text(
            item.get(
                "abstract"
            )
        )

        publication_type = (
            item.get(
                "type"
            )
            or ""
        )

        title = clean_text(
            " ".join(
                item.get(
                    "title"
                )
                or []
            )
        )

        source = {
            "provider": "crossref",
            "provider_id": doi,
            "title": title,
            "abstract": abstract,
            "text": "",
            "doi": doi,
            "url": url,
            "domain": domain_of(
                url
            ),
            "independent_domain":
                independent_domain(
                    url
                ),
            "year": (
                (
                    item.get(
                        "published-print"
                    )
                    or {}
                )
                .get(
                    "date-parts",
                    [[None]],
                )[0][0]
                or
                (
                    item.get(
                        "published-online"
                    )
                    or {}
                )
                .get(
                    "date-parts",
                    [[None]],
                )[0][0]
            ),
            "venue": clean_text(
                " ".join(
                    item.get(
                        "container-title"
                    )
                    or []
                )
            ),
            "open_access": False,
            "publication_type":
                publication_type,
            "tier": (
                "ABSTRACT"
                if abstract
                else
                "STRUCTURED_METADATA"
            ),
        }

        if not is_artifact(
            source
        ):
            output.append(
                source
            )

    return output


# ============================================================
# ARXIV
# ============================================================

def search_arxiv(
    query: str,
    limit: int = 8,
) -> List[dict]:
    params = {
        "search_query":
            "all:" + query,
        "start": 0,
        "max_results":
            min(limit, 15),
        "sortBy":
            "relevance",
    }

    response = get_response(
        "https://export.arxiv.org/api/query",
        params=params,
    )

    if not response:
        return []

    try:
        root = ET.fromstring(
            response.text
        )
    except Exception:
        return []

    ns = {
        "atom":
            "http://www.w3.org/2005/Atom",
        "arxiv":
            "http://arxiv.org/schemas/atom",
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

        abs_url = clean_text(
            entry.findtext(
                "atom:id",
                "",
                ns,
            )
        )

        arxiv_id = canonical_arxiv(
            abs_url
        )

        pdf_url = (
            "https://arxiv.org/pdf/"
            + arxiv_id
            if arxiv_id
            else ""
        )

        doi = canonical_doi(
            entry.findtext(
                "arxiv:doi",
                "",
                ns,
            )
        )

        output.append({
            "provider":
                "arxiv",
            "provider_id":
                arxiv_id,
            "arxiv_id":
                arxiv_id,
            "title":
                title,
            "abstract":
                abstract,
            "text": "",
            "doi": doi,
            "url":
                pdf_url
                or abs_url,
            "pdf_url":
                pdf_url,
            "domain":
                "arxiv.org",
            "independent_domain":
                "arxiv.org",
            "year": None,
            "venue":
                "arXiv",
            "open_access":
                True,
            "publication_type":
                "preprint",
            "tier": (
                "ABSTRACT"
                if abstract
                else
                "STRUCTURED_METADATA"
            ),
        })

    return output


# ============================================================
# DIRECT HTML/PDF RECOVERY
# ============================================================

def extract_html_text(
    raw: str,
) -> str:
    if not raw:
        return ""

    text = raw

    # Remove dangerous/noisy regions first.
    for tag in (
        "script",
        "style",
        "noscript",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
        "menu",
        "svg",
    ):
        text = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>",
            " ",
            text,
            flags=re.I | re.S,
        )

    # Prefer article/main.
    matches = re.findall(
        r"<(?:article|main)\b[^>]*>"
        r"(.*?)"
        r"</(?:article|main)>",
        text,
        flags=re.I | re.S,
    )

    if matches:
        text = " ".join(
            matches
        )

    text = clean_text(
        text
    )

    # Remove obvious page-level navigation fragments.
    text = re.sub(
        r"\b("
        r"home|menu|search|login|"
        r"sign in|share|download|"
        r"view pdf|cite this article"
        r")\b",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def extract_pdf_text(
    data: bytes,
) -> str:
    if (
        not data
        or PdfReader is None
    ):
        return ""

    try:
        import io

        reader = PdfReader(
            io.BytesIO(data)
        )

        chunks = []

        for page in reader.pages[:50]:
            try:
                value = (
                    page.extract_text()
                    or ""
                )

                if value:
                    chunks.append(
                        value
                    )
            except Exception:
                continue

        return clean_text(
            "\n".join(
                chunks
            )
        )

    except Exception:
        return ""


def recover_url(
    source: dict,
    url: str,
) -> str:
    if not url:
        return ""

    if not safe_url(
        url
    ):
        return ""

    response = get_response(
        url
    )

    if not response:
        return ""

    final_url = response.url

    if not safe_url(
        final_url
    ):
        return ""

    content_type = (
        response.headers
        .get(
            "content-type",
            "",
        )
        .lower()
    )

    body = response.content

    if (
        "pdf" in content_type
        or body[:4] == b"%PDF"
        or final_url.lower().endswith(
            ".pdf"
        )
    ):
        text = extract_pdf_text(
            body
        )

        if len(text) >= 600:
            source["text"] = text
            source["tier"] = (
                "FULL_TEXT"
            )
            source["url"] = final_url
            source["domain"] = domain_of(
                final_url
            )
            source[
                "independent_domain"
            ] = independent_domain(
                final_url
            )

            return "FULL_TEXT"

    else:
        try:
            decoded = body.decode(
                "utf-8",
                errors="ignore",
            )

            text = extract_html_text(
                decoded
            )

            if len(text) >= 900:
                source["text"] = text
                source["tier"] = (
                    "FULL_TEXT"
                )
                source["url"] = final_url
                source["domain"] = domain_of(
                    final_url
                )
                source[
                    "independent_domain"
                ] = independent_domain(
                    final_url
                )

                return "FULL_TEXT"

        except Exception:
            pass

    return ""


def recover_full_text(
    source: dict,
) -> dict:
    # --------------------------------------------------------
    # arXiv: NEVER parse /abs/ HTML.
    # Use PDF directly.
    # --------------------------------------------------------

    if source.get(
        "provider"
    ) == "arxiv":

        pdf_url = (
            source.get(
                "pdf_url"
            )
            or (
                "https://arxiv.org/pdf/"
                + source.get(
                    "arxiv_id",
                    "",
                )
                if source.get(
                    "arxiv_id"
                )
                else ""
            )
        )

        if pdf_url:
            recover_url(
                source,
                pdf_url,
            )

        return source

    # --------------------------------------------------------
    # Explicit PDF first.
    # --------------------------------------------------------

    pdf_url = source.get(
        "pdf_url"
    )

    if pdf_url:
        recover_url(
            source,
            pdf_url,
        )

        if source.get(
            "tier"
        ) == "FULL_TEXT":
            return source

    # --------------------------------------------------------
    # Normal landing page.
    # --------------------------------------------------------

    url = source.get(
        "url"
    )

    if url:
        recover_url(
            source,
            url,
        )

    return source


# ============================================================
# SOURCE NORMALIZATION
# ============================================================

def merge_source(
    old: dict,
    new: dict,
) -> dict:
    old_rank = TIER_RANK.get(
        old.get("tier"),
        0,
    )

    new_rank = TIER_RANK.get(
        new.get("tier"),
        0,
    )

    if new_rank > old_rank:
        result = {
            **old,
            **new,
        }
    else:
        result = {
            **new,
            **old,
        }

    # Preserve strongest text/abstract.
    if len(
        new.get(
            "text",
            "",
        )
    ) > len(
        old.get(
            "text",
            "",
        )
    ):
        result["text"] = new.get(
            "text",
            "",
        )

    if len(
        new.get(
            "abstract",
            "",
        )
    ) > len(
        old.get(
            "abstract",
            "",
        )
    ):
        result["abstract"] = new.get(
            "abstract",
            "",
        )

    return result


def deduplicate_sources(
    sources: List[dict],
) -> List[dict]:
    by_key = {}

    for source in sources:
        if is_artifact(
            source
        ):
            continue

        key = work_key(
            source
        )

        if key not in by_key:
            by_key[key] = source
        else:
            by_key[key] = merge_source(
                by_key[key],
                source,
            )

    return list(
        by_key.values()
    )


# ============================================================
# QUERY PLANNER
# ============================================================

def compact_objective(
    objective: str,
) -> str:
    text = clean_text(
        objective
    )

    words = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9'-]*",
        text,
    )

    # Prevent huge repeated queries.
    return " ".join(
        words[:32]
    )


def build_queries(
    objective: str,
) -> List[str]:
    base = compact_objective(
        objective
    )

    return [
        f"{base} empirical study",
        f"{base} benchmark evaluation",
        f"{base} task success failure",
        f"{base} real world deployment",
        f"{base} reliability limitations",
        f"{base} human intervention monitoring",
        f"{base} independent study replication",
        f"{base} systematic evaluation",
        f"{base} failure modes",
        f"{base} performance evaluation",
    ]


# ============================================================
# DISCOVERY
# ============================================================

def discover_sources(
    objective: str,
) -> Tuple[
    List[dict],
    List[str],
]:
    queries = build_queries(
        objective
    )

    sources = []
    providers = set()

    # OpenAlex + Semantic Scholar.
    for query in queries[:7]:

        for name, fn in (
            (
                "openalex",
                search_openalex,
            ),
            (
                "semantic_scholar",
                search_semantic_scholar,
            ),
        ):
            try:
                results = fn(
                    query,
                    limit=7,
                )

                if results:
                    providers.add(
                        name
                    )

                sources.extend(
                    results
                )
            except Exception:
                continue

    # Crossref + arXiv.
    for query in queries[:5]:

        for name, fn in (
            (
                "crossref",
                search_crossref,
            ),
            (
                "arxiv",
                search_arxiv,
            ),
        ):
            try:
                results = fn(
                    query,
                    limit=6,
                )

                if results:
                    providers.add(
                        name
                    )

                sources.extend(
                    results
                )
            except Exception:
                continue

    # Europe PMC.
    for query in queries[:4]:
        try:
            results = search_europe_pmc(
                query,
                limit=7,
            )

            if results:
                providers.add(
                    "europe_pmc"
                )

            sources.extend(
                results
            )
        except Exception:
            continue

    return (
        sources,
        sorted(providers),
    )


# ============================================================
# ENRICHMENT / EVIDENCE RECOVERY
# ============================================================

def enrich_sources(
    objective: str,
    candidates: List[dict],
) -> List[dict]:
    accepted = []

    for source in candidates:

        if is_artifact(
            source
        ):
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
            objective,
            content,
        )

        source[
            "relevance"
        ] = rel

        # Strong title/abstract relevance
        # is required before network recovery.
        if rel < 0.10:
            continue

        # Reviews are not automatically bad,
        # but they are not treated as independent
        # primary studies.
        source[
            "review_like"
        ] = is_review_like(
            source
        )

        if (
            source.get(
                "tier"
            )
            in {
                "ABSTRACT",
                "STRUCTURED_METADATA",
            }
            and source.get(
                "url"
            )
        ):
            recover_full_text(
                source
            )

        # If recovery failed, abstract remains valid.
        usable_text = len(
            source.get(
                "text",
                "",
            )
        ) >= 600

        usable_abstract = len(
            source.get(
                "abstract",
                "",
            )
        ) >= 120

        if not (
            usable_text
            or usable_abstract
        ):
            continue

        # Recalculate domain after redirects.
        source[
            "domain"
        ] = domain_of(
            source.get(
                "url",
                "",
            )
        ) or source.get(
            "domain",
            "",
        )

        source[
            "independent_domain"
        ] = (
            independent_domain(
                source.get(
                    "url",
                    "",
                )
            )
            or source.get(
                "independent_domain",
                "",
            )
        )

        source[
            "quality"
        ] = quality_score(
            source
        )

        source[
            "evidence_score"
        ] = round(
            (
                source[
                    "quality"
                ] * 0.55
                + rel * 0.45
            ),
            3,
        )

        accepted.append(
            source
        )

    # Final work dedup after recovery.
    accepted = deduplicate_sources(
        accepted
    )

    accepted.sort(
        key=lambda s: s.get(
            "evidence_score",
            0,
        ),
        reverse=True,
    )

    return accepted[
        :MAX_SOURCES
    ]


# ============================================================
# CLAIM FAMILIES
# ============================================================

SYNONYMS = {
    "success": "success",
    "successful": "success",
    "reliability": "reliability",
    "reliable": "reliability",
    "failure": "failure",
    "failures": "failure",
    "evaluation": "evaluation",
    "evaluated": "evaluation",
    "benchmark": "evaluation",
    "agent": "agent",
    "agents": "agent",
    "intervention": "intervention",
    "oversight": "intervention",
    "monitoring": "monitoring",
    "performance": "performance",
    "accuracy": "accuracy",
    "limitation": "limitation",
    "limitations": "limitation",
    "replication": "replication",
    "deployment": "deployment",
}


def claim_family(
    text: str,
) -> List[str]:
    result = []

    for word in tokens(text):
        if word in SYNONYMS:
            value = SYNONYMS[word]

            if value not in result:
                result.append(
                    value
                )

    return result[:12]


def extract_numbers(
    text: str,
) -> List[str]:
    return re.findall(
        r"\b\d+(?:\.\d+)?\s*"
        r"(?:%|percent|percentage|times|fold)?",
        text,
        flags=re.I,
    )


# ============================================================
# CLAIM EXTRACTION
# ============================================================

def normalize_claim(
    sentence: str,
) -> str:
    sentence = clean_text(
        sentence
    )

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


def extract_claims(
    objective: str,
    sources: List[dict],
) -> List[dict]:
    claims = []

    for source in sources:

        body = clean_text(
            source.get(
                "text",
            )
            or source.get(
                "abstract",
            )
            or ""
        )

        if not body:
            continue

        selected = []

        for sentence in sentence_split(
            body
        ):

            if not looks_like_claim(
                sentence
            ):
                continue

            score = relevance(
                objective,
                sentence,
            )

            # Abstracts may have less direct
            # objective overlap, so retain useful
            # empirical statements.
            if score < 0.05:
                continue

            sentence = normalize_claim(
                sentence
            )

            if any(
                similarity(
                    sentence,
                    old,
                ) >= 0.80
                for old in selected
            ):
                continue

            selected.append(
                sentence
            )

            if len(
                selected
            ) >= 4:
                break

        for sentence in selected:

            sid = source_id(
                source
            )

            claim_id = (
                "claim-"
                + hashlib.sha256(
                    (
                        sid
                        + "|"
                        + sentence
                    ).encode()
                ).hexdigest()[:16]
            )

            claim = {
                "claim_id":
                    claim_id,
                "text":
                    sentence,
                "source_id":
                    sid,
                "provider":
                    source.get(
                        "provider"
                    ),
                "domain":
                    source.get(
                        "independent_domain"
                    )
                    or source.get(
                        "domain"
                    ),
                "quality":
                    source.get(
                        "quality",
                        0,
                    ),
                "relevance":
                    relevance(
                        objective,
                        sentence,
                    ),
                "family":
                    claim_family(
                        sentence
                    ),
                "numbers":
                    extract_numbers(
                        sentence
                    ),
                "stance":
                    classify_stance(
                        sentence
                    ),
                "status":
                    "UNCERTAIN",
                "supporting_sources":
                    [],
                "contradicting_sources":
                    [],
                "limiting_sources":
                    [],
                "corroborating_sources":
                    [],
            }

            duplicate = False

            for old in claims:

                if similarity(
                    sentence,
                    old[
                        "text"
                    ],
                ) >= 0.82:
                    duplicate = True
                    break

            if not duplicate:
                claims.append(
                    claim
                )

    return claims[
        :MAX_CLAIMS
    ]


# ============================================================
# STANCE
# ============================================================

POSITIVE = {
    "improve",
    "improved",
    "increase",
    "increased",
    "higher",
    "better",
    "success",
    "successful",
    "effective",
    "reliable",
    "accurate",
    "outperformed",
    "benefit",
}

NEGATIVE = {
    "failure",
    "failed",
    "decrease",
    "decreased",
    "lower",
    "worse",
    "error",
    "errors",
    "unreliable",
    "underperformed",
    "harm",
    "problem",
}

LIMITATION = {
    "however",
    "limitation",
    "limitations",
    "caution",
    "caveat",
    "cannot",
    "unable",
    "restricted",
    "depends",
    "boundary",
    "condition",
}


def classify_stance(
    text: str,
) -> str:
    words = set(
        re.findall(
            r"[a-z]+",
            text.lower(),
        )
    )

    positive = len(
        words & POSITIVE
    )

    negative = len(
        words & NEGATIVE
    )

    limitation = len(
        words & LIMITATION
    )

    if limitation:
        return "limitation"

    if negative > positive:
        return "negative"

    if positive > negative:
        return "positive"

    return "neutral"


# ============================================================
# GRAPH
# ============================================================

def relation_score(
    a: dict,
    b: dict,
) -> float:
    base = similarity(
        a["text"],
        b["text"],
    )

    families_a = set(
        a.get(
            "family",
            [],
        )
    )

    families_b = set(
        b.get(
            "family",
            [],
        )
    )

    family_overlap = (
        len(
            families_a
            & families_b
        )
        / max(
            1,
            len(
                families_a
                | families_b
            ),
        )
    )

    score = (
        base * 0.70
        + family_overlap * 0.30
    )

    # Shared numeric findings are a useful
    # corroboration signal.
    nums_a = set(
        a.get(
            "numbers",
            [],
        )
    )

    nums_b = set(
        b.get(
            "numbers",
            [],
        )
    )

    if (
        nums_a
        and nums_b
        and nums_a & nums_b
    ):
        score += 0.08

    return min(
        1.0,
        score,
    )


def build_evidence_graph(
    claims: List[dict],
    sources: List[dict],
) -> dict:
    edges = []

    for i, a in enumerate(
        claims
    ):
        for b in claims[
            i + 1:
        ]:

            if (
                a["source_id"]
                == b["source_id"]
            ):
                continue

            score = relation_score(
                a,
                b,
            )

            if score < 0.28:
                continue

            stance_a = a[
                "stance"
            ]

            stance_b = b[
                "stance"
            ]

            domain_a = a.get(
                "domain"
            )

            domain_b = b.get(
                "domain"
            )

            independent = bool(
                domain_a
                and domain_b
                and domain_a
                != domain_b
            )

            relation = None

            if (
                stance_a
                == "positive"
                and stance_b
                == "positive"
            ):
                relation = (
                    "CORROBORATES"
                    if score >= 0.45
                    else "SUPPORTS"
                )

            elif (
                stance_a
                == "negative"
                and stance_b
                == "negative"
            ):
                relation = (
                    "CORROBORATES"
                    if score >= 0.45
                    else "SUPPORTS"
                )

            elif {
                stance_a,
                stance_b,
            } == {
                "positive",
                "negative",
            }:
                if score >= 0.38:
                    relation = (
                        "CONTRADICTS"
                    )

            elif (
                stance_a
                == "limitation"
                or stance_b
                == "limitation"
            ):
                if score >= 0.30:
                    relation = (
                        "LIMITS"
                    )

            if not relation:
                continue

            edges.append({
                "from":
                    a["claim_id"],
                "to":
                    b["claim_id"],
                "type":
                    relation,
                "score":
                    round(
                        score,
                        3,
                    ),
                "independent_domain":
                    independent,
                "different_work":
                    True,
            })

    # Populate relationships.
    claim_map = {
        c["claim_id"]: c
        for c in claims
    }

    for edge in edges:

        a = claim_map.get(
            edge["from"]
        )

        b = claim_map.get(
            edge["to"]
        )

        if not a or not b:
            continue

        relation = edge[
            "type"
        ]

        if relation in {
            "SUPPORTS",
            "CORROBORATES",
        }:
            if (
                b["source_id"]
                not in b[
                    "supporting_sources"
                ]
            ):
                b[
                    "supporting_sources"
                ].append(
                    a["source_id"]
                )

            if relation == "CORROBORATES":
                if (
                    a["source_id"]
                    not in b[
                        "corroborating_sources"
                    ]
                ):
                    b[
                        "corroborating_sources"
                    ].append(
                        a["source_id"]
                    )

        elif relation == "CONTRADICTS":
            if (
                a["source_id"]
                not in b[
                    "contradicting_sources"
                ]
            ):
                b[
                    "contradicting_sources"
                ].append(
                    a["source_id"]
                )

        elif relation == "LIMITS":
            if (
                a["source_id"]
                not in b[
                    "limiting_sources"
                ]
            ):
                b[
                    "limiting_sources"
                ].append(
                    a["source_id"]
                )

    nodes = []

    for source in sources:
        nodes.append({
            "id":
                source_id(source),
            "type":
                "source",
            "title":
                source.get(
                    "title"
                ),
            "provider":
                source.get(
                    "provider"
                ),
            "domain":
                source.get(
                    "independent_domain"
                )
                or source.get(
                    "domain"
                ),
            "tier":
                source.get(
                    "tier"
                ),
            "quality":
                source.get(
                    "quality"
                ),
        })

    for claim in claims:
        nodes.append({
            "id":
                claim["claim_id"],
            "type":
                "claim",
            "text":
                claim["text"],
            "family":
                claim.get(
                    "family",
                    [],
                ),
            "stance":
                claim.get(
                    "stance"
                ),
            "status":
                claim.get(
                    "status"
                ),
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
    limited = 0

    for claim in claims:

        support_ids = set(
            claim.get(
                "supporting_sources",
                [],
            )
        )

        contradiction_ids = set(
            claim.get(
                "contradicting_sources",
                [],
            )
        )

        limiting_ids = set(
            claim.get(
                "limiting_sources",
                [],
            )
        )

        support_works = {
            sid
            for sid in support_ids
            if sid in source_map
        }

        support_domains = {
            source_map[sid].get(
                "independent_domain"
            )
            for sid in support_ids
            if sid in source_map
            and source_map[sid].get(
                "independent_domain"
            )
        }

        contradiction_domains = {
            source_map[sid].get(
                "independent_domain"
            )
            for sid in contradiction_ids
            if sid in source_map
        }

        # Strong verification:
        # two distinct works + two domains.
        if (
            len(
                support_works
            ) >= 2
            and len(
                support_domains
            ) >= 2
            and not (
                contradiction_domains
                & support_domains
            )
        ):
            claim[
                "status"
            ] = "VERIFIED"

            verified += 1

        elif (
            contradiction_ids
            and len(
                contradiction_ids
            )
            >= max(
                1,
                len(
                    support_ids
                ),
            )
        ):
            claim[
                "status"
            ] = "CONTRADICTED"

            contradicted += 1

        elif limiting_ids:
            claim[
                "status"
            ] = "LIMITED"

            limited += 1

        elif support_ids:
            claim[
                "status"
            ] = "UNCERTAIN"

            uncertain += 1

        else:
            claim[
                "status"
            ] = "UNCERTAIN"

            uncertain += 1

    total = len(
        claims
    )

    confidence = (
        (
            verified * 1.0
            + limited * 0.55
            + uncertain * 0.40
            + contradicted * 0.10
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
        "limited": limited,
        "unsupported": 0,
        "confidence": round(
            confidence,
            3,
        ),
    }


# ============================================================
# COUNTER EVIDENCE
# ============================================================

def claim_core(
    claim: str,
) -> str:
    words = [
        normalize_word(w)
        for w in re.findall(
            r"[A-Za-z][A-Za-z0-9'-]{2,}",
            claim.lower(),
        )
        if w not in STOPWORDS
    ]

    # Keep strongest conceptual terms.
    priority = [
        w for w in words
        if w in (
            CLAIM_MARKERS
            | set(SYNONYMS.keys())
        )
    ]

    result = (
        priority
        or words
    )

    return " ".join(
        dict.fromkeys(
            result
        )
    )[:500]


def counter_queries(
    claim: str,
) -> List[str]:
    base = claim_core(
        claim
    )

    return [
        f"{base} failure",
        f"{base} limitation",
        f"{base} replication",
        f"{base} contradictory",
        f"{base} null result",
        f"{base} boundary condition",
    ]


def collect_counter_evidence(
    claims: List[dict],
    existing_sources: List[dict],
) -> Tuple[
    List[dict],
    int,
]:
    targets = [
        c for c in claims
        if c.get(
            "status"
        ) in {
            "VERIFIED",
            "UNCERTAIN",
            "LIMITED",
        }
    ][:8]

    existing = {
        source_id(s)
        for s in existing_sources
    }

    found = []
    tested = 0

    for claim in targets:

        tested += 1

        queries = counter_queries(
            claim["text"]
        )

        # Three distinct counter directions.
        for query in queries[
            :4
        ]:

            for provider in (
                "openalex",
                "semantic_scholar",
            ):

                try:
                    if (
                        provider
                        == "openalex"
                    ):
                        results = (
                            search_openalex(
                                query,
                                limit=4,
                            )
                        )
                    else:
                        results = (
                            search_semantic_scholar(
                                query,
                                limit=4,
                            )
                        )
                except Exception:
                    continue

                for source in results:

                    if is_artifact(
                        source
                    ):
                        continue

                    sid = source_id(
                        source
                    )

                    if sid in existing:
                        continue

                    text = " ".join([
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
                        text,
                    )

                    if rel < 0.10:
                        continue

                    source[
                        "relevance"
                    ] = rel

                    recover_full_text(
                        source
                    )

                    source[
                        "quality"
                    ] = quality_score(
                        source
                    )

                    source[
                        "evidence_score"
                    ] = round(
                        (
                            source[
                                "quality"
                            ] * 0.55
                            + rel * 0.45
                        ),
                        3,
                    )

                    source[
                        "counter_for"
                    ] = claim[
                        "claim_id"
                    ]

                    # Preserve why it was searched.
                    source[
                        "counter_query"
                    ] = query

                    found.append(
                        source
                    )

                    existing.add(
                        sid
                    )

                    if len(
                        found
                    ) >= MAX_COUNTER_SOURCES:
                        return (
                            found,
                            tested,
                        )

    return (
        found,
        tested,
    )


def attach_counter_evidence(
    claims: List[dict],
    counter_sources: List[dict],
):
    by_id = {
        c["claim_id"]: c
        for c in claims
    }

    for source in counter_sources:

        claim_id = source.get(
            "counter_for"
        )

        claim = by_id.get(
            claim_id
        )

        if not claim:
            continue

        claim_text = claim[
            "text"
        ]

        source_text = " ".join([
            source.get(
                "title",
                "",
            ),
            source.get(
                "text",
                "",
            )
            or source.get(
                "abstract",
                "",
            ),
        ])

        sim = similarity(
            claim_text,
            source_text,
        )

        if sim < 0.25:
            continue

        source_stance = classify_stance(
            source_text
        )

        claim_stance = claim[
            "stance"
        ]

        sid = source_id(
            source
        )

        if (
            {
                source_stance,
                claim_stance,
            }
            == {
                "positive",
                "negative",
            }
        ):
            if sid not in claim[
                "contradicting_sources"
            ]:
                claim[
                    "contradicting_sources"
                ].append(
                    sid
                )

        elif source_stance == "limitation":
            if sid not in claim[
                "limiting_sources"
            ]:
                claim[
                    "limiting_sources"
                ].append(
                    sid
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
        if c.get(
            "status"
        ) == "VERIFIED"
    ]

    uncertain = [
        c for c in claims
        if c.get(
            "status"
        ) == "UNCERTAIN"
    ]

    contradicted = [
        c for c in claims
        if c.get(
            "status"
        ) == "CONTRADICTED"
    ]

    limited = [
        c for c in claims
        if c.get(
            "status"
        ) == "LIMITED"
    ]

    domains = sorted({
        s.get(
            "independent_domain"
        )
        for s in sources
        if s.get(
            "independent_domain"
        )
    })

    providers = sorted({
        s.get(
            "provider"
        )
        for s in sources
        if s.get(
            "provider"
        )
    })

    if not claims:
        conclusion = (
            "The evidence cycle did not recover "
            "enough substantive claims for a "
            "defensible synthesis."
        )

    elif contradicted:
        conclusion = (
            "The evidence contains conflicting "
            "findings. Claims with contradictory "
            "evidence should not be treated as "
            "settled."
        )

    elif verified:
        conclusion = (
            "Some claims reached the engine's "
            "independent-corroboration threshold. "
            "Other claims remain uncertain or "
            "limited and should be interpreted "
            "individually."
        )

    else:
        conclusion = (
            "Substantive evidence was recovered, "
            "but independent corroboration was "
            "insufficient to classify the major "
            "claims as verified."
        )

    next_actions = []

    if not claims:
        next_actions.append(
            "Expand abstract and full-text recovery."
        )

    if len(domains) < 3:
        next_actions.append(
            "Seek evidence from additional independent domains."
        )

    if not verified and claims:
        next_actions.append(
            "Seek independent studies addressing the major claims."
        )

    if contradicted:
        next_actions.append(
            "Investigate contradictory findings claim-by-claim."
        )

    if limited:
        next_actions.append(
            "Identify the populations, tasks, or conditions limiting the evidence."
        )

    if counter_sources:
        next_actions.append(
            "Review the counter-evidence before treating supported claims as settled."
        )

    if not next_actions:
        next_actions.append(
            "Run another independent evidence cycle with narrower questions."
        )

    return {
        "conclusion":
            conclusion,
        "verified_claims":
            len(verified),
        "uncertain_claims":
            len(uncertain),
        "contradicted_claims":
            len(contradicted),
        "limited_claims":
            len(limited),
        "independent_domains":
            len(domains),
        "providers":
            providers,
        "counter_evidence_sources":
            len(counter_sources),
        "next_actions":
            next_actions,
    }


# ============================================================
# MISSION
# ============================================================

def research_mission(
    mission_id: str,
    objective: str,
):
    started = time.time()

    try:

        # ----------------------------------------------------
        # 1. QUERY PLANNING
        # ----------------------------------------------------

        queries = build_queries(
            objective
        )

        trace = [{
            "stage":
                "query_planning",
            "status":
                "completed",
            "queries":
                queries,
            "count":
                len(queries),
        }]

        # ----------------------------------------------------
        # 2. DISCOVERY
        # ----------------------------------------------------

        discovered, providers = (
            discover_sources(
                objective
            )
        )

        trace.append({
            "stage":
                "multi_provider_discovery",
            "status":
                "completed",
            "sources_discovered":
                len(discovered),
            "providers":
                providers,
        })

        # ----------------------------------------------------
        # 3. SOURCE FILTER
        # ----------------------------------------------------

        candidates = (
            deduplicate_sources(
                discovered
            )
        )

        trace.append({
            "stage":
                "source_filter",
            "status":
                "completed",
            "candidate_works":
                len(candidates),
        })

        # ----------------------------------------------------
        # 4. REAL EVIDENCE RECOVERY
        # ----------------------------------------------------

        sources = enrich_sources(
            objective,
            candidates,
        )

        trace.append({
            "stage":
                "evidence_recovery",
            "status":
                "completed",
            "accepted_sources":
                len(sources),
            "full_text":
                sum(
                    1
                    for s in sources
                    if s.get(
                        "tier"
                    )
                    == "FULL_TEXT"
                ),
            "abstract":
                sum(
                    1
                    for s in sources
                    if s.get(
                        "tier"
                    )
                    == "ABSTRACT"
                ),
        })

        # ----------------------------------------------------
        # 5. CLAIMS
        # ----------------------------------------------------

        claims = extract_claims(
            objective,
            sources,
        )

        trace.append({
            "stage":
                "claim_extraction",
            "status":
                "completed",
            "substantive_claims":
                len(claims),
        })

        # ----------------------------------------------------
        # 6. GRAPH
        # ----------------------------------------------------

        graph = build_evidence_graph(
            claims,
            sources,
        )

        trace.append({
            "stage":
                "evidence_graph",
            "status":
                "completed",
            "nodes":
                len(
                    graph[
                        "nodes"
                    ]
                ),
            "edges":
                len(
                    graph[
                        "edges"
                    ]
                ),
        })

        # ----------------------------------------------------
        # 7. FIRST VERIFICATION
        # ----------------------------------------------------

        verification = verify_claims(
            claims,
            sources,
        )

        trace.append({
            "stage":
                "verification",
            "status":
                "completed",
            "verified":
                verification[
                    "verified"
                ],
            "uncertain":
                verification[
                    "uncertain"
                ],
            "contradicted":
                verification[
                    "contradicted"
                ],
            "limited":
                verification[
                    "limited"
                ],
        })

        # ----------------------------------------------------
        # 8. COUNTER EVIDENCE
        # ----------------------------------------------------

        (
            counter_sources,
            tested,
        ) = collect_counter_evidence(
            claims,
            sources,
        )

        attach_counter_evidence(
            claims,
            counter_sources,
        )

        # Re-run verification after counter-evidence.
        verification = verify_claims(
            claims,
            sources
            + counter_sources,
        )

        trace.append({
            "stage":
                "counter_evidence",
            "status":
                "completed",
            "sources":
                len(
                    counter_sources
                ),
            "claims_tested":
                tested,
        })

        # ----------------------------------------------------
        # 9. FINAL GRAPH
        # ----------------------------------------------------

        graph = build_evidence_graph(
            claims,
            sources
            + counter_sources,
        )

        # ----------------------------------------------------
        # 10. SYNTHESIS
        # ----------------------------------------------------

        synthesis = synthesize(
            claims,
            sources
            + counter_sources,
            verification,
            counter_sources,
        )

        trace.append({
            "stage":
                "synthesis",
            "status":
                "completed",
        })

        # ----------------------------------------------------
        # TELEMETRY
        # ----------------------------------------------------

        all_sources = (
            sources
            + counter_sources
        )

        tiers = {}

        for source in all_sources:
            tier = source.get(
                "tier",
                "UNKNOWN",
            )

            tiers[tier] = (
                tiers.get(
                    tier,
                    0,
                )
                + 1
            )

        domains = sorted({
            s.get(
                "independent_domain"
            )
            for s in all_sources
            if s.get(
                "independent_domain"
            )
        })

        works = {
            source_id(s)
            for s in all_sources
        }

        provider_set = {
            s.get(
                "provider"
            )
            for s in all_sources
            if s.get(
                "provider"
            )
        }

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

        edge_counts = {}

        for edge in graph[
            "edges"
        ]:
            edge_type = edge[
                "type"
            ]

            edge_counts[
                edge_type
            ] = (
                edge_counts.get(
                    edge_type,
                    0,
                )
                + 1
            )

        verified_ratio = (
            verification[
                "verified"
            ]
            / max(
                1,
                len(claims),
            )
        )

        evidence_strength = round(
            min(
                1.0,
                (
                    min(
                        1.0,
                        len(sources)
                        / 20,
                    )
                    * 0.20
                    +
                    min(
                        1.0,
                        len(claims)
                        / 20,
                    )
                    * 0.20
                    +
                    verified_ratio
                    * 0.30
                    +
                    min(
                        1.0,
                        len(domains)
                        / 5,
                    )
                    * 0.20
                    +
                    min(
                        1.0,
                        len(provider_set)
                        / 5,
                    )
                    * 0.10
                ),
            ),
            3,
        )

        result = {
            "task_id":
                mission_id,
            "mission_id":
                mission_id,
            "status":
                "completed",
            "version":
                VERSION,
            "build":
                BUILD,
            "objective":
                objective,

            "agent_trace":
                trace,

            "providers": {
                "used":
                    sorted(
                        provider_set
                    ),
                "count":
                    len(
                        provider_set
                    ),
            },

            "evidence": {
                "count":
                    len(sources),
                "graph_nodes":
                    len(
                        graph[
                            "nodes"
                        ]
                    ),
                "graph_edges":
                    len(
                        graph[
                            "edges"
                        ]
                    ),
                "relationships":
                    edge_counts,
                "independent_domains":
                    len(domains),
                "domains":
                    domains,
                "independent_works":
                    len(works),
                "average_source_quality":
                    round(
                        average_quality,
                        3,
                    ),
                "evidence_tiers":
                    tiers,
            },

            "claims":
                claims,

            "verification": {
                **verification,
                "research_strength":
                    evidence_strength,
            },

            "counter_evidence": {
                "sources":
                    len(
                        counter_sources
                    ),
                "claims_tested":
                    tested,
                "items": [
                    {
                        "id":
                            source_id(s),
                        "title":
                            s.get(
                                "title"
                            ),
                        "provider":
                            s.get(
                                "provider"
                            ),
                        "domain":
                            s.get(
                                "independent_domain"
                            )
                            or s.get(
                                "domain"
                            ),
                        "tier":
                            s.get(
                                "tier"
                            ),
                        "relevance":
                            s.get(
                                "relevance"
                            ),
                        "for_claim":
                            s.get(
                                "counter_for"
                            ),
                    }
                    for s in counter_sources
                ],
            },

            "evidence_graph":
                graph,

            "source_index": [
                {
                    "id":
                        source_id(s),
                    "title":
                        s.get(
                            "title"
                        ),
                    "provider":
                        s.get(
                            "provider"
                        ),
                    "domain":
                        s.get(
                            "independent_domain"
                        )
                        or s.get(
                            "domain"
                        ),
                    "tier":
                        s.get(
                            "tier"
                        ),
                    "quality":
                        s.get(
                            "quality"
                        ),
                    "relevance":
                        s.get(
                            "relevance"
                        ),
                    "evidence_score":
                        s.get(
                            "evidence_score"
                        ),
                    "doi":
                        s.get(
                            "doi"
                        ),
                    "url":
                        s.get(
                            "url"
                        ),
                }
                for s in sources
            ],

            "synthesis":
                synthesis,

            "next_cycle": {
                "recommended":
                    synthesis[
                        "next_actions"
                    ],
            },

            "timing": {
                "started_at":
                    started,
                "completed_at":
                    time.time(),
                "duration_seconds":
                    round(
                        time.time()
                        - started,
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
            "task_id":
                mission_id,
            "mission_id":
                mission_id,
            "status":
                "failed",
            "version":
                VERSION,
            "build":
                BUILD,
            "error":
                str(exc),
            "timing": {
                "started_at":
                    started,
                "completed_at":
                    time.time(),
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
# API MODEL
# ============================================================

class MissionRequest(
    BaseModel
):
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

@app.get("/health")
def health():
    return {
        "status":
            "ok",
        "online":
            True,
        "version":
            VERSION,
        "build":
            BUILD,
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
        "engine":
            "AUTONOMOUS-EVIDENCE-REASONING",
        "workers":
            WORKERS,
        "database":
            str(DB_PATH),
        "providers": [
            "openalex",
            "semantic_scholar",
            "crossref",
            "arxiv",
            "europe_pmc",
        ],
        "pipeline": [
            "query_planning",
            "multi_provider_discovery",
            "source_filter",
            "work_deduplication",
            "abstract_full_text_recovery",
            "claim_extraction",
            "claim_normalization",
            "evidence_graph",
            "support_contradiction_limitation",
            "independent_corroboration",
            "verification",
            "counter_evidence",
            "synthesis",
        ],
    }


# ============================================================
# DASHBOARD
# ============================================================

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
*{box-sizing:border-box}

body{
 margin:0;
 background:#070b12;
 color:#eaf2ff;
 font-family:system-ui,-apple-system,sans-serif
}

.wrap{
 max-width:1050px;
 margin:auto;
 padding:20px
}

.header{
 display:flex;
 justify-content:space-between;
 align-items:center;
 margin-bottom:18px
}

.logo{
 font-size:28px;
 font-weight:800
}

.badge{
 border:1px solid #2b9f70;
 border-radius:20px;
 padding:7px 12px;
 font-size:12px
}

.card{
 background:#0d1420;
 border:1px solid #1d2b3e;
 border-radius:18px;
 padding:18px;
 margin-bottom:15px
}

.small{
 color:#8ea0b7;
 font-size:12px
}

textarea{
 width:100%;
 min-height:145px;
 background:#070b12;
 color:#fff;
 border:1px solid #26384e;
 border-radius:14px;
 padding:15px;
 font-size:16px;
 outline:none
}

button{
 width:100%;
 margin-top:12px;
 border:0;
 border-radius:14px;
 padding:15px;
 font-size:16px;
 font-weight:700;
 cursor:pointer
}

.grid{
 display:grid;
 grid-template-columns:
 repeat(4,minmax(0,1fr));
 gap:10px
}

.stat{
 background:#080e17;
 border:1px solid #1c2a3c;
 border-radius:14px;
 padding:14px
}

.stat b{
 display:block;
 font-size:22px;
 margin-top:5px
}

pre{
 white-space:pre-wrap;
 word-break:break-word;
 background:#05080d;
 border:1px solid #1b2939;
 border-radius:14px;
 padding:15px;
 overflow:auto;
 max-height:650px
}

@media(max-width:700px){
 .grid{
  grid-template-columns:
  repeat(2,minmax(0,1fr))
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
TARGET-2050.27 · AUTONOMOUS EVIDENCE REASONING
</div>

<h2>Research Mission</h2>

<textarea id="objective"
placeholder="What should AI Infinity investigate?"></textarea>

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

<div class="stat">
Domains
<b id="domains">—</b>
</div>

<div class="stat">
Contradictions
<b id="contradictions">—</b>
</div>

<div class="stat">
Counter
<b id="counter">—</b>
</div>

<div class="stat">
Strength
<b id="strength">—</b>
</div>

</div>

</div>

<div class="card">

<div class="small">
MISSION OUTPUT
</div>

<pre id="output">Ready.</pre>

</div>

</div>

<script>

let activeMission=null;

async function runMission(){

 const objective=
  document.getElementById(
   "objective"
  ).value.trim();

 if(!objective){
  alert("Enter a research objective.");
  return;
 }

 document.getElementById(
  "output"
 ).textContent=
  "Starting TARGET-2050.27...";

 try{

  const response=await fetch(
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

  const text=
   await response.text();

  let data;

  try{
   data=JSON.parse(text);
  }catch(e){
   document.getElementById(
    "output"
   ).textContent=text;
   return;
  }

  activeMission=data.mission_id;

  poll();

 }catch(error){

  document.getElementById(
   "output"
  ).textContent=
   String(error);
 }
}


async function poll(){

 if(!activeMission)return;

 try{

  const response=
   await fetch(
    "/mission/"
    +activeMission
   );

  const text=
   await response.text();

  let data;

  try{
   data=JSON.parse(text);
  }catch(e){

   document.getElementById(
    "output"
   ).textContent=text;

   setTimeout(
    poll,
    2500
   );

   return;
  }

  document.getElementById(
   "output"
  ).textContent=
   JSON.stringify(
    data,
    null,
    2
   );

  const result=
   data.result||{};

  const evidence=
   result.evidence||{};

  const verification=
   result.verification||{};

  const counter=
   result.counter_evidence||{};

  document.getElementById(
   "sources"
  ).textContent=
   evidence.count??"—";

  document.getElementById(
   "claims"
  ).textContent=
   result.claims
    ?result.claims.length
    :"—";

  document.getElementById(
   "verified"
  ).textContent=
   verification.verified??"—";

  document.getElementById(
   "edges"
  ).textContent=
   evidence.graph_edges??"—";

  document.getElementById(
   "domains"
  ).textContent=
   evidence.independent_domains??"—";

  document.getElementById(
   "contradictions"
  ).textContent=
   verification.contradicted??"—";

  document.getElementById(
   "counter"
  ).textContent=
   counter.sources??"—";

  document.getElementById(
   "strength"
  ).textContent=
   verification.research_strength
   ??"—";

  if(
   data.status==="completed"
   ||
   data.status==="failed"
  ){
   return;
  }

 }catch(error){

  document.getElementById(
   "output"
  ).textContent=
   String(error);
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


# ============================================================
# MISSION API
# ============================================================

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
            detail=(
                "command or objective "
                "is required"
            ),
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
        "task_id":
            mission_id,
        "mission_id":
            mission_id,
        "status":
            "running",
        "version":
            VERSION,
        "build":
            BUILD,
        "message":
            (
                "Mission accepted. "
                "Autonomous evidence "
                "reasoning is running."
            ),
    }


@app.get(
    "/mission/{mission_id}"
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
            detail=
                "Mission not found",
        )

    return mission


@app.get("/missions")
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
# GLOBAL ERROR
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


# ============================================================
# LOCAL
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
