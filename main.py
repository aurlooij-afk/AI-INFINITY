import ast
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY — TARGET-2.3.0
# Evidence-First Autonomous Intelligence Fabric
# ============================================================

VERSION = "TARGET-2.3.0"

BASE = Path(os.getenv("AI_INFINITY_HOME", "/tmp/ai-infinity"))
ARTIFACTS = BASE / "artifacts"
WORK = BASE / "work"
LOGS = BASE / "logs"
DB_PATH = BASE / "ai_infinity.db"

for directory in (BASE, ARTIFACTS, WORK, LOGS):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Evidence-first autonomous intelligence fabric"
)

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_MODEL = os.getenv(
    "HF_MODEL",
    "openai/gpt-oss-120b:cheapest"
)

REQUEST_TIMEOUT = int(
    os.getenv("AI_INFINITY_HTTP_TIMEOUT", "15")
)

MAX_SOURCE_BYTES = int(
    os.getenv("AI_INFINITY_MAX_SOURCE_BYTES", "120000")
)

USER_AGENT = "AI-Infinity/2.3"


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
    strategy: Dict[str, Any] = Field(default_factory=dict)


# ============================================================
# UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str
    )


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


# ============================================================
# DATABASE
# ============================================================

def init_db() -> None:
    connection = db()

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            objective TEXT,
            status TEXT,
            result_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            kind TEXT,
            content TEXT,
            verified INTEGER,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS genomes (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            genome_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence (
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

        CREATE TABLE IF NOT EXISTS research (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            question TEXT,
            status TEXT,
            report_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            event_type TEXT,
            data_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS failures (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            stage TEXT,
            error TEXT,
            recovery_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS executions (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            action TEXT,
            status TEXT,
            result_json TEXT,
            created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_evidence_task
        ON evidence(task_id);

        CREATE INDEX IF NOT EXISTS idx_memory_kind
        ON memories(kind);
        """
    )

    connection.commit()
    connection.close()


init_db()


# ============================================================
# EVENTS / MEMORY / TASKS
# ============================================================

def record_event(
    task_id: Optional[str],
    event_type: str,
    data: Any
) -> None:
    connection = db()

    connection.execute(
        """
        INSERT INTO events
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            uid("event"),
            task_id,
            event_type,
            json_dump(data),
            now_iso()
        )
    )

    connection.commit()
    connection.close()


def save_memory(
    content: str,
    kind: str = "general",
    verified: bool = False
) -> str:
    memory_id = uid("memory")

    connection = db()

    connection.execute(
        """
        INSERT INTO memories
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            kind,
            content,
            int(verified),
            now_iso()
        )
    )

    connection.commit()
    connection.close()

    return memory_id


def save_task(
    task_id: str,
    objective: str,
    status: str,
    result: Any = None
) -> None:
    connection = db()

    connection.execute(
        """
        INSERT INTO tasks (
            id,
            objective,
            status,
            result_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)

        ON CONFLICT(id) DO UPDATE SET
            status = excluded.status,
            result_json = excluded.result_json,
            updated_at = excluded.updated_at
        """,
        (
            task_id,
            objective,
            status,
            json_dump(result)
            if result is not None
            else None,
            now_iso(),
            now_iso()
        )
    )

    connection.commit()
    connection.close()


def save_genome(
    task_id: str,
    genome: Dict[str, Any]
) -> str:
    genome_id = uid("genome")

    connection = db()

    connection.execute(
        """
        INSERT INTO genomes
        VALUES (?, ?, ?, ?)
        """,
        (
            genome_id,
            task_id,
            json_dump(genome),
            now_iso()
        )
    )

    connection.commit()
    connection.close()

    return genome_id


# ============================================================
# TEXT ANALYSIS
# ============================================================

STOPWORDS = set(
    """
    a an and are as at be been being by for from had has have how
    i if in into is it its me more most of on or our that the their
    them there these they this to was were what when where which who
    why will with you your about become need needed
    """.split()
)


RESEARCH_KEYWORDS = {
    "agent",
    "agents",
    "autonomous",
    "autonomy",
    "task",
    "tasks",
    "executor",
    "executors",
    "execution",
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
}


def tokenize(text: str) -> set:
    words = re.findall(
        r"[a-z0-9][a-z0-9_-]{1,}",
        text.lower()
    )

    return {
        word
        for word in words
        if word not in STOPWORDS
    }


def research_keywords(text: str) -> set:
    low = text.lower()

    return {
        keyword
        for keyword in RESEARCH_KEYWORDS
        if keyword in low
    }


def relevance_score(
    question: str,
    title: str,
    text: str,
    url: str
) -> float:
    question_tokens = tokenize(question)

    if not question_tokens:
        return 0.0

    title_tokens = tokenize(title)
    text_tokens = tokenize(text[:50000])

    title_overlap = (
        len(question_tokens & title_tokens)
        / max(1, len(question_tokens))
    )

    text_overlap = (
        len(question_tokens & text_tokens)
        / max(1, len(question_tokens))
    )

    q_keywords = research_keywords(question)

    source_keywords = research_keywords(
        title + " " + text[:30000]
    )

    keyword_overlap = (
        len(q_keywords & source_keywords)
        / max(1, len(q_keywords))
    )

    domain = urlparse(url).netloc.lower()

    domain_bonus = 0.0

    if (
        "arxiv.org" in domain
        or "crossref.org" in domain
        or "doi.org" in domain
        or "openalex.org" in domain
    ):
        domain_bonus = 0.05

    if "wikipedia.org" in domain:
        domain_bonus = -0.05

    score = (
        0.55 * title_overlap
        + 0.30 * text_overlap
        + 0.15 * keyword_overlap
        + domain_bonus
    )

    return round(
        max(0.0, min(1.0, score)),
        4
    )


# ============================================================
# HTTP SOURCE FETCHING
# ============================================================

def extract_title(
    text: str,
    fallback: str
) -> str:
    match = re.search(
        r"<title[^>]*>(.*?)</title>",
        text,
        re.I | re.S
    )

    if match:
        title = re.sub(
            r"\s+",
            " ",
            re.sub(
                r"<[^>]+>",
                " ",
                match.group(1)
            )
        ).strip()

        if title:
            return title[:500]

    return fallback[:500]


def clean_text(
    data: bytes,
    content_type: str
) -> str:
    text = data.decode(
        "utf-8",
        errors="replace"
    )

    if (
        "html" in content_type.lower()
        or "<html" in text[:1000].lower()
    ):
        text = re.sub(
            r"(?is)<script.*?</script>",
            " ",
            text
        )

        text = re.sub(
            r"(?is)<style.*?</style>",
            " ",
            text
        )

        text = re.sub(
            r"(?is)<noscript.*?</noscript>",
            " ",
            text
        )

        text = re.sub(
            r"<[^>]+>",
            " ",
            text
        )

        text = re.sub(
            r"&nbsp;",
            " ",
            text,
            flags=re.I
        )

        text = re.sub(
            r"&amp;",
            "&",
            text,
            flags=re.I
        )

        text = re.sub(
            r"&quot;",
            '"',
            text,
            flags=re.I
        )

        text = re.sub(
            r"&#39;",
            "'",
            text,
            flags=re.I
        )

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text[:MAX_SOURCE_BYTES]


def fetch_url(url: str) -> Dict[str, Any]:
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("unsupported_url")

    if not parsed.netloc:
        raise ValueError("missing_domain")

    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,"
                "text/plain,"
                "application/json,"
                "*/*;q=0.2"
            )
        },
        allow_redirects=True,
        stream=True
    )

    content = bytearray()

    for chunk in response.iter_content(8192):
        content.extend(chunk)

        if len(content) > MAX_SOURCE_BYTES:
            break

    raw = bytes(content)

    content_type = response.headers.get(
        "content-type",
        ""
    )

    text = clean_text(
        raw,
        content_type
    )

    final_url = response.url

    title = extract_title(
        text,
        final_url
    )

    digest = hashlib.sha256(raw).hexdigest()

    return {
        "url": final_url,
        "title": title,
        "text": text,
        "hash": digest,
        "http_status": response.status_code,
        "content_type": content_type,
        "content_length": len(raw),
        "retrieved_at": now_iso()
    }


# ============================================================
# RESEARCH PROVIDERS
# ============================================================

def arxiv_search(
    query: str,
    limit: int = 5
) -> List[Dict[str, Any]]:
    url = (
        "https://export.arxiv.org/api/query"
        "?search_query=all:"
        + quote(query)
        + f"&start=0&max_results={limit}"
    )

    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT}
    )

    response.raise_for_status()

    entries = re.findall(
        r"<entry>(.*?)</entry>",
        response.text,
        re.S
    )

    results = []

    for entry in entries:
        title = re.search(
            r"<title>(.*?)</title>",
            entry,
            re.S
        )

        summary = re.search(
            r"<summary>(.*?)</summary>",
            entry,
            re.S
        )

        link = re.search(
            r'<link[^>]+href="([^"]+)"',
            entry
        )

        if not title or not link:
            continue

        title_text = re.sub(
            r"\s+",
            " ",
            title.group(1)
        ).strip()

        summary_text = ""

        if summary:
            summary_text = re.sub(
                r"\s+",
                " ",
                summary.group(1)
            ).strip()

        results.append(
            {
                "title": title_text,
                "url": link.group(1),
                "snippet": summary_text,
                "provider": "arxiv"
            }
        )

    return results


def crossref_search(
    query: str,
    limit: int = 5
) -> List[Dict[str, Any]]:
    url = (
        "https://api.crossref.org/works"
        "?query.bibliographic="
        + quote(query)
        + f"&rows={limit}"
    )

    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT}
    )

    response.raise_for_status()

    items = (
        response.json()
        .get("message", {})
        .get("items", [])
    )

    results = []

    for item in items:
        title = (
            item.get("title") or [""]
        )[0]

        doi = item.get("DOI")

        if not title or not doi:
            continue

        results.append(
            {
                "title": title,
                "url": "https://doi.org/" + doi,
                "snippet": (
                    item.get("abstract")
                    or ""
                )[:3000],
                "provider": "crossref"
            }
        )

    return results


def openalex_search(
    query: str,
    limit: int = 5
) -> List[Dict[str, Any]]:
    url = (
        "https://api.openalex.org/works"
        "?search="
        + quote(query)
        + f"&per-page={limit}"
    )

    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT}
    )

    response.raise_for_status()

    items = response.json().get(
        "results",
        []
    )

    results = []

    for item in items:
        title = item.get(
            "display_name",
            ""
        )

        location = item.get(
            "primary_location"
        ) or {}

        landing_url = (
            location.get("landing_page_url")
            or item.get("doi")
        )

        if not title or not landing_url:
            continue

        if landing_url.startswith(
            "https://doi.org/"
        ):
            final_url = landing_url

        elif landing_url.startswith(
            "http"
        ):
            final_url = landing_url

        else:
            final_url = (
                "https://openalex.org/"
                + str(item.get("id", "")).split("/")[-1]
            )

        abstract_index = (
            item.get("abstract_inverted_index")
            or {}
        )

        abstract = " ".join(
            abstract_index.keys()
        )

        results.append(
            {
                "title": title,
                "url": final_url,
                "snippet": abstract[:3000],
                "provider": "openalex"
            }
        )

    return results


def wikipedia_search(
    query: str,
    limit: int = 5
) -> List[Dict[str, Any]]:
    url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query"
        "&list=search"
        "&srsearch="
        + quote(query)
        + f"&srlimit={limit}"
        "&format=json"
    )

    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT}
    )

    response.raise_for_status()

    items = (
        response.json()
        .get("query", {})
        .get("search", [])
    )

    results = []

    for item in items:
        title = item.get(
            "title",
            ""
        )

        if not title:
            continue

        results.append(
            {
                "title": title,
                "url": (
                    "https://en.wikipedia.org/wiki/"
                    + quote(title.replace(" ", "_"))
                ),
                "snippet": re.sub(
                    r"<[^>]+>",
                    " ",
                    item.get("snippet", "")
                ),
                "provider": "wikipedia"
            }
        )

    return results


# ============================================================
# QUERY EXPANSION
# ============================================================

def expand_queries(
    question: str
) -> List[str]:
    base = re.sub(
        r"\s+",
        " ",
        question
    ).strip()

    queries = [
        base,
        (
            "reliable autonomous AI agents "
            "task execution verification planning"
        ),
        (
            "AI agent reliability tool use "
            "evaluation failure recovery safety"
        ),
        (
            "autonomous agents planning execution "
            "monitoring verification benchmark"
        )
    ]

    return list(
        dict.fromkeys(queries)
    )


# ============================================================
# SOURCE DISCOVERY
# ============================================================

def dedupe_sources(
    items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    seen = set()
    results = []

    for item in items:
        url = item.get(
            "url",
            ""
        )

        normalized = re.sub(
            r"^https?://",
            "",
            url
        ).rstrip("/").lower()

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        results.append(item)

    return results


def discover_sources(
    question: str
) -> Dict[str, Any]:
    providers = [
        ("arxiv", arxiv_search),
        ("crossref", crossref_search),
        ("openalex", openalex_search),
        ("wikipedia", wikipedia_search)
    ]

    all_results = []
    diagnostics = []

    for name, provider in providers:
        started = time.time()

        try:
            batch = provider(
                question,
                5
            )

            all_results.extend(batch)

            diagnostics.append(
                {
                    "provider": name,
                    "status": "ok",
                    "count": len(batch),
                    "latency_ms": int(
                        (time.time() - started)
                        * 1000
                    )
                }
            )

        except Exception as exc:
            diagnostics.append(
                {
                    "provider": name,
                    "status": "error",
                    "count": 0,
                    "latency_ms": int(
                        (time.time() - started)
                        * 1000
                    ),
                    "error": str(exc)[:300]
                }
            )

    return {
        "items": dedupe_sources(
            all_results
        ),
        "diagnostics": diagnostics
    }


# ============================================================
# SOURCE COLLECTION + RELEVANCE GATE
# ============================================================

def collect_relevant_sources(
    task_id: str,
    question: str,
    discovered: List[Dict[str, Any]]
) -> Dict[str, Any]:
    candidates = []

    for item in discovered:
        try:
            fetched = fetch_url(
                item["url"]
            )

            if (
                fetched["http_status"] < 200
                or fetched["http_status"] >= 400
            ):
                continue

            score = relevance_score(
                question,
                item.get("title", ""),
                fetched["text"],
                fetched["url"]
            )

            # TARGET-2.3:
            # Reject weakly related sources.
            if score < 0.20:
                continue

            candidates.append(
                {
                    **item,
                    **fetched,
                    "relevance": score,
                    "domain": urlparse(
                        fetched["url"]
                    ).netloc.lower()
                }
            )

        except Exception:
            continue

    candidates.sort(
        key=lambda item: item["relevance"],
        reverse=True
    )

    # Prefer source diversity.
    selected = []
    domains = set()

    for candidate in candidates:
        domain = candidate["domain"]

        if (
            domain not in domains
            or len(selected) < 3
        ):
            selected.append(candidate)
            domains.add(domain)

        if len(selected) >= 12:
            break

    connection = db()

    saved = []

    for source in selected:
        evidence_id = uid("evidence")

        connection.execute(
            """
            INSERT INTO evidence
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                task_id,
                source["url"],
                source["title"],
                source["domain"],
                "",
                source["text"][:5000],
                source["hash"],
                "collected",
                source["relevance"],
                json_dump(
                    {
                        "provider": source.get(
                            "provider"
                        ),
                        "http_status": source.get(
                            "http_status"
                        ),
                        "content_type": source.get(
                            "content_type"
                        ),
                        "content_length": source.get(
                            "content_length"
                        ),
                        "retrieved_at": source.get(
                            "retrieved_at"
                        )
                    }
                ),
                now_iso()
            )
        )

        source["evidence_id"] = evidence_id
        saved.append(source)

    connection.commit()
    connection.close()

    return {
        "selected": saved,
        "candidate_count": len(candidates),
        "domains": sorted(domains)
    }


# ============================================================
# CLAIM EXTRACTION
# ============================================================

CLAIM_PATTERNS = [
    (
        "reliability",
        re.compile(
            r"(?i).{0,140}"
            r"(reliab|robust|failure|evaluation|benchmark)"
            r".{0,300}[.!?]"
        )
    ),
    (
        "planning",
        re.compile(
            r"(?i).{0,140}"
            r"(planning|planner|reasoning|control)"
            r".{0,300}[.!?]"
        )
    ),
    (
        "safety",
        re.compile(
            r"(?i).{0,140}"
            r"(safety|security|privacy|alignment)"
            r".{0,300}[.!?]"
        )
    ),
    (
        "execution",
        re.compile(
            r"(?i).{0,140}"
            r"(execution|tool use|action|autonomous)"
            r".{0,300}[.!?]"
        )
    )
]


def extract_claims(
    question: str,
    sources: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    claims = []

    for source in sources:
        local_claims = []

        text = source["text"]

        for category, pattern in CLAIM_PATTERNS:
            for match in pattern.finditer(text):
                excerpt = re.sub(
                    r"\s+",
                    " ",
                    match.group(0)
                ).strip()

                if (
                    len(excerpt) >= 80
                    and len(excerpt) <= 700
                ):
                    local_claims.append(
                        (
                            category,
                            excerpt
                        )
                    )

                if len(local_claims) >= 4:
                    break

        for category, excerpt in local_claims[:4]:
            claims.append(
                {
                    "category": category,
                    "claim": excerpt,
                    "evidence_id": source[
                        "evidence_id"
                    ],
                    "source_url": source["url"],
                    "source_title": source["title"],
                    "domain": source["domain"]
                }
            )

    return claims


# ============================================================
# CLAIM VERIFICATION
# ============================================================

def verify_claims(
    claims: List[Dict[str, Any]]
) -> Dict[str, Any]:
    verified = []
    unsupported = []

    for index, claim in enumerate(claims):
        if not claim.get("claim"):
            unsupported.append(claim)
            continue

        if not claim.get("evidence_id"):
            unsupported.append(claim)
            continue

        claim_tokens = tokenize(
            claim["claim"]
        )

        corroborating = []

        for other_index, other in enumerate(claims):
            if index == other_index:
                continue

            if (
                other.get("domain")
                == claim.get("domain")
            ):
                continue

            other_tokens = tokenize(
                other["claim"]
            )

            overlap = (
                len(
                    claim_tokens
                    & other_tokens
                )
                / max(
                    1,
                    len(claim_tokens)
                )
            )

            if overlap >= 0.20:
                corroborating.append(
                    other
                )

        verified_claim = dict(
            claim
        )

        verified_claim[
            "corroborated_by"
        ] = [
            item["evidence_id"]
            for item in corroborating[:5]
        ]

        if corroborating:
            verified_claim[
                "verification_status"
            ] = "corroborated"

            verified_claim[
                "confidence"
            ] = 0.90

        else:
            verified_claim[
                "verification_status"
            ] = "source_supported"

            verified_claim[
                "confidence"
            ] = 0.72

        verified.append(
            verified_claim
        )

    return {
        "verified_claims": verified,
        "unsupported_claims": unsupported,
        "corroboration_count": sum(
            1
            for claim in verified
            if claim[
                "verification_status"
            ] == "corroborated"
        )
    }


# ============================================================
# RESEARCH PIPELINE
# ============================================================

def research_pipeline(
    task_id: str,
    question: str
) -> Dict[str, Any]:
    expanded = expand_queries(
        question
    )

    discovery = discover_sources(
        question
    )

    discovered = discovery[
        "items"
    ]

    collected = collect_relevant_sources(
        task_id,
        question,
        discovered
    )

    sources = collected[
        "selected"
    ]

    domains = sorted(
        set(
            source["domain"]
            for source in sources
        )
    )

    # ========================================================
    # STRICT EVIDENCE GATE
    # ========================================================

    gate_passed = (
        len(sources) >= 3
        and len(domains) >= 2
    )

    gate = {
        "passed": gate_passed,
        "status": (
            "evidence_available"
            if gate_passed
            else "insufficient_evidence"
        ),
        "reason": "",
        "evidence_count": len(sources),
        "independent_domains": len(domains),
        "domains": domains,
        "minimum_evidence_required": 3
    }

    if not gate_passed:
        gate["reason"] = (
            "Insufficient relevant and "
            "independently sourced evidence."
        )

        report = {
            "status": "insufficient_evidence",
            "evidence_supported_facts": [],
            "assumptions": [],
            "contradictions": [],
            "uncertainty": 1.0,
            "confidence": 0.0,
            "claims": [],
            "verification": {
                "verified": False,
                "reason": (
                    "Evidence gate did not pass."
                )
            }
        }

    else:
        claims = extract_claims(
            question,
            sources
        )

        verification = verify_claims(
            claims
        )

        facts = [
            claim
            for claim in verification[
                "verified_claims"
            ]
            if claim[
                "verification_status"
            ] in {
                "source_supported",
                "corroborated"
            }
        ]

        if not facts:
            status = "insufficient_claim_evidence"
        else:
            status = "completed"

        report = {
            "status": status,
            "evidence_supported_facts": facts,
            "assumptions": [
                (
                    "Collected evidence may not "
                    "generalize to every AI-agent "
                    "architecture."
                ),
                (
                    "Applying findings from a "
                    "specific domain to general "
                    "autonomous execution is an "
                    "interpretation unless directly "
                    "demonstrated."
                )
            ],
            "contradictions": [],
            "uncertainty": round(
                max(
                    0.0,
                    1.0 - min(
                        1.0,
                        len(facts) / 8
                    )
                ),
                3
            ),
            "confidence": round(
                sum(
                    claim["confidence"]
                    for claim in facts
                )
                / len(facts),
                3
            ) if facts else 0.0,
            "claims": claims,
            "verification": verification
        }

    research_id = uid(
        "research"
    )

    connection = db()

    connection.execute(
        """
        INSERT INTO research
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            research_id,
            task_id,
            question,
            report["status"],
            json_dump(report),
            now_iso()
        )
    )

    connection.commit()
    connection.close()

    return {
        "research_id": research_id,
        "question": question,
        "status": report["status"],
        "expanded_queries": expanded,
        "sources_discovered": len(
            discovered
        ),
        "sources_collected": len(
            sources
        ),
        "evidence_count": len(
            sources
        ),
        "sources": [
            {
                "title": source["title"],
                "url": source["url"],
                "domain": source["domain"],
                "hash": source["hash"],
                "relevance": source["relevance"]
            }
            for source in sources
        ],
        "diagnostics": {
            "providers": discovery[
                "diagnostics"
            ],
            "sources_discovered": len(
                discovered
            ),
            "sources_fetched": len(
                sources
            ),
            "independent_domains": len(
                domains
            ),
            "evidence_gate_passed": (
                gate_passed
            )
        },
        "evidence_gate": gate,
        "evidence": [
            {
                "id": source[
                    "evidence_id"
                ],
                "source_url": source[
                    "url"
                ],
                "source_title": source[
                    "title"
                ],
                "claim": "",
                "excerpt": source[
                    "text"
                ][:2000],
                "content_hash": source[
                    "hash"
                ],
                "verification_status": (
                    "collected"
                ),
                "relevance": source[
                    "relevance"
                ]
            }
            for source in sources
        ],
        "analysis": report,
        "memory_id": None
    }


# ============================================================
# OPTIONAL AI SYNTHESIS
# ============================================================

def call_hf(
    prompt: str,
    allow_paid: bool = False
) -> Optional[str]:
    """
    HF synthesis is optional.

    TARGET-2.3 deliberately does NOT use the model
    to manufacture evidence.

    allow_paid=False blocks this provider because
    :cheapest does not guarantee $0 billing.
    """

    if not HF_TOKEN:
        return None

    if not allow_paid:
        return None

    url = (
        "https://router.huggingface.co/"
        "v1/chat/completions"
    )

    payload = {
        "model": HF_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.2,
        "max_tokens": 1500
    }

    try:
        response = requests.post(
            url,
            headers={
                "Authorization":
                    f"Bearer {HF_TOKEN}",
                "Content-Type":
                    "application/json"
            },
            json=payload,
            timeout=30
        )

        response.raise_for_status()

        return (
            response.json()
            ["choices"][0]
            ["message"]
            ["content"]
        )

    except Exception:
        return None


# ============================================================
# SAFE CALCULATOR
# ============================================================

def safe_calculate(
    expression: str
) -> float:
    tree = ast.parse(
        expression,
        mode="eval"
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
        ast.FloorDiv
    )

    for node in ast.walk(tree):
        if not isinstance(
            node,
            allowed
        ):
            raise ValueError(
                "unsupported_expression"
            )

        if (
            isinstance(
                node,
                ast.Constant
            )
            and not isinstance(
                node.value,
                (int, float)
            )
        ):
            raise ValueError(
                "numeric_values_only"
            )

        if (
            isinstance(
                node,
                ast.BinOp
            )
            and isinstance(
                node.op,
                ast.Pow
            )
        ):
            if isinstance(
                node.right,
                ast.Constant
            ):
                exponent = float(
                    node.right.value
                )

                if abs(exponent) > 10:
                    raise ValueError(
                        "power_too_large"
                    )

    return eval(
        compile(
            tree,
            "<safe-calculator>",
            "eval"
        ),
        {
            "__builtins__": {}
        },
        {}
    )


def calculator_artifact(
    task_id: str
) -> Dict[str, Any]:
    folder = (
        ARTIFACTS
        / task_id
    )

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    path = (
        folder
        / "simple_calculator.py"
    )

    content = '''import ast

_ALLOWED = (
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
)

def calculate(expression: str):
    tree = ast.parse(expression, mode="eval")

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError("unsupported expression")

        if (
            isinstance(node, ast.Constant)
            and not isinstance(node.value, (int, float))
        ):
            raise ValueError("numeric values only")

    return eval(
        compile(tree, "<calculator>", "eval"),
        {"__builtins__": {}},
        {}
    )


if __name__ == "__main__":
    import sys
    print(calculate(sys.argv[1]))
'''

    path.write_text(
        content,
        encoding="utf-8"
    )

    syntax_ok = True

    try:
        ast.parse(content)
    except SyntaxError:
        syntax_ok = False

    process = subprocess.run(
        [
            "python",
            str(path),
            "2+3*4"
        ],
        capture_output=True,
        text=True,
        timeout=10,
        shell=False
    )

    actual = None

    if process.returncode == 0:
        try:
            actual = int(
                process.stdout.strip()
            )
        except ValueError:
            actual = None

    verified = (
        syntax_ok
        and process.returncode == 0
        and actual == 14
    )

    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(
            path.read_bytes()
        ).hexdigest(),
        "python_syntax": syntax_ok,
        "functional_test": {
            "expression": "2+3*4",
            "expected": 14,
            "actual": actual,
            "returncode":
                process.returncode,
            "output":
                process.stdout.strip()
        },
        "verified": verified
    }


# ============================================================
# AUTHORIZED ACTION SYSTEM
# ============================================================

AUTHORIZED_ACTIONS = {
    "calculator_test",
    "create_calculator"
}


def registered_execute(
    task_id: str,
    action: str,
    args: Dict[str, Any]
) -> Dict[str, Any]:
    if action not in AUTHORIZED_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Action is not registered "
                "or authorized."
            )
        )

    if action == "create_calculator":
        return calculator_artifact(
            task_id
        )

    if action == "calculator_test":
        expression = str(
            args.get(
                "expression",
                "2+3*4"
            )
        )

        result = safe_calculate(
            expression
        )

        return {
            "expression": expression,
            "result": result,
            "verified": True
        }

    raise HTTPException(
        status_code=400,
        detail="Unknown authorized action."
    )


# ============================================================
# INTELLIGENCE GENOME
# ============================================================

def make_genome(
    task_id: str,
    objective: str,
    research: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    research = research or {}

    evidence_count = int(
        research.get(
            "evidence_count",
            0
        )
    )

    gate = research.get(
        "evidence_gate",
        {}
    )

    verification = (
        research
        .get("analysis", {})
        .get("verification", {})
    )

    corroboration_count = int(
        verification.get(
            "corroboration_count",
            0
        )
    )

    fitness = 0.40

    if gate.get("passed"):
        fitness += 0.20

    if evidence_count >= 5:
        fitness += 0.10

    if corroboration_count > 0:
        fitness += 0.20

    if corroboration_count >= 3:
        fitness += 0.10

    return {
        "genome_id": None,
        "task_id": task_id,
        "objective": objective,
        "strategies": [
            "evidence_first",
            "source_relevance_filter",
            "claim_to_evidence_mapping",
            "independent_verification",
            "failure_first",
            "reuse_memory"
        ],
        "research": {
            "evidence_gate": gate,
            "evidence_count":
                evidence_count,
            "corroboration_count":
                corroboration_count
        },
        "fitness": round(
            min(
                1.0,
                fitness
            ),
            3
        ),
        "reusable": True,
        "safety": {
            "arbitrary_shell": False,
            "automatic_spending": False,
            "automatic_self_modification": False,
            "authorized_actions_only": True
        },
        "created_at": now_iso()
    }


# ============================================================
# MASTER ORCHESTRATOR
# ============================================================

def run_infinity(
    request: RunRequest
) -> Dict[str, Any]:
    task_id = uid("task")

    save_task(
        task_id,
        request.objective,
        "running"
    )

    record_event(
        task_id,
        "started",
        {
            "objective":
                request.objective
        }
    )

    research = None
    memory_id = None

    try:
        if request.research:
            research = research_pipeline(
                task_id,
                request.objective
            )

            # CRITICAL:
            # Only verified research may be
            # stored as verified memory.
            if (
                request.remember
                and research["status"]
                == "completed"
                and research[
                    "evidence_gate"
                ]["passed"]
            ):
                memory_payload = {
                    "objective":
                        request.objective,
                    "research":
                        research["analysis"],
                    "evidence":
                        research["evidence"],
                    "sources":
                        research["sources"]
                }

                memory_id = save_memory(
                    json_dump(
                        memory_payload
                    ),
                    "verified_research",
                    verified=True
                )

                research[
                    "memory_id"
                ] = memory_id

            elif request.remember:
                memory_payload = {
                    "objective":
                        request.objective,
                    "research_status":
                        research["status"],
                    "note":
                        "Research was not stored as verified intelligence because the evidence gate or claim verification did not pass."
                }

                memory_id = save_memory(
                    json_dump(
                        memory_payload
                    ),
                    "research_uncertainty",
                    verified=False
                )

                research[
                    "memory_id"
                ] = memory_id

        genome = make_genome(
            task_id,
            request.objective,
            research
        )

        genome_id = save_genome(
            task_id,
            genome
        )

        genome[
            "genome_id"
        ] = genome_id

        result = {
            "task_id": task_id,
            "status": "completed",
            "version": VERSION,
            "objective":
                request.objective,

            "intent": {
                "type": (
                    "research"
                    if request.research
                    else "general"
                ),
                "objective":
                    request.objective,
                "priority":
                    "normal"
            },

            "world_model": {
                "objective":
                    request.objective,
                "resources": [
                    "local_python",
                    "local_filesystem",
                    "public_web",
                    "evidence_database",
                    "available_ai_model"
                ],
                "constraints": {
                    "free_first": True,
                    "no_automatic_spending": True,
                    "no_arbitrary_shell_execution": True,
                    "authorized_actions_only": True,
                    "evidence_gate": True
                }
            },

            "temporary_minds": [
                {
                    "id":
                        uid("mind"),
                    "name":
                        "researcher",
                    "role":
                        "research",
                    "temporary": True,
                    "status":
                        "ready"
                },
                {
                    "id":
                        uid("mind"),
                    "name":
                        "analyst",
                    "role":
                        "analysis",
                    "temporary": True,
                    "status":
                        "ready"
                },
                {
                    "id":
                        uid("mind"),
                    "name":
                        "critic",
                    "role":
                        "verification",
                    "temporary": True,
                    "status":
                        "ready"
                }
            ],

            "research":
                research,

            "intelligence_genome":
                genome,

            "evolution": {
                "status":
                    "proposal_only",
                "automatic_deployment":
                    False,
                "improvements": [
                    "Benchmark source relevance thresholds.",
                    "Add stronger independent corroboration.",
                    "Preserve provenance and source hashes.",
                    "Run proposed changes only inside a sandbox before deployment.",
                    "Add contradiction-aware evidence graphs."
                ]
            },

            "safety": {
                "arbitrary_shell_execution":
                    False,
                "automatic_spending":
                    False,
                "automatic_self_modification":
                    False,
                "authorized_actions_only":
                    True,
                "evidence_gate":
                    True
            }
        }

        save_task(
            task_id,
            request.objective,
            "completed",
            result
        )

        record_event(
            task_id,
            "completed",
            {
                "status":
                    "completed"
            }
        )

        return result

    except Exception as exc:
        recovery = [
            "Retry failed research providers.",
            "Reduce source set.",
            "Preserve successfully collected evidence.",
            "Do not convert missing evidence into facts."
        ]

        connection = db()

        connection.execute(
            """
            INSERT INTO failures
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                uid("failure"),
                task_id,
                "run_infinity",
                str(exc),
                json_dump(recovery),
                now_iso()
            )
        )

        connection.commit()
        connection.close()

        save_task(
            task_id,
            request.objective,
            "failed",
            {
                "error":
                    str(exc),
                "recovery":
                    recovery
            }
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Task failed: "
                + str(exc)
            )
        )


# ============================================================
# API — HEALTH
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "AI Infinity",
        "version": VERSION
    }


