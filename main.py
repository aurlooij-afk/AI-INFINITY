"""
AI Infinity
TARGET-2050.41
BUILD: INDEPENDENT-EVIDENCE-CLOSURE-CORE

Purpose:
- Fault-tolerant autonomous evidence research
- Crossref + OpenAlex discovery
- DOI/title/URL canonicalization
- Real source-domain normalization
- Real source-family normalization
- Abstract reconstruction
- Publisher fallback
- Conservative atomic claims
- CROSS-WORK evidence search
- Independence-aware verification
- Contradiction detection
- Diversity-aware recovery
- Correct usable/rejected accounting
- Persistent SQLite mission state
- Auditable reports
- Never manufactures VERIFIED claims

Strict verification:
1. >= 2 independent works
2. >= 2 independent domains
3. >= 2 independent source families
4. >= 2 strong evidence relationships
5. zero unresolved contradictions
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# CONFIG
# ============================================================

VERSION = "TARGET-2050.41"
BUILD = "INDEPENDENT-EVIDENCE-CLOSURE-CORE"

APP_NAME = "AI Infinity"

DB_PATH = "/tmp/ai_infinity.db"

REQUEST_TIMEOUT = 15
PUBLISHER_TIMEOUT = 12

MAX_DISCOVERY_PER_QUERY = 12
MAX_TOTAL_WORKS = 50
MAX_TEXT_LENGTH = 18000
MAX_CLAIMS_PER_WORK = 12
MAX_EVIDENCE_PER_CLAIM = 10
MAX_RECOVERY_ROUNDS = 2
MAX_RECOVERY_WORKS = 32

USER_AGENT = (
    "AI-Infinity/2050.41 "
    "(autonomous evidence research system)"
)

CROSSREF_URL = "https://api.crossref.org/works"
OPENALEX_URL = "https://api.openalex.org/works"


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="AI Infinity autonomous evidence research core.",
)


# ============================================================
# REQUEST MODEL
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=10,
        max_length=20000,
    )


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
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
            version TEXT NOT NULL,
            build TEXT NOT NULL,
            result_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            work_id TEXT NOT NULL,
            title TEXT,
            doi TEXT,
            url TEXT,
            domain TEXT,
            source_family TEXT,
            publisher TEXT,
            year INTEGER,
            abstract TEXT,
            relevance REAL DEFAULT 0,
            usable INTEGER DEFAULT 0,
            rejected_reason TEXT,
            provenance_json TEXT,
            UNIQUE(mission_id, work_id)
        );

        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            work_id TEXT NOT NULL,
            text TEXT NOT NULL,
            claim_family TEXT,
            polarity TEXT,
            confidence REAL DEFAULT 0,
            UNIQUE(mission_id, claim_id)
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            work_id TEXT NOT NULL,
            excerpt TEXT NOT NULL,
            lexical_similarity REAL DEFAULT 0,
            entailment REAL DEFAULT 0,
            relation TEXT,
            strength REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS contradictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            claim_a TEXT,
            claim_b TEXT,
            reason TEXT,
            severity REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS diagnostics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            stage TEXT,
            level TEXT,
            message TEXT,
            details_json TEXT,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS recovery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            round INTEGER,
            action TEXT,
            reason TEXT,
            result TEXT,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            stage TEXT,
            event TEXT,
            details_json TEXT,
            created_at REAL NOT NULL
        );
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

def now() -> float:
    return time.time()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str, value: str = "") -> str:
    raw = f"{prefix}|{value}|{time.time_ns()}"
    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:20]
    return f"{prefix}-{digest}"


def clean_text(
    value: Any,
    limit: int = MAX_TEXT_LENGTH,
) -> str:
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        try:
            value = json.dumps(
                value,
                ensure_ascii=False,
            )
        except Exception:
            value = str(value)

    text = str(value)
    text = re.sub(r"\s+", " ", text).strip()

    return text[:limit]


def safe_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            default=str,
        )
    except Exception:
        return "{}"


# ============================================================
# DOMAIN / SOURCE FAMILY
# ============================================================

def domain_of(url: str) -> str:
    """
    Only count the real evidence-origin domain.

    Resolver/API domains are intentionally excluded.
    """

    try:
        host = urlparse(url).netloc.lower()

        host = host.split("@")[-1]
        host = host.split(":")[0]
        host = host.strip(".")

        if host.startswith("www."):
            host = host[4:]

        if host in {
            "",
            "doi.org",
            "dx.doi.org",
            "api.crossref.org",
            "api.openalex.org",
        }:
            return ""

        return host

    except Exception:
        return ""


def normalized_source_family(
    domain: str,
    publisher: str = "",
) -> str:

    d = (domain or "").lower().strip()

    p = re.sub(
        r"[^a-z0-9]+",
        " ",
        (publisher or "").lower(),
    ).strip()

    if d:

        if d.endswith("arxiv.org"):
            return "arxiv"

        if d.endswith("acm.org"):
            return "acm"

        if d.endswith("ieee.org"):
            return "ieee"

        if (
            d.endswith("springer.com")
            or d.endswith("link.springer.com")
        ):
            return "springer"

        if (
            d.endswith("sciencedirect.com")
            or d.endswith("elsevier.com")
        ):
            return "elsevier"

        if d.endswith("nature.com"):
            return "nature"

        if d.endswith("frontiersin.org"):
            return "frontiers"

        if d.endswith("plos.org"):
            return "plos"

        if d.endswith("bmj.com"):
            return "bmj"

        if (
            d.endswith("wiley.com")
            or d.endswith("onlinelibrary.wiley.com")
        ):
            return "wiley"

        if (
            d.endswith("sagepub.com")
            or d.endswith("journals.sagepub.com")
        ):
            return "sage"

        if d.endswith("tandfonline.com"):
            return "taylor_francis"

        if (
            d.endswith("oxfordjournals.org")
            or d.endswith("academic.oup.com")
        ):
            return "oxford"

        if d.endswith("cambridge.org"):
            return "cambridge"

        if d.endswith("mdpi.com"):
            return "mdpi"

        if d.endswith("pubmed.ncbi.nlm.nih.gov"):
            return "pubmed"

        return d

    if p:
        families = (
            (
                "association for computing machinery",
                "acm",
            ),
            (
                "institute of electrical and electronics engineers",
                "ieee",
            ),
            ("springer", "springer"),
            ("elsevier", "elsevier"),
            ("nature", "nature"),
            ("frontiers", "frontiers"),
            ("plos", "plos"),
            ("bmj", "bmj"),
            ("wiley", "wiley"),
            ("sage", "sage"),
            ("taylor francis", "taylor_francis"),
            ("oxford", "oxford"),
            ("cambridge", "cambridge"),
            ("mdpi", "mdpi"),
        )

        for key, family in families:
            if key in p:
                return family

        return (
            "publisher:"
            + p[:80].replace(" ", "_")
        )

    return "unknown"


# ============================================================
# LOGGING
# ============================================================

def log_event(
    mission_id: str,
    stage: str,
    event: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:

    try:
        conn = db()

        conn.execute(
            """
            INSERT INTO events
            (mission_id, stage, event, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                stage,
                event,
                safe_json(details or {}),
                now(),
            ),
        )

        conn.commit()
        conn.close()

    except Exception:
        pass


