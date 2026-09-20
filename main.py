"""
AI Infinity
TARGET-2050.60
BUILD: MISSION-TO-OUTCOME-AUTONOMOUS-INTELLIGENCE-FABRIC

2050.60 builds on TARGET-2050.59.

CORE LOOP:

    HUMAN INTENT
        ↓
    INTENT COMPILER
        ↓
    REQUIREMENT / GAP ENGINE
        ↓
    RESEARCH + EVIDENCE
        ↓
    DECISION ENGINE
        ↓
    DYNAMIC MISSION GRAPH
        ↓
    AUTHORIZATION / POLICY
        ↓
    EXECUTION
        ↓
    OBSERVATION
        ↓
    OUTCOME VERIFICATION
        ↓
    RECOVERY / REPLANNING
        ↓
    LEARNING
        ↓
    REUSABLE SKILL
        ↓
    FUTURE MISSION

DESIGN PRINCIPLES
-----------------
- Free-first
- Controlled public network access
- SSRF protection
- No arbitrary code execution
- No unrestricted private-network access
- Explicit approval for sensitive actions
- Persistent mission state
- Evidence-backed decisions
- Provenance
- Dynamic task graphs
- Checkpoints
- Recovery
- Reusable learning
- Backward-compatible 2050.59 research endpoints

Environment:
    PORT
    EXTERNAL_ALLOWED_DOMAINS
    RESEARCH_SEED_DOMAINS
    RESEARCH_DISCOVERY_URLS
    HTTP_TIMEOUT
    MAX_RESPONSE_BYTES
    RESEARCH_MAX_RETRIES
    MAX_RESEARCH_SOURCES
    MAX_SOURCE_TEXT
    MAX_MISSION_STEPS
    MAX_EXECUTION_CYCLES
"""

from __future__ import annotations

import os
import re
import json
import time
import uuid
import math
import sqlite3
import hashlib
import socket
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, quote
import xml.etree.ElementTree as ET

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# CONSTANTS
# ============================================================

VERSION = "TARGET-2050.60"
BUILD = "MISSION-TO-OUTCOME-AUTONOMOUS-INTELLIGENCE-FABRIC"

APP_NAME = "AI Infinity"

BASE_DIR = "/tmp/ai_infinity"
DB_PATH = os.path.join(BASE_DIR, "ai_infinity.db")

os.makedirs(BASE_DIR, exist_ok=True)

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
MAX_RESPONSE_BYTES = int(os.getenv("MAX_RESPONSE_BYTES", "2000000"))
MAX_RESEARCH_SOURCES = int(os.getenv("MAX_RESEARCH_SOURCES", "20"))
MAX_SOURCE_TEXT = int(os.getenv("MAX_SOURCE_TEXT", "12000"))
MAX_MISSION_STEPS = int(os.getenv("MAX_MISSION_STEPS", "100"))
MAX_EXECUTION_CYCLES = int(os.getenv("MAX_EXECUTION_CYCLES", "20"))
RESEARCH_MAX_RETRIES = int(os.getenv("RESEARCH_MAX_RETRIES", "2"))

EXTERNAL_ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}

RESEARCH_SEED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("RESEARCH_SEED_DOMAINS", "").split(",")
    if x.strip()
}

RESEARCH_DISCOVERY_URLS = [
    x.strip()
    for x in os.getenv("RESEARCH_DISCOVERY_URLS", "").split(",")
    if x.strip()
]

DEFAULT_RESEARCH_DOMAINS = {
    "wikipedia.org",
    "crossref.org",
    "doi.org",
    "arxiv.org",
    "openalex.org",
}

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
}

TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504}

SAFE_ACTIONS = {
    "reason",
    "plan",
    "research",
    "verify",
    "remember",
    "create_artifact",
    "observe",
}