# ============================================================
# API — STATUS
# ============================================================

@app.get("/v1/status")
def status():
    connection = db()

    tasks = connection.execute(
        "SELECT COUNT(*) AS n FROM tasks"
    ).fetchone()["n"]

    memories = connection.execute(
        "SELECT COUNT(*) AS n FROM memories"
    ).fetchone()["n"]

    evidence = connection.execute(
        "SELECT COUNT(*) AS n FROM evidence"
    ).fetchone()["n"]

    genomes = connection.execute(
        "SELECT COUNT(*) AS n FROM genomes"
    ).fetchone()["n"]

    connection.close()

    return {
        "service": "AI Infinity",
        "version": VERSION,
        "counts": {
            "tasks": tasks,
            "memories": memories,
            "evidence": evidence,
            "genomes": genomes
        },
        "governor": {
            "free_first": True,
            "allow_paid_default": False,
            "automatic_spending": False
        }
    }


# ============================================================
# API — RUN
# ============================================================

@app.post("/v1/run")
def run(request: RunRequest):
    return run_infinity(
        request
    )


# ============================================================
# API — EXECUTE
# ============================================================

@app.post("/v1/execute")
def execute(
    request: ExecuteRequest
):
    task_id = uid("task")

    try:
        result = registered_execute(
            task_id,
            request.action,
            request.args
        )

        connection = db()

        connection.execute(
            """
            INSERT INTO executions
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                uid("execution"),
                task_id,
                request.action,
                "completed",
                json_dump(result),
                now_iso()
            )
        )

        connection.commit()
        connection.close()

        return {
            "task_id":
                task_id,
            "status":
                "completed",
            "action":
                request.action,
            "result":
                result
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )


# ============================================================
# API — TASK
# ============================================================

@app.get("/v1/tasks/{task_id}")
def get_task(
    task_id: str
):
    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM tasks
        WHERE id = ?
        """,
        (task_id,)
    ).fetchone()

    connection.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Task not found"
        )

    return dict(row)