def log_diagnostic(
    mission_id: str,
    stage: str,
    level: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:

    try:
        conn = db()

        conn.execute(
            """
            INSERT INTO diagnostics
            (mission_id, stage, level, message,
             details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                stage,
                level,
                clean_text(message, 3000),
                safe_json(details or {}),
                now(),
            ),
        )

        conn.commit()
        conn.close()

    except Exception:
        pass


def log_recovery(
    mission_id: str,
    round_number: int,
    action: str,
    reason: str,
    result: str,
) -> None:

    try:
        conn = db()

        conn.execute(
            """
            INSERT INTO recovery
            (mission_id, round, action, reason,
             result, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                round_number,
                action,
                clean_text(reason, 2000),
                clean_text(result, 3000),
                now(),
            ),
        )

        conn.commit()
        conn.close()

    except Exception:
        pass


# ============================================================
# SAFE HTTP
# ============================================================

def http_get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:

    try:
        response = requests.get(
            url,
            params=params,
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )

        if response.status_code >= 400:
            return None, f"HTTP {response.status_code}"

        try:
            data = response.json()
        except Exception:
            return None, "invalid_json"

        if not isinstance(data, dict):
            return None, "unexpected_json_shape"

        return data, None

    except requests.Timeout:
        return None, "timeout"

    except requests.RequestException as exc:
        return (
            None,
            f"request_error:{type(exc).__name__}",
        )

    except Exception as exc:
        return (
            None,
            f"{type(exc).__name__}:{str(exc)[:200]}",
        )


def http_get_text(
    url: str,
    timeout: int = PUBLISHER_TIMEOUT,
) -> Tuple[str, Optional[str]]:

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,"
                    "application/xhtml+xml,"
                    "text/plain"
                ),
            },
        )

        if response.status_code >= 400:
            return "", f"HTTP {response.status_code}"

        text = response.text or ""

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
            r"<[^>]+>",
            " ",
            text,
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        return clean_text(text), None

    except requests.Timeout:
        return "", "timeout"

    except requests.RequestException as exc:
        return (
            "",
            f"request_error:{type(exc).__name__}",
        )

    except Exception as exc:
        return (
            "",
            f"{type(exc).__name__}:{str(exc)[:200]}",
        )


# ============================================================
# OPENALEX ABSTRACT
# ============================================================

def reconstruct_openalex_abstract(
    inverted_index: Any,
) -> str:

    if not isinstance(inverted_index, dict):
        return ""

    positions = []

    for word, indexes in inverted_index.items():

        if not isinstance(indexes, list):
            continue

        for position in indexes:

            if isinstance(position, int):
                positions.append(
                    (position, str(word))
                )

    positions.sort(
        key=lambda x: x[0]
    )

    return clean_text(
        " ".join(
            word
            for _, word in positions
        )
    )


# ============================================================
# DISCOVERY
# ============================================================

