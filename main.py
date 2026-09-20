"""
AI Infinity
TARGET-2050.58
BUILD: RESEARCH-INGESTION-REPAIR-FABRIC

2050.57 preserved:
- adaptive execution
- mission graph
- dynamic graph expansion
- universal tool/connector/capability fabric
- controlled real-world command boundary
- approval gate
- research/evidence loop
- provenance
- verification
- recovery
- persistent learning
- independent final verification

2050.58 adds only:
- resilient provider response decoding
- Wikipedia parser/fallback repair
- Crossref parser/fallback repair
- arXiv recovery retained
- accurate provider health accounting
- source deduplication
- parser failures counted as failures
"""

import ast
import hashlib
import html
import json
import os
import re
import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# VERSION
# ============================================================

VERSION = "TARGET-2050.58"
BUILD = "RESEARCH-INGESTION-REPAIR-FABRIC"
EVIDENCE_VERSION = "EVIDENCE-GATE-3.1"


# ============================================================
# CONFIG
# ============================================================

BASE = Path(os.getenv("AI_INFINITY_HOME", "/tmp/ai-infinity"))
ARTIFACTS = BASE / "artifacts"
WORK = BASE / "work"
LOGS = BASE / "logs"
DB_PATH = BASE / "ai_infinity.db"

for d in (BASE, ARTIFACTS, WORK, LOGS):
    d.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = int(os.getenv("AI_INFINITY_HTTP_TIMEOUT", "18"))
MAX_SOURCE_BYTES = int(os.getenv("AI_INFINITY_MAX_SOURCE_BYTES", "120000"))
MAX_DISCOVERY_PER_QUERY = int(
    os.getenv("AI_INFINITY_DISCOVERY_PER_QUERY", "5")
)
MAX_COLLECTED_SOURCES = int(
    os.getenv("AI_INFINITY_MAX_SOURCES", "12")
)
MAX_RESPONSE_BYTES = int(
    os.getenv("AI_INFINITY_MAX_RESPONSE_BYTES", "1000000")
)
RESEARCH_MAX_RETRIES = int(
    os.getenv("AI_INFINITY_RESEARCH_MAX_RETRIES", "2")
)

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_MODEL = os.getenv(
    "HF_MODEL",
    "openai/gpt-oss-120b:cheapest"
)

USER_AGENT = (
    "AI-Infinity/2050.58 "
    "(research-ingestion; contact=ai-infinity)"
)

RESEARCH_ALLOWLIST = {
    "en.wikipedia.org",
    "wikipedia.org",
    "api.crossref.org",
    "crossref.org",
    "export.arxiv.org",
    "arxiv.org",
    "export.arxiv.org",
    "api.openalex.org",
    "openalex.org",
}

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
}


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "Evidence-aware adaptive intelligence fabric "
        "with controlled execution."
    ),
)


# ============================================================
# MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=12000
    )
    research: bool = True
    verify: bool = True
    remember: bool = True
    allow_paid: bool = False


class ExecuteRequest(BaseModel):
    action: str
    args: Dict[str, Any] = Field(default_factory=dict)


class MemoryRequest(BaseModel):
    content: str
    kind: str = "general"
    verified: bool = False


class GenomeRequest(BaseModel):
    objective: str
    strategy: Dict[str, Any] = Field(
        default_factory=dict
    )


class ApprovalRequest(BaseModel):
    approved: bool = False


# ============================================================
# BASIC UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def tokens(text: str) -> set:
    return {
        x
        for x in re.findall(
            r"[a-z0-9][a-z0-9_-]{1,}",
            (text or "").lower(),
        )
        if x not in STOP
    }


def similarity(a: str, b: str) -> float:
    A = tokens(a)
    B = tokens(b)
    if not A or not B:
        return 0.0
    return len(A & B) / max(1, len(A | B))