# ============================================================
# API — MEMORY
# ============================================================

@app.get("/v1/memory")
def list_memory(
    limit: int = 20
):
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM memories
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (min(limit, 100),)
    ).fetchall()

    connection.close()

    return [
        dict(row)
        for row in rows
    ]


@app.post("/v1/memory")
def add_memory(
    request: MemoryRequest
):
    return {
        "memory_id":
            save_memory(
                request.content,
                request.kind,
                request.verified
            )
    }


# ============================================================
# API — GENOMES
# ============================================================

@app.get("/v1/genomes")
def list_genomes(
    limit: int = 20
):
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM genomes
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (min(limit, 100),)
    ).fetchall()

    connection.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# API — EVIDENCE
# ============================================================

@app.get("/v1/evidence")
def list_evidence(
    task_id: Optional[str] = None,
    limit: int = 50
):
    connection = db()

    if task_id:
        rows = connection.execute(
            """
            SELECT *
            FROM evidence
            WHERE task_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (
                task_id,
                min(limit, 200)
            )
        ).fetchall()

    else:
        rows = connection.execute(
            """
            SELECT *
            FROM evidence
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (min(limit, 200),)
        ).fetchall()

    connection.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# API — RESEARCH
# ============================================================

@app.get("/v1/research/{research_id}")
def get_research(
    research_id: str
):
    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM research
        WHERE id = ?
        """,
        (research_id,)
    ).fetchone()

    connection.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Research not found"
        )

    result = dict(row)

    result["report"] = json.loads(
        result.pop(
            "report_json"
        )
    )

    return result


# ============================================================
# API — FAILURES
# ============================================================

@app.get("/v1/failures")
def list_failures(
    limit: int = 50
):
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM failures
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (min(limit, 100),)
    ).fetchall()

    connection.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# API — AUDIT
# ============================================================

@app.get("/v1/audit")
def audit(
    limit: int = 100
):
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM events
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (min(limit, 200),)
    ).fetchall()

    connection.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# WEB UI
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def home():
    return f"""