def discover_crossref(
    query: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:

    errors = []

    data, error = http_get_json(
        CROSSREF_URL,
        params={
            "query.bibliographic": query,
            "rows": MAX_DISCOVERY_PER_QUERY,
            "select": (
                "DOI,title,URL,published,"
                "published-print,published-online,"
                "publisher,abstract"
            ),
        },
    )

    if error:
        errors.append(error)
        return [], errors

    message = data.get("message")

    if not isinstance(message, dict):
        return [], ["missing_crossref_message"]

    items = message.get("items")

    if not isinstance(items, list):
        return [], ["missing_crossref_items"]

    works = []

    for item in items:

        if not isinstance(item, dict):
            continue

        title = item.get("title")

        if isinstance(title, list):
            title = (
                title[0]
                if title
                else ""
            )

        doi = clean_text(
            item.get("DOI")
        ).lower()

        url = clean_text(
            item.get("URL")
        )

        publisher = clean_text(
            item.get("publisher")
        )

        abstract = clean_text(
            item.get("abstract")
        )

        year = None

        for field in (
            "published",
            "published-print",
            "published-online",
        ):

            value = item.get(field)

            if not isinstance(value, dict):
                continue

            parts = value.get(
                "date-parts"
            )

            if (
                isinstance(parts, list)
                and parts
                and isinstance(parts[0], list)
                and parts[0]
                and isinstance(parts[0][0], int)
            ):
                year = parts[0][0]
                break

        works.append(
            {
                "provider": "crossref",
                "work_id": (
                    f"doi:{doi}"
                    if doi
                    else make_id("crossref")
                ),
                "title": clean_text(title),
                "doi": doi,
                "url": url,
                "publisher": publisher,
                "abstract": abstract,
                "year": year,
            }
        )

    return works, errors


def discover_openalex(
    query: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:

    errors = []

    data, error = http_get_json(
        OPENALEX_URL,
        params={
            "search": query,
            "per-page": MAX_DISCOVERY_PER_QUERY,
        },
    )

    if error:
        errors.append(error)
        return [], errors

    results = data.get("results")

    if not isinstance(results, list):
        return [], ["missing_openalex_results"]

    works = []

    for item in results:

        if not isinstance(item, dict):
            continue

        title = clean_text(
            item.get("title")
        )

        doi = clean_text(
            item.get("doi")
        ).lower()

        primary = (
            item.get("primary_location")
            or {}
        )

        if not isinstance(primary, dict):
            primary = {}

        landing = clean_text(
            primary.get(
                "landing_page_url"
            )
        )

        source = (
            primary.get("source")
            or {}
        )

        if not isinstance(source, dict):
            source = {}

        source_id = clean_text(
            source.get("id")
        )

        source_name = clean_text(
            source.get("display_name")
        )

        host_org = clean_text(
            source.get(
                "host_organization"
            )
        )

        host_org_name = clean_text(
            source.get(
                "host_organization_name"
            )
        )

        abstract = (
            reconstruct_openalex_abstract(
                item.get(
                    "abstract_inverted_index"
                )
            )
        )

        publication_year = item.get(
            "publication_year"
        )

        work_id = clean_text(
            item.get("id")
        )

        if not work_id:
            work_id = (
                f"doi:{doi}"
                if doi
                else make_id("openalex")
            )

        works.append(
            {
                "provider": "openalex",
                "work_id": work_id,
                "title": title,
                "doi": doi,
                "url": landing,
                "publisher": (
                    source_name
                    or host_org_name
                ),
                "abstract": abstract,
                "year": publication_year,
                "source_id": source_id,
                "host_organization": host_org,
                "host_organization_name": (
                    host_org_name
                ),
            }
        )

    return works, errors


# ============================================================
# CANONICALIZATION
# ============================================================

def title_key(title: str) -> str:
    text = (
        title
        or ""
    ).lower()

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text,
    )

    return " ".join(
        text.split()
    )


def canonical_key(work: Dict[str, Any]) -> str:

    doi = clean_text(
        work.get("doi")
    ).lower()

    if doi:
        return f"doi:{doi}"

    title = title_key(
        work.get("title", "")
    )

    if title:
        return f"title:{title}"

    url = clean_text(
        work.get("url")
    ).lower()

    if url:
        return f"url:{url}"

    return clean_text(
        work.get("work_id")
    )


def canonicalize(
    works: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    output = {}
    providers = defaultdict(set)

    for work in works:

        key = canonical_key(work)

        if not key:
            continue

        providers[key].add(
            work.get("provider")
        )

        if key not in output:
            output[key] = dict(work)
            output[key]["providers"] = []

        current = output[key]

        for field in (
            "title",
            "doi",
            "url",
            "publisher",
            "abstract",
            "year",
            "source_id",
            "host_organization",
            "host_organization_name",
        ):

            if not current.get(field) and work.get(field):
                current[field] = work[field]

        current["providers"] = sorted(
            p
            for p in providers[key]
            if p
        )

    return list(output.values())


# ============================================================
# RELEVANCE
# ============================================================

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "their",
    "about",
    "using",
    "were",
    "have",
    "has",
    "are",
    "was",
    "been",
    "than",
    "also",
    "such",
    "these",
    "those",
    "which",
    "while",
    "where",
    "when",
    "what",
    "how",
}


def tokens(text: str) -> set:
    words = re.findall(
        r"[a-zA-Z][a-zA-Z0-9_-]{2,}",
        (text or "").lower(),
    )

    return {
        w
        for w in words
        if w not in STOPWORDS
    }


def relevance_score(
    objective: str,
    work: Dict[str, Any],
) -> float:

    query_tokens = tokens(
        objective
    )

    text_tokens = tokens(
        " ".join(
            [
                work.get("title", ""),
                work.get("abstract", ""),
                work.get("publisher", ""),
            ]
        )
    )

    if not query_tokens or not text_tokens:
        return 0.0

    overlap = (
        len(query_tokens & text_tokens)
        / max(len(query_tokens), 1)
    )

    return round(
        min(overlap, 1.0),
        4,
    )


# ============================================================
# ENRICHMENT
# ============================================================

def enrich_work(
    raw: Dict[str, Any],
    objective: str,
    mission_id: str,
) -> Dict[str, Any]:

    work = dict(raw)

    url = clean_text(
        work.get("url")
    )

    doi = clean_text(
        work.get("doi")
    ).lower()

    if not url and doi:
        url = (
            "https://doi.org/"
            + doi
        )

    work["url"] = url

    work["doi"] = doi

    work["title"] = clean_text(
        work.get("title")
    )

    work["abstract"] = clean_text(
        work.get("abstract")
    )

    work["publisher"] = clean_text(
        work.get("publisher")
    )

    work["domain"] = domain_of(
        url
    )

    work["source_family"] = (
        normalized_source_family(
            work["domain"],
            work["publisher"],
        )
    )

    work["relevance"] = (
        relevance_score(
            objective,
            work,
        )
    )

    if (
        not work["title"]
        and not work["abstract"]
    ):
        work["usable"] = False
        work["rejected_reason"] = (
            "missing_title_and_text"
        )
        return work

    if not work["abstract"] and url:

        publisher_text, error = (
            http_get_text(url)
        )

        if publisher_text:
            work["abstract"] = (
                publisher_text[:MAX_TEXT_LENGTH]
            )

        elif error:
            log_diagnostic(
                mission_id,
                "publisher_fallback",
                "info",
                error,
                {
                    "work_id": work.get(
                        "work_id"
                    )
                },
            )

    if not work["abstract"]:

        work["usable"] = False
        work["rejected_reason"] = (
            "no_research_text"
        )

        return work

    if len(work["abstract"]) < 120:

        work["usable"] = False
        work["rejected_reason"] = (
            "insufficient_research_text"
        )

        return work

    work["usable"] = True
    work["rejected_reason"] = ""

    return work


# ============================================================
# SENTENCE / CLAIM PROCESSING
# ============================================================

def split_sentences(
    text: str,
) -> List[str]:

    text = clean_text(
        text,
        MAX_TEXT_LENGTH,
    )

    pieces = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    return [
        clean_text(p, 1500)
        for p in pieces
        if len(clean_text(p)) >= 45
    ]


def is_noise(
    sentence: str,
) -> bool:

    s = (
        sentence
        or ""
    ).lower()

    bad = (
        "copyright",
        "all rights reserved",
        "download pdf",
        "supplementary material",
        "references",
        "table of contents",
        "cookie policy",
        "sign in",
        "subscribe",
        "citation",
        "doi:",
    )

    return any(
        x in s
        for x in bad
    )


def claim_family(
    text: str,
) -> str:

    s = (
        text
        or ""
    ).lower()

    if any(
        x in s
        for x in (
            "success",
            "completion",
            "accuracy",
            "performance",
        )
    ):
        return "task_performance"

    if any(
        x in s
        for x in (
            "failure",
            "error",
            "fail",
        )
    ):
        return "failure"

    if any(
        x in s
        for x in (
            "planning",
            "reason",
            "reasoning",
        )
    ):
        return "planning_reasoning"

    if any(
        x in s
        for x in (
            "browser",
            "web",
            "api",
            "tool",
        )
    ):
        return "tool_use"

    if any(
        x in s
        for x in (
            "safety",
            "security",
            "attack",
        )
    ):
        return "safety_security"

    if any(
        x in s
        for x in (
            "monitor",
            "verification",
            "verify",
            "evaluation",
        )
    ):
        return "monitoring_verification"

    if any(
        x in s
        for x in (
            "multi-agent",
            "multi agent",
            "coordination",
        )
    ):
        return "multi_agent"

    return "general"


def polarity(
    text: str,
) -> str:

    s = (
        text
        or ""
    ).lower()

    negative = (
        "not",
        "no ",
        "cannot",
        "can't",
        "unable",
        "fails",
        "failure",
        "limited",
        "poor",
        "degrades",
        "unsafe",
        "unreliable",
    )

    if any(
        x in s
        for x in negative
    ):
        return "negative"

    return "positive_or_neutral"


def extract_claims(
    work: Dict[str, Any],
) -> List[Dict[str, Any]]:

    claims = []

    sentences = split_sentences(
        work.get("abstract", "")
    )

    for sentence in sentences:

        if is_noise(sentence):
            continue

        word_count = len(
            sentence.split()
        )

        if word_count < 8:
            continue

        if word_count > 80:
            continue

        family = claim_family(
            sentence
        )

        confidence = min(
            0.95,
            0.40
            + min(
                len(sentence.split()) / 100,
                0.30,
            )
            + (
                0.20
                if any(
                    x in sentence.lower()
                    for x in (
                        "%",
                        "benchmark",
                        "experiment",
                        "evaluation",
                        "study",
                    )
                )
                else 0
            ),
        )

        claims.append(
            {
                "claim_id": make_id(
                    "claim",
                    (
                        work.get("work_id", "")
                        + "|"
                        + sentence
                    ),
                ),
                "work_id": work.get(
                    "work_id"
                ),
                "text": sentence,
                "claim_family": family,
                "polarity": polarity(
                    sentence
                ),
                "confidence": round(
                    confidence,
                    4,
                ),
            }
        )

        if len(claims) >= MAX_CLAIMS_PER_WORK:
            break

    return claims


# ============================================================
# EVIDENCE
# ============================================================

def lexical_similarity(
    a: str,
    b: str,
) -> float:

    ta = tokens(a)
    tb = tokens(b)

    if not ta or not tb:
        return 0.0

    intersection = (
        len(ta & tb)
    )

    union = (
        len(ta | tb)
    )

    if union == 0:
        return 0.0

    return round(
        intersection / union,
        4,
    )


def evidence_relation(
    claim: str,
    sentence: str,
) -> Tuple[str, float]:

    similarity = lexical_similarity(
        claim,
        sentence,
    )

    if similarity < 0.10:
        return "insufficient", 0.0

    claim_tokens = tokens(claim)
    sentence_tokens = tokens(sentence)

    if not claim_tokens:
        return "insufficient", 0.0

    containment = (
        len(
            claim_tokens
            & sentence_tokens
        )
        / len(claim_tokens)
    )

    entailment = (
        0.55 * similarity
        + 0.45 * containment
    )

    if entailment >= 0.60:
        relation = "strong_support"
    elif entailment >= 0.40:
        relation = "weak_support"
    else:
        relation = "insufficient"

    return (
        relation,
        round(
            min(entailment, 1.0),
            4,
        ),
    )


def build_evidence(
    claims: List[Dict[str, Any]],
    works: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    TARGET-2050.41 critical change:

    Search evidence across ALL usable works.

    TARGET-2050.40 searched the originating work,
    which made independent-work verification
    structurally impossible.
    """

    evidence = []

    ordered_works = [
        w
        for w in works
        if (
            w.get("usable")
            and w.get("abstract")
        )
    ]

    for claim in claims:

        candidates = []

        for work in ordered_works:

            for sentence in split_sentences(
                work.get("abstract", "")
            ):

                if is_noise(sentence):
                    continue

                similarity = lexical_similarity(
                    claim["text"],
                    sentence,
                )

                if similarity <= 0:
                    continue

                relation, entailment = (
                    evidence_relation(
                        claim["text"],
                        sentence,
                    )
                )

                if relation == "insufficient":
                    continue

                strength = round(
                    (
                        0.45 * similarity
                        + 0.55 * entailment
                    ),
                    4,
                )

                candidates.append(
                    (
                        strength,
                        similarity,
                        entailment,
                        relation,
                        work.get("work_id"),
                        sentence,
                    )
                )

        candidates.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        selected = []
        selected_work_ids = set()

        for candidate in candidates:

            work_id = candidate[4]

            if work_id in selected_work_ids:
                continue

            selected.append(candidate)
            selected_work_ids.add(work_id)

            if (
                len(selected)
                >= MAX_EVIDENCE_PER_CLAIM
            ):
                break

        for (
            strength,
            similarity,
            entailment,
            relation,
            work_id,
            sentence,
        ) in selected:

            evidence.append(
                {
                    "evidence_id": make_id(
                        "evidence",
                        (
                            f"{claim['claim_id']}"
                            f"|{work_id}"
                            f"|{sentence}"
                        ),
                    ),
                    "claim_id": claim[
                        "claim_id"
                    ],
                    "work_id": work_id,
                    "excerpt": sentence,
                    "lexical_similarity": (
                        similarity
                    ),
                    "entailment": entailment,
                    "relation": relation,
                    "strength": strength,
                }
            )

    return evidence


# ============================================================
# CONTRADICTIONS
# ============================================================

def contradiction_pair(
    a: Dict[str, Any],
    b: Dict[str, Any],
) -> bool:

    if (
        a.get("claim_family")
        != b.get("claim_family")
    ):
        return False

    sim = lexical_similarity(
        a.get("text", ""),
        b.get("text", ""),
    )

    if sim < 0.45:
        return False

    return (
        a.get("polarity")
        != b.get("polarity")
    )


def detect_contradictions(
    claims: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    contradictions = []

    for i in range(
        len(claims)
    ):

        for j in range(
            i + 1,
            len(claims),
        ):

            a = claims[i]
            b = claims[j]

            if (
                a.get("work_id")
                == b.get("work_id")
            ):
                continue

            if not contradiction_pair(
                a,
                b,
            ):
                continue

            similarity = lexical_similarity(
                a.get("text", ""),
                b.get("text", ""),
            )

            contradictions.append(
                {
                    "claim_a": a.get(
                        "claim_id"
                    ),
                    "claim_b": b.get(
                        "claim_id"
                    ),
                    "reason": (
                        "High lexical overlap "
                        "with opposite polarity."
                    ),
                    "severity": round(
                        similarity,
                        4,
                    ),
                }
            )

    return contradictions


# ============================================================
# VERIFICATION
# ============================================================

def verification_for_claim(
    claim: Dict[str, Any],
    evidence: List[Dict[str, Any]],
    works: List[Dict[str, Any]],
    contradictions: List[Dict[str, Any]],
) -> Dict[str, Any]:

    claim_id = claim.get(
        "claim_id"
    )

    work_map = {
        w.get("work_id"): w
        for w in works
    }

    claim_evidence = [
        e
        for e in evidence
        if e.get("claim_id")
        == claim_id
    ]

    strong = [
        e
        for e in claim_evidence
        if (
            e.get("relation")
            == "strong_support"
            and e.get("strength", 0)
            >= 0.60
        )
    ]

    work_ids = {
        e.get("work_id")
        for e in strong
        if e.get("work_id")
    }

    domains = {
        work_map[w].get("domain")
        for w in work_ids
        if (
            w in work_map
            and work_map[w].get("domain")
        )
    }

    families = {
        work_map[w].get(
            "source_family"
        )
        for w in work_ids
        if (
            w in work_map
            and work_map[w].get(
                "source_family"
            )
        )
    }

    contradiction_ids = set()

    for c in contradictions:

        if (
            c.get("claim_a")
            == claim_id
        ):
            contradiction_ids.add(
                c.get("claim_b")
            )

        if (
            c.get("claim_b")
            == claim_id
        ):
            contradiction_ids.add(
                c.get("claim_a")
            )

    blockers = []

    if len(work_ids) < 2:
        blockers.append(
            "requires_2_independent_works"
        )

    if len(domains) < 2:
        blockers.append(
            "requires_2_independent_domains"
        )

    if len(families) < 2:
        blockers.append(
            "requires_2_independent_source_families"
        )

    if len(strong) < 2:
        blockers.append(
            "requires_2_strong_evidence_relationships"
        )

    if contradiction_ids:
        blockers.append(
            "unresolved_contradiction"
        )

    verified = (
        len(work_ids) >= 2
        and len(domains) >= 2
        and len(families) >= 2
        and len(strong) >= 2
        and not contradiction_ids
    )

    return {
        "claim_id": claim_id,
        "verified": verified,
        "strong_evidence_count": len(
            strong
        ),
        "independent_work_count": len(
            work_ids
        ),
        "independent_domain_count": len(
            domains
        ),
        "independent_source_family_count": len(
            families
        ),
        "contradictions": sorted(
            contradiction_ids
        ),
        "blockers": blockers,
    }


# ============================================================
# RECOVERY
# ============================================================

def recovery_needed(
    works: List[Dict[str, Any]],
    claims: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
) -> bool:

    usable = [
        w
        for w in works
        if w.get("usable")
    ]

    domains = {
        w.get("domain")
        for w in usable
        if w.get("domain")
    }

    families = {
        w.get("source_family")
        for w in usable
        if w.get("source_family")
    }

    strong_by_claim = defaultdict(set)

    for item in evidence:

        if (
            item.get("relation")
            == "strong_support"
            and item.get("strength", 0)
            >= 0.60
        ):
            strong_by_claim[
                item.get("claim_id")
            ].add(
                item.get("work_id")
            )

    cross_work_support = any(
        len(work_ids) >= 2
        for work_ids
        in strong_by_claim.values()
    )

    return (
        len(usable) < 4
        or len(claims) < 4
        or len(evidence) < 4
        or len(domains) < 2
        or len(families) < 2
        or not cross_work_support
    )


def recovery_queries() -> List[str]:

    return [
        "autonomous AI agents benchmark planning reasoning",
        "web agents browser task completion empirical study",
        "LLM agents API tool use reliability evaluation",
        "AI agent failure recovery monitoring verification",
        "multi-agent coordination empirical benchmark",
        "AI agent safety security production deployment",
        "autonomous agents real-world task success evaluation",
        "agent benchmark independent empirical study",
    ]


def perform_recovery(
    objective: str,
    mission_id: str,
    existing: List[Dict[str, Any]],
    round_number: int,
) -> List[Dict[str, Any]]:

    recovered = []

    existing_keys = {
        canonical_key(w)
        for w in existing
    }

    queries = recovery_queries()

    # Rotate queries by round so bounded recovery
    # explores different evidence families.
    if round_number > 1:
        queries = (
            queries[4:]
            + queries[:4]
        )

    for query in queries:

        cr, cr_errors = discover_crossref(
            query
        )

        oa, oa_errors = discover_openalex(
            query
        )

        batch = cr + oa

        for error in (
            cr_errors
            + oa_errors
        ):
            log_recovery(
                mission_id,
                round_number,
                "provider_recovery",
                query,
                error,
            )

        for work in batch:

            key = canonical_key(
                work
            )

            if not key:
                continue

            if key in existing_keys:
                continue

            existing_keys.add(key)
            recovered.append(work)

            if (
                len(recovered)
                >= MAX_RECOVERY_WORKS
            ):
                break

        if (
            len(recovered)
            >= MAX_RECOVERY_WORKS
        ):
            break

    log_recovery(
        mission_id,
        round_number,
        "diversity_recovery",
        "missing independent evidence diversity",
        f"recovered={len(recovered)}",
    )

    return recovered


# ============================================================
# DATABASE PERSISTENCE
# ============================================================

def create_mission(
    objective: str,
) -> str:

    mission_id = make_id(
        "mission",
        objective,
    )

    timestamp = now()

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (id, objective, status, version,
         build, result_json,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "running",
            VERSION,
            BUILD,
            None,
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
    result: Dict[str, Any],
) -> None:

    conn = db()

    conn.execute(
        """
        UPDATE missions
        SET status = ?,
            result_json = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            safe_json(result),
            now(),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()


def save_work(
    mission_id: str,
    work: Dict[str, Any],
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT OR REPLACE INTO works
        (
            mission_id,
            work_id,
            title,
            doi,
            url,
            domain,
            source_family,
            publisher,
            year,
            abstract,
            relevance,
            usable,
            rejected_reason,
            provenance_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            work.get("work_id"),
            work.get("title"),
            work.get("doi"),
            work.get("url"),
            work.get("domain"),
            work.get("source_family"),
            work.get("publisher"),
            work.get("year"),
            work.get("abstract"),
            work.get("relevance", 0),
            int(bool(work.get("usable"))),
            work.get(
                "rejected_reason"
            ),
            safe_json(
                {
                    "providers": work.get(
                        "providers",
                        [
                            work.get(
                                "provider"
                            )
                        ],
                    ),
                    "source_id": work.get(
                        "source_id"
                    ),
                    "host_organization": (
                        work.get(
                            "host_organization"
                        )
                    ),
                    "host_organization_name": (
                        work.get(
                            "host_organization_name"
                        )
                    ),
                }
            ),
        ),
    )

    conn.commit()
    conn.close()


def save_claim(
    mission_id: str,
    claim: Dict[str, Any],
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT OR REPLACE INTO claims
        (
            mission_id,
            claim_id,
            work_id,
            text,
            claim_family,
            polarity,
            confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            claim.get("claim_id"),
            claim.get("work_id"),
            claim.get("text"),
            claim.get("claim_family"),
            claim.get("polarity"),
            claim.get("confidence", 0),
        ),
    )

    conn.commit()
    conn.close()


def save_evidence(
    mission_id: str,
    item: Dict[str, Any],
) -> None:

    conn = db()

    conn.execute(
        """
        INSERT INTO evidence
        (
            mission_id,
            evidence_id,
            claim_id,
            work_id,
            excerpt,
            lexical_similarity,
            entailment,
            relation,
            strength
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            item.get("evidence_id"),
            item.get("claim_id"),
            item.get("work_id"),
            item.get("excerpt"),
            item.get(
                "lexical_similarity",
                0,
            ),
            item.get(
                "entailment",
                0,
            ),
            item.get("relation"),
            item.get("strength", 0),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# REPORT
# ============================================================

def build_report(
    objective: str,
    mission_id: str,
    discovered: List[Dict[str, Any]],
    canonical: List[Dict[str, Any]],
    usable: List[Dict[str, Any]],
    claims: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
    contradictions: List[Dict[str, Any]],
    verification: List[Dict[str, Any]],
    recovery_actions: List[Dict[str, Any]],
    diagnostics: List[Dict[str, Any]],
) -> Dict[str, Any]:

    verified = [
        v
        for v in verification
        if v.get("verified")
    ]

    supported_not_verified = [
        v
        for v in verification
        if (
            not v.get("verified")
            and v.get(
                "strong_evidence_count",
                0,
            ) > 0
        )
    ]

    insufficient = [
        v
        for v in verification
        if (
            not v.get("verified")
            and v.get(
                "strong_evidence_count",
                0,
            ) == 0
        )
    ]

    rejected = [
        {
            "work_id": w.get(
                "work_id"
            ),
            "title": w.get(
                "title"
            ),
            "reason": w.get(
                "rejected_reason"
            ),
        }
        for w in canonical
        if not w.get("usable")
    ]

    usable_count = len(usable)
    rejected_count = len(rejected)

    domains = sorted(
        {
            w.get("domain")
            for w in usable
            if w.get("domain")
        }
    )

    families = sorted(
        {
            w.get(
                "source_family"
            )
            for w in usable
            if w.get(
                "source_family"
            )
        }
    )

    strong_evidence = [
        e
        for e in evidence
        if (
            e.get("relation")
            == "strong_support"
            and e.get("strength", 0)
            >= 0.60
        )
    ]

    verification_rate = (
        round(
            len(verified)
            / len(claims),
            4,
        )
        if claims
        else 0
    )

    reality = {
        "verified_claim_count": len(
            verified
        ),
        "supported_but_unverified_count": len(
            supported_not_verified
        ),
        "insufficient_claim_count": len(
            insufficient
        ),
        "usable_source_count": usable_count,
        "independent_domain_count": len(
            domains
        ),
        "independent_source_family_count": len(
            families
        ),
        "strong_evidence_count": len(
            strong_evidence
        ),
        "contradiction_count": len(
            contradictions
        ),
        "verification_rate": verification_rate,
    }

    if len(verified) > 0:
        assessment = (
            "Some claims satisfy the strict "
            "independence and evidence closure "
            "requirements. This does not establish "
            "general autonomous real-world reliability."
        )
    else:
        assessment = (
            "Evidence is insufficient for a broad "
            "claim of general autonomous real-world "
            "reliability."
        )

    reality["assessment"] = assessment

    return {
        "mission_id": mission_id,
        "objective": objective,
        "version": VERSION,
        "build": BUILD,

        "metrics": {
            "discovered": len(
                discovered
            ),
            "canonicalized": len(
                canonical
            ),
            "usable_works": usable_count,
            "rejected_works": rejected_count,

            "accounting_check": (
                usable_count
                + rejected_count
                == len(canonical)
            ),

            "claims": len(
                claims
            ),
            "evidence": len(
                evidence
            ),
            "strong_evidence": len(
                strong_evidence
            ),
            "contradictions": len(
                contradictions
            ),
            "verified_claims": len(
                verified
            ),
            "supported_not_verified": len(
                supported_not_verified
            ),
            "insufficient_claims": len(
                insufficient
            ),
            "verification_rate": (
                verification_rate
            ),
        },

        "reality_assessment": reality,

        "verification_policy": {
            "minimum_independent_works": 2,
            "minimum_independent_domains": 2,
            "minimum_independent_source_families": 2,
            "minimum_strong_evidence": 2,
            "maximum_unresolved_contradictions": 0,
            "verification_is_strict": True,
            "verification_is_never_manufactured": True,
        },

        "independence": {
            "domains": domains,
            "source_families": families,
        },

        "sources": {
            "usable": usable,
            "rejected": rejected,
        },

        "claims": claims,

        "evidence": evidence,

        "contradictions": contradictions,

        "verification": verification,

        "recovery": recovery_actions,

        "diagnostics": diagnostics,

        "generated_at": iso_now(),
    }


# ============================================================
# MAIN RESEARCH ENGINE
# ============================================================

def run_research(
    objective: str,
    mission_id: str,
) -> Dict[str, Any]:

    diagnostics = []

    discovered = []
    canonical = []
    usable = []
    claims = []
    evidence = []
    contradictions = []
    verification = []
    recovery_actions = []

    # --------------------------------------------------------
    # DISCOVERY
    # --------------------------------------------------------

    log_event(
        mission_id,
        "discovery",
        "started",
    )

    queries = [
        "autonomous AI agents reliability empirical evaluation",
        "AI agents real world task execution benchmark",
        "autonomous agents planning reasoning task success",
        "AI agents tool API browser use evaluation",
        "AI agent failure recovery monitoring verification",
        "multi-agent coordination empirical evaluation",
    ]

    for query in queries:

        try:

            cr, cr_errors = discover_crossref(
                query
            )

            oa, oa_errors = discover_openalex(
                query
            )

            discovered.extend(cr)
            discovered.extend(oa)

            for error in (
                cr_errors
                + oa_errors
            ):

                diagnostics.append(
                    {
                        "stage": "discovery",
                        "level": "warning",
                        "message": error,
                        "query": query,
                    }
                )

                log_diagnostic(
                    mission_id,
                    "discovery",
                    "warning",
                    error,
                    {
                        "query": query
                    },
                )

        except Exception as exc:

            message = (
                f"{type(exc).__name__}: "
                f"{str(exc)[:300]}"
            )

            diagnostics.append(
                {
                    "stage": "discovery",
                    "level": "error",
                    "message": message,
                    "query": query,
                }
            )

            log_diagnostic(
                mission_id,
                "discovery",
                "error",
                message,
                {
                    "query": query
                },
            )

        if len(discovered) >= MAX_TOTAL_WORKS:
            break

    discovered = discovered[
        :MAX_TOTAL_WORKS
    ]

    # --------------------------------------------------------
    # CANONICALIZATION
    # --------------------------------------------------------

    canonical = canonicalize(
        discovered
    )

    canonical = canonical[
        :MAX_TOTAL_WORKS
    ]

    # --------------------------------------------------------
    # ENRICHMENT
    # --------------------------------------------------------

    for raw in canonical:

        try:

            work = enrich_work(
                raw,
                objective,
                mission_id,
            )

            save_work(
                mission_id,
                work,
            )

            if work.get("usable"):
                usable.append(work)

            else:
                log_diagnostic(
                    mission_id,
                    "ingestion",
                    "info",
                    "source rejected",
                    {
                        "work_id": work.get(
                            "work_id"
                        ),
                        "reason": work.get(
                            "rejected_reason"
                        ),
                    },
                )

        except Exception as exc:

            message = (
                f"{type(exc).__name__}: "
                f"{str(exc)[:300]}"
            )

            log_diagnostic(
                mission_id,
                "ingestion",
                "error",
                message,
                {
                    "work_id": raw.get(
                        "work_id"
                    )
                },
            )

    # --------------------------------------------------------
    # INITIAL CLAIMS / EVIDENCE
    # --------------------------------------------------------

    for work in usable:

        try:

            extracted = extract_claims(
                work
            )

            for claim in extracted:

                claims.append(claim)

                save_claim(
                    mission_id,
                    claim,
                )

        except Exception as exc:

            log_diagnostic(
                mission_id,
                "claim_extraction",
                "warning",
                str(exc)[:500],
            )

    evidence = build_evidence(
        claims,
        usable,
    )

    for item in evidence:

        try:
            save_evidence(
                mission_id,
                item,
            )
        except Exception:
            pass

    contradictions = detect_contradictions(
        claims
    )

    # --------------------------------------------------------
    # TARGETED RECOVERY
    # --------------------------------------------------------

    for round_number in range(
        1,
        MAX_RECOVERY_ROUNDS + 1,
    ):

        if not recovery_needed(
            usable,
            claims,
            evidence,
        ):
            break

        before = (
            len(usable),
            len(claims),
            len(evidence),
        )

        recovered = perform_recovery(
            objective,
            mission_id,
            usable,
            round_number,
        )

        recovered_canonical = canonicalize(
            canonical + recovered
        )

        existing_ids = {
            w.get("work_id")
            for w in usable
        }

        for raw in recovered_canonical:

            if (
                raw.get("work_id")
                in existing_ids
            ):
                continue

            try:

                work = enrich_work(
                    raw,
                    objective,
                    mission_id,
                )

                save_work(
                    mission_id,
                    work,
                )

                if work.get("usable"):

                    usable.append(work)

                    existing_ids.add(
                        work.get(
                            "work_id"
                        )
                    )

            except Exception as exc:

                log_diagnostic(
                    mission_id,
                    "recovery_ingestion",
                    "warning",
                    str(exc)[:500],
                )

        canonical = recovered_canonical[
            :MAX_TOTAL_WORKS
        ]

        # Rebuild from ALL usable works.
        claims = []

        for work in usable:

            try:

                for claim in extract_claims(
                    work
                ):

                    claims.append(claim)

                    save_claim(
                        mission_id,
                        claim,
                    )

            except Exception:
                continue

        evidence = build_evidence(
            claims,
            usable,
        )

        for item in evidence:

            try:
                save_evidence(
                    mission_id,
                    item,
                )
            except Exception:
                pass

        contradictions = (
            detect_contradictions(
                claims
            )
        )

        after = (
            len(usable),
            len(claims),
            len(evidence),
        )

        progress = (
            (after[0] - before[0])
            + (after[1] - before[1])
            + (after[2] - before[2])
        )

        action = {
            "round": round_number,
            "before": {
                "usable": before[0],
                "claims": before[1],
                "evidence": before[2],
            },
            "after": {
                "usable": after[0],
                "claims": after[1],
                "evidence": after[2],
            },
            "meaningful_progress": (
                progress > 0
            ),
        }

        recovery_actions.append(
            action
        )

        if progress <= 0:

            log_recovery(
                mission_id,
                round_number,
                "stop_recovery",
                "no meaningful progress",
                "stopped",
            )

            break

    # --------------------------------------------------------
    # FINAL VERIFICATION
    # --------------------------------------------------------

    verification = []

    for claim in claims:

        try:

            verification.append(
                verification_for_claim(
                    claim,
                    evidence,
                    usable,
                    contradictions,
                )
            )

        except Exception as exc:

            verification.append(
                {
                    "claim_id": claim.get(
                        "claim_id"
                    ),
                    "verified": False,
                    "blockers": [
                        "verification_error"
                    ],
                    "error": (
                        f"{type(exc).__name__}: "
                        f"{str(exc)[:300]}"
                    ),
                }
            )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        objective=objective,
        mission_id=mission_id,
        discovered=discovered,
        canonical=canonical,
        usable=usable,
        claims=claims,
        evidence=evidence,
        contradictions=contradictions,
        verification=verification,
        recovery_actions=recovery_actions,
        diagnostics=diagnostics,
    )

    log_event(
        mission_id,
        "complete",
        "research_completed",
        report.get(
            "metrics",
            {},
        ),
    )

    return report


# ============================================================
# API
# ============================================================

@app.get("/")
def root() -> Dict[str, Any]:

    return {
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "message": (
            "AI Infinity evidence research "
            "core is running."
        ),
        "endpoints": {
            "health": "/health",
            "status": "/status",
            "capabilities": "/capabilities",
            "docs": "/docs",
            "run": "POST /run",
            "run_help": "GET /run",
            "mission": (
                "GET /mission/{mission_id}"
            ),
        },
    }


@app.get("/health")
def health() -> Dict[str, Any]:

    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
    }


@app.head("/")
def head_root():
    return JSONResponse(
        content=None,
        status_code=200,
    )


@app.get("/status")
def status() -> Dict[str, Any]:

    return {
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "database": DB_PATH,
    }


@app.get("/capabilities")
def capabilities() -> Dict[str, Any]:

    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "mission_execution",
            "crossref_discovery",
            "openalex_discovery",
            "doi_canonicalization",
            "abstract_reconstruction",
            "publisher_fallback",
            "relevance_scoring",
            "atomic_claim_extraction",
            "cross_work_evidence",
            "evidence_relationships",
            "source_independence",
            "domain_independence",
            "source_family_independence",
            "contradiction_detection",
            "strict_verification",
            "diversity_aware_recovery",
            "auditable_reports",
            "sqlite_persistence",
            "fault_tolerant_networking",
            "diagnostic_logging",
            "accounting_integrity",
        ],
        "verification_policy": {
            "works": 2,
            "domains": 2,
            "source_families": 2,
            "strong_evidence": 2,
            "unresolved_contradictions": 0,
        },
    }


@app.get("/run")
def run_help() -> Dict[str, Any]:

    return {
        "status": "ready",
        "message": (
            "Use POST /run to start "
            "a research mission."
        ),
        "docs": "/docs",
        "example": {
            "objective": (
                "Research the reliability "
                "of autonomous AI agents "
                "for real-world task execution."
            )
        },
    }


@app.post("/run")
def run(request: RunRequest):

    objective = clean_text(
        request.objective,
        20000,
    )

    if len(objective) < 10:

        return JSONResponse(
            status_code=400,
            content={
                "status": "failed",
                "error": {
                    "type": "invalid_objective",
                    "message": (
                        "Objective must contain "
                        "at least 10 characters."
                    ),
                },
            },
        )

    mission_id = None
    stage = "initialization"

    try:

        mission_id = create_mission(
            objective
        )

        log_event(
            mission_id,
            "initialization",
            "mission_created",
        )

        stage = "research"

        result = run_research(
            objective,
            mission_id,
        )

        update_mission(
            mission_id,
            "completed",
            result,
        )

        return {
            "mission_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "build": BUILD,
            "metrics": result.get(
                "metrics",
                {},
            ),
            "reality_assessment": result.get(
                "reality_assessment",
                {},
            ),
            "mission_url": (
                f"/mission/{mission_id}"
            ),
        }

    except Exception as exc:

        error_type = type(exc).__name__
        error_message = str(exc)

        trace = traceback.format_exc()

        failure = {
            "version": VERSION,
            "build": BUILD,
            "mission_id": mission_id,
            "status": "failed",
            "stage": stage,
            "error": {
                "type": error_type,
                "message": error_message,
            },
        }

        if mission_id:

            log_diagnostic(
                mission_id,
                stage,
                "fatal",
                error_message,
                {
                    "type": error_type,
                    "traceback": trace[-6000:],
                },
            )

            update_mission(
                mission_id,
                "failed",
                failure,
            )

        return JSONResponse(
            status_code=500,
            content=failure,
        )


@app.get("/mission/{mission_id}")
def get_mission(
    mission_id: str,
):

    try:

        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM missions
            WHERE id = ?
            """,
            (mission_id,),
        ).fetchone()

        conn.close()

        if not row:

            return JSONResponse(
                status_code=404,
                content={
                    "error": "mission_not_found",
                    "mission_id": mission_id,
                },
            )

        result = None

        if row["result_json"]:

            try:
                result = json.loads(
                    row["result_json"]
                )
            except Exception:
                result = {
                    "raw": row[
                        "result_json"
                    ]
                }

        return {
            "mission_id": row["id"],
            "objective": row["objective"],
            "status": row["status"],
            "version": row["version"],
            "build": row["build"],
            "created_at": row[
                "created_at"
            ],
            "updated_at": row[
                "updated_at"
            ],
            "result": result,
        }

    except Exception as exc:

        return JSONResponse(
            status_code=500,
            content={
                "status": "failed",
                "error": {
                    "type": type(
                        exc
                    ).__name__,
                    "message": str(exc),
                },
            },
        )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup_event():

    init_db()

    print(
        f"{APP_NAME} {VERSION} "
        f"{BUILD} started."
    )