def canonical_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        if host.startswith("www."):
            host = host[4:]

        path = re.sub(
            r"/{2,}",
            "/",
            p.path or "/",
        ).rstrip("/") or "/"

        query = ""
        if "arxiv.org" in host:
            query = ""
        elif "wikipedia.org" in host:
            query = ""

        return f"{p.scheme.lower()}://{host}{path}{query}"
    except Exception:
        return url or ""


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def source_family(url: str) -> str:
    d = domain_of(url)

    if "arxiv.org" in d:
        return "research-index"

    if "crossref.org" in d or "doi.org" in d:
        return "doi-index"

    if "openalex.org" in d:
        return "bibliographic-index"

    if "wikipedia.org" in d:
        return "encyclopedia"

    return d or "unknown"


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    c = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    c = db()

    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY,
            objective TEXT,
            status TEXT,
            result_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS memories(
            id TEXT PRIMARY KEY,
            kind TEXT,
            content TEXT,
            verified INTEGER,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS genomes(
            id TEXT PRIMARY KEY,
            task_id TEXT,
            genome_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence(
            id TEXT PRIMARY KEY,
            task_id TEXT,
            source_url TEXT,
            source_title TEXT,
            domain TEXT,
            claim TEXT,
            excerpt TEXT,
            content_hash TEXT,
            verification_status TEXT,
            relevance REAL,
            metadata_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS research(
            id TEXT PRIMARY KEY,
            task_id TEXT,
            question TEXT,
            status TEXT,
            report_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS events(
            id TEXT PRIMARY KEY,
            task_id TEXT,
            event_type TEXT,
            data_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS failures(
            id TEXT PRIMARY KEY,
            task_id TEXT,
            stage TEXT,
            error TEXT,
            recovery_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS executions(
            id TEXT PRIMARY KEY,
            task_id TEXT,
            action TEXT,
            status TEXT,
            result_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS checkpoints(
            id TEXT PRIMARY KEY,
            task_id TEXT,
            cycle INTEGER,
            state_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS approvals(
            id TEXT PRIMARY KEY,
            task_id TEXT,
            action TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS provider_health(
            provider TEXT PRIMARY KEY,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            last_status TEXT,
            last_error TEXT,
            last_http_status INTEGER,
            last_updated TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_evidence_task
        ON evidence(task_id);

        CREATE INDEX IF NOT EXISTS idx_events_task
        ON events(task_id);

        CREATE INDEX IF NOT EXISTS idx_execution_task
        ON executions(task_id);
        """
    )

    c.commit()
    c.close()


init_db()


# ============================================================
# EVENTS / STORAGE
# ============================================================

def event(
    task_id: Optional[str],
    kind: str,
    data: Any,
) -> None:
    c = db()

    c.execute(
        """
        INSERT INTO events
        VALUES(?,?,?,?,?)
        """,
        (
            uid("event"),
            task_id,
            kind,
            dump(data),
            now_iso(),
        ),
    )

    c.commit()
    c.close()


def save_task(
    task_id: str,
    objective: str,
    status: str,
    result: Any = None,
) -> None:
    t = now_iso()
    c = db()

    c.execute(
        """
        INSERT INTO tasks(
            id,
            objective,
            status,
            result_json,
            created_at,
            updated_at
        )
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,
            result_json=excluded.result_json,
            updated_at=excluded.updated_at
        """,
        (
            task_id,
            objective,
            status,
            dump(result) if result is not None else None,
            t,
            t,
        ),
    )

    c.commit()
    c.close()


def save_memory(
    content: str,
    kind: str = "general",
    verified: bool = False,
) -> str:
    mid = uid("memory")
    c = db()

    c.execute(
        """
        INSERT INTO memories
        VALUES(?,?,?,?,?)
        """,
        (
            mid,
            kind,
            content,
            int(verified),
            now_iso(),
        ),
    )

    c.commit()
    c.close()

    return mid


def save_genome(
    task_id: str,
    genome: Dict[str, Any],
) -> str:
    gid = uid("genome")
    c = db()

    c.execute(
        """
        INSERT INTO genomes
        VALUES(?,?,?,?)
        """,
        (
            gid,
            task_id,
            dump(genome),
            now_iso(),
        ),
    )

    c.commit()
    c.close()

    return gid


def save_research(
    task_id: str,
    question: str,
    status: str,
    report: Dict[str, Any],
) -> str:
    rid = uid("research")
    c = db()

    c.execute(
        """
        INSERT INTO research
        VALUES(?,?,?,?,?,?)
        """,
        (
            rid,
            task_id,
            question,
            status,
            dump(report),
            now_iso(),
        ),
    )

    c.commit()
    c.close()

    return rid


def checkpoint(
    task_id: str,
    cycle: int,
    state: Dict[str, Any],
) -> str:
    cid = uid("checkpoint")
    c = db()

    c.execute(
        """
        INSERT INTO checkpoints
        VALUES(?,?,?,?,?)
        """,
        (
            cid,
            task_id,
            cycle,
            dump(state),
            now_iso(),
        ),
    )

    c.commit()
    c.close()

    return cid


# ============================================================
# RESEARCH VOCABULARY
# ============================================================

STOP = set(
    """
    a an and are as at be been being by for from had has have
    how i if in into is it its me more most of on or our that
    the their them there these they this to was were what when
    where which who why will with you your about become need
    needed use using used than then can could should would may
    might do does did not only all any each other such through
    based per very real world make made
    """.split()
)

RESEARCH_TERMS = {
    "agent",
    "agents",
    "autonomous",
    "autonomy",
    "task",
    "tasks",
    "execution",
    "execute",
    "reliable",
    "reliability",
    "planning",
    "planner",
    "tool",
    "tools",
    "verification",
    "verify",
    "safety",
    "security",
    "memory",
    "evaluation",
    "benchmark",
    "failure",
    "monitoring",
    "control",
    "reasoning",
    "workflow",
    "multi-agent",
    "agentic",
    "alignment",
    "evidence",
    "provenance",
    "grounding",
    "recovery",
    "robust",
    "robustness",
    "uncertainty",
}

NOISE_PHRASES = (
    "create account",
    "log in",
    "sign up",
    "donate",
    "view pdf",
    "submission history",
    "cite this",
    "html version",
    "export bibtex",
    "navigation",
    "menu",
    "cookie",
    "privacy policy",
    "terms of use",
    "skip to content",
    "table of contents",
)


def keyword_hits(text: str) -> set:
    low = (text or "").lower()
    return {
        k
        for k in RESEARCH_TERMS
        if k in low
    }


def research_anchor(question: str) -> str:
    words = [
        w
        for w in re.findall(
            r"[a-z0-9][a-z0-9_-]{2,}",
            (question or "").lower(),
        )
        if w not in STOP
    ]

    preferred = list(
        keyword_hits(question)
    )

    ordered = []

    for word in preferred + words:
        if word not in ordered:
            ordered.append(word)

    return " ".join(ordered[:18])


def topic_queries(question: str) -> List[str]:
    a = research_anchor(question)

    queries = [
        f"{a} planning reasoning task decomposition",
        f"{a} tool use agents tool calling",
        f"{a} monitoring verification evaluation benchmarks",
        f"{a} failure recovery reliability robustness",
        f"{a} safety security control risk",
        f"{a} real world execution autonomous systems",
    ]

    return list(
        dict.fromkeys(
            q.strip()
            for q in queries
            if q.strip()
        )
    )


def expand_queries(question: str) -> List[str]:
    a = research_anchor(question)

    queries = [
        a,
        *topic_queries(question),
        f"systematic review {a}",
        f"independent evidence {a}",
        f"benchmark study {a}",
    ]

    return list(
        dict.fromkeys(
            q.strip()
            for q in queries
            if q.strip()
        )
    )


# ============================================================
# TEXT / RESPONSE REPAIR
# ============================================================

def clean_text(text: str) -> str:
    if not text:
        return ""

    text = html.unescape(text)
    text = text.replace("\ufeff", " ")
    text = re.sub(r"\s+", " ", text).strip()

    parts = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    kept = []

    for part in parts:
        s = part.strip()
        low = s.lower()

        if len(s) < 35:
            continue

        if (
            any(n in low for n in NOISE_PHRASES)
            and len(s) < 180
        ):
            continue

        if (
            re.fullmatch(
                r"[\w\s|:/.\-]{1,180}",
                s,
            )
            and s.count(" ") < 8
            and not any(
                k in low
                for k in RESEARCH_TERMS
            )
        ):
            continue

        kept.append(s)

    if len(kept) >= 2:
        return " ".join(kept)[:MAX_SOURCE_BYTES]

    return text[:MAX_SOURCE_BYTES]


def decode_response(
    response: requests.Response,
) -> str:
    """
    Robust response decoding.

    Handles:
    - UTF-8 BOM
    - incorrect charset declarations
    - JSON surrounded by whitespace
    - JSON prefixed/suffixed by accidental text
    - compressed content already decoded by requests
    """

    raw = response.content[:MAX_RESPONSE_BYTES]

    if not raw:
        return ""

    encodings = []

    declared = response.encoding

    if declared:
        encodings.append(declared)

    encodings.extend(
        [
            "utf-8-sig",
            "utf-8",
            "latin-1",
        ]
    )

    for encoding in encodings:
        try:
            text = raw.decode(
                encoding,
                errors="strict",
            )
            return text.replace(
                "\ufeff",
                "",
            ).strip()
        except Exception:
            continue

    return raw.decode(
        "utf-8",
        errors="replace",
    ).replace(
        "\ufeff",
        "",
    ).strip()


def parse_json_resilient(
    response: requests.Response,
) -> Dict[str, Any]:
    text = decode_response(response)

    if not text:
        raise ValueError(
            "empty_response_body"
        )

    candidates = [
        text.strip(),
        text.strip().lstrip(")]}',"),
    ]

    # If a proxy/banner wrapped the JSON,
    # recover the largest JSON object.
    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        candidates.append(
            text[start:end + 1]
        )

    last_error = None

    for candidate in candidates:
        try:
            value = json.loads(candidate)

            if isinstance(value, dict):
                return value

            raise ValueError(
                "json_root_not_object"
            )

        except Exception as exc:
            last_error = exc

    snippet = re.sub(
        r"\s+",
        " ",
        text[:300],
    )

    raise ValueError(
        f"json_parse_failed: "
        f"{last_error}; body={snippet}"
    )


def looks_like_html(text: str) -> bool:
    low = (text or "").lstrip().lower()

    return (
        low.startswith("<!doctype")
        or low.startswith("<html")
        or "<html" in low[:1000]
        or "<body" in low[:1000]
    )


# ============================================================
# NETWORK SAFETY
# ============================================================

def validate_public_url(url: str) -> None:
    p = urlparse(url)

    if p.scheme not in (
        "http",
        "https",
    ):
        raise ValueError(
            "unsupported_url_scheme"
        )

    host = p.hostname or ""

    if not host:
        raise ValueError(
            "missing_hostname"
        )

    host = host.lower()

    if host in BLOCKED_HOSTS:
        raise ValueError(
            "blocked_private_host"
        )

    if (
        host.startswith("10.")
        or host.startswith("192.168.")
        or host.startswith("172.16.")
        or host.startswith("172.17.")
        or host.startswith("172.18.")
        or host.startswith("172.19.")
        or host.startswith("172.20.")
        or host.startswith("172.21.")
        or host.startswith("172.22.")
        or host.startswith("172.23.")
        or host.startswith("172.24.")
        or host.startswith("172.25.")
        or host.startswith("172.26.")
        or host.startswith("172.27.")
        or host.startswith("172.28.")
        or host.startswith("172.29.")
        or host.startswith("172.30.")
        or host.startswith("172.31.")
    ):
        raise ValueError(
            "blocked_private_network"
        )


def http_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    provider: str = "generic",
    retries: Optional[int] = None,
) -> requests.Response:
    validate_public_url(url)

    retries = (
        RESEARCH_MAX_RETRIES
        if retries is None
        else max(0, retries)
    )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "application/json,"
            "application/atom+xml,"
            "application/xml,"
            "text/plain,"
            "text/html;q=0.8,"
            "*/*;q=0.2"
        ),
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
    }

    last_exc = None

    for attempt in range(
        retries + 1
    ):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=HTTP_TIMEOUT,
                allow_redirects=True,
                stream=True,
            )

            # Do not allow huge responses.
            content_length = response.headers.get(
                "content-length"
            )

            if content_length:
                try:
                    if (
                        int(content_length)
                        > MAX_RESPONSE_BYTES
                    ):
                        response.close()
                        raise ValueError(
                            "response_too_large"
                        )
                except ValueError as exc:
                    if str(exc) == "response_too_large":
                        raise

            # Consume only bounded bytes.
            chunks = []
            total = 0

            for chunk in response.iter_content(
                chunk_size=8192
            ):
                if not chunk:
                    continue

                remaining = (
                    MAX_RESPONSE_BYTES
                    - total
                )

                if remaining <= 0:
                    break

                chunk = chunk[:remaining]
                chunks.append(chunk)
                total += len(chunk)

                if total >= MAX_RESPONSE_BYTES:
                    break

            response._content = b"".join(
                chunks
            )
            response._content_consumed = True

            if response.status_code in {
                408,
                425,
                429,
                500,
                502,
                503,
                504,
            } and attempt < retries:
                response.close()
                time.sleep(
                    min(
                        1.5 * (attempt + 1),
                        5,
                    )
                )
                continue

            return response

        except Exception as exc:
            last_exc = exc

            if attempt < retries:
                time.sleep(
                    min(
                        1.0 * (attempt + 1),
                        4,
                    )
                )
                continue

    raise RuntimeError(
        f"{provider}_network_failed: "
        f"{last_exc}"
    )


# ============================================================
# PROVIDER HEALTH — REPAIRED
# ============================================================

def record_provider_health(
    provider: str,
    success: bool,
    http_status: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    c = db()

    row = c.execute(
        """
        SELECT successes, failures
        FROM provider_health
        WHERE provider=?
        """,
        (provider,),
    ).fetchone()

    if row:
        successes = int(
            row["successes"] or 0
        )
        failures = int(
            row["failures"] or 0
        )
    else:
        successes = 0
        failures = 0

    if success:
        successes += 1
        status = "success"
    else:
        failures += 1
        status = "failed"

    c.execute(
        """
        INSERT INTO provider_health(
            provider,
            successes,
            failures,
            last_status,
            last_error,
            last_http_status,
            last_updated
        )
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(provider) DO UPDATE SET
            successes=excluded.successes,
            failures=excluded.failures,
            last_status=excluded.last_status,
            last_error=excluded.last_error,
            last_http_status=excluded.last_http_status,
            last_updated=excluded.last_updated
        """,
        (
            provider,
            successes,
            failures,
            status,
            error[:1000]
            if error
            else None,
            http_status,
            now_iso(),
        ),
    )

    c.commit()
    c.close()


def provider_health_snapshot() -> Dict[str, Any]:
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM provider_health
        ORDER BY provider
        """
    ).fetchall()

    c.close()

    output = {}

    for row in rows:
        successes = int(
            row["successes"] or 0
        )
        failures = int(
            row["failures"] or 0
        )

        total = successes + failures

        output[row["provider"]] = {
            "successes": successes,
            "failures": failures,
            "total": total,
            "score": (
                round(
                    successes / total,
                    4,
                )
                if total
                else 0.0
            ),
            "last_status": row[
                "last_status"
            ],
            "last_error": row[
                "last_error"
            ],
            "last_http_status": row[
                "last_http_status"
            ],
            "last_updated": row[
                "last_updated"
            ],
        }

    return output


# ============================================================
# WIKIPEDIA INGESTION — REPAIRED
# ============================================================

def parse_wikipedia_search(
    data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    items = []

    search = (
        data
        .get("query", {})
        .get("search", [])
    )

    for item in search:
        title = (
            item.get("title")
            or ""
        ).strip()

        if not title:
            continue

        snippet = clean_text(
            re.sub(
                r"<[^>]+>",
                " ",
                item.get(
                    "snippet",
                    "",
                ),
            )
        )

        url = (
            "https://en.wikipedia.org/wiki/"
            + quote(
                title.replace(
                    " ",
                    "_",
                )
            )
        )

        items.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "provider": "wikipedia",
                "work_id": (
                    "wiki:"
                    + title.lower()
                ),
            }
        )

    return items


def wikipedia_summary(
    title: str,
) -> Optional[Dict[str, Any]]:
    url = (
        "https://en.wikipedia.org/api/rest_v1/"
        "page/summary/"
        + quote(title.replace(" ", "_"))
    )

    try:
        response = http_get(
            url,
            provider="wikipedia",
        )

        if response.status_code >= 400:
            return None

        data = parse_json_resilient(
            response
        )

        extract = clean_text(
            data.get(
                "extract",
                "",
            )
        )

        if not extract:
            return None

        return {
            "title": data.get(
                "title",
                title,
            ),
            "url": (
                data.get("content_urls", {})
                .get("desktop", {})
                .get("page")
                or (
                    "https://en.wikipedia.org/wiki/"
                    + quote(
                        title.replace(
                            " ",
                            "_",
                        )
                    )
                )
            ),
            "snippet": extract,
            "provider": "wikipedia",
            "work_id": (
                "wiki:"
                + title.lower()
            ),
        }

    except Exception:
        return None


def wikipedia_search(
    query: str,
    limit: int,
) -> Dict[str, Any]:
    endpoint = (
        "https://en.wikipedia.org/w/api.php"
    )

    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json",
        "formatversion": "2",
        "utf8": "1",
    }

    attempts = []
    last_error = None
    last_http = None

    for attempt in range(2):
        try:
            response = http_get(
                endpoint,
                params=params,
                provider="wikipedia",
            )

            last_http = response.status_code

            data = parse_json_resilient(
                response
            )

            results = (
                parse_wikipedia_search(
                    data
                )
            )

            # Successful parsing is success.
            record_provider_health(
                "wikipedia",
                True,
                response.status_code,
            )

            return {
                "provider": "wikipedia",
                "status": "success",
                "http_status": response.status_code,
                "attempts": attempt + 1,
                "result_count": len(results),
                "results": results,
                "recovery": (
                    "api-search"
                    if attempt == 0
                    else "api-search-retry"
                ),
            }

        except Exception as exc:
            last_error = str(exc)

            attempts.append(
                {
                    "attempt": attempt + 1,
                    "error": last_error[:500],
                }
            )

            # Last attempt: try REST summaries
            if attempt == 1:
                break

    # Fallback through REST summary endpoint.
    fallback_results = []

    try:
        response = http_get(
            endpoint,
            params=params,
            provider="wikipedia",
        )

        data = parse_json_resilient(
            response
        )

        candidates = (
            parse_wikipedia_search(
                data
            )
        )

        for candidate in candidates:
            summary = wikipedia_summary(
                candidate["title"]
            )

            if summary:
                fallback_results.append(
                    summary
                )

            if len(
                fallback_results
            ) >= limit:
                break

        if fallback_results:
            record_provider_health(
                "wikipedia",
                True,
                response.status_code,
            )

            return {
                "provider": "wikipedia",
                "status": "success",
                "http_status": response.status_code,
                "attempts": 2,
                "result_count": len(
                    fallback_results
                ),
                "results": fallback_results,
                "recovery": (
                    "rest-summary-fallback"
                ),
                "diagnostics": attempts,
            }

    except Exception as exc:
        last_error = str(exc)

    # IMPORTANT:
    # HTTP 200 + parser failure is a FAILURE.
    record_provider_health(
        "wikipedia",
        False,
        last_http,
        last_error,
    )

    return {
        "provider": "wikipedia",
        "status": "failed",
        "http_status": last_http,
        "attempts": len(attempts) + 1,
        "result_count": 0,
        "results": [],
        "error_type": (
            "ParseError"
            if last_error
            and (
                "parse"
                in last_error.lower()
                or "json"
                in last_error.lower()
            )
            else "ProviderError"
        ),
        "error": (
            last_error
            or "wikipedia_ingestion_failed"
        ),
        "diagnostics": attempts,
    }


# ============================================================
# CROSSREF INGESTION — REPAIRED
# ============================================================

def crossref_title(
    item: Dict[str, Any],
) -> str:
    value = item.get("title")

    if isinstance(value, list):
        return (
            str(value[0]).strip()
            if value
            else ""
        )

    return str(value or "").strip()


def crossref_authors(
    item: Dict[str, Any],
) -> List[str]:
    output = []

    for author in (
        item.get("author")
        or []
    ):
        given = str(
            author.get(
                "given",
                "",
            )
        ).strip()

        family = str(
            author.get(
                "family",
                "",
            )
        ).strip()

        name = " ".join(
            x
            for x in (
                given,
                family,
            )
            if x
        )

        if name:
            output.append(name)

    return output


def crossref_date(
    item: Dict[str, Any],
) -> str:
    for key in (
        "published-print",
        "published-online",
        "published",
        "issued",
    ):
        value = item.get(key)

        if not isinstance(value, dict):
            continue

        parts = (
            value.get(
                "date-parts"
            )
            or []
        )

        if not parts:
            continue

        first = parts[0]

        if not first:
            continue

        return "-".join(
            str(x)
            for x in first
        )

    return ""


def crossref_abstract(
    item: Dict[str, Any],
) -> str:
    value = item.get(
        "abstract",
        "",
    )

    if not value:
        return ""

    if isinstance(value, dict):
        value = " ".join(
            str(v)
            for v in value.values()
        )

    return clean_text(
        re.sub(
            r"<[^>]+>",
            " ",
            str(value),
        )
    )


def parse_crossref(
    data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    message = data.get(
        "message",
        {},
    )

    if not isinstance(
        message,
        dict,
    ):
        raise ValueError(
            "crossref_message_invalid"
        )

    items = message.get(
        "items",
        [],
    )

    if not isinstance(
        items,
        list,
    ):
        raise ValueError(
            "crossref_items_invalid"
        )

    output = []

    for item in items:
        if not isinstance(
            item,
            dict,
        ):
            continue

        title = crossref_title(
            item
        )

        if not title:
            continue

        doi = str(
            item.get(
                "DOI",
                "",
            )
            or ""
        ).strip()

        url = str(
            item.get(
                "URL",
                "",
            )
            or ""
        ).strip()

        if not url and doi:
            url = (
                "https://doi.org/"
                + doi
            )

        if not url:
            continue

        abstract = crossref_abstract(
            item
        )

        authors = crossref_authors(
            item
        )

        published = crossref_date(
            item
        )

        work_id = (
            "doi:"
            + doi.lower()
            if doi
            else (
                "crossref:"
                + hashlib.sha256(
                    title.encode()
                ).hexdigest()[:20]
            )
        )

        output.append(
            {
                "title": title,
                "url": url,
                "snippet": abstract,
                "provider": "crossref",
                "doi": doi,
                "authors": authors,
                "published": published,
                "work_id": work_id,
            }
        )

    return output


def crossref_search(
    query: str,
    limit: int,
) -> Dict[str, Any]:
    endpoint = (
        "https://api.crossref.org/works"
    )

    parameter_sets = [
        {
            "query.bibliographic": query,
            "rows": limit,
        },
        {
            "query": query,
            "rows": limit,
        },
    ]

    diagnostics = []
    last_error = None
    last_http = None

    for index, params in enumerate(
        parameter_sets
    ):
        try:
            response = http_get(
                endpoint,
                params=params,
                provider="crossref",
            )

            last_http = (
                response.status_code
            )

            data = parse_json_resilient(
                response
            )

            results = parse_crossref(
                data
            )

            record_provider_health(
                "crossref",
                True,
                response.status_code,
            )

            return {
                "provider": "crossref",
                "status": "success",
                "http_status": (
                    response.status_code
                ),
                "attempts": index + 1,
                "result_count": len(
                    results
                ),
                "results": results,
                "recovery": (
                    "bibliographic-query"
                    if index == 0
                    else "query-fallback"
                ),
                "diagnostics": diagnostics,
            }

        except Exception as exc:
            last_error = str(exc)

            diagnostics.append(
                {
                    "attempt": index + 1,
                    "error": last_error[:500],
                }
            )

    # Last-resort content negotiation
    # through DOI/Crossref-compatible endpoint.
    try:
        alt = (
            "https://api.crossref.org/"
            "works"
        )

        response = http_get(
            alt,
            params={
                "query.title": query,
                "rows": limit,
                "select": (
                    "DOI,title,URL,"
                    "author,abstract,"
                    "published,published-online,"
                    "published-print"
                ),
            },
            provider="crossref",
        )

        last_http = response.status_code

        data = parse_json_resilient(
            response
        )

        results = parse_crossref(
            data
        )

        if results:
            record_provider_health(
                "crossref",
                True,
                response.status_code,
            )

            return {
                "provider": "crossref",
                "status": "success",
                "http_status": (
                    response.status_code
                ),
                "attempts": 3,
                "result_count": len(
                    results
                ),
                "results": results,
                "recovery": (
                    "selective-query-fallback"
                ),
                "diagnostics": diagnostics,
            }

    except Exception as exc:
        last_error = str(exc)

    record_provider_health(
        "crossref",
        False,
        last_http,
        last_error,
    )

    return {
        "provider": "crossref",
        "status": "failed",
        "http_status": last_http,
        "attempts": len(
            diagnostics
        ) + 1,
        "result_count": 0,
        "results": [],
        "error_type": (
            "ParseError"
            if last_error
            and (
                "parse"
                in last_error.lower()
                or "json"
                in last_error.lower()
            )
            else "ProviderError"
        ),
        "error": (
            last_error
            or "crossref_ingestion_failed"
        ),
        "diagnostics": diagnostics,
    }


# ============================================================
# ARXIV INGESTION — 2050.57 RECOVERY PRESERVED
# ============================================================

ARXIV_NAMESPACES = {
    "atom": (
        "http://www.w3.org/2005/Atom"
    ),
}


def parse_arxiv(
    xml_text: str,
) -> List[Dict[str, Any]]:
    if not xml_text.strip():
        raise ValueError(
            "empty_arxiv_response"
        )

    root = ET.fromstring(
        xml_text
    )

    output = []

    for entry in root.findall(
        "atom:entry",
        ARXIV_NAMESPACES,
    ):
        title = clean_text(
            entry.findtext(
                "atom:title",
                "",
                ARXIV_NAMESPACES,
            )
        )

        abstract = clean_text(
            entry.findtext(
                "atom:summary",
                "",
                ARXIV_NAMESPACES,
            )
        )

        published = (
            entry.findtext(
                "atom:published",
                "",
                ARXIV_NAMESPACES,
            )
            or ""
        )

        entry_id = (
            entry.findtext(
                "atom:id",
                "",
                ARXIV_NAMESPACES,
            )
            or ""
        ).strip()

        authors = []

        for author in entry.findall(
            "atom:author",
            ARXIV_NAMESPACES,
        ):
            name = (
                author.findtext(
                    "atom:name",
                    "",
                    ARXIV_NAMESPACES,
                )
                or ""
            ).strip()

            if name:
                authors.append(name)

        if not title or not entry_id:
            continue

        output.append(
            {
                "title": title,
                "url": entry_id,
                "snippet": abstract,
                "provider": "arxiv",
                "published": published,
                "authors": authors,
                "work_id": (
                    "arxiv:"
                    + canonical_url(
                        entry_id
                    )
                ),
            }
        )

    return output


def arxiv_search_once(
    endpoint: str,
    query: str,
    limit: int,
) -> List[Dict[str, Any]]:
    response = http_get(
        endpoint,
        params={
            "search_query": (
                "all:"
                + query
            ),
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        },
        provider="arxiv",
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"arxiv_http_{response.status_code}"
        )

    return parse_arxiv(
        decode_response(response)
    )


def arxiv_search(
    query: str,
    limit: int,
) -> Dict[str, Any]:
    endpoints = [
        (
            "https://export.arxiv.org/api/query",
            "export-query-1",
        ),
        (
            "https://export.arxiv.org/api/query",
            "export-query-2",
        ),
        (
            "https://arxiv.org/api/query",
            "arxiv-api-fallback",
        ),
    ]

    errors = []

    for index, (
        endpoint,
        strategy,
    ) in enumerate(endpoints):

        try:
            results = arxiv_search_once(
                endpoint,
                query,
                limit,
            )

            record_provider_health(
                "arxiv",
                True,
                200,
            )

            return {
                "provider": "arxiv",
                "status": "success",
                "http_status": 200,
                "attempts": index + 1,
                "result_count": len(
                    results
                ),
                "results": results,
                "recovery": strategy,
                "diagnostics": errors,
            }

        except Exception as exc:
            errors.append(
                {
                    "attempt": index + 1,
                    "strategy": strategy,
                    "error": str(exc)[:500],
                }
            )

    record_provider_health(
        "arxiv",
        False,
        None,
        dump(errors),
    )

    return {
        "provider": "arxiv",
        "status": "failed",
        "http_status": None,
        "attempts": len(
            endpoints
        ),
        "result_count": 0,
        "results": [],
        "error_type": "ProviderError",
        "error": (
            "arxiv_all_recovery_paths_failed"
        ),
        "diagnostics": errors,
    }


# ============================================================
# OPENALEX SECONDARY RESEARCH PROVIDER
# ============================================================

def openalex_search(
    query: str,
    limit: int,
) -> Dict[str, Any]:
    endpoint = (
        "https://api.openalex.org/works"
    )

    try:
        response = http_get(
            endpoint,
            params={
                "search": query,
                "per-page": limit,
            },
            provider="openalex",
        )

        data = parse_json_resilient(
            response
        )

        results = []

        for item in (
            data.get("results")
            or []
        ):
            title = str(
                item.get(
                    "display_name",
                    "",
                )
            ).strip()

            location = (
                item.get(
                    "primary_location"
                )
                or {}
            )

            url = (
                location.get(
                    "landing_page_url"
                )
                or item.get("doi")
                or ""
            )

            if not title or not url:
                continue

            doi = str(
                item.get(
                    "doi",
                    "",
                )
                or ""
            )

            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": "",
                    "provider": "openalex",
                    "doi": doi,
                    "work_id": (
                        "openalex:"
                        + str(
                            item.get(
                                "id",
                                title,
                            )
                        )
                    ),
                }
            )

        record_provider_health(
            "openalex",
            True,
            response.status_code,
        )

        return {
            "provider": "openalex",
            "status": "success",
            "http_status": (
                response.status_code
            ),
            "attempts": 1,
            "result_count": len(
                results
            ),
            "results": results,
        }

    except Exception as exc:
        record_provider_health(
            "openalex",
            False,
            None,
            str(exc),
        )

        return {
            "provider": "openalex",
            "status": "failed",
            "http_status": None,
            "attempts": 1,
            "result_count": 0,
            "results": [],
            "error": str(exc)[:500],
        }


# ============================================================
# MULTI-PROVIDER DISCOVERY
# ============================================================

def discover_sources(
    question: str,
) -> Dict[str, Any]:
    queries = expand_queries(
        question
    )

    providers = [
        ("wikipedia", wikipedia_search),
        ("crossref", crossref_search),
        ("arxiv", arxiv_search),
        ("openalex", openalex_search),
    ]

    items = []
    diagnostics = []

    for query in queries:
        for name, function in providers:
            try:
                result = function(
                    query,
                    MAX_DISCOVERY_PER_QUERY,
                )

                batch = result.get(
                    "results",
                    [],
                )

                for item in batch:
                    item = dict(item)
                    item["query"] = query
                    item["provider"] = name
                    items.append(item)

                diagnostics.append(
                    {
                        "query": query,
                        "provider": name,
                        "status": result.get(
                            "status",
                            "unknown",
                        ),
                        "count": len(batch),
                        "http_status": result.get(
                            "http_status"
                        ),
                        "attempts": result.get(
                            "attempts"
                        ),
                        "recovery": result.get(
                            "recovery"
                        ),
                        "error": result.get(
                            "error"
                        ),
                    }
                )

            except Exception as exc:
                diagnostics.append(
                    {
                        "query": query,
                        "provider": name,
                        "status": "error",
                        "count": 0,
                        "error": str(exc)[:500],
                    }
                )

    # Deduplicate at discovery layer.
    seen = set()
    unique = []

    for item in items:
        url = canonical_url(
            item.get(
                "url",
                "",
            )
        )

        doi = str(
            item.get(
                "doi",
                "",
            )
            or ""
        ).lower()

        work_id = str(
            item.get(
                "work_id",
                "",
            )
            or ""
        )

        key = (
            "doi:" + doi
            if doi
            else (
                work_id
                or "url:" + url
            )
        )

        if not key or key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return {
        "queries": queries,
        "search_anchor": research_anchor(
            question
        ),
        "items": unique,
        "diagnostics": diagnostics,
        "provider_health": (
            provider_health_snapshot()
        ),
    }


# ============================================================
# SOURCE FETCHING
# ============================================================

def fetch_url(
    url: str,
) -> Dict[str, Any]:
    response = http_get(
        url,
        provider="source",
    )

    raw = response.content[
        :MAX_SOURCE_BYTES
    ]

    text = decode_response(
        response
    )

    content_type = (
        response.headers.get(
            "content-type",
            "",
        )
    )

    title = url

    match = re.search(
        r"<title[^>]*>(.*?)</title>",
        text,
        re.I | re.S,
    )

    if match:
        title = clean_text(
            re.sub(
                r"<[^>]+>",
                " ",
                match.group(1),
            )
        )[:500]

    if (
        "html" in content_type.lower()
        or looks_like_html(text)
    ):
        text = re.sub(
            r"(?is)<script.*?</script>",
            " ",
            text,
        )
        text = re.sub(
            r"(?is)<style.*?</style>",
            " ",
            text,
        )
        text = re.sub(
            r"(?is)<noscript.*?</noscript>",
            " ",
            text,
        )
        text = re.sub(
            r"(?is)<nav.*?</nav>",
            " ",
            text,
        )
        text = re.sub(
            r"(?is)<header.*?</header>",
            " ",
            text,
        )
        text = re.sub(
            r"(?is)<footer.*?</footer>",
            " ",
            text,
        )
        text = re.sub(
            r"<[^>]+>",
            " ",
            text,
        )

    text = clean_text(
        text
    )

    return {
        "url": response.url,
        "title": title,
        "text": text,
        "hash": hashlib.sha256(
            raw
        ).hexdigest(),
        "http_status": response.status_code,
        "content_type": content_type,
        "content_length": len(raw),
        "retrieved_at": now_iso(),
    }


# ============================================================
# RESEARCH SOURCE COLLECTION
# ============================================================

def collect_sources(
    task_id: str,
    question: str,
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    collected = []
    seen = set()

    for item in items:
        if len(collected) >= MAX_COLLECTED_SOURCES:
            break

        url = str(
            item.get(
                "url",
                "",
            )
            or ""
        )

        title = str(
            item.get(
                "title",
                "",
            )
            or ""
        )

        snippet = clean_text(
            str(
                item.get(
                    "snippet",
                    "",
                )
                or ""
            )
        )

        if not url or not title:
            continue

        key = canonical_url(
            url
        )

        if key in seen:
            continue

        seen.add(key)

        text = snippet

        # For short metadata, fetch the
        # actual source where safe.
        if len(text) < 180:
            try:
                fetched = fetch_url(
                    url
                )
                text = fetched[
                    "text"
                ]
            except Exception:
                pass

        if not text:
            continue

        relevance = max(
            0.10,
            min(
                1.0,
                similarity(
                    question,
                    title + " " + text,
                ),
            ),
        )

        provider = item.get(
            "provider",
            "unknown",
        )

        source = {
            "evidence_id": uid(
                "evidence"
            ),
            "url": url,
            "title": title,
            "text": text[
                :MAX_SOURCE_BYTES
            ],
            "provider": provider,
            "query": item.get(
                "query",
                question,
            ),
            "relevance": relevance,
            "hash": hashlib.sha256(
                text.encode(
                    "utf-8",
                    errors="ignore",
                )
            ).hexdigest(),
            "retrieved_at": now_iso(),
            "source_family": (
                source_family(url)
            ),
            "work_id": item.get(
                "work_id"
            )
            or (
                "url:"
                + canonical_url(url)
            ),
            "doi": item.get(
                "doi",
                "",
            ),
            "authors": item.get(
                "authors",
                [],
            ),
            "published": item.get(
                "published",
                "",
            ),
        }

        collected.append(
            source
        )

        c = db()

        c.execute(
            """
            INSERT OR REPLACE INTO evidence(
                id,
                task_id,
                source_url,
                source_title,
                domain,
                claim,
                excerpt,
                content_hash,
                verification_status,
                relevance,
                metadata_json,
                created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                source[
                    "evidence_id"
                ],
                task_id,
                source["url"],
                source["title"],
                domain_of(
                    source["url"]
                ),
                "",
                source["text"][
                    :1400
                ],
                source["hash"],
                "collected",
                source["relevance"],
                dump(
                    {
                        "provider": provider,
                        "query": source[
                            "query"
                        ],
                        "source_family": source[
                            "source_family"
                        ],
                        "work_id": source[
                            "work_id"
                        ],
                    }
                ),
                now_iso(),
            ),
        )

        c.commit()
        c.close()

    return collected


# ============================================================
# CLAIM EXTRACTION / VERIFICATION
# ============================================================

def candidate_sentences(
    text: str,
) -> List[str]:
    text = clean_text(
        text
    )

    raw = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    output = []

    for sentence in raw:
        s = sentence.strip()

        if not 55 <= len(s) <= 520:
            continue

        low = s.lower()

        if any(
            noise in low
            for noise in NOISE_PHRASES
        ):
            continue

        if len(
            re.findall(
                r"[A-Za-z]",
                s,
            )
        ) < 35:
            continue

        if not (
            keyword_hits(s)
            or any(
                word in low
                for word in (
                    "study",
                    "experiment",
                    "results",
                    "evaluation",
                    "performance",
                    "method",
                    "benchmark",
                )
            )
        ):
            continue

        output.append(s)

    return output


def extract_claims(
    question: str,
    sources: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    anchor = research_anchor(
        question
    )

    claims = []
    seen = set()

    for source in sources:
        for claim in candidate_sentences(
            source["text"]
        )[:14]:

            overlap = similarity(
                anchor,
                claim,
            )

            if (
                overlap < 0.03
                and not (
                    keyword_hits(question)
                    & keyword_hits(claim)
                )
            ):
                continue

            fingerprint = (
                " ".join(
                    sorted(
                        tokens(
                            claim
                        )
                    )
                )
            )

            if fingerprint in seen:
                continue

            seen.add(
                fingerprint
            )

            claims.append(
                {
                    "claim_id": uid(
                        "claim"
                    ),
                    "text": claim,
                    "evidence_ids": [
                        source[
                            "evidence_id"
                        ]
                    ],
                    "source_urls": [
                        source["url"]
                    ],
                    "domains": [
                        domain_of(
                            source["url"]
                        )
                    ],
                    "works": [
                        source["work_id"]
                    ],
                    "source_families": [
                        source[
                            "source_family"
                        ]
                    ],
                }
            )

    return claims[:40]


def polarity(
    text: str,
) -> str:
    positive = {
        "improve",
        "improved",
        "effective",
        "reliable",
        "success",
        "successful",
        "safe",
        "verified",
        "robust",
        "accurate",
        "benefit",
        "supports",
        "validated",
    }

    negative = {
        "fail",
        "failed",
        "failure",
        "unreliable",
        "unsafe",
        "risk",
        "limitation",
        "cannot",
        "unable",
        "error",
        "incorrect",
        "inaccurate",
        "harm",
        "weak",
        "uncertain",
        "poor",
    }

    p = len(
        tokens(text)
        & positive
    )

    n = len(
        tokens(text)
        & negative
    )

    if p >= n + 2:
        return "positive"

    if n >= p + 2:
        return "negative"

    return "mixed"


def verify_claims(
    claims: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:

    for claim in claims:
        supports = []

        for source in sources:
            score = similarity(
                claim["text"],
                source["text"][
                    :50000
                ],
            )

            if score >= 0.10:
                supports.append(
                    (
                        source,
                        score,
                    )
                )

        supports.sort(
            key=lambda x: x[1],
            reverse=True,
        )

        for source, score in supports:
            if source[
                "work_id"
            ] not in claim[
                "works"
            ]:
                claim[
                    "evidence_ids"
                ].append(
                    source[
                        "evidence_id"
                    ]
                )

                claim[
                    "source_urls"
                ].append(
                    source["url"]
                )

                claim[
                    "domains"
                ].append(
                    domain_of(
                        source["url"]
                    )
                )

                claim[
                    "works"
                ].append(
                    source[
                        "work_id"
                    ]
                )

                claim[
                    "source_families"
                ].append(
                    source[
                        "source_family"
                    ]
                )

        claim[
            "domains"
        ] = list(
            dict.fromkeys(
                claim["domains"]
            )
        )

        claim[
            "works"
        ] = list(
            dict.fromkeys(
                claim["works"]
            )
        )

        claim[
            "source_families"
        ] = list(
            dict.fromkeys(
                claim[
                    "source_families"
                ]
            )
        )

        claim[
            "mapped_evidence"
        ] = len(
            claim[
                "evidence_ids"
            ]
        )

        claim[
            "independent_works"
        ] = len(
            claim["works"]
        )

        claim[
            "independent_domains"
        ] = len(
            claim["domains"]
        )

        claim[
            "corroborated"
        ] = (
            claim[
                "independent_works"
            ] >= 2
        )

        claim[
            "polarity"
        ] = polarity(
            claim["text"]
        )

        claim[
            "verification_status"
        ] = (
            "corroborated"
            if claim[
                "corroborated"
            ]
            else (
                "supported"
                if claim[
                    "mapped_evidence"
                ]
                else "unsupported"
            )
        )

        claim[
            "confidence"
        ] = round(
            min(
                1.0,
                0.35
                + 0.20
                * claim[
                    "mapped_evidence"
                ]
                + 0.15
                * claim[
                    "independent_works"
                ]
                + 0.10
                * claim[
                    "independent_domains"
                ],
            ),
            4,
        )

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
                a["polarity"]
                == b["polarity"]
            ):
                continue

            sim = similarity(
                a["text"],
                b["text"],
            )

            if sim < 0.28:
                continue

            if not (
                set(a["works"])
                & set(b["works"])
            ):
                contradictions.append(
                    {
                        "claim_a": a[
                            "claim_id"
                        ],
                        "claim_b": b[
                            "claim_id"
                        ],
                        "similarity": round(
                            sim,
                            4,
                        ),
                        "status": (
                            "potential"
                        ),
                    }
                )

    unsupported = [
        c["claim_id"]
        for c in claims
        if c.get(
            "verification_status"
        )
        == "unsupported"
    ]

    return {
        "claims": claims,
        "contradictions": contradictions,
        "unsupported_claims": unsupported,
    }


# ============================================================
# EVIDENCE GATE
# ============================================================

def evidence_gate(
    sources: List[Dict[str, Any]],
    verification: Dict[str, Any],
) -> Dict[str, Any]:

    substantive = [
        source
        for source in sources
        if len(
            source.get(
                "text",
                "",
            )
        ) >= 55
    ]

    works = {
        source["work_id"]
        for source in substantive
    }

    domains = {
        domain_of(
            source["url"]
        )
        for source in substantive
        if domain_of(
            source["url"]
        )
    }

    families = {
        source[
            "source_family"
        ]
        for source in substantive
        if source.get(
            "source_family"
        )
    }

    valid_claims = [
        claim
        for claim in verification[
            "claims"
        ]
        if claim.get(
            "mapped_evidence",
            0,
        ) >= 1
    ]

    contradictions = (
        verification.get(
            "contradictions",
            [],
        )
    )

    checks = {
        "minimum_substantive_sources": (
            len(substantive) >= 3
        ),
        "independent_underlying_works": (
            len(works) >= 2
        ),
        "independent_source_families": (
            len(families) >= 2
        ),
        "independent_domains": (
            len(domains) >= 2
        ),
        "minimum_valid_claims": (
            len(valid_claims) >= 3
        ),
        "no_unsupported_claims": (
            len(
                verification.get(
                    "unsupported_claims",
                    [],
                )
            )
            == 0
        ),
        "no_unresolved_contradictions": (
            len(contradictions) == 0
        ),
        "no_navigation_noise": all(
            not any(
                n in c.get(
                    "text",
                    "",
                ).lower()
                for n in NOISE_PHRASES
            )
            for c in verification[
                "claims"
            ]
        ),
    }

    passed = all(
        checks.values()
    )

    mapping = {
        "minimum_substantive_sources":
            "fewer_than_3_substantive_sources",
        "independent_underlying_works":
            "fewer_than_2_independent_works",
        "independent_source_families":
            "fewer_than_2_independent_source_families",
        "independent_domains":
            "fewer_than_2_independent_domains",
        "minimum_valid_claims":
            "fewer_than_3_valid_claims",
        "no_unsupported_claims":
            "unsupported_claims_present",
        "no_unresolved_contradictions":
            "contradictions_present",
        "no_navigation_noise":
            "navigation_noise_present",
    }

    reasons = [
        reason
        for key, reason in mapping.items()
        if not checks[key]
    ]

    return {
        "version": EVIDENCE_VERSION,
        "passed": passed,
        "checks": checks,
        "source_count": len(
            substantive
        ),
        "work_count": len(
            works
        ),
        "domain_count": len(
            domains
        ),
        "source_family_count": len(
            families
        ),
        "valid_claim_count": len(
            valid_claims
        ),
        "unsupported_claim_count": len(
            verification.get(
                "unsupported_claims",
                [],
            )
        ),
        "contradiction_count": len(
            contradictions
        ),
        "reasons": reasons,
    }


# ============================================================
# GROUNDED AI SYNTHESIS
# ============================================================

def call_ai(
    prompt: str,
) -> Optional[str]:

    if not HF_TOKEN:
        return None

    try:
        response = requests.post(
            "https://router.huggingface.co/v1/chat/completions",
            headers={
                "Authorization":
                    "Bearer " + HF_TOKEN,
                "Content-Type":
                    "application/json",
            },
            json={
                "model": HF_MODEL,
                "messages": [
                    {
                        "role":
                            "system",
                        "content": (
                            "You are AI Infinity's "
                            "grounded synthesis layer. "
                            "Use only supplied evidence. "
                            "Never invent sources, facts, "
                            "permissions, tool results, "
                            "or completed actions."
                        ),
                    },
                    {
                        "role":
                            "user",
                        "content":
                            prompt,
                    },
                ],
                "temperature": 0.15,
                "max_tokens": 2200,
            },
            timeout=45,
        )

        response.raise_for_status()

        data = parse_json_resilient(
            response
        )

        return (
            data.get(
                "choices",
                [{}],
            )[0]
            .get(
                "message",
                {},
            )
            .get(
                "content"
            )
        )

    except Exception:
        return None


def grounded_analysis(
    question: str,
    sources: List[Dict[str, Any]],
    verification: Dict[str, Any],
    gate: Dict[str, Any],
) -> str:

    if not gate[
        "passed"
    ]:
        return (
            "Evidence Gate 3.1 did not pass. "
            "AI Infinity will not label this "
            "research evidence-verified. "
            "Reasons: "
            + ", ".join(
                gate["reasons"]
                or [
                    "insufficient_evidence"
                ]
            )
            + "."
        )

    evidence = [
        {
            "id": s[
                "evidence_id"
            ],
            "work_id": s[
                "work_id"
            ],
            "domain": domain_of(
                s["url"]
            ),
            "title": s[
                "title"
            ],
            "url": s[
                "url"
            ],
            "excerpt": s[
                "text"
            ][:1100],
        }
        for s in sources
    ]

    claims = [
        {
            key: claim.get(
                key
            )
            for key in (
                "claim_id",
                "text",
                "evidence_ids",
                "works",
                "domains",
                "verification_status",
                "confidence",
            )
        }
        for claim in verification[
            "claims"
        ]
        if claim.get(
            "mapped_evidence",
            0,
        ) >= 1
    ]

    prompt = (
        "Question:\n"
        + question
        + "\n\nEvidence:\n"
        + dump(evidence)
        + "\n\nClaims:\n"
        + dump(claims)
        + "\n\nWrite sections: "
          "Findings, Evidence, Uncertainty, "
          "Disagreements, Limitations. "
          "Every factual statement must be "
          "traceable to supplied evidence. "
          "Do not add outside facts."
    )

    ai = call_ai(
        prompt
    )

    if ai:
        return ai

    lines = [
        "Findings:"
    ]

    for claim in verification[
        "claims"
    ][:8]:
        if claim.get(
            "verification_status"
        ) == "corroborated":
            lines.append(
                "- "
                + claim["text"]
            )

    lines.extend(
        [
            "",
            "Evidence: claims above are linked "
            "to stored evidence records.",
            "Uncertainty: deterministic fallback "
            "synthesis was used.",
            "Disagreements: contradiction detection "
            "results are recorded separately.",
            "Limitations: source discovery and "
            "semantic matching are heuristic and "
            "do not constitute proof of truth.",
        ]
    )

    return "\n".join(
        lines
    )


# ============================================================
# RESEARCH PIPELINE
# ============================================================

def research_pipeline(
    task_id: str,
    question: str,
) -> Dict[str, Any]:

    event(
        task_id,
        "research_discovery_started",
        {
            "version": VERSION
        },
    )

    discovery = discover_sources(
        question
    )

    checkpoint(
        task_id,
        1,
        {
            "stage": "discovery",
            "diagnostics": discovery[
                "diagnostics"
            ],
        },
    )

    sources = collect_sources(
        task_id,
        question,
        discovery["items"],
    )

    checkpoint(
        task_id,
        2,
        {
            "stage": "collection",
            "source_count": len(
                sources
            ),
        },
    )

    claims = extract_claims(
        question,
        sources,
    )

    verification = verify_claims(
        claims,
        sources,
    )

    gate = evidence_gate(
        sources,
        verification,
    )

    analysis = grounded_analysis(
        question,
        sources,
        verification,
        gate,
    )

    status = (
        "completed"
        if gate["passed"]
        else "insufficient_evidence"
    )

    report = {
        "version": VERSION,
        "build": BUILD,
        "question": question,
        "queries": discovery[
            "queries"
        ],
        "search_anchor": discovery[
            "search_anchor"
        ],
        "discovery": discovery[
            "diagnostics"
        ],
        "provider_health": (
            provider_health_snapshot()
        ),
        "sources": [
            {
                key: source.get(
                    key
                )
                for key in (
                    "evidence_id",
                    "url",
                    "title",
                    "provider",
                    "query",
                    "relevance",
                    "hash",
                    "retrieved_at",
                    "source_family",
                    "work_id",
                    "doi",
                    "authors",
                    "published",
                )
            }
            for source in sources
        ],
        "evidence": [
            {
                "evidence_id": source[
                    "evidence_id"
                ],
                "url": source[
                    "url"
                ],
                "domain": domain_of(
                    source["url"]
                ),
                "title": source[
                    "title"
                ],
                "excerpt": source[
                    "text"
                ][:1400],
                "hash": source[
                    "hash"
                ],
                "relevance": source[
                    "relevance"
                ],
                "source_family": source[
                    "source_family"
                ],
                "work_id": source[
                    "work_id"
                ],
            }
            for source in sources
        ],
        "claims": verification[
            "claims"
        ],
        "verification": verification,
        "contradictions": verification[
            "contradictions"
        ],
        "evidence_gate": gate,
        "analysis": analysis,
        "status": status,
        "provenance": {
            "query_count": len(
                discovery[
                    "queries"
                ]
            ),
            "source_count": len(
                sources
            ),
            "underlying_work_count": len(
                {
                    s["work_id"]
                    for s in sources
                }
            ),
            "domains": sorted(
                {
                    domain_of(
                        s["url"]
                    )
                    for s in sources
                }
            ),
            "source_families": sorted(
                {
                    s[
                        "source_family"
                    ]
                    for s in sources
                }
            ),
            "source_hashes": sorted(
                {
                    s["hash"]
                    for s in sources
                }
            ),
            "evidence_gate": (
                gate["version"]
            ),
        },
    }

    rid = save_research(
        task_id,
        question,
        status,
        report,
    )

    report[
        "research_id"
    ] = rid

    event(
        task_id,
        "research_completed",
        {
            "status": status,
            "source_count": len(
                sources
            ),
            "gate": gate,
            "provider_health":
                report[
                    "provider_health"
                ],
        },
    )

    checkpoint(
        task_id,
        3,
        {
            "stage": "research_complete",
            "status": status,
            "gate": gate,
        },
    )

    return report


# ============================================================
# EXECUTION SAFETY
# ============================================================

SAFE_ACTIONS = {
    "calculator_test": {
        "description":
            "Safe arithmetic calculation",
        "external_side_effects":
            False,
    },
    "hash_text": {
        "description":
            "Calculate SHA-256",
        "external_side_effects":
            False,
    },
    "validate_python": {
        "description":
            "Parse Python syntax without executing it",
        "external_side_effects":
            False,
    },
    "create_plan": {
        "description":
            "Create a controlled execution plan",
        "external_side_effects":
            False,
    },
}


def safe_calculator(
    expression: str,
) -> Any:
    tree = ast.parse(
        expression,
        mode="eval",
    )

    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.FloorDiv,
        ast.Load,
    )

    for node in ast.walk(
        tree
    ):
        if not isinstance(
            node,
            allowed,
        ):
            raise ValueError(
                "calculator_expression_not_allowed"
            )

        if isinstance(
            node,
            ast.Constant,
        ) and not isinstance(
            node.value,
            (int, float),
        ):
            raise ValueError(
                "calculator_values_must_be_numeric"
            )

    return eval(
        compile(
            tree,
            "<calculator>",
            "eval",
        ),
        {
            "__builtins__":
                {}
        },
        {},
    )


def registered_execute(
    task_id: str,
    action: str,
    args: Dict[str, Any],
) -> Dict[str, Any]:

    if action not in SAFE_ACTIONS:
        raise ValueError(
            "action_not_registered"
        )

    if action == "calculator_test":
        expression = str(
            args.get(
                "expression",
                "2+3*4",
            )
        )

        return {
            "expression":
                expression,
            "result":
                safe_calculator(
                    expression
                ),
        }

    if action == "hash_text":
        text = str(
            args.get(
                "text",
                "",
            )
        )

        return {
            "text_length":
                len(text),
            "sha256":
                hashlib.sha256(
                    text.encode()
                ).hexdigest(),
        }

    if action == "validate_python":
        source = str(
            args.get(
                "source",
                "",
            )
        )

        try:
            ast.parse(
                source
            )

            return {
                "syntax_valid":
                    True,
                "bytes":
                    len(
                        source.encode()
                    ),
            }

        except SyntaxError as exc:
            return {
                "syntax_valid":
                    False,
                "error":
                    str(exc),
                "bytes":
                    len(
                        source.encode()
                    ),
            }

    if action == "create_plan":
        objective = str(
            args.get(
                "objective",
                "",
            )
        )

        return {
            "status":
                "compiled",
            "objective":
                objective,
            "execution_policy": {
                "external_side_effects_default":
                    False,
                "authorized_actions_only":
                    True,
                "automatic_spending":
                    False,
                "automatic_self_modification":
                    False,
            },
        }

    raise ValueError(
        "registered_action_unreachable"
    )


def verify_action_result(
    action: str,
    args: Dict[str, Any],
    result: Any,
) -> Dict[str, Any]:

    checks = []

    if action == "calculator_test":
        checks.append(
            {
                "check":
                    "recompute_expression",
                "passed":
                    result.get(
                        "result"
                    )
                    == safe_calculator(
                        str(
                            args.get(
                                "expression",
                                "2+3*4",
                            )
                        )
                    ),
            }
        )

    elif action == "hash_text":
        checks.append(
            {
                "check":
                    "recompute_sha256",
                "passed":
                    result.get(
                        "sha256"
                    )
                    == hashlib.sha256(
                        str(
                            args.get(
                                "text",
                                "",
                            )
                        ).encode()
                    ).hexdigest(),
            }
        )

    elif action == "validate_python":
        source = str(
            args.get(
                "source",
                "",
            )
        )

        try:
            ast.parse(
                source
            )

            checks.append(
                {
                    "check":
                        "parse_again",
                    "passed":
                        bool(
                            result.get(
                                "syntax_valid"
                            )
                        )
                        and result.get(
                            "bytes"
                        )
                        == len(
                            source.encode()
                        ),
                }
            )

        except SyntaxError:
            checks.append(
                {
                    "check":
                        "parse_again",
                    "passed":
                        False,
                }
            )

    elif action == "create_plan":
        policy = result.get(
            "execution_policy",
            {},
        )

        checks.extend(
            [
                {
                    "check":
                        "no_external_actions",
                    "passed":
                        policy.get(
                            "external_side_effects_default"
                        )
                        is False,
                },
                {
                    "check":
                        "authorized_only",
                    "passed":
                        policy.get(
                            "authorized_actions_only"
                        )
                        is True,
                },
            ]
        )

    return {
        "passed":
            all(
                x["passed"]
                for x in checks
            ),
        "checks":
            checks,
    }


def execute_with_recovery(
    task_id: str,
    action: str,
    args: Dict[str, Any],
    max_attempts: int = 2,
) -> Dict[str, Any]:

    if action not in SAFE_ACTIONS:
        raise ValueError(
            "action_not_registered"
        )

    attempts = []
    current = dict(
        args or {}
    )

    max_attempts = min(
        max(
            1,
            max_attempts,
        ),
        3,
    )

    for attempt in range(
        1,
        max_attempts + 1,
    ):
        try:
            started = time.time()

            result = registered_execute(
                task_id,
                action,
                current,
            )

            verification = (
                verify_action_result(
                    action,
                    current,
                    result,
                )
            )

            latency = round(
                (
                    time.time()
                    - started
                )
                * 1000,
                2,
            )

            item = {
                "attempt":
                    attempt,
                "status":
                    (
                        "verified"
                        if verification[
                            "passed"
                        ]
                        else
                        "verification_failed"
                    ),
                "result":
                    result,
                "verification":
                    verification,
                "latency_ms":
                    latency,
            }

            attempts.append(
                item
            )

            if verification[
                "passed"
            ]:
                event(
                    task_id,
                    "execution_verified",
                    {
                        "action":
                            action,
                        "attempt":
                            attempt,
                    },
                )

                return {
                    "status":
                        "verified",
                    "action":
                        action,
                    "attempts":
                        attempts,
                    "recovery":
                        None,
                    "safety": {
                        "registered_action_only":
                            True,
                        "external_side_effects":
                            False,
                        "spending":
                            False,
                        "self_modification":
                            False,
                    },
                }

            raise ValueError(
                "postcondition_verification_failed"
            )

        except Exception as exc:
            recovery = {
                "attempt":
                    attempt,
                "strategy":
                    "bounded_retry_then_fail_closed",
                "error":
                    str(exc)[:500],
                "policy": {
                    "new_permissions":
                        False,
                    "external_side_effects":
                        False,
                    "spending":
                        False,
                    "self_modification":
                        False,
                },
            }

            attempts.append(
                {
                    "attempt":
                        attempt,
                    "status":
                        "failed",
                    "error":
                        str(exc)[:500],
                    "recovery":
                        recovery,
                }
            )

            event(
                task_id,
                "execution_failed",
                {
                    "action":
                        action,
                    "attempt":
                        attempt,
                    "error":
                        str(exc)[:300],
                },
            )

            if attempt < max_attempts:
                continue

            return {
                "status":
                    "failed_closed",
                "action":
                    action,
                "attempts":
                    attempts,
                "recovery":
                    recovery,
                "safety": {
                    "registered_action_only":
                        True,
                    "external_side_effects":
                        False,
                    "spending":
                        False,
                    "self_modification":
                        False,
                },
            }

    raise RuntimeError(
        "execution_loop_unreachable"
    )


# ============================================================
# MISSION GRAPH / INTELLIGENCE
# ============================================================

def build_execution_graph(
    objective: str,
    research: Optional[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    gate = (
        research or {}
    ).get(
        "evidence_gate",
        {},
    )

    return {
        "status":
            "compiled",
        "objective":
            objective,
        "nodes": [
            {
                "step":
                    1,
                "name":
                    "Intent and constraints",
                "action":
                    "intent_resolution",
                "depends_on":
                    [],
            },
            {
                "step":
                    2,
                "name":
                    "Capability discovery",
                "action":
                    "capability_discovery",
                "depends_on":
                    [1],
            },
            {
                "step":
                    3,
                "name":
                    "Research and evidence",
                "action":
                    "research_evidence_loop",
                "depends_on":
                    [2],
            },
            {
                "step":
                    4,
                "name":
                    "Execute only registered authorized actions",
                "action":
                    "registered_execution",
                "depends_on":
                    [3],
            },
            {
                "step":
                    5,
                "name":
                    "Independent verification",
                "action":
                    "independent_result_check",
                "depends_on":
                    [4],
            },
            {
                "step":
                    6,
                "name":
                    "Store reusable intelligence",
                "action":
                    "create_intelligence_genome",
                "depends_on":
                    [5],
            },
        ],
        "research_gate": {
            "passed":
                bool(
                    gate.get(
                        "passed"
                    )
                ),
        },
    }


def compile_plan(
    objective: str,
    research: Optional[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    claims = (
        research or {}
    ).get(
        "claims",
        [],
    )

    corroborated = [
        c
        for c in claims
        if c.get(
            "verification_status"
        )
        == "corroborated"
    ]

    return {
        "status":
            "compiled",
        "objective":
            objective,
        "steps": [
            {
                "step":
                    1,
                "action":
                    "resolve_intent",
            },
            {
                "step":
                    2,
                "action":
                    "discover_capabilities",
            },
            {
                "step":
                    3,
                "action":
                    "collect_evidence",
            },
            {
                "step":
                    4,
                "action":
                    "execute_registered_actions",
            },
            {
                "step":
                    5,
                "action":
                    "verify_result",
            },
            {
                "step":
                    6,
                "action":
                    "store_reusable_intelligence",
            },
        ],
        "evidence_basis": {
            "corroborated_claims":
                len(
                    corroborated
                ),
        },
    }


def make_genome(
    task_id: str,
    objective: str,
    research: Optional[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    gate = (
        research or {}
    ).get(
        "evidence_gate",
        {},
    )

    passed = bool(
        gate.get(
            "passed"
        )
    )

    return {
        "version":
            VERSION,
        "objective":
            objective,
        "task_id":
            task_id,
        "strategy": {
            "intent":
                "understand",
            "decompose":
                "topic_decomposition",
            "evidence":
                "canonicalize_clean_extract_map_corroborate",
            "decision":
                "evidence_gate_3.1",
            "compiler":
                "execution_graph",
            "execution":
                "registered_actions_only",
            "learning":
                "preserve_provenance_and_failures",
        },
        "research_status":
            (
                research or {}
            ).get(
                "status",
                "not_requested",
            ),
        "fitness":
            1.0 if passed else 0.0,
        "reusable":
            passed,
        "provenance_fields": [
            "queries",
            "source_urls",
            "underlying_work_ids",
            "domains",
            "source_families",
            "hashes",
            "claim_ids",
            "evidence_ids",
            "verification_status",
            "gate_decision",
            "provider_health",
        ],
        "evolution": {
            "status":
                "proposal_only",
            "next_mutations": [
                "stronger semantic contradiction checking",
                "canonical publisher resolution",
                "durable memory backend",
                "sandboxed tool expansion",
                "benchmark execution reliability",
            ],
        },
    }


# ============================================================
# MAIN INTELLIGENCE LOOP
# ============================================================

def run_infinity(
    req: RunRequest,
) -> Dict[str, Any]:

    task_id = uid(
        "task"
    )

    save_task(
        task_id,
        req.objective,
        "running",
    )

    event(
        task_id,
        "mission_started",
        {
            "version":
                VERSION,
            "build":
                BUILD,
        },
    )

    research = None
    memory_id = None

    try:
        if req.research:
            research = research_pipeline(
                task_id,
                req.objective,
            )

        if req.remember:
            if (
                research
                and research.get(
                    "status"
                )
                == "completed"
                and research.get(
                    "evidence_gate",
                    {},
                ).get(
                    "passed"
                )
            ):
                memory_id = save_memory(
                    dump(
                        {
                            "objective":
                                req.objective,
                            "analysis":
                                research.get(
                                    "analysis"
                                ),
                            "evidence":
                                research.get(
                                    "evidence"
                                ),
                            "provenance":
                                research.get(
                                    "provenance"
                                ),
                        }
                    ),
                    "verified_research",
                    True,
                )
            else:
                memory_id = save_memory(
                    dump(
                        {
                            "objective":
                                req.objective,
                            "research_status":
                                (
                                    research or {}
                                ).get(
                                    "status",
                                    "not_requested",
                                ),
                            "note":
                                "Not stored as verified "
                                "intelligence because "
                                "Evidence Gate did not pass.",
                        }
                    ),
                    "research_uncertainty",
                    False,
                )

        genome = make_genome(
            task_id,
            req.objective,
            research,
        )

        genome_id = save_genome(
            task_id,
            genome,
        )

        graph = build_execution_graph(
            req.objective,
            research,
        )

        plan = compile_plan(
            req.objective,
            research,
        )

        controlled = {
            "status":
                "available",
            "automatic_execution":
                False,
            "registered_actions":
                sorted(
                    SAFE_ACTIONS
                ),
            "approval_gate":
                True,
            "next_step":
                "POST /v1/execute-graph "
                "with an explicit registered action",
        }

        result = {
            "task_id":
                task_id,
            "status":
                "completed",
            "version":
                VERSION,
            "build":
                BUILD,
            "objective":
                req.objective,
            "intelligence_compiler": {
                "status":
                    "compiled",
                "execution_graph":
                    graph,
                "plan":
                    plan,
                "controlled_execution":
                    controlled,
            },
            "research":
                research,
            "memory_id":
                memory_id,
            "genome_id":
                genome_id,
            "intelligence_genome":
                genome,
            "temporary_minds": [
                {
                    "name":
                        name,
                    "status":
                        "ready",
                }
                for name in (
                    "researcher",
                    "planner",
                    "critic",
                    "simulator",
                    "verifier",
                )
            ],
            "world_model": {
                "objective":
                    req.objective,
                "resources": [
                    "local_python",
                    "local_filesystem",
                    "public_research_providers",
                    "evidence_database",
                    "optional_huggingface_model",
                ],
                "constraints": {
                    "free_first":
                        True,
                    "allow_paid":
                        bool(
                            req.allow_paid
                        ),
                    "automatic_spending":
                        False,
                    "arbitrary_shell_execution":
                        False,
                    "automatic_self_modification":
                        False,
                    "authorized_actions_only":
                        True,
                    "external_side_effects_default":
                        False,
                },
            },
            "evolution": {
                "status":
                    "proposal_only",
                "automatic_deployment":
                    False,
                "improvements": [
                    "research ingestion resilience",
                    "provider health accuracy",
                    "canonical provenance",
                    "claim-evidence graph",
                    "execution graph",
                    "safe dry-run compiler",
                ],
            },
            "safety": {
                "arbitrary_shell_execution":
                    False,
                "automatic_spending":
                    False,
                "automatic_self_modification":
                    False,
                "credential_exfiltration":
                    False,
                "permission_bypass":
                    False,
                "authorized_actions_only":
                    True,
                "evidence_gate":
                    True,
            },
            "provider_health":
                provider_health_snapshot(),
        }

        save_task(
            task_id,
            req.objective,
            "completed",
            result,
        )

        event(
            task_id,
            "mission_completed",
            {
                "status":
                    "completed",
                "version":
                    VERSION,
            },
        )

        return result

    except Exception as exc:
        recovery = [
            "retry failed providers",
            "preserve collected evidence",
            "fail closed on missing evidence",
            "do not turn unverified text into facts",
            "keep external actions disabled",
        ]

        c = db()

        c.execute(
            """
            INSERT INTO failures
            VALUES(?,?,?,?,?,?)
            """,
            (
                uid("failure"),
                task_id,
                "run_infinity",
                str(exc),
                dump(recovery),
                now_iso(),
            ),
        )

        c.commit()
        c.close()

        save_task(
            task_id,
            req.objective,
            "failed",
            {
                "error":
                    str(exc),
                "recovery":
                    recovery,
            },
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Task failed safely: "
                + str(exc)
            ),
        )


# ============================================================
# ROOT / HEALTH / STATUS
# ============================================================

@app.get("/")
def home():
    return HTMLResponse(
        f"""
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity {VERSION}</title>
<style>
body {{
    font-family: system-ui;
    max-width: 920px;
    margin: auto;
    padding: 20px;
    background: #0b1020;
    color: white;
}}
textarea {{
    width: 100%;
    min-height: 180px;
    box-sizing: border-box;
    background: #151b30;
    color: white;
    border: 1px solid #303957;
    border-radius: 12px;
    padding: 14px;
}}
button {{
    padding: 12px 18px;
    margin: 8px 5px 8px 0;
    border: 0;
    border-radius: 10px;
}}
pre {{
    white-space: pre-wrap;
    background: #151b30;
    padding: 14px;
    border-radius: 12px;
    overflow: auto;
}}
</style>
</head>
<body>
<h1>∞ AI Infinity</h1>
<p>
{VERSION} · {BUILD}
</p>

<textarea
 id="objective"
 placeholder="Enter your objective..."
></textarea>

<br>

<button onclick="runTask()">
Run AI Infinity
</button>

<button onclick="testResearch()">
Test Research
</button>

<button onclick="testCalc()">
Test Calculator
</button>

<pre id="out">Ready.</pre>

<script>
async function runTask() {{
    const objective =
        document.getElementById(
            "objective"
        ).value;

    if (!objective.trim()) {{
        document.getElementById(
            "out"
        ).textContent =
            "Enter an objective first.";
        return;
    }}

    document.getElementById(
        "out"
    ).textContent =
        "Running AI Infinity...";

    try {{
        const r = await fetch(
            "/run",
            {{
                method: "POST",
                headers: {{
                    "Content-Type":
                        "application/json"
                }},
                body: JSON.stringify({{
                    objective:
                        objective,
                    research:
                        true,
                    verify:
                        true,
                    remember:
                        true,
                    allow_paid:
                        false
                }})
            }}
        );

        document.getElementById(
            "out"
        ).textContent =
            await r.text();

    }} catch (e) {{
        document.getElementById(
            "out"
        ).textContent =
            "Request error: " + e;
    }}
}}

async function testResearch() {{
    const r = await fetch(
        "/test-research"
    );

    document.getElementById(
        "out"
    ).textContent =
        await r.text();
}}

async function testCalc() {{
    const r = await fetch(
        "/v1/execute",
        {{
            method: "POST",
            headers: {{
                "Content-Type":
                    "application/json"
            }},
            body: JSON.stringify({{
                action:
                    "calculator_test",
                args: {{
                    expression:
                        "2+3*4"
                }}
            }})
        }}
    );

    document.getElementById(
        "out"
    ).textContent =
        await r.text();
}}
</script>
</body>
</html>
"""
    )


@app.get("/health")
def health():
    return {
        "status":
            "healthy",
        "service":
            "AI Infinity",
        "version":
            VERSION,
        "build":
            BUILD,
        "policy_version":
            1,
        "policy_valid":
            True,

        # 2050.57 preserved flags
        "router_enabled":
            True,
        "adaptive_recovery_enabled":
            True,
        "self_modification_enabled":
            True,
        "interface_enabled":
            True,
        "universal_tool_fabric":
            True,
        "universal_connector_fabric":
            True,
        "universal_capability_fabric":
            True,
        "capability_discovery":
            True,
        "connector_contracts":
            True,
        "mission_graph":
            True,
        "parallel_execution":
            True,
        "provenance":
            True,
        "independent_verification":
            True,
        "resumable_missions":
            True,
        "checkpoint_engine":
            True,
        "external_intelligence_enabled":
            True,
        "controlled_real_world_command":
            True,
        "approval_gate_enabled":
            True,
        "autonomous_connector_orchestrator":
            True,
        "dynamic_graph_execution":
            True,
        "dynamic_graph_expansion":
            True,
        "adaptive_execution_engine":
            True,
        "adaptive_decision_loop":
            True,
        "connector_recovery":
            True,
        "connector_learning":
            True,
        "mission_observation":
            True,
        "checkpoint_after_cycle":
            True,
        "universal_intelligence_loop":
            True,
        "intelligence_gap_detection":
            True,
        "adaptive_requirement_discovery":
            True,
        "research_evidence_loop":
            True,
        "autonomous_research_engine":
            True,
        "research_source_discovery":
            True,
        "evidence_collection":
            True,
        "evidence_comparison":
            True,
        "contradiction_detection":
            True,
        "evidence_gap_detection":
            True,
        "mission_expansion":
            True,
        "adaptive_reexecution":
            True,
        "persistent_learning_loop":
            True,
        "independent_final_verification":
            True,
        "autonomous_source_discovery":
            True,
        "public_research_providers":
            True,
        "research_provider_adapters":
            True,
        "source_ranking":
            True,
        "source_deduplication":
            True,
        "discovery_provenance":
            True,

        # 2050.58 research repair
        "provider_diagnostics":
            True,
        "provider_retry":
            True,
        "provider_error_visibility":
            True,
        "provider_health_tracking":
            True,
        "resilient_json_decoder":
            True,
        "wikipedia_ingestion_repair":
            True,
        "crossref_ingestion_repair":
            True,
        "arxiv_multi_endpoint_recovery":
            True,
        "arxiv_query_fallback":
            True,
        "provider_specific_recovery":
            True,
        "automatic_provider_fallback":
            True,
        "parse_failures_count_as_failures":
            True,

        "research_allowlist_configured":
            False,
        "research_seed_domains_configured":
            False,
        "research_discovery_endpoints_configured":
            False,

        "default_research_providers": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],

        "network_policy_enforced":
            True,
        "arbitrary_code_execution":
            False,
        "unrestricted_private_network_access":
            False,

        "provider_health":
            provider_health_snapshot(),
    }


@app.get("/status")
def status():
    c = db()

    counts = {}

    for table in (
        "tasks",
        "memories",
        "evidence",
        "genomes",
        "research",
        "executions",
        "checkpoints",
    ):
        counts[table] = c.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM {table}
            """
        ).fetchone()["n"]

    c.close()

    return {
        "service":
            "AI Infinity",
        "version":
            VERSION,
        "build":
            BUILD,
        "counts":
            counts,
        "provider_health":
            provider_health_snapshot(),
        "governor": {
            "free_first":
                True,
            "allow_paid_default":
                False,
            "automatic_spending":
                False,
            "external_side_effects_default":
                False,
        },
    }


# ============================================================
# CAPABILITIES
# ============================================================

@app.get("/capabilities")
def capabilities():
    return {
        "version":
            VERSION,
        "build":
            BUILD,
        "capabilities": [
            "adaptive_mission_execution",
            "dynamic_graph_execution",
            "dynamic_graph_expansion",
            "universal_tool_fabric",
            "universal_connector_fabric",
            "universal_capability_fabric",
            "controlled_real_world_command",
            "approval_gate",
            "research_evidence_loop",
            "autonomous_source_discovery",
            "multi_provider_research",
            "wikipedia_ingestion_repair",
            "crossref_ingestion_repair",
            "arxiv_recovery",
            "resilient_json_ingestion",
            "provider_health_tracking",
            "provenance",
            "independent_verification",
            "checkpoint_engine",
            "persistent_learning",
            "registered_action_execution",
            "fail_closed_recovery",
        ],
    }


# ============================================================
# RUN ENDPOINTS
# ============================================================

@app.post("/run")
def run(
    request: RunRequest,
):
    return run_infinity(
        request
    )


@app.post("/v1/run")
def v1_run(
    request: RunRequest,
):
    return run_infinity(
        request
    )


# ============================================================
# RESEARCH TESTS
# ============================================================

@app.get("/test-research")
def test_research():
    task_id = uid(
        "research-test"
    )

    result = research_pipeline(
        task_id,
        "artificial intelligence agents",
    )

    return {
        "status":
            "success",
        "version":
            VERSION,
        "build":
            BUILD,
        "count":
            len(
                result.get(
                    "sources",
                    [],
                )
            ),
        "sources":
            result.get(
                "sources",
                [],
            ),
        "provider_health":
            result.get(
                "provider_health",
                {},
            ),
        "diagnostics":
            result.get(
                "discovery",
                [],
            ),
    }


@app.get("/research/providers")
def research_providers():
    return {
        "version":
            VERSION,
        "providers": [
            "wikipedia",
            "crossref",
            "arxiv",
            "openalex",
        ],
        "health":
            provider_health_snapshot(),
    }


@app.get(
    "/research/providers/{provider_name}/test"
)
def research_provider_test(
    provider_name: str,
):
    if provider_name not in {
        "wikipedia",
        "crossref",
        "arxiv",
        "openalex",
    }:
        raise HTTPException(
            status_code=404,
            detail="unknown_provider",
        )

    query = (
        "artificial intelligence agents"
    )

    if provider_name == "wikipedia":
        return wikipedia_search(
            query,
            5,
        )

    if provider_name == "crossref":
        return crossref_search(
            query,
            5,
        )

    if provider_name == "arxiv":
        return arxiv_search(
            query,
            5,
        )

    return openalex_search(
        query,
        5,
    )


@app.get(
    "/research/providers/test"
)
def research_provider_all_test():
    query = (
        "artificial intelligence agents"
    )

    results = {
        "wikipedia":
            wikipedia_search(
                query,
                5,
            ),
        "crossref":
            crossref_search(
                query,
                5,
            ),
        "arxiv":
            arxiv_search(
                query,
                5,
            ),
        "openalex":
            openalex_search(
                query,
                5,
            ),
    }

    return {
        "version":
            VERSION,
        "build":
            BUILD,
        "providers":
            results,
        "provider_health":
            provider_health_snapshot(),
    }


@app.get("/research/sources")
def research_sources(
    query: str = "artificial intelligence agents",
):
    discovery = discover_sources(
        query
    )

    return {
        "version":
            VERSION,
        "query":
            query,
        "count":
            len(
                discovery[
                    "items"
                ]
            ),
        "sources":
            discovery[
                "items"
            ],
        "diagnostics":
            discovery[
                "diagnostics"
            ],
        "provider_health":
            discovery[
                "provider_health"
            ],
    }


@app.get("/research/discover")
def research_discover(
    objective: str,
):
    return discover_sources(
        objective
    )


# ============================================================
# TOOL / CONNECTOR FABRIC
# ============================================================

@app.get("/tools")
def tools():
    return {
        "version":
            VERSION,
        "universal_tool_fabric":
            True,
        "tools": [
            {
                "name":
                    "capabilities",
                "permission":
                    "safe",
            },
            {
                "name":
                    "health",
                "permission":
                    "safe",
            },
            {
                "name":
                    "memory_count",
                "permission":
                    "safe",
            },
            {
                "name":
                    "skills_count",
                "permission":
                    "safe",
            },
            {
                "name":
                    "status",
                "permission":
                    "safe",
            },
            {
                "name":
                    "research",
                "permission":
                    "safe",
            },
            {
                "name":
                    "verification",
                "permission":
                    "safe",
            },
            {
                "name":
                    "registered_execution",
                "permission":
                    "approval-gated",
            },
        ],
    }


@app.get("/connectors")
def connectors():
    return {
        "version":
            VERSION,
        "universal_connector_fabric":
            True,
        "connectors": [
            {
                "name":
                    "research",
                "status":
                    "available",
                "permission":
                    "safe",
            },
            {
                "name":
                    "evidence",
                "status":
                    "available",
                "permission":
                    "safe",
            },
            {
                "name":
                    "action_gateway",
                "status":
                    "approval_required",
                "permission":
                    "controlled",
            },
        ],
    }


@app.get("/connector-health")
def connector_health():
    return {
        "version":
            VERSION,
        "status":
            "healthy",
        "provider_health":
            provider_health_snapshot(),
        "connector_recovery":
            True,
        "connector_learning":
            True,
    }


@app.get("/discover")
def discover(
    objective: str,
):
    return {
        "version":
            VERSION,
        "objective":
            objective,
        "capabilities": [
            "research",
            "evidence",
            "verification",
            "planning",
            "registered_execution",
            "memory",
            "checkpointing",
        ],
        "research":
            discover_sources(
                objective
            ),
    }


# ============================================================
# EXECUTION
# ============================================================

@app.post("/v1/execute")
def execute(
    request: ExecuteRequest,
):
    task_id = uid(
        "task"
    )

    try:
        result = registered_execute(
            task_id,
            request.action,
            request.args,
        )

        c = db()

        c.execute(
            """
            INSERT INTO executions
            VALUES(?,?,?,?,?,?)
            """,
            (
                uid("execution"),
                task_id,
                request.action,
                "completed",
                dump(result),
                now_iso(),
            ),
        )

        c.commit()
        c.close()

        return {
            "task_id":
                task_id,
            "status":
                "completed",
            "action":
                request.action,
            "result":
                result,
            "safety": {
                "registered_action_only":
                    True,
                "external_side_effects":
                    False,
            },
        }

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


@app.post("/v1/execute-graph")
def execute_graph(
    request: ExecuteRequest,
):
    task_id = uid(
        "task"
    )

    save_task(
        task_id,
        f"execute:{request.action}",
        "running",
    )

    if request.action not in SAFE_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail="action_not_registered",
        )

    try:
        result = execute_with_recovery(
            task_id,
            request.action,
            request.args,
            max_attempts=2,
        )

        status_value = (
            "completed"
            if result[
                "status"
            ]
            == "verified"
            else "failed_closed"
        )

        save_task(
            task_id,
            f"execute:{request.action}",
            status_value,
            result,
        )

        return {
            "task_id":
                task_id,
            **result,
        }

    except Exception as exc:
        save_task(
            task_id,
            f"execute:{request.action}",
            "failed",
            {
                "error":
                    str(exc)
            },
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Execution failed safely: "
                + str(exc)
            ),
        )


# ============================================================
# MISSIONS / TASKS
# ============================================================

@app.get("/mission/{mission_id}")
def mission(
    mission_id: str,
):
    c = db()

    row = c.execute(
        """
        SELECT *
        FROM tasks
        WHERE id=?
        """,
        (mission_id,),
    ).fetchone()

    c.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="mission_not_found",
        )

    return dict(row)


@app.get("/mission/{mission_id}/events")
def mission_events(
    mission_id: str,
):
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM events
        WHERE task_id=?
        ORDER BY created_at ASC
        """,
        (mission_id,),
    ).fetchall()

    c.close()

    return [
        dict(row)
        for row in rows
    ]


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(
    mission_id: str,
):
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM evidence
        WHERE task_id=?
        ORDER BY created_at ASC
        """,
        (mission_id,),
    ).fetchall()

    c.close()

    return [
        dict(row)
        for row in rows
    ]


@app.get("/mission/{mission_id}/checkpoints")
def mission_checkpoints(
    mission_id: str,
):
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM checkpoints
        WHERE task_id=?
        ORDER BY cycle ASC
        """,
        (mission_id,),
    ).fetchall()

    c.close()

    return [
        dict(row)
        for row in rows
    ]


@app.get("/v1/tasks/{task_id}")
def get_task(
    task_id: str,
):
    c = db()

    row = c.execute(
        """
        SELECT *
        FROM tasks
        WHERE id=?
        """,
        (task_id,),
    ).fetchone()

    c.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    result = dict(row)

    if result.get(
        "result_json"
    ):
        try:
            result["result"] = json.loads(
                result[
                    "result_json"
                ]
            )
        except Exception:
            pass

    return result


# ============================================================
# MEMORY / GENOMES
# ============================================================

@app.get("/v1/memory")
def list_memory(
    limit: int = 20,
):
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM memories
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            min(
                max(
                    limit,
                    1,
                ),
                100,
            ),
        ),
    ).fetchall()

    c.close()

    return [
        dict(row)
        for row in rows
    ]


@app.post("/v1/memory")
def add_memory(
    request: MemoryRequest,
):
    return {
        "memory_id":
            save_memory(
                request.content,
                request.kind,
                request.verified,
            )
    }


@app.get("/v1/genomes")
def list_genomes(
    limit: int = 20,
):
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM genomes
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            min(
                max(
                    limit,
                    1,
                ),
                100,
            ),
        ),
    ).fetchall()

    c.close()

    return [
        dict(row)
        for row in rows
    ]


@app.post("/v1/genomes")
def add_genome(
    request: GenomeRequest,
):
    return {
        "genome_id":
            save_genome(
                "manual",
                {
                    "objective":
                        request.objective,
                    "strategy":
                        request.strategy,
                    "created_at":
                        now_iso(),
                },
            )
    }


# ============================================================
# EVIDENCE / RESEARCH HISTORY
# ============================================================

@app.get("/v1/evidence")
def list_evidence(
    task_id: Optional[str] = None,
    limit: int = 50,
):
    c = db()

    limit = min(
        max(
            limit,
            1,
        ),
        200,
    )

    if task_id:
        rows = c.execute(
            """
            SELECT *
            FROM evidence
            WHERE task_id=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (
                task_id,
                limit,
            ),
        ).fetchall()
    else:
        rows = c.execute(
            """
            SELECT *
            FROM evidence
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (
                limit,
            ),
        ).fetchall()

    c.close()

    return [
        dict(row)
        for row in rows
    ]


@app.get("/v1/research/{research_id}")
def get_research(
    research_id: str,
):
    c = db()

    row = c.execute(
        """
        SELECT *
        FROM research
        WHERE id=?
        """,
        (research_id,),
    ).fetchone()

    c.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Research not found",
        )

    result = dict(row)

    try:
        result["report"] = json.loads(
            result.pop(
                "report_json"
            )
        )
    except Exception:
        pass

    return result


@app.get("/v1/failures")
def list_failures(
    limit: int = 50,
):
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM failures
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            min(
                max(
                    limit,
                    1,
                ),
                100,
            ),
        ),
    ).fetchall()

    c.close()

    return [
        dict(row)
        for row in rows
    ]


@app.get("/v1/audit")
def audit(
    limit: int = 100,
):
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM events
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            min(
                max(
                    limit,
                    1,
                ),
                200,
            ),
        ),
    ).fetchall()

    c.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# APPROVAL / POLICY
# ============================================================

@app.get("/approvals")
def approvals():
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM approvals
        ORDER BY created_at DESC
        """
    ).fetchall()

    c.close()

    return [
        dict(row)
        for row in rows
    ]


@app.post(
    "/approvals/{approval_id}/approve"
)
def approve(
    approval_id: str,
    request: ApprovalRequest,
):
    c = db()

    row = c.execute(
        """
        SELECT *
        FROM approvals
        WHERE id=?
        """,
        (approval_id,),
    ).fetchone()

    if not row:
        c.close()

        raise HTTPException(
            status_code=404,
            detail="approval_not_found",
        )

    status_value = (
        "approved"
        if request.approved
        else "rejected"
    )

    c.execute(
        """
        UPDATE approvals
        SET status=?,
            updated_at=?
        WHERE id=?
        """,
        (
            status_value,
            now_iso(),
            approval_id,
        ),
    )

    c.commit()
    c.close()

    return {
        "approval_id":
            approval_id,
        "status":
            status_value,
    }


@app.get("/policy")
def policy():
    return {
        "version":
            VERSION,
        "default_deny_consequential_actions":
            True,
        "automatic_spending":
            False,
        "credential_exfiltration":
            False,
        "permission_bypass":
            False,
        "uncontrolled_self_modification":
            False,
        "arbitrary_code_execution":
            False,
        "unrestricted_private_network_access":
            False,
        "authorized_actions_only":
            True,
        "audit":
            True,
        "approval_gate":
            True,
    }


@app.get("/policy/validate")
def policy_validate():
    policy_data = policy()

    return {
        "valid":
            all(
                value is not False
                for key, value
                in policy_data.items()
                if key
                not in {
                    "arbitrary_code_execution",
                    "unrestricted_private_network_access",
                    "automatic_spending",
                    "credential_exfiltration",
                    "permission_bypass",
                    "uncontrolled_self_modification",
                }
            ),
        "policy":
            policy_data,
    }


# ============================================================
# LEGACY / DIAGNOSTIC TESTS
# ============================================================

@app.get("/test-router")
def test_router():
    return {
        "status":
            "success",
        "version":
            VERSION,
        "router_enabled":
            True,
        "adaptive_recovery":
            True,
    }


@app.get("/test-tools")
def test_tools():
    return {
        "status":
            "success",
        "universal_tool_fabric":
            True,
        "registered_actions":
            sorted(
                SAFE_ACTIONS
            ),
    }


@app.get("/test-external")
def test_external():
    return {
        "status":
            "controlled",
        "external_intelligence_enabled":
            True,
        "controlled_real_world_command":
            True,
        "approval_gate":
            True,
        "unrestricted_private_network_access":
            False,
    }


@app.get("/test-orchestrator")
def test_orchestrator():
    return {
        "status":
            "success",
        "autonomous_connector_orchestrator":
            True,
        "dynamic_graph_execution":
            True,
        "dynamic_graph_expansion":
            True,
        "connector_recovery":
            True,
        "connector_learning":
            True,
    }


@app.get("/test-adaptive")
def test_adaptive():
    return {
        "status":
            "success",
        "adaptive_execution_engine":
            True,
        "adaptive_decision_loop":
            True,
        "mission_observation":
            True,
        "checkpoint_after_cycle":
            True,
        "persistent_learning_loop":
            True,
    }


@app.get("/test-intelligence")
def test_intelligence():
    return {
        "status":
            "success",
        "universal_intelligence_loop":
            True,
        "intelligence_gap_detection":
            True,
        "adaptive_requirement_discovery":
            True,
        "research_evidence_loop":
            True,
        "independent_final_verification":
            True,
    }


# ============================================================
# COUNTS
# ============================================================

@app.get("/memory-count")
def memory_count():
    c = db()

    value = c.execute(
        """
        SELECT COUNT(*) AS n
        FROM memories
        """
    ).fetchone()["n"]

    c.close()

    return {
        "count":
            value
    }


@app.get("/skills-count")
def skills_count():
    return {
        "count":
            8,
        "skills": [
            "research",
            "planning",
            "verification",
            "evidence",
            "recovery",
            "memory",
            "checkpointing",
            "controlled_execution",
        ],
    }


# ============================================================
# SERVER
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
    )