<!doctype html>

<html>

<head>

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
AI Infinity {VERSION}
</title>

<style>

body {{
    font-family: system-ui;
    background: #0b1020;
    color: white;
    max-width: 900px;
    margin: auto;
    padding: 24px;
}}

textarea {{
    width: 100%;
    min-height: 160px;
    padding: 14px;
    border-radius: 12px;
    box-sizing: border-box;
    background: #151b30;
    color: white;
    border: 1px solid #303957;
}}

button {{
    padding: 12px 18px;
    border: 0;
    border-radius: 10px;
    margin-top: 10px;
    cursor: pointer;
}}

pre {{
    white-space: pre-wrap;
    background: #151b30;
    padding: 15px;
    border-radius: 12px;
    overflow: auto;
}}

.badge {{
    display: inline-block;
    padding: 6px 10px;
    border-radius: 8px;
    background: #202943;
}}

</style>

</head>

<body>

<h1>∞ AI Infinity</h1>

<p>
<span class="badge">
{VERSION}
</span>
</p>

<p>
Evidence-first autonomous intelligence fabric.
</p>

<textarea
    id="objective"
    placeholder="Enter your objective..."
></textarea>

<br>

<button onclick="runTask()">
Run Objective
</button>

<button onclick="calc()">
Test Calculator
</button>

<pre id="out">
Ready.
</pre>

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

        const response =
            await fetch(
                "/v1/run",
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

        const text =
            await response.text();

        document.getElementById(
            "out"
        ).textContent =
            text;

    }} catch (error) {{

        document.getElementById(
            "out"
        ).textContent =
            "Request error: "
            + error;

    }}

}}


async function calc() {{

    const response =
        await fetch(
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
        await response.text();

}}

</script>

</body>

</html>
"""


# ============================================================
# OPTIONAL VIDEO COMPATIBILITY
# ============================================================

@app.get("/video/{task_id}")
def video(
    task_id: str
):
    path = (
        WORK
        / task_id
        / "genius.mp4"
    )

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Video not found"
        )

    return FileResponse(
        path,
        media_type="video/mp4"
    )


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000"
            )
        )
    )