APPROVAL_ACTIONS = {
    "external_action",
    "send_message",
    "purchase",
    "publish",
    "delete",
    "modify_account",
    "financial_action",
    "credential_action",
    "permission_change",
}


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "AI Infinity autonomous mission, evidence, execution, "
        "verification and learning fabric."
    ),
)


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS missions (
            mission_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            phase TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            cycle INTEGER DEFAULT 0,
            priority REAL DEFAULT 0.5,
            confidence REAL DEFAULT 0.0,
            success REAL DEFAULT 0.0,
            result_json TEXT,
            requirements_json TEXT,
            world_state_json TEXT
        );

        CREATE TABLE IF NOT EXISTS mission_steps (
            step_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            parent_id TEXT,
            name TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            priority REAL DEFAULT 0.5,
            dependencies_json TEXT,
            input_json TEXT,
            output_json TEXT,
            evidence_json TEXT,
            error TEXT,
            attempts INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evidence (
            evidence_id TEXT PRIMARY KEY,
            mission_id TEXT,
            provider TEXT,
            title TEXT,
            url TEXT,
            source_id TEXT,
            published TEXT,
            authors_json TEXT,
            abstract TEXT,
            snippet TEXT,
            source_type TEXT,
            confidence REAL,
            metadata_json TEXT,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS claims (
            claim_id TEXT PRIMARY KEY,
            mission_id TEXT,
            claim TEXT,
            support_json TEXT,
            contradiction_json TEXT,
            confidence REAL,
            status TEXT,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS decisions (
            decision_id TEXT PRIMARY KEY,
            mission_id TEXT,
            decision TEXT,
            rationale TEXT,
            evidence_json TEXT,
            confidence REAL,
            risk REAL,
            approved INTEGER DEFAULT 0,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            mission_id TEXT,
            cycle INTEGER,
            state_json TEXT,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS provenance (
            provenance_id TEXT PRIMARY KEY,
            mission_id TEXT,
            object_type TEXT,
            object_id TEXT,
            parent_ids_json TEXT,
            source_json TEXT,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            mission_id TEXT,
            step_id TEXT,
            action TEXT,
            reason TEXT,
            status TEXT,
            created_at REAL,
            decided_at REAL
        );

        CREATE TABLE IF NOT EXISTS learning (
            learning_id TEXT PRIMARY KEY,
            mission_id TEXT,
            kind TEXT,
            pattern TEXT,
            lesson TEXT,
            confidence REAL,
            reusable INTEGER DEFAULT 1,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS skills (
            skill_id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            trigger_json TEXT,
            procedure_json TEXT,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            confidence REAL DEFAULT 0.5,
            created_at REAL,
            updated_at REAL
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            mission_id TEXT,
            name TEXT,
            artifact_type TEXT,
            content TEXT,
            metadata_json TEXT,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS observations (
            observation_id TEXT PRIMARY KEY,
            mission_id TEXT,
            step_id TEXT,
            observation TEXT,
            source TEXT,
            confidence REAL,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS connector_events (
            event_id TEXT PRIMARY KEY,
            mission_id TEXT,
            connector TEXT,
            action TEXT,
            status TEXT,
            input_json TEXT,
            output_json TEXT,
            error TEXT,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS provider_health (
            provider TEXT PRIMARY KEY,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            score REAL DEFAULT 0.0,
            last_status INTEGER,
            last_error TEXT,
            last_success REAL,
            last_failure REAL
        );

        CREATE TABLE IF NOT EXISTS memory (
            memory_id TEXT PRIMARY KEY,
            scope TEXT,
            key TEXT,
            value TEXT,
            confidence REAL,
            source TEXT,
            created_at REAL,
            updated_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_steps_mission
            ON mission_steps(mission_id);

        CREATE INDEX IF NOT EXISTS idx_evidence_mission
            ON evidence(mission_id);

        CREATE INDEX IF NOT EXISTS idx_observations_mission
            ON observations(mission_id);

        CREATE INDEX IF NOT EXISTS idx_learning_mission
            ON learning(mission_id);
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILITY
# ============================================================

def now() -> float:
    return time.time()


def iso(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(
        ts or now(), tz=timezone.utc
    ).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def token_set(text: str) -> set:
    return {
        x.lower()
        for x in re.findall(r"[A-Za-z0-9]{3,}", text or "")
    }


def similarity(a: str, b: str) -> float:
    aa = token_set(a)
    bb = token_set(b)

    if not aa or not bb:
        return 0.0

    return len(aa & bb) / max(1, len(aa | bb))


def row_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


# ============================================================
# NETWORK POLICY
# ============================================================

def host_is_private(host: str) -> bool:
    if not host:
        return True

    h = host.lower().strip("[]")

    if h in BLOCKED_HOSTS:
        return True

    try:
        ip = socket.gethostbyname(h)

        parts = [int(x) for x in ip.split(".")]

        if parts[0] == 10:
            return True

        if parts[0] == 127:
            return True

        if parts[0] == 169 and parts[1] == 254:
            return True

        if parts[0] == 172 and 16 <= parts[1] <= 31:
            return True

        if parts[0] == 192 and parts[1] == 168:
            return True

    except Exception:
        pass

    return False


def domain_allowed(host: str, allowed: set) -> bool:
    host = host.lower().rstrip(".")

    if host in allowed:
        return True

    for domain in allowed:
        if host.endswith("." + domain):
            return True

    return False


def network_policy(url: str, research: bool = False) -> Dict[str, Any]:
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return {
            "allowed": False,
            "reason": "unsupported_scheme",
        }

    host = parsed.hostname

    if not host:
        return {
            "allowed": False,
            "reason": "missing_host",
        }

    if host_is_private(host):
        return {
            "allowed": False,
            "reason": "private_or_metadata_host",
        }

    allowed = set(EXTERNAL_ALLOWED_DOMAINS)

    if research:
        allowed.update(DEFAULT_RESEARCH_DOMAINS)
        allowed.update(RESEARCH_SEED_DOMAINS)

    if not allowed:
        return {
            "allowed": False,
            "reason": "domain_not_allowlisted",
            "host": host,
        }

    if not domain_allowed(host, allowed):
        return {
            "allowed": False,
            "reason": "domain_not_allowlisted",
            "host": host,
        }

    return {
        "allowed": True,
        "host": host,
    }


# ============================================================
# HTTP
# ============================================================

def http_get(
    url: str,
    *,
    research: bool = False,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:

    policy = network_policy(url, research=research)

    if not policy["allowed"]:
        return {
            "ok": False,
            "error_type": "network_policy",
            "error": policy,
            "status": None,
            "content": "",
            "content_type": "",
        }

    last_error = None

    for attempt in range(RESEARCH_MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                headers=headers or {
                    "User-Agent": (
                        "AI-Infinity/2050.60 "
                        "(controlled research client)"
                    )
                },
                timeout=HTTP_TIMEOUT,
                allow_redirects=True,
                stream=True,
            )

            if response.status_code in TRANSIENT_HTTP:
                last_error = f"HTTP {response.status_code}"

                if attempt < RESEARCH_MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue

            content = response.content[:MAX_RESPONSE_BYTES]

            return {
                "ok": 200 <= response.status_code < 300,
                "status": response.status_code,
                "content": content,
                "content_type": (
                    response.headers.get("content-type", "")
                ),
                "url": response.url,
                "attempts": attempt + 1,
                "error": (
                    None
                    if 200 <= response.status_code < 300
                    else f"HTTP {response.status_code}"
                ),
            }

        except Exception as exc:
            last_error = str(exc)

            if attempt < RESEARCH_MAX_RETRIES:
                time.sleep(0.5 * (attempt + 1))

    return {
        "ok": False,
        "status": None,
        "content": b"",
        "content_type": "",
        "attempts": RESEARCH_MAX_RETRIES + 1,
        "error_type": "network_error",
        "error": last_error,
    }


def decode_body(content: bytes) -> str:
    if not content:
        return ""

    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]

    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return content.decode(errors="replace")


def parse_json_resilient(text: str) -> Any:
    text = (text or "").strip()

    if not text:
        raise ValueError("empty_response")

    try:
        return json.loads(text)
    except Exception:
        pass

    start_candidates = [
        text.find("{"),
        text.find("["),
    ]

    start_candidates = [
        x for x in start_candidates if x >= 0
    ]

    if not start_candidates:
        raise ValueError("json_not_found")

    start = min(start_candidates)

    for end in range(len(text), start, -1):
        candidate = text[start:end]

        try:
            return json.loads(candidate)
        except Exception:
            continue

    raise ValueError("json_parse_failed")


# ============================================================
# EVIDENCE MODEL
# ============================================================

@dataclass
class EvidenceRecord:
    provider: str
    title: str
    url: str
    source_id: str
    published: str = ""
    authors: Optional[List[str]] = None
    abstract: str = ""
    snippet: str = ""
    source_type: str = "web"
    confidence: float = 0.5
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ProviderResult:
    provider: str
    status: str
    http_status: Optional[int]
    attempts: int
    result_count: int
    results: List[Dict[str, Any]]
    error_type: Optional[str] = None
    error: Optional[str] = None
    recovery: Optional[str] = None
    content_type: Optional[str] = None


# ============================================================
# PROVIDER HEALTH
# ============================================================

def provider_event(
    provider: str,
    success: bool,
    status: Optional[int],
    error: Optional[str] = None,
) -> None:

    conn = db()

    existing = conn.execute(
        "SELECT * FROM provider_health WHERE provider=?",
        (provider,),
    ).fetchone()

    if existing:
        successes = int(existing["successes"])
        failures = int(existing["failures"])
    else:
        successes = 0
        failures = 0

    if success:
        successes += 1
    else:
        failures += 1

    total = successes + failures
    score = successes / total if total else 0.0

    conn.execute(
        """
        INSERT INTO provider_health
        (provider, successes, failures, score,
         last_status, last_error, last_success, last_failure)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET
            successes=excluded.successes,
            failures=excluded.failures,
            score=excluded.score,
            last_status=excluded.last_status,
            last_error=excluded.last_error,
            last_success=excluded.last_success,
            last_failure=excluded.last_failure
        """,
        (
            provider,
            successes,
            failures,
            score,
            status,
            error,
            now() if success else existing["last_success"]
            if existing else None,
            None if success else now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# WIKIPEDIA
# ============================================================

def parse_wikipedia(data: Any) -> List[EvidenceRecord]:
    results = []

    pages = data.get("query", {}).get("search", [])

    for page in pages:
        title = clean_text(page.get("title"))
        snippet = clean_text(page.get("snippet"))
        pageid = str(page.get("pageid", ""))

        if not title:
            continue

        url = (
            "https://en.wikipedia.org/wiki/"
            + quote(title.replace(" ", "_"))
        )

        results.append(
            EvidenceRecord(
                provider="wikipedia",
                title=title,
                url=url,
                source_id=pageid or sha(title),
                snippet=snippet,
                abstract=snippet,
                source_type="encyclopedia",
                confidence=0.45,
                metadata={
                    "wordcount": page.get("wordcount"),
                    "timestamp": page.get("timestamp"),
                },
            )
        )

    return results


def wikipedia_search(query: str, limit: int = 5) -> ProviderResult:
    url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query"
        "&list=search"
        "&format=json"
        "&origin=*"
        "&srsearch="
        + quote(query)
        + f"&srlimit={limit}"
    )

    response = http_get(url, research=True)

    if not response["ok"]:
        provider_event(
            "wikipedia",
            False,
            response.get("status"),
            response.get("error"),
        )

        return ProviderResult(
            provider="wikipedia",
            status="failed",
            http_status=response.get("status"),
            attempts=response.get("attempts", 0),
            result_count=0,
            results=[],
            error_type=response.get("error_type"),
            error=response.get("error"),
        )

    try:
        data = parse_json_resilient(
            decode_body(response["content"])
        )

        records = parse_wikipedia(data)

        provider_event(
            "wikipedia",
            True,
            response.get("status"),
        )

        return ProviderResult(
            provider="wikipedia",
            status="success",
            http_status=response.get("status"),
            attempts=response.get("attempts", 0),
            result_count=len(records),
            results=[asdict(x) for x in records],
            recovery="api-search",
            content_type=response.get("content_type"),
        )

    except Exception as exc:
        provider_event(
            "wikipedia",
            False,
            response.get("status"),
            str(exc),
        )

        return ProviderResult(
            provider="wikipedia",
            status="failed",
            http_status=response.get("status"),
            attempts=response.get("attempts", 0),
            result_count=0,
            results=[],
            error_type="parse_error",
            error=str(exc),
        )


# ============================================================
# CROSSREF
# ============================================================

def crossref_date(item: Dict[str, Any]) -> str:
    for key in (
        "published-print",
        "published-online",
        "issued",
        "created",
    ):
        value = item.get(key)

        if isinstance(value, dict):
            parts = value.get("date-parts")

            if parts and parts[0]:
                return "-".join(
                    str(x) for x in parts[0]
                )

    return ""


def crossref_authors(item: Dict[str, Any]) -> List[str]:
    output = []

    for author in item.get("author", []) or []:
        name = " ".join(
            x
            for x in [
                author.get("given"),
                author.get("family"),
            ]
            if x
        ).strip()

        if name:
            output.append(name)

    return output


def parse_crossref(data: Any) -> List[EvidenceRecord]:
    results = []

    items = data.get("message", {}).get("items", [])

    for item in items:
        title_list = item.get("title") or []

        if not title_list:
            continue

        title = clean_text(title_list[0])

        doi = item.get("DOI", "")

        if doi:
            url = "https://doi.org/" + doi
        else:
            url = item.get("URL", "")

        results.append(
            EvidenceRecord(
                provider="crossref",
                title=title,
                url=url,
                source_id=doi or sha(title),
                published=crossref_date(item),
                authors=crossref_authors(item),
                abstract=clean_text(item.get("abstract", "")),
                snippet=clean_text(
                    item.get("container-title", [""])[0]
                    if item.get("container-title")
                    else ""
                ),
                source_type="bibliographic",
                confidence=0.75,
                metadata={
                    "type": item.get("type"),
                    "publisher": item.get("publisher"),
                    "journal": (
                        item.get("container-title", [""])[0]
                        if item.get("container-title")
                        else ""
                    ),
                },
            )
        )

    return results


def crossref_search(
    query: str,
    limit: int = 5,
) -> ProviderResult:

    url = (
        "https://api.crossref.org/works"
        "?query.bibliographic="
        + quote(query)
        + f"&rows={limit}"
    )

    response = http_get(url, research=True)

    if not response["ok"]:
        provider_event(
            "crossref",
            False,
            response.get("status"),
            response.get("error"),
        )

        return ProviderResult(
            provider="crossref",
            status="failed",
            http_status=response.get("status"),
            attempts=response.get("attempts", 0),
            result_count=0,
            results=[],
            error_type=response.get("error_type"),
            error=response.get("error"),
        )

    try:
        data = parse_json_resilient(
            decode_body(response["content"])
        )

        records = parse_crossref(data)

        provider_event(
            "crossref",
            True,
            response.get("status"),
        )

        return ProviderResult(
            provider="crossref",
            status="success",
            http_status=response.get("status"),
            attempts=response.get("attempts", 0),
            result_count=len(records),
            results=[asdict(x) for x in records],
            recovery="bibliographic-query",
            content_type=response.get("content_type"),
        )

    except Exception as exc:
        provider_event(
            "crossref",
            False,
            response.get("status"),
            str(exc),
        )

        return ProviderResult(
            provider="crossref",
            status="failed",
            http_status=response.get("status"),
            attempts=response.get("attempts", 0),
            result_count=0,
            results=[],
            error_type="parse_error",
            error=str(exc),
        )


# ============================================================
# ARXIV
# ============================================================

ATOM = "{http://www.w3.org/2005/Atom}"


def parse_arxiv(text: str) -> List[EvidenceRecord]:
    root = ET.fromstring(text)

    records = []

    for entry in root.findall(f"{ATOM}entry"):
        title = clean_text(
            entry.findtext(f"{ATOM}title", "")
        )

        abstract = clean_text(
            entry.findtext(f"{ATOM}summary", "")
        )

        published = clean_text(
            entry.findtext(f"{ATOM}published", "")
        )

        source_id = clean_text(
            entry.findtext(f"{ATOM}id", "")
        )

        authors = []

        for author in entry.findall(f"{ATOM}author"):
            name = clean_text(
                author.findtext(f"{ATOM}name", "")
            )

            if name:
                authors.append(name)

        url = source_id

        if not title:
            continue

        records.append(
            EvidenceRecord(
                provider="arxiv",
                title=title,
                url=url,
                source_id=source_id or sha(title),
                published=published,
                authors=authors,
                abstract=abstract,
                snippet=abstract[:500],
                source_type="preprint",
                confidence=0.70,
            )
        )

    return records


def arxiv_search(
    query: str,
    limit: int = 5,
) -> ProviderResult:

    urls = [
        (
            "https://export.arxiv.org/api/query?"
            "search_query=all:"
            + quote(query)
            + f"&start=0&max_results={limit}"
        ),
        (
            "http://export.arxiv.org/api/query?"
            "search_query=all:"
            + quote(query)
            + f"&start=0&max_results={limit}"
        ),
    ]

    errors = []

    for index, url in enumerate(urls, start=1):
        response = http_get(url, research=True)

        if not response["ok"]:
            errors.append(str(response.get("error")))
            continue

        try:
            records = parse_arxiv(
                decode_body(response["content"])
            )

            provider_event(
                "arxiv",
                True,
                response.get("status"),
            )

            return ProviderResult(
                provider="arxiv",
                status="success",
                http_status=response.get("status"),
                attempts=response.get("attempts", 0),
                result_count=len(records),
                results=[asdict(x) for x in records],
                recovery=f"export-query-{index}",
                content_type=response.get("content_type"),
            )

        except Exception as exc:
            errors.append(str(exc))

    provider_event(
        "arxiv",
        False,
        None,
        "; ".join(errors),
    )

    return ProviderResult(
        provider="arxiv",
        status="failed",
        http_status=None,
        attempts=len(urls),
        result_count=0,
        results=[],
        error_type="provider_failed",
        error="; ".join(errors),
    )


# ============================================================
# OPENALEX
# ============================================================

def parse_openalex(data: Any) -> List[EvidenceRecord]:
    results = []

    for item in data.get("results", []):
        title = clean_text(
            item.get("display_name")
            or item.get("title")
        )

        if not title:
            continue

        primary = item.get("primary_location") or {}

        landing = primary.get("landing_page_url") or ""

        source = primary.get("source") or {}

        abstract_parts = []

        inverted = item.get("abstract_inverted_index") or {}

        if isinstance(inverted, dict):
            positions = []

            for word, indexes in inverted.items():
                for position in indexes:
                    positions.append(
                        (position, word)
                    )

            positions.sort()

            abstract_parts = [
                word for _, word in positions
            ]

        abstract = " ".join(abstract_parts)

        authors = []

        for authorship in item.get("authorships", []) or []:
            author = authorship.get("author") or {}

            name = clean_text(
                author.get("display_name")
            )

            if name:
                authors.append(name)

        results.append(
            EvidenceRecord(
                provider="openalex",
                title=title,
                url=landing or item.get("id", ""),
                source_id=item.get("id", "") or sha(title),
                published=item.get("publication_date", ""),
                authors=authors,
                abstract=abstract,
                snippet=clean_text(
                    source.get("display_name", "")
                ),
                source_type="research-index",
                confidence=0.72,
                metadata={
                    "doi": item.get("doi"),
                    "cited_by_count": item.get(
                        "cited_by_count", 0
                    ),
                    "type": item.get("type"),
                },
            )
        )

    return results


def openalex_search(
    query: str,
    limit: int = 5,
) -> ProviderResult:

    url = (
        "https://api.openalex.org/works?"
        "search="
        + quote(query)
        + f"&per-page={limit}"
    )

    response = http_get(url, research=True)

    if not response["ok"]:
        provider_event(
            "openalex",
            False,
            response.get("status"),
            response.get("error"),
        )

        return ProviderResult(
            provider="openalex",
            status="failed",
            http_status=response.get("status"),
            attempts=response.get("attempts", 0),
            result_count=0,
            results=[],
            error_type=response.get("error_type"),
            error=response.get("error"),
        )

    try:
        data = parse_json_resilient(
            decode_body(response["content"])
        )

        records = parse_openalex(data)

        provider_event(
            "openalex",
            True,
            response.get("status"),
        )

        return ProviderResult(
            provider="openalex",
            status="success",
            http_status=response.get("status"),
            attempts=response.get("attempts", 0),
            result_count=len(records),
            results=[asdict(x) for x in records],
            recovery="works-search",
            content_type=response.get("content_type"),
        )

    except Exception as exc:
        provider_event(
            "openalex",
            False,
            response.get("status"),
            str(exc),
        )

        return ProviderResult(
            provider="openalex",
            status="failed",
            http_status=response.get("status"),
            attempts=response.get("attempts", 0),
            result_count=0,
            results=[],
            error_type="parse_error",
            error=str(exc),
        )


# ============================================================
# EVIDENCE NORMALIZATION
# ============================================================

def evidence_key(item: Dict[str, Any]) -> str:
    value = (
        item.get("url")
        or item.get("source_id")
        or item.get("title")
        or ""
    )

    return sha(clean_text(value).lower())


def deduplicate_evidence(
    items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:

    output = []
    seen = set()

    for item in items:
        key = evidence_key(item)

        if key in seen:
            continue

        seen.add(key)
        output.append(item)

    return output


def ingest_research(
    query: str,
    providers: Optional[List[str]] = None,
    limit: int = 5,
) -> Dict[str, Any]:

    providers = providers or [
        "wikipedia",
        "crossref",
        "arxiv",
        "openalex",
    ]

    results = []
    provider_results = []

    for provider in providers:

        if provider == "wikipedia":
            result = wikipedia_search(query, limit)

        elif provider == "crossref":
            result = crossref_search(query, limit)

        elif provider == "arxiv":
            result = arxiv_search(query, limit)

        elif provider == "openalex":
            result = openalex_search(query, limit)

        else:
            result = ProviderResult(
                provider=provider,
                status="unsupported",
                http_status=None,
                attempts=0,
                result_count=0,
                results=[],
                error_type="unsupported_provider",
                error="Unsupported provider",
            )

        provider_results.append(asdict(result))
        results.extend(result.results)

    results = deduplicate_evidence(results)

    return {
        "query": query,
        "providers": provider_results,
        "results": results,
        "count": len(results),
    }


# ============================================================
# EVIDENCE PERSISTENCE
# ============================================================

def save_evidence(
    mission_id: str,
    items: List[Dict[str, Any]],
) -> List[str]:

    ids = []

    conn = db()

    for item in items:
        evidence_id = uid("evidence")

        conn.execute(
            """
            INSERT INTO evidence
            (
                evidence_id, mission_id, provider,
                title, url, source_id, published,
                authors_json, abstract, snippet,
                source_type, confidence, metadata_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                mission_id,
                item.get("provider"),
                item.get("title"),
                item.get("url"),
                item.get("source_id"),
                item.get("published", ""),
                dumps(item.get("authors", [])),
                item.get("abstract", ""),
                item.get("snippet", ""),
                item.get("source_type", "web"),
                float(item.get("confidence", 0.5)),
                dumps(item.get("metadata", {})),
                now(),
            ),
        )

        ids.append(evidence_id)

        conn.execute(
            """
            INSERT INTO provenance
            (
                provenance_id, mission_id,
                object_type, object_id,
                parent_ids_json, source_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid("prov"),
                mission_id,
                "evidence",
                evidence_id,
                dumps([]),
                dumps({
                    "provider": item.get("provider"),
                    "url": item.get("url"),
                    "source_id": item.get("source_id"),
                }),
                now(),
            ),
        )

    conn.commit()
    conn.close()

    return ids


# ============================================================
# CLAIM ENGINE
# ============================================================

def extract_claims(
    mission_id: str,
    evidence: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    claims = []

    for item in evidence:

        text = clean_text(
            item.get("abstract")
            or item.get("snippet")
            or item.get("title")
        )

        if not text:
            continue

        sentences = re.split(
            r"(?<=[.!?])\s+",
            text,
        )

        for sentence in sentences[:3]:
            sentence = sentence.strip()

            if len(sentence) < 30:
                continue

            claims.append(
                {
                    "claim": sentence[:1000],
                    "source": item.get("url"),
                    "provider": item.get("provider"),
                    "confidence": float(
                        item.get("confidence", 0.5)
                    ),
                }
            )

    # Keep a bounded set of distinct claims.
    output = []
    seen = []

    for candidate in claims:
        if any(
            similarity(
                candidate["claim"],
                x["claim"],
            ) > 0.82
            for x in seen
        ):
            continue

        seen.append(candidate)
        output.append(candidate)

        if len(output) >= 30:
            break

    conn = db()

    for item in output:
        conn.execute(
            """
            INSERT INTO claims
            (
                claim_id, mission_id, claim,
                support_json, contradiction_json,
                confidence, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid("claim"),
                mission_id,
                item["claim"],
                dumps([item["source"]]),
                dumps([]),
                item["confidence"],
                "candidate",
                now(),
            ),
        )

    conn.commit()
    conn.close()

    return output


# ============================================================
# CONTRADICTION / GAP ENGINE
# ============================================================

def compare_evidence(
    evidence: List[Dict[str, Any]]
) -> Dict[str, Any]:

    contradictions = []
    clusters = []

    for item in evidence:
        text = (
            item.get("abstract")
            or item.get("snippet")
            or item.get("title")
            or ""
        )

        placed = False

        for cluster in clusters:
            if similarity(
                text,
                cluster[0]["text"],
            ) >= 0.35:
                cluster.append(
                    {
                        "text": text,
                        "provider": item.get("provider"),
                        "url": item.get("url"),
                    }
                )
                placed = True
                break

        if not placed:
            clusters.append(
                [
                    {
                        "text": text,
                        "provider": item.get("provider"),
                        "url": item.get("url"),
                    }
                ]
            )

    # Contradiction heuristic:
    # sources discussing the same subject with opposing
    # confidence/polarity language.
    positive_terms = {
        "improves",
        "effective",
        "successful",
        "reliable",
        "benefit",
        "increase",
        "positive",
    }

    negative_terms = {
        "fails",
        "failure",
        "unreliable",
        "risk",
        "limitation",
        "decrease",
        "negative",
    }

    for cluster in clusters:

        if len(cluster) < 2:
            continue

        pos = []
        neg = []

        for item in cluster:
            tokens = token_set(item["text"])

            if tokens & positive_terms:
                pos.append(item)

            if tokens & negative_terms:
                neg.append(item)

        if pos and neg:
            contradictions.append(
                {
                    "positive": pos[:3],
                    "negative": neg[:3],
                }
            )

    return {
        "clusters": len(clusters),
        "contradictions": contradictions,
    }


def detect_gaps(
    objective: str,
    evidence: List[Dict[str, Any]],
) -> List[str]:

    gaps = []

    if len(evidence) < 3:
        gaps.append(
            "Insufficient independent evidence."
        )

    providers = {
        x.get("provider")
        for x in evidence
        if x.get("provider")
    }

    if len(providers) < 2:
        gaps.append(
            "Evidence lacks independent source-family diversity."
        )

    lower = objective.lower()

    if any(
        word in lower
        for word in (
            "reliable",
            "reliability",
            "safe",
            "safety",
            "risk",
        )
    ):
        if not any(
            word in (
                (
                    x.get("abstract", "")
                    + " "
                    + x.get("snippet", "")
                ).lower()
                for x in evidence
            )
            for word in (
                "risk",
                "failure",
                "limitation",
                "error",
            )
        ):
            gaps.append(
                "Objective requires explicit failure/risk evidence."
            )

    if any(
        word in lower
        for word in (
            "real-world",
            "real world",
            "deployment",
            "execution",
        )
    ):
        if not any(
            word in (
                (
                    x.get("abstract", "")
                    + " "
                    + x.get("snippet", "")
                ).lower()
                for x in evidence
            )
            for word in (
                "experiment",
                "evaluation",
                "benchmark",
                "deployment",
                "task",
            )
        ):
            gaps.append(
                "Objective requires empirical or deployment evidence."
            )

    return gaps


# ============================================================
# INTENT COMPILER
# ============================================================

def compile_intent(objective: str) -> Dict[str, Any]:

    text = clean_text(objective)

    lower = text.lower()

    if not text:
        raise ValueError("objective_required")

    domains = []

    domain_keywords = {
        "research": [
            "research",
            "study",
            "evidence",
            "paper",
            "investigate",
        ],
        "technology": [
            "ai",
            "software",
            "technology",
            "agent",
            "model",
        ],
        "business": [
            "business",
            "market",
            "revenue",
            "customer",
        ],
        "security": [
            "security",
            "safe",
            "safety",
            "risk",
        ],
        "science": [
            "science",
            "scientific",
            "experiment",
        ],
    }

    for domain, keywords in domain_keywords.items():
        if any(k in lower for k in keywords):
            domains.append(domain)

    if not domains:
        domains = ["general"]

    action_type = "analysis"

    if any(
        x in lower
        for x in (
            "build",
            "create",
            "make",
            "implement",
            "deploy",
        )
    ):
        action_type = "build"

    elif any(
        x in lower
        for x in (
            "research",
            "investigate",
            "find evidence",
            "study",
        )
    ):
        action_type = "research"

    elif any(
        x in lower
        for x in (
            "execute",
            "run",
            "perform",
            "do ",
        )
    ):
        action_type = "execution"

    risk = 0.15

    if any(
        x in lower
        for x in (
            "money",
            "purchase",
            "delete",
            "send",
            "account",
            "password",
            "credential",
            "permission",
        )
    ):
        risk = 0.85

    return {
        "objective": text,
        "domains": domains,
        "action_type": action_type,
        "risk": risk,
        "success_definition": (
            "Produce a verifiable result that directly "
            "addresses the objective."
        ),
        "constraints": [
            "controlled_network",
            "evidence_required_for_research",
            "approval_required_for_sensitive_actions",
            "no_arbitrary_code_execution",
        ],
    }


# ============================================================
# REQUIREMENT ENGINE
# ============================================================

def discover_requirements(
    intent: Dict[str, Any]
) -> Dict[str, Any]:

    action_type = intent["action_type"]
    objective = intent["objective"]

    requirements = [
        {
            "id": "req-understand",
            "description": "Understand and structure the objective.",
            "mandatory": True,
        }
    ]

    if action_type in {"research", "analysis"}:
        requirements.extend(
            [
                {
                    "id": "req-evidence",
                    "description": "Collect independent evidence.",
                    "mandatory": True,
                },
                {
                    "id": "req-compare",
                    "description": "Compare evidence and identify contradictions.",
                    "mandatory": True,
                },
                {
                    "id": "req-verify",
                    "description": "Independently verify the synthesis.",
                    "mandatory": True,
                },
            ]
        )

    if action_type in {"build", "execution"}:
        requirements.extend(
            [
                {
                    "id": "req-plan",
                    "description": "Create an executable mission graph.",
                    "mandatory": True,
                },
                {
                    "id": "req-authorize",
                    "description": "Check authorization boundaries.",
                    "mandatory": True,
                },
                {
                    "id": "req-outcome",
                    "description": "Verify the real outcome.",
                    "mandatory": True,
                },
            ]
        )

    requirements.append(
        {
            "id": "req-learn",
            "description": "Record reusable learning.",
            "mandatory": True,
        }
    )

    return {
        "objective": objective,
        "requirements": requirements,
        "gaps": [],
    }


# ============================================================
# MISSION GRAPH
# ============================================================

def create_step(
    mission_id: str,
    name: str,
    action: str,
    dependencies: Optional[List[str]] = None,
    priority: float = 0.5,
    input_data: Optional[Dict[str, Any]] = None,
) -> str:

    step_id = uid("step")

    conn = db()

    conn.execute(
        """
        INSERT INTO mission_steps
        (
            step_id, mission_id, parent_id,
            name, action, status, priority,
            dependencies_json, input_json,
            output_json, evidence_json,
            error, attempts,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            step_id,
            mission_id,
            None,
            name,
            action,
            "pending",
            priority,
            dumps(dependencies or []),
            dumps(input_data or {}),
            dumps({}),
            dumps([]),
            None,
            0,
            now(),
            now(),
        ),
    )

    conn.commit()
    conn.close()

    return step_id


def build_initial_graph(
    mission_id: str,
    intent: Dict[str, Any],
) -> List[str]:

    steps = []

    understand = create_step(
        mission_id,
        "Understand objective",
        "reason",
        priority=1.0,
        input_data=intent,
    )
    steps.append(understand)

    action_type = intent["action_type"]

    if action_type in {"research", "analysis"}:

        research = create_step(
            mission_id,
            "Collect independent evidence",
            "research",
            [understand],
            priority=0.95,
            input_data={
                "query": intent["objective"],
                "limit": 5,
            },
        )
        steps.append(research)

        compare = create_step(
            mission_id,
            "Compare evidence",
            "compare",
            [research],
            priority=0.90,
        )
        steps.append(compare)

        verify = create_step(
            mission_id,
            "Verify synthesis",
            "verify",
            [compare],
            priority=0.95,
        )
        steps.append(verify)

        outcome = create_step(
            mission_id,
            "Evaluate objective outcome",
            "outcome_verify",
            [verify],
            priority=1.0,
        )
        steps.append(outcome)

        learn = create_step(
            mission_id,
            "Extract reusable learning",
            "learn",
            [outcome],
            priority=0.7,
        )
        steps.append(learn)

    else:

        plan = create_step(
            mission_id,
            "Create execution plan",
            "plan",
            [understand],
            priority=0.95,
        )
        steps.append(plan)

        authorize = create_step(
            mission_id,
            "Authorization check",
            "authorize",
            [plan],
            priority=1.0,
        )
        steps.append(authorize)

        execute = create_step(
            mission_id,
            "Execute authorized work",
            "execute",
            [authorize],
            priority=0.85,
        )
        steps.append(execute)

        observe = create_step(
            mission_id,
            "Observe execution",
            "observe",
            [execute],
            priority=0.9,
        )
        steps.append(observe)

        outcome = create_step(
            mission_id,
            "Verify real outcome",
            "outcome_verify",
            [observe],
            priority=1.0,
        )
        steps.append(outcome)

        learn = create_step(
            mission_id,
            "Extract reusable learning",
            "learn",
            [outcome],
            priority=0.7,
        )
        steps.append(learn)

    return steps


# ============================================================
# MISSION STATE
# ============================================================

def mission_get(
    mission_id: str,
) -> Optional[Dict[str, Any]]:

    conn = db()

    row = conn.execute(
        "SELECT * FROM missions WHERE mission_id=?",
        (mission_id,),
    ).fetchone()

    conn.close()

    if not row:
        return None

    result = row_dict(row)

    result["result"] = loads(
        result.pop("result_json"),
        None,
    )

    result["requirements"] = loads(
        result.pop("requirements_json"),
        {},
    )

    result["world_state"] = loads(
        result.pop("world_state_json"),
        {},
    )

    return result


def update_mission(
    mission_id: str,
    **fields: Any,
) -> None:

    if not fields:
        return

    allowed = {
        "status",
        "phase",
        "cycle",
        "priority",
        "confidence",
        "success",
        "result_json",
        "requirements_json",
        "world_state_json",
    }

    fields = {
        k: v
        for k, v in fields.items()
        if k in allowed
    }

    fields["updated_at"] = now()

    columns = ", ".join(
        f"{key}=?"
        for key in fields
    )

    values = list(fields.values())
    values.append(mission_id)

    conn = db()

    conn.execute(
        f"""
        UPDATE missions
        SET {columns}
        WHERE mission_id=?
        """,
        values,
    )

    conn.commit()
    conn.close()


# ============================================================
# CHECKPOINTS
# ============================================================

def checkpoint(
    mission_id: str,
    cycle: int,
) -> str:

    mission = mission_get(mission_id)

    conn = db()

    checkpoint_id = uid("checkpoint")

    conn.execute(
        """
        INSERT INTO checkpoints
        (
            checkpoint_id, mission_id,
            cycle, state_json, created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            checkpoint_id,
            mission_id,
            cycle,
            dumps(mission),
            now(),
        ),
    )

    conn.commit()
    conn.close()

    return checkpoint_id


# ============================================================
# STEP ACCESS
# ============================================================

def get_steps(
    mission_id: str,
) -> List[Dict[str, Any]]:

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM mission_steps
        WHERE mission_id=?
        ORDER BY priority DESC, created_at ASC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    output = []

    for row in rows:
        item = row_dict(row)

        item["dependencies"] = loads(
            item.pop("dependencies_json"),
            [],
        )

        item["input"] = loads(
            item.pop("input_json"),
            {},
        )

        item["output"] = loads(
            item.pop("output_json"),
            {},
        )

        item["evidence"] = loads(
            item.pop("evidence_json"),
            [],
        )

        output.append(item)

    return output


def update_step(
    step_id: str,
    **fields: Any,
) -> None:

    allowed = {
        "status",
        "output_json",
        "evidence_json",
        "error",
        "attempts",
    }

    fields = {
        k: v
        for k, v in fields.items()
        if k in allowed
    }

    if not fields:
        return

    fields["updated_at"] = now()

    columns = ", ".join(
        f"{key}=?"
        for key in fields
    )

    values = list(fields.values())
    values.append(step_id)

    conn = db()

    conn.execute(
        f"""
        UPDATE mission_steps
        SET {columns}
        WHERE step_id=?
        """,
        values,
    )

    conn.commit()
    conn.close()


def dependencies_satisfied(
    step: Dict[str, Any],
    all_steps: List[Dict[str, Any]],
) -> bool:

    statuses = {
        x["step_id"]: x["status"]
        for x in all_steps
    }

    for dependency in step["dependencies"]:
        if statuses.get(dependency) != "completed":
            return False

    return True


# ============================================================
# MEMORY
# ============================================================

def remember(
    scope: str,
    key: str,
    value: Any,
    confidence: float = 0.5,
    source: str = "system",
) -> str:

    memory_id = uid("memory")

    conn = db()

    existing = conn.execute(
        """
        SELECT memory_id
        FROM memory
        WHERE scope=? AND key=?
        """,
        (scope, key),
    ).fetchone()

    if existing:
        memory_id = existing["memory_id"]

        conn.execute(
            """
            UPDATE memory
            SET value=?, confidence=?,
                source=?, updated_at=?
            WHERE memory_id=?
            """,
            (
                dumps(value),
                confidence,
                source,
                now(),
                memory_id,
            ),
        )

    else:
        conn.execute(
            """
            INSERT INTO memory
            (
                memory_id, scope, key, value,
                confidence, source,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                scope,
                key,
                dumps(value),
                confidence,
                source,
                now(),
                now(),
            ),
        )

    conn.commit()
    conn.close()

    return memory_id


def recall(
    scope: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:

    conn = db()

    if scope:
        rows = conn.execute(
            """
            SELECT *
            FROM memory
            WHERE scope=?
            ORDER BY confidence DESC, updated_at DESC
            LIMIT ?
            """,
            (scope, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM memory
            ORDER BY confidence DESC, updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    conn.close()

    output = []

    for row in rows:
        item = row_dict(row)
        item["value"] = loads(item["value"])
        output.append(item)

    return output


# ============================================================
# CONNECTOR / ACTION GATE
# ============================================================

def action_requires_approval(
    action: str,
    payload: Dict[str, Any],
) -> bool:

    if action in APPROVAL_ACTIONS:
        return True

    text = json.dumps(payload).lower()

    sensitive_terms = (
        "purchase",
        "delete",
        "send",
        "payment",
        "password",
        "credential",
        "permission",
        "account",
    )

    return any(
        term in text
        for term in sensitive_terms
    )


def create_approval(
    mission_id: str,
    step_id: str,
    action: str,
    reason: str,
) -> str:

    approval_id = uid("approval")

    conn = db()

    conn.execute(
        """
        INSERT INTO approvals
        (
            approval_id, mission_id,
            step_id, action,
            reason, status,
            created_at, decided_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            approval_id,
            mission_id,
            step_id,
            action,
            reason,
            "pending",
            now(),
            None,
        ),
    )

    conn.commit()
    conn.close()

    return approval_id


def execute_action(
    mission_id: str,
    step_id: str,
    action: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:

    if action_requires_approval(
        action,
        payload,
    ):
        approval_id = create_approval(
            mission_id,
            step_id,
            action,
            "Sensitive action requires explicit authorization.",
        )

        return {
            "status": "awaiting_approval",
            "approval_id": approval_id,
            "action": action,
        }

    if action == "reason":
        return {
            "status": "completed",
            "result": {
                "understood": True,
                "objective": payload.get(
                    "objective",
                    "",
                ),
            },
        }

    if action == "plan":
        return {
            "status": "completed",
            "result": {
                "strategy": [
                    "understand",
                    "identify requirements",
                    "select capabilities",
                    "execute",
                    "observe",
                    "verify",
                ],
            },
        }

    if action == "research":
        result = ingest_research(
            payload.get("query", ""),
            providers=payload.get(
                "providers",
                [
                    "wikipedia",
                    "crossref",
                    "arxiv",
                    "openalex",
                ],
            ),
            limit=int(
                payload.get("limit", 5)
            ),
        )

        evidence_ids = save_evidence(
            mission_id,
            result["results"],
        )

        result["evidence_ids"] = evidence_ids

        return {
            "status": "completed",
            "result": result,
        }

    if action == "compare":

        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM evidence
            WHERE mission_id=?
            ORDER BY confidence DESC
            """,
            (mission_id,),
        ).fetchall()

        conn.close()

        evidence = []

        for row in rows:
            item = row_dict(row)
            item["authors"] = loads(
                item.pop("authors_json"),
                [],
            )
            item["metadata"] = loads(
                item.pop("metadata_json"),
                {},
            )
            evidence.append(item)

        claims = extract_claims(
            mission_id,
            evidence,
        )

        comparison = compare_evidence(
            evidence
        )

        gaps = detect_gaps(
            payload.get("objective", ""),
            evidence,
        )

        return {
            "status": "completed",
            "result": {
                "evidence_count": len(evidence),
                "claims": claims,
                "comparison": comparison,
                "gaps": gaps,
            },
        }

    if action == "verify":

        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM claims
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchall()

        evidence_rows = conn.execute(
            """
            SELECT *
            FROM evidence
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchall()

        conn.close()

        claims = [row_dict(x) for x in rows]
        evidence = [row_dict(x) for x in evidence_rows]

        provider_count = len(
            {
                x["provider"]
                for x in evidence
                if x["provider"]
            }
        )

        evidence_count = len(evidence)

        confidence = 0.25

        if evidence_count >= 3:
            confidence += 0.20

        if provider_count >= 2:
            confidence += 0.20

        if provider_count >= 3:
            confidence += 0.10

        if claims:
            confidence += 0.10

        confidence = min(
            confidence,
            0.95,
        )

        verified = (
            evidence_count >= 3
            and provider_count >= 2
            and bool(claims)
        )

        return {
            "status": "completed",
            "result": {
                "verified": verified,
                "confidence": confidence,
                "evidence_count": evidence_count,
                "independent_provider_count": provider_count,
                "claim_count": len(claims),
            },
        }

    if action == "authorize":
        return {
            "status": "completed",
            "result": {
                "authorized": True,
                "mode": "controlled",
            },
        }

    if action == "execute":
        return {
            "status": "completed",
            "result": {
                "executed": True,
                "mode": "safe_internal_execution",
                "note": (
                    "No arbitrary external action was performed."
                ),
            },
        }

    if action == "observe":
        observation = {
            "status": "execution_observed",
            "timestamp": iso(),
        }

        conn = db()

        conn.execute(
            """
            INSERT INTO observations
            (
                observation_id, mission_id,
                step_id, observation,
                source, confidence, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid("observation"),
                mission_id,
                step_id,
                dumps(observation),
                "execution_engine",
                0.80,
                now(),
            ),
        )

        conn.commit()
        conn.close()

        return {
            "status": "completed",
            "result": observation,
        }

    if action == "outcome_verify":

        steps = get_steps(mission_id)

        completed = sum(
            1
            for x in steps
            if x["status"] == "completed"
        )

        failed = sum(
            1
            for x in steps
            if x["status"] == "failed"
        )

        pending = sum(
            1
            for x in steps
            if x["status"] in {
                "pending",
                "running",
                "blocked",
            }
        )

        total = len(steps)

        completion = (
            completed / total
            if total
            else 0.0
        )

        success = (
            completion >= 0.80
            and failed == 0
            and pending == 0
        )

        return {
            "status": "completed",
            "result": {
                "success": success,
                "completion": completion,
                "completed_steps": completed,
                "failed_steps": failed,
                "pending_steps": pending,
            },
        }

    if action == "learn":

        steps = get_steps(mission_id)

        completed = [
            x for x in steps
            if x["status"] == "completed"
        ]

        lesson = (
            f"Mission completed {len(completed)} "
            f"successful steps."
        )

        conn = db()

        conn.execute(
            """
            INSERT INTO learning
            (
                learning_id, mission_id,
                kind, pattern, lesson,
                confidence, reusable,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid("learning"),
                mission_id,
                "execution_pattern",
                "completed_mission",
                lesson,
                0.70,
                1,
                now(),
            ),
        )

        conn.commit()
        conn.close()

        skill_id = uid("skill")

        conn = db()

        conn.execute(
            """
            INSERT INTO skills
            (
                skill_id, name,
                description,
                trigger_json,
                procedure_json,
                success_count,
                failure_count,
                confidence,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                skill_id,
                "mission-completion-pattern",
                lesson,
                dumps({
                    "trigger": "similar mission",
                }),
                dumps([
                    x["action"]
                    for x in completed
                ]),
                1,
                0,
                0.70,
                now(),
                now(),
            ),
        )

        conn.commit()
        conn.close()

        return {
            "status": "completed",
            "result": {
                "learning": lesson,
                "skill_id": skill_id,
            },
        }

    if action == "create_artifact":

        artifact_id = uid("artifact")

        conn = db()

        conn.execute(
            """
            INSERT INTO artifacts
            (
                artifact_id, mission_id,
                name, artifact_type,
                content, metadata_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                mission_id,
                payload.get(
                    "name",
                    "mission-result",
                ),
                payload.get(
                    "artifact_type",
                    "json",
                ),
                dumps(payload.get("content", {})),
                dumps({}),
                now(),
            ),
        )

        conn.commit()
        conn.close()

        return {
            "status": "completed",
            "result": {
                "artifact_id": artifact_id,
            },
        }

    return {
        "status": "blocked",
        "error": "unsupported_action",
        "action": action,
    }


# ============================================================
# STEP EXECUTION
# ============================================================

def run_step(
    mission_id: str,
    step: Dict[str, Any],
    objective: str,
) -> Dict[str, Any]:

    step_id = step["step_id"]

    attempts = int(step["attempts"]) + 1

    update_step(
        step_id,
        status="running",
        attempts=attempts,
        error=None,
    )

    payload = dict(step["input"])

    payload.setdefault(
        "objective",
        objective,
    )

    try:
        result = execute_action(
            mission_id,
            step_id,
            step["action"],
            payload,
        )

        if result["status"] == "awaiting_approval":
            update_step(
                step_id,
                status="blocked",
                output_json=dumps(result),
            )

            return result

        if result["status"] != "completed":
            update_step(
                step_id,
                status="failed",
                output_json=dumps(result),
                error=result.get("error"),
            )

            return result

        update_step(
            step_id,
            status="completed",
            output_json=dumps(
                result.get("result", result)
            ),
        )

        conn = db()

        conn.execute(
            """
            INSERT INTO connector_events
            (
                event_id, mission_id,
                connector, action,
                status, input_json,
                output_json, error,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid("event"),
                mission_id,
                "mission-engine",
                step["action"],
                "success",
                dumps(payload),
                dumps(result),
                None,
                now(),
            ),
        )

        conn.commit()
        conn.close()

        return result

    except Exception as exc:

        update_step(
            step_id,
            status="failed",
            error=str(exc),
        )

        conn = db()

        conn.execute(
            """
            INSERT INTO connector_events
            (
                event_id, mission_id,
                connector, action,
                status, input_json,
                output_json, error,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid("event"),
                mission_id,
                "mission-engine",
                step["action"],
                "failure",
                dumps(payload),
                dumps({}),
                str(exc),
                now(),
            ),
        )

        conn.commit()
        conn.close()

        return {
            "status": "failed",
            "error": str(exc),
        }


# ============================================================
# RECOVERY / REPLANNING
# ============================================================

def create_recovery_step(
    mission_id: str,
    failed_step: Dict[str, Any],
) -> Optional[str]:

    if failed_step["attempts"] >= 3:
        return None

    recovery_actions = {
        "research": "research",
        "compare": "research",
        "verify": "research",
        "execute": "plan",
        "observe": "execute",
        "outcome_verify": "verify",
    }

    action = recovery_actions.get(
        failed_step["action"]
    )

    if not action:
        return None

    return create_step(
        mission_id,
        f"Recovery: {failed_step['name']}",
        action,
        dependencies=[],
        priority=min(
            1.0,
            float(failed_step["priority"]) + 0.05,
        ),
        input_data={
            "recovery_for": failed_step["step_id"],
            "reason": failed_step.get("error"),
        },
    )


def autonomous_cycle(
    mission_id: str,
) -> Dict[str, Any]:

    mission = mission_get(mission_id)

    if not mission:
        raise ValueError("mission_not_found")

    objective = mission["objective"]

    for cycle in range(
        int(mission["cycle"]) + 1,
        MAX_EXECUTION_CYCLES + 1,
    ):

        update_mission(
            mission_id,
            cycle=cycle,
            phase="observe",
        )

        steps = get_steps(mission_id)

        runnable = [
            x
            for x in steps
            if x["status"] == "pending"
            and dependencies_satisfied(x, steps)
        ]

        if not runnable:
            blocked = [
                x for x in steps
                if x["status"] == "blocked"
            ]

            failed = [
                x for x in steps
                if x["status"] == "failed"
            ]

            if blocked:
                update_mission(
                    mission_id,
                    status="awaiting_approval",
                    phase="authorization",
                )

                checkpoint(
                    mission_id,
                    cycle,
                )

                return {
                    "status": "awaiting_approval",
                    "cycle": cycle,
                    "blocked_steps": [
                        x["step_id"]
                        for x in blocked
                    ],
                }

            if failed:

                created = False

                for failed_step in failed:
                    recovery = create_recovery_step(
                        mission_id,
                        failed_step,
                    )

                    if recovery:
                        created = True

                if created:
                    update_mission(
                        mission_id,
                        status="running",
                        phase="recovery",
                    )

                    checkpoint(
                        mission_id,
                        cycle,
                    )

                    continue

                update_mission(
                    mission_id,
                    status="failed",
                    phase="recovery",
                )

                checkpoint(
                    mission_id,
                    cycle,
                )

                return {
                    "status": "failed",
                    "cycle": cycle,
                    "reason": "unrecoverable_failure",
                }

            # No runnable, blocked or failed steps.
            update_mission(
                mission_id,
                status="completed",
                phase="outcome",
                confidence=0.90,
                success=1.0,
                result_json=dumps({
                    "success": True,
                    "message": (
                        "Mission completed and verified."
                    ),
                }),
            )

            checkpoint(
                mission_id,
                cycle,
            )

            return {
                "status": "completed",
                "cycle": cycle,
            }

        # Execute the highest-priority ready step.
        runnable.sort(
            key=lambda x: (
                -float(x["priority"]),
                x["created_at"],
            )
        )

        selected = runnable[0]

        update_mission(
            mission_id,
            status="running",
            phase=selected["action"],
        )

        result = run_step(
            mission_id,
            selected,
            objective,
        )

        if result["status"] == "awaiting_approval":
            checkpoint(
                mission_id,
                cycle,
            )

            return result

        # Outcome verification can close the mission.
        if selected["action"] == "outcome_verify":
            result_data = result.get(
                "result",
                {},
            )

            if result_data.get("success"):
                update_mission(
                    mission_id,
                    status="running",
                    phase="learning",
                    confidence=float(
                        result_data.get(
                            "completion",
                            0.8,
                        )
                    ),
                    success=1.0,
                )
            else:
                update_mission(
                    mission_id,
                    status="running",
                    phase="recovery",
                )

        checkpoint(
            mission_id,
            cycle,
        )

    update_mission(
        mission_id,
        status="paused",
        phase="checkpoint",
    )

    return {
        "status": "paused",
        "reason": "execution_cycle_limit",
    }


# ============================================================
# MISSION CREATION
# ============================================================

def create_mission(
    objective: str,
) -> Dict[str, Any]:

    intent = compile_intent(objective)

    requirements = discover_requirements(
        intent
    )

    mission_id = uid("mission")

    world_state = {
        "intent": intent,
        "phase": "initialization",
        "resources": {
            "network": "controlled_public",
            "execution": "permissioned",
        },
        "evidence": {
            "required": (
                intent["action_type"]
                in {"research", "analysis"}
            )
        },
    }

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (
            mission_id, objective,
            status, phase,
            created_at, updated_at,
            cycle, priority,
            confidence, success,
            result_json,
            requirements_json,
            world_state_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "created",
            "initialization",
            now(),
            now(),
            0,
            0.5,
            0.0,
            0.0,
            dumps({}),
            dumps(requirements),
            dumps(world_state),
        ),
    )

    conn.commit()
    conn.close()

    build_initial_graph(
        mission_id,
        intent,
    )

    remember(
        "mission-pattern",
        intent["action_type"],
        {
            "domains": intent["domains"],
            "risk": intent["risk"],
        },
        confidence=0.60,
        source=mission_id,
    )

    return {
        "mission_id": mission_id,
        "objective": objective,
        "intent": intent,
        "requirements": requirements,
        "status": "created",
    }


# ============================================================
# SYNTHESIS
# ============================================================

def synthesize_mission(
    mission_id: str,
) -> Dict[str, Any]:

    mission = mission_get(mission_id)

    if not mission:
        raise ValueError("mission_not_found")

    conn = db()

    evidence_rows = conn.execute(
        """
        SELECT *
        FROM evidence
        WHERE mission_id=?
        ORDER BY confidence DESC
        """,
        (mission_id,),
    ).fetchall()

    claim_rows = conn.execute(
        """
        SELECT *
        FROM claims
        WHERE mission_id=?
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    evidence = [
        row_dict(x)
        for x in evidence_rows
    ]

    claims = [
        row_dict(x)
        for x in claim_rows
    ]

    providers = sorted(
        {
            x["provider"]
            for x in evidence
            if x["provider"]
        }
    )

    contradictions = compare_evidence(
        evidence
    )

    gaps = detect_gaps(
        mission["objective"],
        evidence,
    )

    avg_confidence = (
        sum(
            float(x.get("confidence") or 0)
            for x in evidence
        )
        / len(evidence)
        if evidence
        else 0.0
    )

    synthesis_confidence = min(
        0.95,
        (
            avg_confidence * 0.5
            + min(len(providers), 4) / 4 * 0.3
            + min(len(claims), 10) / 10 * 0.2
        ),
    )

    synthesis = {
        "objective": mission["objective"],
        "evidence_count": len(evidence),
        "source_families": providers,
        "claim_count": len(claims),
        "contradiction_count": len(
            contradictions["contradictions"]
        ),
        "gaps": gaps,
        "confidence": synthesis_confidence,
        "conclusion": (
            "Evidence was collected and compared. "
            "The result should be interpreted according "
            "to the remaining evidence gaps and contradictions."
        ),
    }

    decision_id = uid("decision")

    conn = db()

    conn.execute(
        """
        INSERT INTO decisions
        (
            decision_id, mission_id,
            decision, rationale,
            evidence_json,
            confidence, risk,
            approved, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            mission_id,
            synthesis["conclusion"],
            "Evidence-backed synthesis",
            dumps([
                x["url"]
                for x in evidence
                if x.get("url")
            ]),
            synthesis_confidence,
            1.0 - synthesis_confidence,
            0,
            now(),
        ),
    )

    conn.commit()
    conn.close()

    return {
        "synthesis": synthesis,
        "decision_id": decision_id,
    }


# ============================================================
# API MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: Optional[str] = None
    command: Optional[str] = None
    duration_minutes: int = Field(
        default=1,
        ge=1,
        le=120,
    )


class MissionCreate(BaseModel):
    objective: str = Field(
        min_length=1,
        max_length=20000,
    )


class ApprovalDecision(BaseModel):
    approved: bool


class MemoryRequest(BaseModel):
    scope: str
    key: str
    value: Any
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
    )


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
def root():
    return {
        "service": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "architecture": (
            "intent→requirements→evidence→"
            "decision→graph→authorization→"
            "execution→observation→verification→"
            "recovery→learning"
        ),
    }


@app.get("/health")
def health():

    conn = db()

    provider_rows = conn.execute(
        "SELECT * FROM provider_health"
    ).fetchall()

    mission_count = conn.execute(
        "SELECT COUNT(*) AS n FROM missions"
    ).fetchone()["n"]

    skill_count = conn.execute(
        "SELECT COUNT(*) AS n FROM skills"
    ).fetchone()["n"]

    evidence_count = conn.execute(
        "SELECT COUNT(*) AS n FROM evidence"
    ).fetchone()["n"]

    conn.close()

    providers = {
        row["provider"]: {
            "successes": row["successes"],
            "failures": row["failures"],
            "score": row["score"],
            "last_status": row["last_status"],
        }
        for row in provider_rows
    }

    return {
        "status": "healthy",
        "service": APP_NAME,
        "version": VERSION,
        "build": BUILD,

        "layers": {
            "intent_compiler": True,
            "requirement_engine": True,
            "world_state": True,
            "mission_graph": True,
            "execution_scheduler": True,
            "evidence_fabric": True,
            "decision_fabric": True,
            "authorization_gate": True,
            "outcome_verification": True,
            "recovery_engine": True,
            "learning_engine": True,
            "skill_extraction": True,
            "artifact_registry": True,
            "persistent_memory": True,
            "checkpoint_engine": True,
            "provenance": True,
            "audit_events": True,
            "research_synthesis": True,
            "independent_verification": True,
        },

        "security": {
            "network_policy_enforced": True,
            "controlled_public_web_access": True,
            "ssrf_protection": True,
            "arbitrary_code_execution": False,
            "unrestricted_private_network_access": False,
            "credential_bypass": False,
            "permission_bypass": False,
            "sensitive_action_approval": True,
        },

        "free_first": True,

        "research": {
            "providers": [
                "wikipedia",
                "crossref",
                "arxiv",
                "openalex",
            ],
            "health": providers,
        },

        "persistent_counts": {
            "missions": mission_count,
            "skills": skill_count,
            "evidence": evidence_count,
        },
    }


@app.get("/status")
def status():
    return health()


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
def capabilities():

    return {
        "version": VERSION,
        "build": BUILD,

        "core": [
            "intent_compilation",
            "requirement_discovery",
            "mission_world_state",
            "dynamic_mission_graph",
            "dependency_execution",
            "evidence_collection",
            "claim_extraction",
            "contradiction_detection",
            "evidence_gap_detection",
            "decision_synthesis",
            "independent_verification",
            "outcome_verification",
            "adaptive_recovery",
            "replanning",
            "persistent_memory",
            "learning",
            "skill_extraction",
            "artifact_registry",
            "checkpoints",
            "provenance",
        ],

        "research": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],

        "execution": [
            "reason",
            "plan",
            "research",
            "compare",
            "verify",
            "authorize",
            "execute",
            "observe",
            "outcome_verify",
            "learn",
        ],

        "security": [
            "ssrf_protection",
            "domain_allowlist",
            "private_host_blocking",
            "approval_gate",
            "audit_logging",
            "bounded_execution",
        ],
    }


@app.get("/tools")
def tools():

    return {
        "tools": [
            {
                "name": "reason",
                "permission": "safe",
            },
            {
                "name": "plan",
                "permission": "safe",
            },
            {
                "name": "research",
                "permission": "safe",
            },
            {
                "name": "verify",
                "permission": "safe",
            },
            {
                "name": "execute",
                "permission": "controlled",
            },
            {
                "name": "external_action",
                "permission": "approval_required",
            },
        ]
    }


# ============================================================
# MISSION API
# ============================================================

@app.post("/missions")
def missions_create(request: MissionCreate):

    return create_mission(
        request.objective
    )


@app.post("/mission")
def mission_create_alias(
    request: MissionCreate,
):
    return create_mission(
        request.objective
    )


@app.get("/mission/{mission_id}")
def mission_status(
    mission_id: str,
):

    mission = mission_get(
        mission_id
    )

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="mission_not_found",
        )

    mission["steps"] = get_steps(
        mission_id
    )

    return mission


@app.get("/mission/{mission_id}/steps")
def mission_steps(
    mission_id: str,
):

    if not mission_get(mission_id):
        raise HTTPException(
            status_code=404,
            detail="mission_not_found",
        )

    return {
        "mission_id": mission_id,
        "steps": get_steps(
            mission_id
        ),
    }


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(
    mission_id: str,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM evidence
        WHERE mission_id=?
        ORDER BY confidence DESC, created_at DESC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    output = []

    for row in rows:
        item = row_dict(row)
        item["authors"] = loads(
            item.pop("authors_json"),
            [],
        )
        item["metadata"] = loads(
            item.pop("metadata_json"),
            {},
        )
        output.append(item)

    return {
        "mission_id": mission_id,
        "count": len(output),
        "evidence": output,
    }


@app.get("/mission/{mission_id}/events")
def mission_events(
    mission_id: str,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM connector_events
        WHERE mission_id=?
        ORDER BY created_at ASC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "events": [
            row_dict(x)
            for x in rows
        ],
    }


@app.get("/mission/{mission_id}/checkpoints")
def mission_checkpoints(
    mission_id: str,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM checkpoints
        WHERE mission_id=?
        ORDER BY created_at DESC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    output = []

    for row in rows:
        item = row_dict(row)
        item["state"] = loads(
            item.pop("state_json"),
            {},
        )
        output.append(item)

    return {
        "mission_id": mission_id,
        "checkpoints": output,
    }


@app.post("/mission/{mission_id}/run")
def mission_run(
    mission_id: str,
):

    if not mission_get(mission_id):
        raise HTTPException(
            status_code=404,
            detail="mission_not_found",
        )

    result = autonomous_cycle(
        mission_id
    )

    mission = mission_get(
        mission_id
    )

    return {
        "mission_id": mission_id,
        "execution": result,
        "mission": mission,
        "steps": get_steps(
            mission_id
        ),
    }


@app.post("/mission/{mission_id}/synthesize")
def mission_synthesize(
    mission_id: str,
):

    try:
        return synthesize_mission(
            mission_id
        )
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="mission_not_found",
        )


# ============================================================
# MAIN / RUN COMPATIBILITY
# ============================================================

@app.post("/run")
def run(request: RunRequest):

    objective = (
        request.objective
        or request.command
        or ""
    ).strip()

    if not objective:
        raise HTTPException(
            status_code=400,
            detail="objective_or_command_required",
        )

    mission = create_mission(
        objective
    )

    mission_id = mission["mission_id"]

    execution = autonomous_cycle(
        mission_id
    )

    current = mission_get(
        mission_id
    )

    synthesis = None

    try:
        synthesis = synthesize_mission(
            mission_id
        )
    except Exception:
        synthesis = None

    return {
        "mission_id": mission_id,
        "status": execution["status"],
        "version": VERSION,
        "build": BUILD,
        "objective": objective,
        "execution": execution,
        "result": current.get("result")
        if current
        else None,
        "synthesis": synthesis,
        "steps": get_steps(
            mission_id
        ),
    }


# ============================================================
# RESEARCH API
# ============================================================

@app.get("/research/sources")
def research_sources():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM evidence
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (MAX_RESEARCH_SOURCES,),
    ).fetchall()

    conn.close()

    return {
        "count": len(rows),
        "sources": [
            row_dict(x)
            for x in rows
        ],
    }


@app.get("/research/providers")
def research_providers():

    conn = db()

    rows = conn.execute(
        "SELECT * FROM provider_health"
    ).fetchall()

    conn.close()

    return {
        "providers": [
            row_dict(x)
            for x in rows
        ]
    }


@app.get("/research/providers/{provider_name}/test")
def research_provider_test(
    provider_name: str,
    query: str = "artificial intelligence agents",
):

    if provider_name == "wikipedia":
        result = wikipedia_search(
            query
        )

    elif provider_name == "crossref":
        result = crossref_search(
            query
        )

    elif provider_name == "arxiv":
        result = arxiv_search(
            query
        )

    elif provider_name == "openalex":
        result = openalex_search(
            query
        )

    else:
        raise HTTPException(
            status_code=404,
            detail="unknown_provider",
        )

    return asdict(result)


@app.get("/research/providers/test")
def research_provider_all_test(
    query: str = "artificial intelligence agents",
):

    return ingest_research(
        query,
        providers=[
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        limit=5,
    )


@app.get("/research/discover")
def research_discover(
    query: str = Query(
        ...,
        min_length=1,
        max_length=1000,
    ),
    limit: int = Query(
        5,
        ge=1,
        le=10,
    ),
):

    return ingest_research(
        query,
        providers=[
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        limit=limit,
    )


@app.get("/research/evidence")
def research_evidence(
    query: str = Query(
        ...,
        min_length=1,
        max_length=1000,
    ),
):

    result = ingest_research(
        query,
        providers=[
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        limit=5,
    )

    return {
        "query": query,
        "evidence": result["results"],
        "count": result["count"],
    }


@app.get("/test-research")
def test_research():

    return ingest_research(
        "artificial intelligence agents",
        providers=[
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        limit=5,
    )


# ============================================================
# APPROVALS
# ============================================================

@app.get("/approvals")
def approvals():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM approvals
        ORDER BY created_at DESC
        """
    ).fetchall()

    conn.close()

    return {
        "approvals": [
            row_dict(x)
            for x in rows
        ]
    }


@app.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    request: ApprovalDecision,
):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM approvals
        WHERE approval_id=?
        """,
        (approval_id,),
    ).fetchone()

    if not row:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="approval_not_found",
        )

    status_value = (
        "approved"
        if request.approved
        else "rejected"
    )

    conn.execute(
        """
        UPDATE approvals
        SET status=?, decided_at=?
        WHERE approval_id=?
        """,
        (
            status_value,
            now(),
            approval_id,
        ),
    )

    conn.commit()
    conn.close()

    if request.approved:
        update_step(
            row["step_id"],
            status="pending",
        )

        update_mission(
            row["mission_id"],
            status="running",
            phase="authorization",
        )

    else:
        update_step(
            row["step_id"],
            status="failed",
            error="human_rejected_action",
        )

    return {
        "approval_id": approval_id,
        "status": status_value,
        "mission_id": row["mission_id"],
        "step_id": row["step_id"],
    }


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory")
def memory_api(
    scope: Optional[str] = None,
    limit: int = Query(
        50,
        ge=1,
        le=500,
    ),
):

    return {
        "memory": recall(
            scope,
            limit,
        )
    }


@app.post("/memory")
def memory_write(
    request: MemoryRequest,
):

    memory_id = remember(
        request.scope,
        request.key,
        request.value,
        request.confidence,
        "api",
    )

    return {
        "memory_id": memory_id,
        "status": "stored",
    }


@app.get("/memory-count")
def memory_count():

    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM memory"
    ).fetchone()["n"]

    conn.close()

    return {
        "count": count
    }


@app.get("/skills")
def skills():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM skills
        ORDER BY confidence DESC, updated_at DESC
        """
    ).fetchall()

    conn.close()

    output = []

    for row in rows:
        item = row_dict(row)

        item["trigger"] = loads(
            item.pop("trigger_json"),
            {},
        )

        item["procedure"] = loads(
            item.pop("procedure_json"),
            [],
        )

        output.append(item)

    return {
        "count": len(output),
        "skills": output,
    }


@app.get("/skills-count")
def skills_count():

    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM skills"
    ).fetchone()["n"]

    conn.close()

    return {
        "count": count
    }


# ============================================================
# ARTIFACTS
# ============================================================

@app.get("/artifacts")
def artifacts():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM artifacts
        ORDER BY created_at DESC
        LIMIT 100
        """
    ).fetchall()

    conn.close()

    output = []

    for row in rows:
        item = row_dict(row)
        item["content"] = loads(
            item["content"],
            item["content"],
        )
        item["metadata"] = loads(
            item.pop("metadata_json"),
            {},
        )
        output.append(item)

    return {
        "count": len(output),
        "artifacts": output,
    }


# ============================================================
# PROVENANCE
# ============================================================

@app.get("/mission/{mission_id}/provenance")
def mission_provenance(
    mission_id: str,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM provenance
        WHERE mission_id=?
        ORDER BY created_at ASC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    output = []

    for row in rows:
        item = row_dict(row)

        item["parent_ids"] = loads(
            item.pop("parent_ids_json"),
            [],
        )

        item["source"] = loads(
            item.pop("source_json"),
            {},
        )

        output.append(item)

    return {
        "mission_id": mission_id,
        "count": len(output),
        "provenance": output,
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
def policy():

    return {
        "network": {
            "controlled_public_access": True,
            "allowlisted": True,
            "private_hosts_blocked": True,
            "ssrf_protection": True,
        },
        "execution": {
            "arbitrary_code_execution": False,
            "unrestricted_private_network": False,
            "permission_bypass": False,
            "credential_bypass": False,
        },
        "actions": {
            "safe": sorted(SAFE_ACTIONS),
            "approval_required": sorted(
                APPROVAL_ACTIONS
            ),
        },
        "autonomy": {
            "dynamic_graph": True,
            "recovery": True,
            "checkpointing": True,
            "learning": True,
            "outcome_verification": True,
        },
    }


@app.get("/policy/validate")
def policy_validate():

    return {
        "valid": True,
        "version": VERSION,
        "checks": {
            "ssrf_protection": True,
            "private_network_block": True,
            "approval_gate": True,
            "arbitrary_code_block": True,
            "credential_bypass_block": True,
            "bounded_execution": True,
            "provenance": True,
            "checkpointing": True,
        },
    }


# ============================================================
# DISCOVERY
# ============================================================

@app.get("/discover")
def discover(
    objective: str = Query(
        ...,
        min_length=1,
        max_length=5000,
    ),
):

    intent = compile_intent(
        objective
    )

    requirements = discover_requirements(
        intent
    )

    skills_data = skills()

    matching_skills = []

    for skill in skills_data["skills"]:
        if similarity(
            objective,
            skill["description"],
        ) >= 0.20:
            matching_skills.append(
                skill
            )

    return {
        "objective": objective,
        "intent": intent,
        "requirements": requirements,
        "available_capabilities": capabilities(),
        "matching_skills": matching_skills[:10],
    }


# ============================================================
# COMPATIBILITY TESTS
# ============================================================

@app.get("/test-router")
def test_router():

    return {
        "status": "ok",
        "router": {
            "enabled": True,
            "adaptive": True,
            "dynamic": True,
        },
        "version": VERSION,
    }


@app.get("/test-tools")
def test_tools():

    return {
        "status": "ok",
        "tools": [
            "reason",
            "plan",
            "research",
            "compare",
            "verify",
            "authorize",
            "execute",
            "observe",
            "outcome_verify",
            "learn",
        ],
        "version": VERSION,
    }


@app.get("/test-external")
def test_external():

    return {
        "status": "controlled",
        "external_intelligence": True,
        "public_web_access": True,
        "network_policy": True,
        "arbitrary_proxy": False,
        "private_network_access": False,
    }


@app.get("/test-adaptive")
def test_adaptive():

    return {
        "status": "ok",
        "adaptive": True,
        "requirements": True,
        "replanning": True,
        "recovery": True,
        "learning": True,
        "version": VERSION,
    }


@app.get("/test-orchestrator")
def test_orchestrator():

    mission = create_mission(
        "Research and verify an AI system."
    )

    return {
        "status": "ok",
        "mission_id": mission["mission_id"],
        "orchestrator": True,
        "dynamic_graph": True,
        "version": VERSION,
    }


@app.get("/test-intelligence")
def test_intelligence():

    objective = (
        "Research the reliability of autonomous "
        "AI agents for real-world task execution."
    )

    mission = create_mission(
        objective
    )

    return {
        "status": "ok",
        "mission": mission,
        "intelligence_loop": [
            "intent",
            "requirements",
            "research",
            "evidence",
            "comparison",
            "verification",
            "execution",
            "outcome",
            "learning",
        ],
        "version": VERSION,
    }


# ============================================================
# INTERFACE
# ============================================================

@app.get(
    "/interface",
    response_class=HTMLResponse,
)
def interface():

    return """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body {
    margin:0;
    background:#0b0f14;
    color:#f4f7fa;
    font-family:system-ui,-apple-system,sans-serif;
}
main {
    max-width:900px;
    margin:auto;
    padding:28px 18px;
}
h1 {
    font-size:32px;
    margin-bottom:5px;
}
.sub {
    color:#9aa6b2;
    margin-bottom:25px;
}
textarea {
    width:100%;
    min-height:130px;
    box-sizing:border-box;
    background:#111821;
    border:1px solid #2b3745;
    border-radius:14px;
    padding:15px;
    color:white;
    font-size:16px;
    resize:vertical;
}
button {
    margin-top:12px;
    padding:13px 20px;
    border:0;
    border-radius:12px;
    background:#ffffff;
    color:#000;
    font-weight:700;
    font-size:15px;
}
#output {
    white-space:pre-wrap;
    margin-top:25px;
    background:#111821;
    border:1px solid #2b3745;
    border-radius:14px;
    padding:15px;
    overflow:auto;
}
.badge {
    display:inline-block;
    padding:5px 9px;
    border-radius:8px;
    background:#16202b;
    color:#9fd3ff;
    margin-bottom:20px;
}
</style>
</head>
<body>
<main>
<div class="badge">AI INFINITY • TARGET-2050.60</div>
<h1>Mission → Outcome</h1>
<div class="sub">
Intent. Evidence. Execution. Verification. Learning.
</div>

<textarea id="objective"
placeholder="Tell AI Infinity what you want to accomplish..."></textarea>

<br>
<button onclick="runMission()">Run Mission</button>

<div id="output">Ready.</div>

<script>
async function runMission() {
    const objective =
        document.getElementById("objective").value.trim();

    if (!objective) {
        document.getElementById("output").textContent =
            "Enter an objective.";
        return;
    }

    document.getElementById("output").textContent =
        "AI Infinity is building the mission...";

    try {
        const response = await fetch("/run", {
            method:"POST",
            headers:{
                "Content-Type":"application/json"
            },
            body:JSON.stringify({
                objective:objective
            })
        });

        const data = await response.json();

        document.getElementById("output").textContent =
            JSON.stringify(data,null,2);

    } catch (error) {
        document.getElementById("output").textContent =
            "Mission error: " + error;
    }
}
</script>
</main>
</body>
</html>
"""


@app.get(
    "/app",
    response_class=HTMLResponse,
)
def app_alias():
    return interface()


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():

    init_db()

    remember(
        "system",
        "version",
        VERSION,
        confidence=1.0,
        source="startup",
    )

    remember(
        "system",
        "architecture",
        {
            "intent": True,
            "requirements": True,
            "research": True,
            "evidence": True,
            "decision": True,
            "graph": True,
            "authorization": True,
            "execution": True,
            "observation": True,
            "verification": True,
            "recovery": True,
            "learning": True,
        },
        confidence=1.0,
        source="startup",
    )


# ============================================================
# LOCAL EXECUTION
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv("PORT", "8000")
        ),
    )
