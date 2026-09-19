"""
AI Infinity
TARGET-2050.40
BUILD: FAULT-TOLERANT-EVIDENCE-RESEARCH-CORE

Practical upgrade:
- Keeps FastAPI + SQLite
- Keeps Crossref + OpenAlex discovery
- Fault-tolerant external requests
- Safe JSON parsing
- Safe publisher fetching
- OpenAlex abstract reconstruction
- Relevance scoring without brittle hard rejection
- Conservative atomic claim extraction
- Evidence relationships
- Source/domain/source-family independence
- Contradiction detection
- Strict verification closure
- Bounded recovery
- Complete auditable report
- Persistent mission state
- Diagnostics on every failure
- /run GET gives usage instead of 405
- /run POST never crashes because of one bad provider
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import traceback
from collections import Counter, defaultdict
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

VERSION = "TARGET-2050.40"
BUILD = "FAULT-TOLERANT-EVIDENCE-RESEARCH-CORE"

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

USER_AGENT = (
    "AI-Infinity/2050.40 "
    "(evidence research system; contact unavailable)"
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
    objective: str = Field(..., min_length=10, max_length=20000)


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
# BASIC HELPERS
# ============================================================

def now() -> float:
    return time.time()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str, value: str = "") -> str:
    raw = f"{prefix}|{value}|{time.time_ns()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def clean_text(value: Any, limit: int = MAX_TEXT_LENGTH) -> str:
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except Exception:
            value = str(value)

    text = str(value)

    text = re.sub(r"\s+", " ", text).strip()

    return text[:limit]


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        host = host.split("@")[-1]
        host = host.split(":")[0]

        if host.startswith("www."):
            host = host[4:]

        return host
    except Exception:
        return ""


def source_family(domain: str, publisher: str = "") -> str:
    d = (domain or "").lower()
    p = (publisher or "").lower()

    if "doi.org" in d:
        return "doi"

    if "crossref.org" in d:
        return "crossref"

    if "openalex.org" in d:
        return "openalex"

    if "arxiv.org" in d:
        return "arxiv"

    if "acm.org" in d:
        return "acm"

    if "ieee.org" in d:
        return "ieee"

    if "springer" in d:
        return "springer"

    if "sciencedirect.com" in d:
        return "elsevier"

    if "elsevier" in p:
        return "elsevier"

    if "nature.com" in d:
        return "nature"

    if "frontiersin.org" in d:
        return "frontiers"

    if "plos.org" in d:
        return "plos"

    if "pubmed" in d:
        return "pubmed"

    if "semanticscholar" in d:
        return "semantic_scholar"

    return d or "unknown"


# ============================================================
# DATABASE LOGGING
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
            (mission_id, stage, level, message, details_json, created_at)
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
            (mission_id, round, action, reason, result, created_at)
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
        return None, f"request_error:{type(exc).__name__}"

    except Exception as exc:
        return None, f"{type(exc).__name__}:{str(exc)[:200]}"


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
                "Accept": "text/html,application/xhtml+xml,text/plain",
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

        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)

        return clean_text(text), None

    except requests.Timeout:
        return "", "timeout"

    except requests.RequestException as exc:
        return "", f"request_error:{type(exc).__name__}"

    except Exception as exc:
        return "", f"{type(exc).__name__}:{str(exc)[:200]}"


# ============================================================
# QUERY GENERATION
# ============================================================

def objective_terms(objective: str) -> List[str]:
    text = objective.lower()

    groups = {
        "autonomous agents": [
            "autonomous agent",
            "AI agent",
            "agent reliability",
            "agentic AI",
        ],
        "planning reasoning": [
            "planning",
            "reasoning",
            "task execution",
        ],
        "tool use": [
            "tool use",
            "API use",
            "browser use",
            "web agent",
        ],
        "task success": [
            "task completion",
            "success rate",
            "benchmark",
            "evaluation",
        ],
        "failure": [
            "failure",
            "error",
            "failure modes",
            "recovery",
        ],
        "safety": [
            "monitoring",
            "verification",
            "safety",
            "security",
        ],
        "production": [
            "production deployment",
            "real world",
            "real-world",
            "deployment",
        ],
        "multi agent": [
            "multi-agent",
            "multi agent",
            "coordination",
        ],
    }

    selected = []

    for values in groups.values():
        if any(v in text for v in values):
            selected.extend(values[:2])

    if not selected:
        selected = [
            "autonomous AI agents",
            "agent task execution",
            "AI agent reliability",
        ]

    # preserve order
    seen = set()
    output = []

    for item in selected:
        if item not in seen:
            seen.add(item)
            output.append(item)

    return output[:8]


def build_queries(objective: str) -> List[str]:
    terms = objective_terms(objective)

    queries = [
        "autonomous AI agents reliability empirical evaluation",
        "AI agents real world task execution benchmark",
        "autonomous agents planning reasoning task success",
        "AI agents tool API browser use evaluation",
        "AI agent failure recovery monitoring verification",
        "multi-agent coordination empirical evaluation",
    ]

    queries.extend(terms)

    # deduplicate
    result = []

    for q in queries:
        q = clean_text(q, 300)

        if q and q.lower() not in [x.lower() for x in result]:
            result.append(q)

    return result[:12]


# ============================================================
# OPENALEX ABSTRACT RECONSTRUCTION
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
                positions.append((position, str(word)))

    positions.sort(key=lambda x: x[0])

    words = [word for _, word in positions]

    return clean_text(" ".join(words))


# ============================================================
# DISCOVERY
# ============================================================

def discover_crossref(
    query: str,
    mission_id: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:

    diagnostics = []

    data, error = http_get_json(
        CROSSREF_URL,
        params={
            "query.bibliographic": query,
            "rows": MAX_DISCOVERY_PER_QUERY,
            "select": (
                "DOI,title,URL,published,published-print,"
                "published-online,author,publisher,abstract"
            ),
        },
    )

    if error:
        diagnostics.append(error)
        return [], diagnostics

    message = data.get("message")

    if not isinstance(message, dict):
        diagnostics.append("missing_crossref_message")
        return [], diagnostics

    items = message.get("items")

    if not isinstance(items, list):
        diagnostics.append("missing_crossref_items")
        return [], diagnostics

    works = []

    for item in items:
        if not isinstance(item, dict):
            continue

        title_value = item.get("title")

        if isinstance(title_value, list):
            title = title_value[0] if title_value else ""
        else:
            title = title_value or ""

        title = clean_text(title)

        doi = clean_text(item.get("DOI")).lower()

        url = clean_text(item.get("URL"))

        publisher = clean_text(item.get("publisher"))

        abstract = clean_text(item.get("abstract"))

        year = None

        for field in (
            "published",
            "published-print",
            "published-online",
        ):
            value = item.get(field)

            if isinstance(value, dict):
                date_parts = value.get("date-parts")

                if (
                    isinstance(date_parts, list)
                    and date_parts
                    and isinstance(date_parts[0], list)
                    and date_parts[0]
                    and isinstance(date_parts[0][0], int)
                ):
                    year = date_parts[0][0]
                    break

        works.append(
            {
                "provider": "crossref",
                "work_id": f"doi:{doi}" if doi else make_id("cr"),
                "title": title,
                "doi": doi,
                "url": url,
                "publisher": publisher,
                "abstract": abstract,
                "year": year,
            }
        )

    return works, diagnostics


def discover_openalex(
    query: str,
    mission_id: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:

    diagnostics = []

    data, error = http_get_json(
        OPENALEX_URL,
        params={
            "search": query,
            "per-page": MAX_DISCOVERY_PER_QUERY,
        },
    )

    if error:
        diagnostics.append(error)
        return [], diagnostics

    results = data.get("results")

    if not isinstance(results, list):
        diagnostics.append("missing_openalex_results")
        return [], diagnostics

    works = []

    for item in results:
        if not isinstance(item, dict):
            continue

        title = clean_text(item.get("title"))

        doi = clean_text(item.get("doi")).lower()

        primary_location = item.get("primary_location") or {}

        if not isinstance(primary_location, dict):
            primary_location = {}

        landing_page = clean_text(
            primary_location.get("landing_page_url")
        )

        source = primary_location.get("source") or {}

        if not isinstance(source, dict):
            source = {}

        publisher = clean_text(source.get("display_name"))

        url = landing_page or doi

        abstract = reconstruct_openalex_abstract(
            item.get("abstract_inverted_index")
        )

        year = item.get("publication_year")

        openalex_id = clean_text(item.get("id"))

        work_id = (
            f"doi:{doi}"
            if doi
            else f"openalex:{openalex_id}"
            if openalex_id
            else make_id("oa")
        )

        works.append(
            {
                "provider": "openalex",
                "work_id": work_id,
                "title": title,
                "doi": doi,
                "url": url,
                "publisher": publisher,
                "abstract": abstract,
                "year": year,
                "openalex_id": openalex_id,
            }
        )

    return works, diagnostics


# ============================================================
# CANONICALIZATION
# ============================================================

def canonical_key(work: Dict[str, Any]) -> str:
    doi = clean_text(work.get("doi")).lower()

    if doi:
        return f"doi:{doi}"

    title = clean_text(work.get("title")).lower()

    title = re.sub(r"[^a-z0-9]+", " ", title)

    title = re.sub(r"\s+", " ", title).strip()

    if title:
        return f"title:{title}"

    url = clean_text(work.get("url")).lower()

    return f"url:{url}" if url else make_id("unknown")


def canonicalize(
    works: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    merged: Dict[str, Dict[str, Any]] = {}

    for work in works:
        if not isinstance(work, dict):
            continue

        key = canonical_key(work)

        if key not in merged:
            merged[key] = dict(work)
            merged[key]["providers"] = [
                work.get("provider")
            ]
            continue

        existing = merged[key]

        providers = existing.setdefault("providers", [])

        provider = work.get("provider")

        if provider and provider not in providers:
            providers.append(provider)

        for field in (
            "title",
            "doi",
            "url",
            "publisher",
            "abstract",
            "year",
        ):
            if not existing.get(field) and work.get(field):
                existing[field] = work[field]

    return list(merged.values())


# ============================================================
# RELEVANCE
# ============================================================

RELEVANCE_TERMS = [
    "agent",
    "agents",
    "autonomous",
    "agentic",
    "planning",
    "reasoning",
    "task",
    "execution",
    "tool",
    "api",
    "browser",
    "web",
    "benchmark",
    "evaluation",
    "reliability",
    "failure",
    "recovery",
    "monitoring",
    "verification",
    "safety",
    "security",
    "deployment",
    "coordination",
    "multi-agent",
    "real-world",
]


def relevance_score(
    objective: str,
    title: str,
    text: str,
) -> float:

    combined = f"{title} {text}".lower()

    score = 0.0

    for term in RELEVANCE_TERMS:
        if term in combined:
            score += 0.045

    objective_words = set(
        re.findall(
            r"\b[a-z][a-z0-9-]{3,}\b",
            objective.lower(),
        )
    )

    document_words = set(
        re.findall(
            r"\b[a-z][a-z0-9-]{3,}\b",
            combined,
        )
    )

    if objective_words:
        overlap = len(objective_words & document_words)

        score += min(0.35, overlap * 0.025)

    if len(text) >= 250:
        score += 0.10

    if len(text) >= 700:
        score += 0.05

    return round(min(1.0, score), 4)


# ============================================================
# PUBLISHER FALLBACK
# ============================================================

def should_fetch_publisher(url: str) -> bool:
    if not url:
        return False

    domain = domain_of(url)

    blocked = {
        "",
        "api.crossref.org",
        "api.openalex.org",
        "openalex.org",
        "doi.org",
    }

    return domain not in blocked


def enrich_work(
    work: Dict[str, Any],
    objective: str,
    mission_id: str,
) -> Dict[str, Any]:

    work = dict(work)

    title = clean_text(work.get("title"))

    abstract = clean_text(work.get("abstract"))

    url = clean_text(work.get("url"))

    publisher_text = ""

    publisher_error = None

    if (
        len(abstract) < 250
        and should_fetch_publisher(url)
    ):
        publisher_text, publisher_error = http_get_text(url)

        if publisher_text:
            # Keep only a bounded amount of page text.
            abstract = clean_text(
                f"{abstract} {publisher_text}",
                MAX_TEXT_LENGTH,
            )

    work["title"] = title
    work["abstract"] = abstract

    work["domain"] = domain_of(url)

    work["source_family"] = source_family(
        work["domain"],
        clean_text(work.get("publisher")),
    )

    work["relevance"] = relevance_score(
        objective,
        title,
        abstract,
    )

    # Practical usability:
    # We do NOT require publisher access.
    # Title + meaningful abstract is enough to enter
    # evidence processing.
    work["usable"] = bool(
        title
        and len(abstract) >= 120
        and work["relevance"] >= 0.10
    )

    if not work["usable"]:
        reasons = []

        if not title:
            reasons.append("missing_title")

        if len(abstract) < 120:
            reasons.append("insufficient_text")

        if work["relevance"] < 0.10:
            reasons.append("low_relevance")

        work["rejected_reason"] = ",".join(reasons)

    else:
        work["rejected_reason"] = ""

    work["provenance"] = {
        "providers": work.get("providers", [work.get("provider")]),
        "publisher_fetch_attempted": bool(
            url and len(abstract) < 250
        ),
        "publisher_fetch_error": publisher_error,
        "retrieved_at": iso_now(),
    }

    return work


# ============================================================
# SENTENCE PROCESSING
# ============================================================

def split_sentences(text: str) -> List[str]:

    text = clean_text(text)

    if not text:
        return []

    parts = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    result = []

    for part in parts:
        part = clean_text(part, 1200)

        if len(part) < 45:
            continue

        if len(part.split()) < 8:
            continue

        result.append(part)

    return result


def is_noise(sentence: str) -> bool:

    lower = sentence.lower()

    noise = [
        "copyright",
        "all rights reserved",
        "cookie",
        "sign in",
        "log in",
        "javascript",
        "navigation",
        "subscribe",
        "references",
        "figure 1",
        "table 1",
        "doi:",
        "http://",
        "https://",
    ]

    return any(x in lower for x in noise)


# ============================================================
# CLAIM EXTRACTION
# ============================================================

def claim_family(text: str) -> str:

    lower = text.lower()

    if any(
        x in lower
        for x in ["planning", "reasoning"]
    ):
        return "planning_reasoning"

    if any(
        x in lower
        for x in ["tool", "api", "browser", "web"]
    ):
        return "tool_api_browser"

    if any(
        x in lower
        for x in ["success", "completion", "performance"]
    ):
        return "task_completion"

    if any(
        x in lower
        for x in ["failure", "error", "fail"]
    ):
        return "failure_modes"

    if any(
        x in lower
        for x in ["monitor", "verification", "verify"]
    ):
        return "monitoring_verification"

    if any(
        x in lower
        for x in ["safety", "security", "attack"]
    ):
        return "safety_security"

    if any(
        x in lower
        for x in ["multi-agent", "multi agent", "coordination"]
    ):
        return "multi_agent"

    if any(
        x in lower
        for x in ["deploy", "production", "real-world"]
    ):
        return "production"

    return "general"


def polarity(text: str) -> str:

    lower = text.lower()

    negative = [
        "failed",
        "failure",
        "poor",
        "unable",
        "error",
        "unsafe",
        "unreliable",
        "limitation",
        "limited",
        "vulnerable",
        "degraded",
        "did not",
        "does not",
        "cannot",
        "could not",
    ]

    positive = [
        "successful",
        "successfully",
        "improved",
        "effective",
        "reliable",
        "robust",
        "accurate",
        "outperformed",
        "achieved",
    ]

    neg = sum(1 for x in negative if x in lower)

    pos = sum(1 for x in positive if x in lower)

    if neg > pos:
        return "negative"

    if pos > neg:
        return "positive"

    return "neutral"


def extract_claims(
    work: Dict[str, Any],
) -> List[Dict[str, Any]]:

    text = clean_text(work.get("abstract"))

    sentences = split_sentences(text)

    claims = []

    for sentence in sentences:

        if is_noise(sentence):
            continue

        lower = sentence.lower()

        relevance_hits = sum(
            1
            for term in RELEVANCE_TERMS
            if term in lower
        )

        # Conservative gate.
        if relevance_hits < 1:
            continue

        if len(sentence) < 55:
            continue

        cid = make_id(
            "claim",
            f"{work.get('work_id')}|{sentence}",
        )

        confidence = min(
            0.95,
            0.45 + relevance_hits * 0.06,
        )

        claims.append(
            {
                "claim_id": cid,
                "work_id": work.get("work_id"),
                "text": sentence,
                "claim_family": claim_family(sentence),
                "polarity": polarity(sentence),
                "confidence": round(confidence, 4),
            }
        )

        if len(claims) >= MAX_CLAIMS_PER_WORK:
            break

    return claims


# ============================================================
# TEXT SIMILARITY / EVIDENCE
# ============================================================

def tokens(text: str) -> set:
    return set(
        re.findall(
            r"\b[a-z][a-z0-9-]{2,}\b",
            text.lower(),
        )
    )


def lexical_similarity(a: str, b: str) -> float:

    ta = tokens(a)
    tb = tokens(b)

    if not ta or not tb:
        return 0.0

    intersection = len(ta & tb)

    union = len(ta | tb)

    if union == 0:
        return 0.0

    return round(intersection / union, 4)


def entailment_score(
    claim: str,
    evidence: str,
) -> float:

    similarity = lexical_similarity(
        claim,
        evidence,
    )

    claim_tokens = tokens(claim)

    evidence_tokens = tokens(evidence)

    if not claim_tokens:
        return 0.0

    coverage = (
        len(claim_tokens & evidence_tokens)
        / len(claim_tokens)
    )

    score = (
        similarity * 0.45
        + coverage * 0.55
    )

    return round(min(1.0, score), 4)


def evidence_relation(
    claim: str,
    evidence: str,
) -> Tuple[str, float]:

    entailment = entailment_score(
        claim,
        evidence,
    )

    if entailment >= 0.70:
        return "strong_support", entailment

    if entailment >= 0.52:
        return "moderate_support", entailment

    if entailment >= 0.35:
        return "weak_support", entailment

    return "insufficient", entailment


def build_evidence(
    claims: List[Dict[str, Any]],
    works: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    work_map = {
        w.get("work_id"): w
        for w in works
    }

    evidence = []

    for claim in claims:

        work = work_map.get(
            claim.get("work_id")
        )

        if not work:
            continue

        sentences = split_sentences(
            work.get("abstract", "")
        )

        candidates = []

        for sentence in sentences:

            if is_noise(sentence):
                continue

            similarity = lexical_similarity(
                claim["text"],
                sentence,
            )

            if similarity <= 0:
                continue

            relation, entailment = evidence_relation(
                claim["text"],
                sentence,
            )

            strength = round(
                0.45 * similarity
                + 0.55 * entailment,
                4,
            )

            candidates.append(
                (
                    strength,
                    similarity,
                    entailment,
                    relation,
                    sentence,
                )
            )

        candidates.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        for (
            strength,
            similarity,
            entailment,
            relation,
            sentence,
        ) in candidates[:MAX_EVIDENCE_PER_CLAIM]:

            if relation == "insufficient":
                continue

            evidence.append(
                {
                    "evidence_id": make_id(
                        "evidence",
                        f"{claim['claim_id']}|{sentence}",
                    ),
                    "claim_id": claim["claim_id"],
                    "work_id": claim["work_id"],
                    "excerpt": sentence,
                    "lexical_similarity": similarity,
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
) -> Optional[Dict[str, Any]]:

    if a.get("claim_family") != b.get("claim_family"):
        return None

    if a.get("work_id") == b.get("work_id"):
        return None

    similarity = lexical_similarity(
        a.get("text", ""),
        b.get("text", ""),
    )

    if similarity < 0.20:
        return None

    pa = a.get("polarity")
    pb = b.get("polarity")

    if (
        {pa, pb}
        == {"positive", "negative"}
    ):
        return {
            "claim_a": a.get("claim_id"),
            "claim_b": b.get("claim_id"),
            "reason": (
                "same claim family with "
                "opposite polarity and lexical overlap"
            ),
            "severity": round(
                min(1.0, similarity + 0.30),
                4,
            ),
        }

    return None


def detect_contradictions(
    claims: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    contradictions = []

    # Compare bounded pairs.
    for i in range(len(claims)):

        for j in range(i + 1, len(claims)):

            result = contradiction_pair(
                claims[i],
                claims[j],
            )

            if result:
                contradictions.append(result)

    return contradictions[:100]


# ============================================================
# INDEPENDENCE / VERIFICATION
# ============================================================

def verification_for_claim(
    claim: Dict[str, Any],
    evidence: List[Dict[str, Any]],
    works: List[Dict[str, Any]],
    contradictions: List[Dict[str, Any]],
) -> Dict[str, Any]:

    work_map = {
        w.get("work_id"): w
        for w in works
    }

    claim_evidence = [
        e
        for e in evidence
        if e.get("claim_id")
        == claim.get("claim_id")
    ]

    strong = [
        e
        for e in claim_evidence
        if e.get("relation")
        == "strong_support"
        and e.get("strength", 0) >= 0.60
    ]

    work_ids = set(
        e.get("work_id")
        for e in strong
    )

    domains = set()
    families = set()

    for work_id in work_ids:

        work = work_map.get(work_id)

        if not work:
            continue

        if work.get("domain"):
            domains.add(work["domain"])

        if work.get("source_family"):
            families.add(work["source_family"])

    unresolved = []

    for c in contradictions:

        if (
            c.get("claim_a")
            == claim.get("claim_id")
            or c.get("claim_b")
            == claim.get("claim_id")
        ):
            unresolved.append(c)

    requirements = {
        "two_independent_works": len(work_ids) >= 2,
        "two_independent_domains": len(domains) >= 2,
        "two_independent_source_families": len(families) >= 2,
        "two_strong_evidence_relationships": len(strong) >= 2,
        "no_unresolved_contradiction": len(unresolved) == 0,
    }

    verified = all(requirements.values())

    blockers = [
        key
        for key, value in requirements.items()
        if not value
    ]

    return {
        "claim_id": claim.get("claim_id"),
        "verified": verified,
        "requirements": requirements,
        "blockers": blockers,
        "strong_evidence_count": len(strong),
        "independent_work_count": len(work_ids),
        "independent_domain_count": len(domains),
        "independent_source_family_count": len(families),
        "domains": sorted(domains),
        "source_families": sorted(families),
        "unresolved_contradictions": unresolved,
    }


# ============================================================
# PERSISTENCE
# ============================================================

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
            1 if work.get("usable") else 0,
            work.get("rejected_reason"),
            safe_json(work.get("provenance", {})),
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
            item.get("lexical_similarity", 0),
            item.get("entailment", 0),
            item.get("relation"),
            item.get("strength", 0),
        ),
    )

    conn.commit()
    conn.close()


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
        (
            id,
            objective,
            status,
            version,
            build,
            result_json,
            created_at,
            updated_at
        )
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
    result: Optional[Dict[str, Any]] = None,
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
            safe_json(result) if result is not None else None,
            now(),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# RECOVERY
# ============================================================

def recovery_needed(
    works: List[Dict[str, Any]],
    claims: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
) -> bool:

    usable = [
        w for w in works
        if w.get("usable")
    ]

    return (
        len(usable) < 4
        or len(claims) < 4
        or len(evidence) < 4
    )


def perform_recovery(
    objective: str,
    mission_id: str,
    existing_works: List[Dict[str, Any]],
    round_number: int,
) -> List[Dict[str, Any]]:

    recovery_queries = [
        "AI agent benchmark failure recovery",
        "autonomous web agents empirical study",
        "LLM agents real world task success",
        "agent reliability tool use evaluation",
        "AI agent safety verification monitoring",
        "multi agent coordination benchmark",
    ]

    # Rotate query selection between rounds.
    if round_number > 1:
        recovery_queries = list(
            reversed(recovery_queries)
        )

    recovered = []

    for query in recovery_queries[:4]:

        crossref, cross_diag = discover_crossref(
            query,
            mission_id,
        )

        openalex, open_diag = discover_openalex(
            query,
            mission_id,
        )

        for diagnostic in (
            cross_diag + open_diag
        ):
            log_diagnostic(
                mission_id,
                "recovery",
                "info",
                diagnostic,
            )

        recovered.extend(crossref)
        recovered.extend(openalex)

        if len(recovered) >= 20:
            break

    log_recovery(
        mission_id,
        round_number,
        "targeted_research_recovery",
        "verification inputs were below threshold",
        f"discovered {len(recovered)} additional candidate works",
    )

    return recovered


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

    supported_unverified = [
        v
        for v in verification
        if not v.get("verified")
        and v.get("strong_evidence_count", 0) >= 1
    ]

    insufficient = [
        v
        for v in verification
        if v.get("strong_evidence_count", 0) == 0
    ]

    domains = sorted(
        {
            w.get("domain")
            for w in usable
            if w.get("domain")
        }
    )

    families = sorted(
        {
            w.get("source_family")
            for w in usable
            if w.get("source_family")
        }
    )

    rejected = [
        {
            "work_id": w.get("work_id"),
            "title": w.get("title"),
            "reason": w.get("rejected_reason"),
        }
        for w in canonical
        if not w.get("usable")
    ]

    claim_families = Counter(
        c.get("claim_family")
        for c in claims
    )

    reality = {
        "verified_claim_count": len(verified),
        "supported_but_unverified_count": len(
            supported_unverified
        ),
        "insufficient_claim_count": len(
            insufficient
        ),
        "usable_source_count": len(usable),
        "independent_domain_count": len(domains),
        "independent_source_family_count": len(families),
        "contradiction_count": len(contradictions),
        "verification_rate": round(
            (
                len(verified) / len(claims)
                if claims
                else 0
            ),
            4,
        ),
        "assessment": (
            "Evidence is insufficient for a broad "
            "claim of general autonomous real-world "
            "reliability."
            if len(verified) == 0
            else
            "Some narrow claims satisfy the configured "
            "verification closure, but this does not "
            "establish AGI, ASI, general autonomy, or "
            "continuous self-improvement."
        ),
    }

    return {
        "version": VERSION,
        "build": BUILD,
        "mission_id": mission_id,
        "objective": objective,
        "status": "completed",
        "generated_at": iso_now(),

        "metrics": {
            "discovered": len(discovered),
            "canonicalized": len(canonical),
            "usable_works": len(usable),
            "rejected_works": len(rejected),
            "claims": len(claims),
            "evidence": len(evidence),
            "contradictions": len(contradictions),
            "verified_claims": len(verified),
            "supported_not_verified": len(
                supported_unverified
            ),
            "insufficient_claims": len(insufficient),
            "verification_rate": reality[
                "verification_rate"
            ],
        },

        "sources": canonical,

        "rejected_sources": rejected,

        "usable_sources": usable,

        "domains": domains,

        "source_families": families,

        "atomic_claims": claims,

        "evidence": evidence,

        "claim_families": dict(claim_families),

        "contradictions": contradictions,

        "verification": verification,

        "verified_claims": verified,

        "supported_but_unverified_claims":
            supported_unverified,

        "insufficient_claims": insufficient,

        "recovery_actions": recovery_actions,

        "diagnostics": diagnostics,

        "independence_matrix": [
            {
                "claim_id": v.get("claim_id"),
                "works": v.get(
                    "independent_work_count",
                    0,
                ),
                "domains": v.get(
                    "independent_domain_count",
                    0,
                ),
                "source_families": v.get(
                    "independent_source_family_count",
                    0,
                ),
            }
            for v in verification
        ],

        "verification_policy": {
            "minimum_independent_works": 2,
            "minimum_independent_domains": 2,
            "minimum_independent_source_families": 2,
            "minimum_strong_evidence": 2,
            "unresolved_contradictions_allowed": 0,
            "policy": (
                "A claim is VERIFIED only when "
                "every requirement is satisfied."
            ),
        },

        "reality_assessment": reality,
    }


# ============================================================
# MAIN RESEARCH ENGINE
# ============================================================

def run_research(
    objective: str,
    mission_id: str,
) -> Dict[str, Any]:

    diagnostics: List[Dict[str, Any]] = []

    discovered: List[Dict[str, Any]] = []

    canonical: List[Dict[str, Any]] = []

    usable: List[Dict[str, Any]] = []

    claims: List[Dict[str, Any]] = []

    evidence: List[Dict[str, Any]] = []

    contradictions: List[Dict[str, Any]] = []

    verification: List[Dict[str, Any]] = []

    recovery_actions: List[Dict[str, Any]] = []

    # --------------------------------------------------------
    # DISCOVERY
    # --------------------------------------------------------

    log_event(
        mission_id,
        "discovery",
        "started",
    )

    queries = build_queries(objective)

    for query in queries:

        try:
            cr, cr_errors = discover_crossref(
                query,
                mission_id,
            )

            oa, oa_errors = discover_openalex(
                query,
                mission_id,
            )

            discovered.extend(cr)
            discovered.extend(oa)

            for error in cr_errors + oa_errors:
                item = {
                    "stage": "discovery",
                    "level": "warning",
                    "message": error,
                    "query": query,
                }

                diagnostics.append(item)

                log_diagnostic(
                    mission_id,
                    "discovery",
                    "warning",
                    error,
                    {"query": query},
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
                {"query": query},
            )

        if len(discovered) >= MAX_TOTAL_WORKS:
            break

    discovered = discovered[:MAX_TOTAL_WORKS]

    # --------------------------------------------------------
    # CANONICALIZATION
    # --------------------------------------------------------

    log_event(
        mission_id,
        "canonicalization",
        "started",
        {"discovered": len(discovered)},
    )

    canonical = canonicalize(discovered)

    canonical = canonical[:MAX_TOTAL_WORKS]

    # --------------------------------------------------------
    # ENRICHMENT
    # --------------------------------------------------------

    for raw_work in canonical:

        try:

            work = enrich_work(
                raw_work,
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
                        "work_id": work.get("work_id"),
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

            diagnostics.append(
                {
                    "stage": "ingestion",
                    "level": "error",
                    "message": message,
                    "work_id": raw_work.get(
                        "work_id"
                    ),
                }
            )

            log_diagnostic(
                mission_id,
                "ingestion",
                "error",
                message,
                {
                    "work_id": raw_work.get(
                        "work_id"
                    )
                },
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

        # Enrich only new candidates.
        existing_ids = {
            w.get("work_id")
            for w in usable
        }

        for raw_work in recovered_canonical:

            if (
                raw_work.get("work_id")
                in existing_ids
            ):
                continue

            try:

                work = enrich_work(
                    raw_work,
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
                        work.get("work_id")
                    )

            except Exception as exc:

                message = (
                    f"{type(exc).__name__}: "
                    f"{str(exc)[:300]}"
                )

                log_diagnostic(
                    mission_id,
                    "recovery_ingestion",
                    "warning",
                    message,
                )

        canonical = recovered_canonical[
            :MAX_TOTAL_WORKS
        ]

        # Rebuild claims/evidence after recovery.
        claims = []

        for work in usable:

            try:

                extracted = extract_claims(work)

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
            except Exception as exc:
                log_diagnostic(
                    mission_id,
                    "evidence_persistence",
                    "warning",
                    str(exc)[:500],
                )

        contradictions = detect_contradictions(
            claims
        )

        after = (
            len(usable),
            len(claims),
            len(evidence),
        )

        progress = (
            after[0] - before[0]
            + after[1] - before[1]
            + after[2] - before[2]
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
            "meaningful_progress": progress > 0,
        }

        recovery_actions.append(action)

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
    # FINAL EXTRACTION IF RECOVERY WAS NOT NEEDED
    # --------------------------------------------------------

    if not claims:

        for work in usable:

            try:

                extracted = extract_claims(work)

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

    if not evidence:

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
    # VERIFICATION
    # --------------------------------------------------------

    for claim in claims:

        try:

            result = verification_for_claim(
                claim,
                evidence,
                usable,
                contradictions,
            )

            verification.append(result)

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
        report.get("metrics", {}),
    )

    return report


# ============================================================
# API ROUTES
# ============================================================

@app.get("/")
def root() -> Dict[str, Any]:

    return {
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "message": (
            "AI Infinity evidence research core "
            "is running."
        ),
        "endpoints": {
            "health": "/health",
            "status": "/status",
            "capabilities": "/capabilities",
            "docs": "/docs",
            "run": "POST /run",
            "run_help": "GET /run",
            "mission": "GET /mission/{mission_id}",
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
            "evidence_relationships",
            "source_independence",
            "domain_independence",
            "source_family_independence",
            "contradiction_detection",
            "strict_verification",
            "targeted_recovery",
            "auditable_reports",
            "sqlite_persistence",
            "fault_tolerant_networking",
            "diagnostic_logging",
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
            "Use POST /run to start a research mission."
        ),
        "docs": "/docs",
        "example": {
            "objective": (
                "Research the reliability of "
                "autonomous AI agents for real-world "
                "task execution."
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

        # Create mission FIRST so even a later failure
        # has a persistent identifier.
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

        # Compact top-level response while keeping the
        # complete report available from /mission/{id}.
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

            update_mission(
                mission_id,
                "failed",
                failure,
            )

            return JSONResponse(
                status_code=500,
                content=failure,
            )

        return JSONResponse(
            status_code=500,
            content={
                "version": VERSION,
                "build": BUILD,
                "status": "failed",
                "stage": stage,
                "error": {
                    "type": error_type,
                    "message": error_message,
                },
            },
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
                    "raw": row["result_json"]
                }

        return {
            "mission_id": row["id"],
            "objective": row["objective"],
            "status": row["status"],
            "version": row["version"],
            "build": row["build"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "result": result,
        }

    except Exception as exc:

        return JSONResponse(
            status_code=500,
            content={
                "status": "failed",
                "error": {
                    "type": type(exc).__name__,
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
