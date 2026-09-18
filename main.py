import ast
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse, parse_qs

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# ♾️ AI INFINITY — TARGET 2.2.0
# Resilient Evidence-Gated Intelligence Fabric
#
# INTENT
#   ↓
# DISCOVER
#   ↓
# FETCH
#   ↓
# EXTRACT EVIDENCE
#   ↓
# VERIFY
#   ↓
# REASON
#   ↓
# MEMORY
#   ↓
# INTELLIGENCE GENOME
#   ↓
# EVOLVE
#
# Safety:
# - No arbitrary shell execution
# - No automatic spending
# - No automatic self-modification
# - Registered actions only
# - No fabricated evidence
# - Evidence must come from collected sources
# ============================================================

VERSION = "TARGET-2.2.0"
SERVICE = "AI Infinity"

BASE = Path("/tmp/ai-infinity")
ARTIFACTS = BASE / "artifacts"
WORK = BASE / "work"
LOGS = BASE / "logs"

for directory in (BASE, ARTIFACTS, WORK, LOGS):
    directory.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_MODEL = os.getenv(
    "HF_MODEL",
    "openai/gpt-oss-120b:cheapest",
)
HF_BACKUP_MODEL = os.getenv(
    "HF_BACKUP_MODEL",
    "openai/gpt-oss-20b:cheapest",
)
HF_URL = os.getenv(
    "HF_URL",
    "https://router.huggingface.co/v1/chat/completions",
)

REQUEST_TIMEOUT = 15
MAX_RESEARCH_SOURCES = 8
MAX_SEARCH_RESULTS = 20
MAX_SOURCE_CHARS = 14000

USER_AGENT = (
    "AI-Infinity/2.2 "
    "(evidence research; public web retrieval)"
)

app = FastAPI(
    title=SERVICE,
    version=VERSION,
    description="AI Infinity TARGET-2.2 Resilient Intelligence Fabric",
)


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
        indent=2,
        default=str,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8", errors="ignore")
    ).hexdigest()


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def normalize_url(url: str) -> str:
    if not url:
        return ""

    url = html.unescape(str(url).strip())

    if url.startswith("//"):
        url = "https:" + url

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return ""

    if not parsed.netloc:
        return ""

    blocked = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
    }

    query = parse_qs(parsed.query)

    clean = {
        key: values
        for key, values in query.items()
        if key.lower() not in blocked
    }

    query_string = "&".join(
        f"{key}={quote_plus(values[0])}"
        for key, values in sorted(clean.items())
    )

    result = (
        f"{parsed.scheme.lower()}://"
        f"{parsed.netloc.lower()}"
        f"{parsed.path or '/'}"
    )

    if query_string:
        result += "?" + query_string

    return result


def request_get(url: str, **kwargs):
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", USER_AGENT)

    last_error = None

    for attempt in range(2):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
                **kwargs,
            )
            return response
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.4)

    raise last_error


# ============================================================
# DATABASE
# ============================================================

def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = db()

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            provenance_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS genomes (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            genome_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS executions (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS failures (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            stage TEXT NOT NULL,
            error TEXT NOT NULL,
            recovery_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            source_url TEXT NOT NULL,
            source_title TEXT,
            claim TEXT,
            excerpt TEXT,
            content_hash TEXT,
            retrieved_at TEXT NOT NULL,
            verification_status TEXT DEFAULT 'unverified',
            metadata_json TEXT
        );

        CREATE TABLE IF NOT EXISTS research (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            question TEXT NOT NULL,
            summary TEXT,
            evidence_json TEXT,
            uncertainty REAL DEFAULT 0.5,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            url TEXT NOT NULL,
            domain TEXT,
            title TEXT,
            discovery_method TEXT,
            fetched INTEGER DEFAULT 0,
            http_status INTEGER,
            content_hash TEXT,
            retrieved_at TEXT,
            metadata_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_memories_type
        ON memories(memory_type);

        CREATE INDEX IF NOT EXISTS idx_evidence_task
        ON evidence(task_id);

        CREATE INDEX IF NOT EXISTS idx_research_task
        ON research(task_id);

        CREATE INDEX IF NOT EXISTS idx_sources_task
        ON sources(task_id);
        """
    )

    connection.commit()
    connection.close()


init_db()


# ============================================================
# EVENT / EXECUTION LOGGING
# ============================================================

def log_event(
    task_id: Optional[str],
    event_type: str,
    payload: Any,
):
    connection = db()

    connection.execute(
        """
        INSERT INTO events
        (id, task_id, event_type, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            uid("event"),
            task_id,
            event_type,
            json_dump(payload),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()


def log_execution(
    task_id: Optional[str],
    action: str,
    status: str,
    result: Any,
):
    connection = db()

    connection.execute(
        """
        INSERT INTO executions
        (id, task_id, action, status, result_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            uid("execution"),
            task_id,
            action,
            status,
            json_dump(result),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()


# ============================================================
# TASKS
# ============================================================

def create_task(objective: str) -> str:
    task_id = uid("task")
    timestamp = now_iso()

    connection = db()

    connection.execute(
        """
        INSERT INTO tasks
        (id, objective, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            task_id,
            objective,
            "running",
            timestamp,
            timestamp,
        ),
    )

    connection.commit()
    connection.close()

    return task_id


def update_task(
    task_id: str,
    status: str,
    result: Any,
):
    connection = db()

    connection.execute(
        """
        UPDATE tasks
        SET status = ?, result_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            json_dump(result),
            now_iso(),
            task_id,
        ),
    )

    connection.commit()
    connection.close()


def get_task(task_id: str):
    connection = db()

    row = connection.execute(
        "SELECT * FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()

    connection.close()

    if not row:
        return None

    result = dict(row)

    if result.get("result_json"):
        try:
            result["result"] = json.loads(
                result["result_json"]
            )
        except Exception:
            result["result"] = result["result_json"]

    result.pop("result_json", None)

    return result


# ============================================================
# MEMORY
# ============================================================

def save_memory(
    content: str,
    memory_type: str = "verified",
    confidence: float = 0.5,
    provenance: Optional[Dict[str, Any]] = None,
):
    memory_id = uid("memory")

    connection = db()

    connection.execute(
        """
        INSERT INTO memories
        (id, content, memory_type, confidence,
         provenance_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            content,
            memory_type,
            max(0.0, min(1.0, confidence)),
            json_dump(provenance or {}),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()

    return memory_id


def search_memory(
    query: str,
    limit: int = 8,
):
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM memories
        WHERE content LIKE ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            f"%{query[:200]}%",
            limit,
        ),
    ).fetchall()

    connection.close()

    return [dict(row) for row in rows]


# ============================================================
# INTENT ENGINE
# ============================================================

def classify_intent(objective: str):
    text = objective.lower()

    keyword_map = {
        "software": [
            "code",
            "python",
            "file",
            "software",
            "api",
            "program",
            "github",
        ],
        "research": [
            "research",
            "investigate",
            "sources",
            "evidence",
            "study",
            "literature",
            "papers",
        ],
        "science": [
            "science",
            "scientific",
            "experiment",
            "physics",
            "biology",
        ],
        "business": [
            "business",
            "market",
            "company",
            "startup",
        ],
        "content": [
            "video",
            "image",
            "article",
            "content",
        ],
    }

    domains = []

    for domain, terms in keyword_map.items():
        if any(term in text for term in terms):
            domains.append(domain)

    if not domains:
        domains.append("general")

    research_intent = any(
        term in text
        for term in (
            "research",
            "investigate",
            "sources",
            "evidence",
            "study",
            "literature",
        )
    )

    return {
        "type": (
            "research"
            if research_intent
            else "goal"
        ),
        "domains": domains,
        "objective": objective,
        "priority": "normal",
    }


# ============================================================
# WORLD MODEL
# ============================================================

def build_world_model(
    objective: str,
    intent: Dict[str, Any],
):
    return {
        "objective": objective,
        "known_entities": [],
        "events": [],
        "relationships": [],
        "resources": [
            "local_python",
            "local_filesystem",
            "public_web",
            "evidence_database",
            "available_ai_model",
        ],
        "constraints": {
            "free_first": True,
            "no_automatic_spending": True,
            "no_arbitrary_shell_execution": True,
            "authorized_actions_only": True,
            "evidence_gate": True,
        },
        "domains": intent["domains"],
        "uncertainty": 0.5,
    }


# ============================================================
# DREAM ENGINE
# ============================================================

def dream_strategies():
    return [
        {
            "strategy": "evidence_first",
            "description":
                "Collect external evidence before factual conclusions.",
        },
        {
            "strategy": "parallel_specialists",
            "description":
                "Use researcher, analyst and critic perspectives.",
        },
        {
            "strategy": "independent_verification",
            "description":
                "Check important claims against multiple sources.",
        },
        {
            "strategy": "failure_first",
            "description":
                "Predict failure modes before execution.",
        },
        {
            "strategy": "reuse_memory",
            "description":
                "Reuse previously verified intelligence.",
        },
        {
            "strategy": "counterfactual_search",
            "description":
                "Explore alternative explanations and strategies.",
        },
    ]


def build_counterfactuals(strategies):
    return [
        {
            "world": item["strategy"],
            "assumption": item["description"],
            "status": "simulation_only",
        }
        for item in strategies
    ]


# ============================================================
# TEMPORARY MINDS
# ============================================================

def create_temporary_minds(domains):
    roles = [
        ("researcher", "research"),
        ("analyst", "analysis"),
        ("critic", "verification"),
        ("engineer", "software"),
    ]

    minds = []

    for name, role in roles:
        if (
            role in domains
            or role in ("analysis", "verification")
        ):
            minds.append(
                {
                    "id": uid("mind"),
                    "name": name,
                    "role": role,
                    "temporary": True,
                    "status": "ready",
                }
            )

    if not minds:
        minds.append(
            {
                "id": uid("mind"),
                "name": "generalist",
                "role": "general",
                "temporary": True,
                "status": "ready",
            }
        )

    return minds


# ============================================================
# HTML EXTRACTION
# ============================================================

class TextExtractor(HTMLParser):

    def __init__(self):
        super().__init__()
        self.parts = []
        self.title_parts = []
        self.in_title = False
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == "title":
            self.in_title = True

        if tag in (
            "script",
            "style",
            "noscript",
            "svg",
        ):
            self.skip_depth += 1

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "title":
            self.in_title = False

        if tag in (
            "script",
            "style",
            "noscript",
            "svg",
        ):
            self.skip_depth = max(
                0,
                self.skip_depth - 1,
            )

    def handle_data(self, data):
        if self.skip_depth:
            return

        value = re.sub(
            r"\s+",
            " ",
            data,
        ).strip()

        if not value:
            return

        if self.in_title:
            self.title_parts.append(value)

        self.parts.append(value)

    def text(self):
        return " ".join(self.parts)

    def title(self):
        return " ".join(self.title_parts).strip()


def extract_html(raw: str):
    parser = TextExtractor()

    try:
        parser.feed(raw)
    except Exception:
        pass

    return {
        "title": parser.title()[:500],
        "text": re.sub(
            r"\s+",
            " ",
            parser.text(),
        ).strip(),
    }


# ============================================================
# RESEARCH QUERY COMPILER
# ============================================================

def research_query_terms(query: str) -> str:
    text = re.sub(
        r"https?://\S+",
        " ",
        query,
    )

    text = re.sub(
        r"[^A-Za-z0-9\s\-]",
        " ",
        text,
    )

    stop = {
        "research",
        "what",
        "how",
        "why",
        "need",
        "needs",
        "become",
        "becoming",
        "reliable",
        "collect",
        "multiple",
        "public",
        "sources",
        "separate",
        "evidence",
        "supported",
        "facts",
        "assumptions",
        "verify",
        "verification",
        "save",
        "create",
        "intelligence",
        "genome",
        "and",
        "the",
        "for",
        "with",
        "from",
        "into",
        "that",
        "this",
        "are",
        "is",
        "to",
        "of",
        "a",
        "an",
        "on",
        "in",
        "as",
        "by",
        "or",
    }

    words = []

    for word in text.lower().split():
        if (
            len(word) >= 4
            and word not in stop
            and word not in words
        ):
            words.append(word)

    return " ".join(words[:14]) or query[:200]


# ============================================================
# DISCOVERY PROVIDER 1 — DIRECT URL
# ============================================================

def discover_direct_urls(query: str):
    results = []

    for match in re.findall(
        r"https?://[^\s<>\"']+",
        query,
    ):
        url = normalize_url(
            match.rstrip(".,);")
        )

        if url:
            results.append(
                {
                    "url": url,
                    "domain": domain_of(url),
                    "method": "direct_url",
                }
            )

    return results


# ============================================================
# DISCOVERY PROVIDER 2 — WIKIPEDIA
# ============================================================

def discover_wikipedia(query: str):
    search = research_query_terms(query)

    url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query"
        "&list=search"
        "&format=json"
        "&formatversion=2"
        "&srlimit=5"
        "&srsearch="
        + quote_plus(search)
    )

    response = request_get(
        url,
        headers={
            "Accept": "application/json",
        },
    )

    if response.status_code != 200:
        return []

    data = response.json()

    results = []

    for item in data.get(
        "query",
        {},
    ).get(
        "search",
        [],
    ):
        title = item.get("title", "")

        if not title:
            continue

        page_url = (
            "https://en.wikipedia.org/wiki/"
            + quote_plus(
                title.replace(" ", "_")
            )
        )

        results.append(
            {
                "url": normalize_url(page_url),
                "domain": "en.wikipedia.org",
                "title": title,
                "method": "wikipedia_api",
            }
        )

    return results


# ============================================================
# DISCOVERY PROVIDER 3 — ARXIV
# ============================================================

def discover_arxiv(query: str):
    search = research_query_terms(query)

    url = (
        "https://export.arxiv.org/api/query"
        "?search_query=all:"
        + quote_plus(search)
        + "&start=0"
        "&max_results=5"
    )

    response = request_get(url)

    if response.status_code != 200:
        return []

    root = ET.fromstring(response.text)

    namespace = {
        "a": "http://www.w3.org/2005/Atom"
    }

    results = []

    for entry in root.findall(
        "a:entry",
        namespace,
    ):
        identifier = (
            entry.findtext(
                "a:id",
                default="",
                namespaces=namespace,
            )
            or ""
        ).strip()

        title = (
            entry.findtext(
                "a:title",
                default="",
                namespaces=namespace,
            )
            or ""
        ).strip()

        if not identifier.startswith("http"):
            continue

        results.append(
            {
                "url": normalize_url(identifier),
                "domain": domain_of(identifier),
                "title": re.sub(
                    r"\s+",
                    " ",
                    title,
                ),
                "method": "arxiv_api",
            }
        )

    return results


# ============================================================
# DISCOVERY PROVIDER 4 — CROSSREF
# ============================================================

def discover_crossref(query: str):
    search = research_query_terms(query)

    url = (
        "https://api.crossref.org/v1/works"
        "?rows=5"
        "&select=DOI,title,URL,abstract,published"
        "&query.bibliographic="
        + quote_plus(search)
    )

    response = request_get(
        url,
        headers={
            "Accept": "application/json",
        },
    )

    if response.status_code != 200:
        return []

    data = response.json()

    results = []

    for item in data.get(
        "message",
        {},
    ).get(
        "items",
        [],
    ):
        doi = item.get("DOI", "")
        raw_url = item.get("URL", "")

        url_value = raw_url

        if not url_value and doi:
            url_value = (
                "https://doi.org/"
                + doi
            )

        normalized = normalize_url(
            url_value
        )

        if not normalized:
            continue

        title = " ".join(
            item.get("title", [])
        ).strip()

        results.append(
            {
                "url": normalized,
                "domain": domain_of(normalized),
                "title": title,
                "method": "crossref_api",
                "metadata": {
                    "doi": doi,
                    "abstract": item.get(
                        "abstract",
                        "",
                    ),
                    "published": item.get(
                        "published",
                        {},
                    ),
                },
            }
        )

    return results


# ============================================================
# DISCOVERY PROVIDER 5 — OPENALEX
# ============================================================

def discover_openalex(query: str):
    search = research_query_terms(query)

    url = (
        "https://api.openalex.org/works"
        "?per-page=5"
        "&search="
        + quote_plus(search)
    )

    response = request_get(
        url,
        headers={
            "Accept": "application/json",
        },
    )

    if response.status_code != 200:
        return []

    data = response.json()

    results = []

    for item in data.get(
        "results",
        [],
    ):
        raw_url = (
            item.get("doi")
            or item.get("id")
            or ""
        )

        normalized = normalize_url(
            raw_url
        )

        if not normalized:
            continue

        results.append(
            {
                "url": normalized,
                "domain": domain_of(normalized),
                "title": item.get(
                    "title",
                    "",
                ),
                "method": "openalex_api",
                "metadata": {
                    "openalex_id":
                        item.get("id", ""),
                    "publication_year":
                        item.get(
                            "publication_year"
                        ),
                    "type":
                        item.get(
                            "type",
                            "",
                        ),
                },
            }
        )

    return results


# ============================================================
# DISCOVERY ORCHESTRATOR
# ============================================================

def discover_sources(query: str):
    providers = [
        (
            "direct_url",
            discover_direct_urls,
        ),
        (
            "wikipedia",
            discover_wikipedia,
        ),
        (
            "arxiv",
            discover_arxiv,
        ),
        (
            "crossref",
            discover_crossref,
        ),
        (
            "openalex",
            discover_openalex,
        ),
    ]

    discovered = []
    diagnostics = []

    for name, provider in providers:
        started = time.time()

        try:
            items = provider(query)

            diagnostics.append(
                {
                    "provider": name,
                    "status": "ok",
                    "count": len(items),
                    "latency_ms": round(
                        (
                            time.time()
                            - started
                        ) * 1000
                    ),
                }
            )

            discovered.extend(items)

        except Exception as exc:
            diagnostics.append(
                {
                    "provider": name,
                    "status": "error",
                    "count": 0,
                    "error": str(exc)[:500],
                    "latency_ms": round(
                        (
                            time.time()
                            - started
                        ) * 1000
                    ),
                }
            )

    unique = {}

    for item in discovered:
        url = normalize_url(
            item.get("url", "")
        )

        if not url:
            continue

        if url not in unique:
            item["url"] = url
            unique[url] = item

    results = list(unique.values())[
        :MAX_SEARCH_RESULTS
    ]

    return results, diagnostics


# ============================================================
# SOURCE FETCH
# ============================================================

def fetch_source(
    task_id: str,
    source: Dict[str, Any],
):
    url = normalize_url(
        source.get("url", "")
    )

    if not url:
        return None

    try:
        response = request_get(
            url,
            headers={
                "Accept":
                    "text/html,text/plain,"
                    "application/xhtml+xml,"
                    "application/xml,"
                    "application/json",
            },
        )

        final_url = normalize_url(
            response.url
        ) or url

        status = response.status_code

        if status >= 400:
            log_event(
                task_id,
                "source_fetch_failed",
                {
                    "url": url,
                    "status": status,
                },
            )
            return None

        raw = response.text[:500000]

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        if (
            "text/html" in content_type
            or "application/xhtml" in content_type
        ):
            extracted = extract_html(raw)

            title = (
                extracted["title"]
                or source.get("title")
                or final_url
            )

            text = extracted["text"]

        elif "json" in content_type:
            title = (
                source.get("title")
                or final_url
            )
            text = raw

        else:
            title = (
                source.get("title")
                or final_url
            )
            text = raw

        text = re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

        text = text[:MAX_SOURCE_CHARS]

        if len(text) < 120:
            return None

        content_hash = sha256_text(text)

        metadata = {
            "discovery_method":
                source.get(
                    "method",
                    "unknown",
                ),
            "domain":
                domain_of(final_url),
            "original_url": url,
            "final_url": final_url,
            "http_status": status,
            "content_type":
                content_type,
            "content_length":
                len(text),
            "retrieved_at":
                now_iso(),
        }

        source_id = uid("source")

        connection = db()

        connection.execute(
            """
            INSERT INTO sources
            (id, task_id, url, domain, title,
             discovery_method, fetched,
             http_status, content_hash,
             retrieved_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                task_id,
                final_url,
                domain_of(final_url),
                title,
                source.get(
                    "method",
                    "unknown",
                ),
                1,
                status,
                content_hash,
                now_iso(),
                json_dump(metadata),
            ),
        )

        connection.commit()
        connection.close()

        return {
            "source_id": source_id,
            "url": final_url,
            "title": title,
            "domain":
                domain_of(final_url),
            "text": text,
            "content_hash":
                content_hash,
            "metadata":
                metadata,
        }

    except Exception as exc:
        log_event(
            task_id,
            "source_fetch_failed",
            {
                "url": url,
                "error": str(exc)[:500],
            },
        )

        return None


# ============================================================
# EVIDENCE
# ============================================================

def save_evidence(
    task_id: str,
    source: Dict[str, Any],
    excerpt: str,
    claim: str = "",
):
    evidence_id = uid("evidence")

    connection = db()

    connection.execute(
        """
        INSERT INTO evidence
        (id, task_id, source_url,
         source_title, claim, excerpt,
         content_hash, retrieved_at,
         verification_status,
         metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            task_id,
            source["url"],
            source["title"],
            claim,
            excerpt[:5000],
            source["content_hash"],
            now_iso(),
            "collected",
            json_dump(
                source.get(
                    "metadata",
                    {},
                )
            ),
        ),
    )

    connection.commit()
    connection.close()

    return evidence_id


def evidence_for_task(task_id: str):
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM evidence
        WHERE task_id = ?
        ORDER BY retrieved_at ASC
        """,
        (task_id,),
    ).fetchall()

    connection.close()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["metadata"] = json.loads(
                item.pop(
                    "metadata_json"
                )
            )
        except Exception:
            item["metadata"] = {}

        result.append(item)

    return result


# ============================================================
# EVIDENCE EXCERPT SELECTOR
# ============================================================

def select_evidence_excerpt(
    text: str,
    query: str,
    max_chars: int = 4500,
):
    clean = re.sub(
        r"\s+",
        " ",
        text or "",
    ).strip()

    if not clean:
        return ""

    sentences = re.split(
        r"(?<=[.!?])\s+",
        clean,
    )

    terms = [
        term
        for term in re.findall(
            r"[A-Za-z0-9]{4,}",
            research_query_terms(
                query
            ).lower(),
        )
    ]

    ranked = []

    for index, sentence in enumerate(
        sentences
    ):
        lower = sentence.lower()

        score = sum(
            1
            for term in terms
            if term in lower
        )

        if score:
            ranked.append(
                (
                    score,
                    -index,
                    sentence,
                )
            )

    ranked.sort(reverse=True)

    selected = []
    total = 0

    for _, _, sentence in ranked:
        if (
            total
            + len(sentence)
            + 1
            > max_chars
        ):
            continue

        selected.append(sentence)
        total += len(sentence) + 1

        if len(selected) >= 8:
            break

    if not selected:
        return clean[:max_chars]

    return " ".join(selected)[:max_chars]


# ============================================================
# EVIDENCE GATE
# ============================================================

def evidence_gate(
    evidence: List[Dict[str, Any]],
):
    valid = []
    domains = set()

    for item in evidence:
        url = item.get(
            "source_url",
            "",
        )

        excerpt = item.get(
            "excerpt",
            "",
        )

        content_hash = item.get(
            "content_hash",
            "",
        )

        if (
            url
            and excerpt
            and content_hash
        ):
            valid.append(item)

            domain = domain_of(url)

            if domain:
                domains.add(domain)

    domain_list = sorted(domains)

    if not valid:
        return {
            "passed": False,
            "status":
                "insufficient_evidence",
            "reason":
                "No valid collected evidence exists.",
            "evidence_count": 0,
            "independent_domains": 0,
            "domains": [],
            "minimum_evidence_required": 2,
        }

    if (
        len(valid) < 2
        or len(domain_list) < 2
    ):
        return {
            "passed": False,
            "status":
                "insufficient_independent_evidence",
            "reason":
                "At least two evidence records from two independent domains are required.",
            "evidence_count": len(valid),
            "independent_domains":
                len(domain_list),
            "domains": domain_list,
            "minimum_evidence_required": 2,
        }

    return {
        "passed": True,
        "status":
            "evidence_available",
        "reason":
            "Collected evidence passed structural and source-diversity validation.",
        "evidence_count": len(valid),
        "independent_domains":
            len(domain_list),
        "domains": domain_list,
        "minimum_evidence_required": 2,
    }


# ============================================================
# HUGGING FACE REASONING
# ============================================================

def call_hf(
    prompt: str,
    model: Optional[str] = None,
):
    if not HF_TOKEN:
        return None

    selected_model = (
        model
        or HF_MODEL
    )

    payload = {
        "model": selected_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the reasoning layer "
                    "of AI Infinity. "
                    "Never invent sources, URLs, "
                    "citations, experiments, "
                    "evidence, or Evidence IDs. "
                    "Only use supplied evidence."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.1,
        "max_tokens": 1800,
    }

    try:
        response = requests.post(
            HF_URL,
            headers={
                "Authorization":
                    f"Bearer {HF_TOKEN}",
                "Content-Type":
                    "application/json",
            },
            json=payload,
            timeout=45,
        )

        if response.status_code >= 400:
            return None

        data = response.json()

        choices = data.get(
            "choices",
            [],
        )

        if not choices:
            return None

        content = choices[0].get(
            "message",
            {},
        ).get("content")

        if not content:
            return None

        return str(content)

    except Exception:
        return None


# ============================================================
# RESEARCH SYNTHESIS
# ============================================================

def build_evidence_packet(
    question: str,
    evidence: List[Dict[str, Any]],
):
    packet = []

    for index, item in enumerate(
        evidence,
        start=1,
    ):
        packet.append(
            f"""
SOURCE {index}
Evidence ID: {item["id"]}
Title: {item["source_title"]}
URL: {item["source_url"]}
Domain: {domain_of(item["source_url"])}
Hash: {item["content_hash"]}

EXCERPT:
{item["excerpt"]}
"""
        )

    return (
        "QUESTION:\n"
        + question
        + "\n\n"
        + "\n".join(packet)
    )


def synthesize_research(
    question: str,
    evidence: List[Dict[str, Any]],
):
    gate = evidence_gate(evidence)

    if not gate["passed"]:
        return {
            "status":
                "insufficient_evidence",
            "summary":
                "No evidence-supported synthesis was produced because the evidence gate failed.",
            "evidence_supported_facts": [],
            "assumptions": [
                "Further independent source collection is required."
            ],
            "uncertainty": 1.0,
            "confidence": 0.0,
            "verification": {
                "verified": False,
                "reason":
                    gate["reason"],
            },
        }

    packet = build_evidence_packet(
        question,
        evidence,
    )

    prompt = f"""
Research question:

{question}

Use ONLY the evidence below.

{packet}

Produce:

1. Summary
2. Evidence-supported facts
3. Assumptions / interpretations
4. Contradictions / uncertainty
5. Evidence IDs supporting each important fact
6. Confidence from 0 to 1

Rules:

- Never invent a source.
- Never invent an Evidence ID.
- Never claim something is supported unless supplied evidence supports it.
- Clearly separate facts from assumptions.
- If evidence conflicts, report the conflict.
"""

    answer = call_hf(prompt)

    if not answer:
        return {
            "status":
                "evidence_collected_ai_unavailable",
            "summary":
                "Evidence was collected, but the AI synthesis provider was unavailable.",
            "evidence_supported_facts": [
                {
                    "evidence_id":
                        item["id"],
                    "source":
                        item["source_title"],
                    "url":
                        item["source_url"],
                    "excerpt":
                        item["excerpt"][:1000],
                }
                for item in evidence
            ],
            "assumptions": [],
            "uncertainty": 0.4,
            "confidence": 0.6,
            "verification": {
                "verified": True,
                "reason":
                    "Evidence was collected from multiple independent domains.",
            },
        }

    return {
        "status":
            "completed",
        "summary":
            answer,
        "evidence_supported_facts":
            "See evidence-mapped synthesis above.",
        "assumptions":
            "See explicit assumptions in synthesis.",
        "uncertainty":
            max(
                0.0,
                1.0
                - min(
                    1.0,
                    len(evidence) / 6.0,
                ),
            ),
        "confidence":
            min(
                0.95,
                0.55
                + min(
                    0.35,
                    len(evidence) * 0.05,
                ),
            ),
        "verification": {
            "verified": True,
            "reason":
                "Synthesis was generated only after the evidence gate passed.",
        },
    }


# ============================================================
# RESEARCH ENGINE
# ============================================================

def run_research(
    task_id: str,
    question: str,
):
    research_id = uid("research")

    log_event(
        task_id,
        "research_started",
        {
            "question": question,
            "version": VERSION,
        },
    )

    normalized_query = (
        research_query_terms(question)
    )

    discovered, discovery_diagnostics = (
        discover_sources(question)
    )

    log_event(
        task_id,
        "sources_discovered",
        {
            "count":
                len(discovered),
            "normalized_query":
                normalized_query,
            "sources":
                discovered,
            "diagnostics":
                discovery_diagnostics,
        },
    )

    fetched = []
    fetch_failures = []

    for source in discovered:
        if len(fetched) >= MAX_RESEARCH_SOURCES:
            break

        result = fetch_source(
            task_id,
            source,
        )

        if result:
            fetched.append(result)
        else:
            fetch_failures.append(
                {
                    "url":
                        source.get(
                            "url",
                            "",
                        ),
                    "domain":
                        source.get(
                            "domain",
                            "",
                        ),
                    "method":
                        source.get(
                            "method",
                            "",
                        ),
                    "reason":
                        "fetch_failed_or_content_too_short",
                }
            )

    for source in fetched:
        excerpt = select_evidence_excerpt(
            source.get(
                "text",
                "",
            ),
            question,
        )

        if len(excerpt.strip()) < 120:
            continue

        save_evidence(
            task_id,
            source,
            excerpt,
        )

    evidence = evidence_for_task(
        task_id
    )

    gate = evidence_gate(
        evidence
    )

    log_event(
        task_id,
        "evidence_gate",
        gate,
    )

    synthesis = synthesize_research(
        question,
        evidence,
    )

    diagnostics = {
        "query":
            question,
        "normalized_query":
            normalized_query,
        "providers":
            discovery_diagnostics,
        "sources_discovered":
            len(discovered),
        "sources_fetched":
            len(fetched),
        "fetch_failures":
            fetch_failures[:20],
        "independent_domains":
            gate.get(
                "independent_domains",
                0,
            ),
        "evidence_gate_passed":
            gate.get(
                "passed",
                False,
            ),
    }

    connection = db()

    connection.execute(
        """
        INSERT INTO research
        (id, task_id, question, summary,
         evidence_json, uncertainty, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            research_id,
            task_id,
            question,
            synthesis.get(
                "summary",
                "",
            ),
            json_dump(evidence),
            synthesis.get(
                "uncertainty",
                1.0,
            ),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()

    memory_id = save_memory(
        content=(
            "AI Infinity research result.\n"
            f"Question: {question}\n"
            f"Research ID: {research_id}\n"
            f"Sources discovered: {len(discovered)}\n"
            f"Sources fetched: {len(fetched)}\n"
            f"Evidence records: {len(evidence)}\n"
            f"Evidence gate: {gate['status']}\n"
            f"Summary:\n{synthesis.get('summary', '')}"
        ),
        memory_type=(
            "verified_research"
            if gate["passed"]
            else "research_insufficient_evidence"
        ),
        confidence=synthesis.get(
            "confidence",
            0.0,
        ),
        provenance={
            "task_id":
                task_id,
            "research_id":
                research_id,
            "sources": [
                item["source_url"]
                for item in evidence
            ],
            "evidence_gate":
                gate,
            "diagnostics":
                diagnostics,
        },
    )

    return {
        "research_id":
            research_id,
        "question":
            question,
        "status":
            synthesis.get(
                "status",
                "completed",
            ),
        "sources_discovered":
            len(discovered),
        "sources_collected":
            len(fetched),
        "evidence_count":
            len(evidence),
        "sources": [
            {
                "title":
                    item["source_title"],
                "url":
                    item["source_url"],
                "domain":
                    domain_of(
                        item["source_url"]
                    ),
                "hash":
                    item["content_hash"],
            }
            for item in evidence
        ],
        "diagnostics":
            diagnostics,
        "evidence_gate":
            gate,
        "evidence":
            evidence,
        "analysis":
            synthesis,
        "memory_id":
            memory_id,
        "verification":
            synthesis.get(
                "verification",
                {
                    "verified":
                        False
                },
            ),
    }


# ============================================================
# SAFE CALCULATOR
# ============================================================

ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
}

ALLOWED_UNARYOPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}


def safe_calculate(expression: str):
    if len(expression) > 200:
        raise ValueError(
            "Expression too long"
        )

    tree = ast.parse(
        expression,
        mode="eval",
    )

    def evaluate(node):
        if isinstance(
            node,
            ast.Expression,
        ):
            return evaluate(
                node.body
            )

        if isinstance(
            node,
            ast.Constant,
        ):
            if isinstance(
                node.value,
                bool,
            ):
                raise ValueError(
                    "Boolean values are not allowed"
                )

            if isinstance(
                node.value,
                (int, float),
            ):
                if not math.isfinite(
                    float(node.value)
                ):
                    raise ValueError(
                        "Non-finite number"
                    )

                return node.value

            raise ValueError(
                "Only numeric constants are allowed"
            )

        if isinstance(
            node,
            ast.BinOp,
        ):
            operation = ALLOWED_BINOPS.get(
                type(node.op)
            )

            if operation is None:
                raise ValueError(
                    "Operator not allowed"
                )

            return operation(
                evaluate(node.left),
                evaluate(node.right),
            )

        if isinstance(
            node,
            ast.UnaryOp,
        ):
            operation = ALLOWED_UNARYOPS.get(
                type(node.op)
            )

            if operation is None:
                raise ValueError(
                    "Unary operator not allowed"
                )

            return operation(
                evaluate(node.operand)
            )

        raise ValueError(
            "Expression node not allowed: "
            + type(node).__name__
        )

    return evaluate(tree)


# ============================================================
# SAFE REGISTERED EXECUTION
# ============================================================

def create_calculator_artifact(
    task_id: str,
):
    directory = ARTIFACTS / task_id
    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = (
        directory
        / "simple_calculator.py"
    )

    source = r'''import ast
import math

ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
}

ALLOWED_UNARYOPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}


def evaluate(expression):
    tree = ast.parse(
        expression,
        mode="eval",
    )

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                raise ValueError("Boolean not allowed")

            if isinstance(node.value, (int, float)):
                if not math.isfinite(float(node.value)):
                    raise ValueError("Non-finite number")
                return node.value

            raise ValueError("Number required")

        if isinstance(node, ast.BinOp):
            operation = ALLOWED_BINOPS.get(type(node.op))

            if operation is None:
                raise ValueError("Operator not allowed")

            return operation(
                walk(node.left),
                walk(node.right),
            )

        if isinstance(node, ast.UnaryOp):
            operation = ALLOWED_UNARYOPS.get(type(node.op))

            if operation is None:
                raise ValueError("Unary operator not allowed")

            return operation(
                walk(node.operand)
            )

        raise ValueError("Node not allowed")

    return walk(tree)


if __name__ == "__main__":
    print(evaluate("2+3*4"))
'''

    path.write_text(
        source,
        encoding="utf-8",
    )

    data = path.read_bytes()

    return {
        "status":
            "completed",
        "filename":
            path.name,
        "path":
            str(path),
        "bytes":
            len(data),
        "sha256":
            hashlib.sha256(
                data
            ).hexdigest(),
    }


def verify_calculator(
    artifact,
):
    path = Path(
        artifact["path"]
    )

    if not path.exists():
        return {
            "verified": False,
            "status": "failed",
            "reason":
                "Artifact does not exist.",
        }

    try:
        source = path.read_text(
            encoding="utf-8"
        )

        ast.parse(
            source,
            mode="exec",
        )

        syntax_ok = True

    except Exception as exc:
        return {
            "verified": False,
            "status": "failed",
            "reason":
                f"Syntax error: {exc}",
        }

    try:
        process = subprocess.run(
            [
                sys.executable,
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        output = process.stdout.strip()

        return {
            "verified":
                syntax_ok
                and process.returncode == 0
                and output == "14",
            "status":
                "completed",
            "syntax":
                syntax_ok,
            "functional_test":
                process.returncode == 0,
            "expression":
                "2+3*4",
            "expected":
                14,
            "actual":
                output,
            "returncode":
                process.returncode,
            "stderr":
                process.stderr[-1000:],
        }

    except Exception as exc:
        return {
            "verified": False,
            "status": "failed",
            "reason": str(exc),
        }


def execute_registered_action(
    task_id: str,
    action: str,
    objective: str = "",
):
    if action == "create_calculator":
        artifact = create_calculator_artifact(
            task_id
        )

        verification = verify_calculator(
            artifact
        )

        result = {
            "action":
                action,
            "artifact":
                artifact,
            "verification":
                verification,
            "authorized":
                True,
        }

        log_execution(
            task_id,
            action,
            (
                "completed"
                if verification.get(
                    "verified",
                    False,
                )
                else "failed"
            ),
            result,
        )

        return result

    result = {
        "action":
            action,
        "status":
            "rejected",
        "reason":
            "Action is not registered.",
        "authorized":
            False,
    }

    log_execution(
        task_id,
        action,
        "rejected",
        result,
    )

    return result


# ============================================================
# INTELLIGENCE GENOME
# ============================================================

def create_genome(
    task_id: str,
    objective: str,
    intent: Dict[str, Any],
    strategies: List[Dict[str, Any]],
    minds: List[Dict[str, Any]],
    execution: Any,
    verification: Any,
    research: Any,
):
    verified = bool(
        verification
        and verification.get(
            "verified",
            False,
        )
    )

    evidence_verified = bool(
        research
        and research.get(
            "evidence_gate",
            {},
        ).get(
            "passed",
            False,
        )
    )

    fitness = 0.0

    if evidence_verified:
        fitness += 0.5

    if verified:
        fitness += 0.5

    genome_id = uid("genome")

    genome = {
        "genome_id":
            genome_id,
        "task_id":
            task_id,
        "objective":
            objective,
        "intent":
            intent,
        "strategies":
            strategies,
        "temporary_minds":
            minds,
        "research":
            {
                "evidence_gate":
                    (
                        research or {}
                    ).get(
                        "evidence_gate",
                        {},
                    ),
                "evidence_count":
                    (
                        research or {}
                    ).get(
                        "evidence_count",
                        0,
                    ),
            },
        "execution":
            execution,
        "verification":
            verification,
        "fitness":
            fitness,
        "reusable":
            fitness >= 0.5,
        "safety":
            {
                "arbitrary_shell":
                    False,
                "automatic_spending":
                    False,
                "automatic_self_modification":
                    False,
            },
        "created_at":
            now_iso(),
    }

    connection = db()

    connection.execute(
        """
        INSERT INTO genomes
        (id, task_id, genome_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            genome_id,
            task_id,
            json_dump(genome),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()

    return genome


# ============================================================
# EVOLUTION
# ============================================================

def propose_evolution(genome):
    improvements = []

    research = genome.get(
        "research",
        {},
    )

    if not (
        research.get(
            "evidence_gate",
            {},
        ).get(
            "passed",
            False,
        )
    ):
        improvements.append(
            "Improve independent source discovery and retrieval."
        )

    if not genome.get(
        "verification"
    ):
        improvements.append(
            "Add stronger independent verification."
        )

    improvements.extend(
        [
            "Benchmark alternative strategies.",
            "Preserve successful strategies as reusable genomes.",
            "Run proposed changes only inside a sandbox before deployment.",
        ]
    )

    return {
        "status":
            "proposal_only",
        "automatic_deployment":
            False,
        "improvements":
            improvements,
    }


# ============================================================
# MASTER LOOP
# ============================================================

def run_infinity(
    objective: str,
    research: bool = True,
    verify: bool = True,
    remember: bool = True,
):
    task_id = create_task(
        objective
    )

    try:
        intent = classify_intent(
            objective
        )

        world_model = build_world_model(
            objective,
            intent,
        )

        strategies = dream_strategies()

        counterfactuals = (
            build_counterfactuals(
                strategies
            )
        )

        minds = create_temporary_minds(
            intent["domains"]
        )

        compiler = {
            "intent":
                objective,
            "goal_model":
                intent,
            "constraints":
                world_model[
                    "constraints"
                ],
            "available_resources":
                world_model[
                    "resources"
                ],
            "candidate_strategies":
                strategies,
            "execution_graph":
                "generated",
        }

        research_result = None

        if research:
            research_result = run_research(
                task_id,
                objective,
            )

        execution_result = None
        verification_result = None

        if (
            "calculator"
            in objective.lower()
            or "calculate" in objective.lower()
        ):
            execution_result = (
                execute_registered_action(
                    task_id,
                    "create_calculator",
                    objective,
                )
            )

            verification_result = (
                execution_result.get(
                    "verification"
                )
            )

        memory_id = None

        if remember:
            confidence = 0.0

            if research_result:
                confidence = (
                    research_result
                    .get(
                        "analysis",
                        {},
                    )
                    .get(
                        "confidence",
                        0.0,
                    )
                )

            elif verification_result:
                confidence = (
                    1.0
                    if verification_result.get(
                        "verified",
                        False,
                    )
                    else 0.0
                )

            memory_id = save_memory(
                content=(
                    "AI Infinity task.\n"
                    f"Objective: {objective}\n"
                    f"Task: {task_id}\n"
                    f"Research:\n"
                    f"{json_dump(research_result)}\n"
                    f"Execution:\n"
                    f"{json_dump(execution_result)}\n"
                    f"Verification:\n"
                    f"{json_dump(verification_result)}"
                ),
                memory_type=(
                    "verified"
                    if confidence > 0
                    else "unverified"
                ),
                confidence=confidence,
                provenance={
                    "task_id":
                        task_id,
                    "version":
                        VERSION,
                },
            )

        genome = create_genome(
            task_id,
            objective,
            intent,
            strategies,
            minds,
            execution_result,
            verification_result,
            research_result,
        )

        evolution = propose_evolution(
            genome
        )

        result = {
            "task_id":
                task_id,
            "status":
                "completed",
            "version":
                VERSION,
            "objective":
                objective,
            "intent":
                intent,
            "world_model":
                world_model,
            "dream_engine":
                {
                    "strategies":
                        strategies,
                },
            "counterfactual_universe":
                {
                    "scenarios":
                        counterfactuals,
                    "warning":
                        "Hypothetical only.",
                },
            "temporary_minds":
                minds,
            "intelligence_compiler":
                compiler,
            "research":
                research_result,
            "execution":
                execution_result,
            "verification":
                verification_result,
            "memory":
                {
                    "saved":
                        memory_id,
                },
            "intelligence_genome":
                genome,
            "evolution":
                evolution,
            "safety":
                {
                    "arbitrary_shell_execution":
                        False,
                    "automatic_spending":
                        False,
                    "automatic_self_modification":
                        False,
                    "authorized_actions_only":
                        True,
                    "evidence_gate":
                        True,
                },
        }

        update_task(
            task_id,
            "completed",
            result,
        )

        return result

    except Exception as exc:
        failure = {
            "failure_id":
                uid("failure"),
            "stage":
                "master_loop",
            "error":
                str(exc),
            "recovery":
                {
                    "action":
                        "diagnose_and_retry",
                    "automatic_code_change":
                        False,
                },
        }

        connection = db()

        connection.execute(
            """
            INSERT INTO failures
            (id, task_id, stage, error,
             recovery_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                failure["failure_id"],
                task_id,
                failure["stage"],
                failure["error"],
                json_dump(
                    failure["recovery"]
                ),
                now_iso(),
            ),
        )

        connection.commit()
        connection.close()

        result = {
            "task_id":
                task_id,
            "status":
                "failed",
            "version":
                VERSION,
            "objective":
                objective,
            "failure":
                failure,
        }

        update_task(
            task_id,
            "failed",
            result,
        )

        return result


# ============================================================
# API MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )

    research: bool = True
    verify: bool = True
    remember: bool = True


class ResearchRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=2,
        max_length=10000,
    )


class ExecuteRequest(BaseModel):
    action: str
    objective: str = ""


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    return {
        "status":
            "ok",
        "service":
            SERVICE,
        "version":
            VERSION,
        "research":
            True,
        "resilient_discovery":
            True,
        "web_discovery":
            True,
        "source_fetching":
            True,
        "evidence_gate":
            True,
        "provenance":
            True,
        "verification":
            True,
        "memory":
            True,
        "intelligence_genome":
            True,
        "free_first":
            True,
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/v1/status")
def status():
    connection = db()

    counts = {}

    for table in (
        "tasks",
        "memories",
        "genomes",
        "executions",
        "failures",
        "events",
        "evidence",
        "research",
        "sources",
    ):
        row = connection.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM {table}
            """
        ).fetchone()

        counts[table] = row["count"]

    connection.close()

    return {
        "service":
            SERVICE,
        "version":
            VERSION,
        "status":
            "online",
        "architecture":
            "Intent → Discover → Evidence → Reason → Verify → Learn → Evolve",
        "capabilities":
            {
                "intent_engine":
                    True,
                "world_model":
                    True,
                "dream_engine":
                    True,
                "counterfactual_engine":
                    True,
                "temporary_minds":
                    True,
                "web_discovery":
                    True,
                "multi_provider_discovery":
                    True,
                "source_fetching":
                    True,
                "evidence_fabric":
                    True,
                "evidence_gate":
                    True,
                "provenance":
                    True,
                "verification":
                    True,
                "memory":
                    True,
                "intelligence_genome":
                    True,
                "self_healing":
                    True,
                "safe_execution_registry":
                    True,
            },
        "research_providers":
            [
                "direct_url",
                "wikipedia",
                "arxiv",
                "crossref",
                "openalex",
            ],
        "safety":
            {
                "arbitrary_shell_execution":
                    False,
                "automatic_spending":
                    False,
                "automatic_self_modification":
                    False,
            },
        "database":
            counts,
    }


# ============================================================
# RUN
# ============================================================

@app.post("/v1/run")
def run_endpoint(
    request: RunRequest,
):
    return run_infinity(
        objective=request.objective,
        research=request.research,
        verify=request.verify,
        remember=request.remember,
    )


# ============================================================
# RESEARCH
# ============================================================

@app.post("/v1/research")
def research_endpoint(
    request: ResearchRequest,
):
    task_id = create_task(
        request.question
    )

    result = run_research(
        task_id,
        request.question,
    )

    update_task(
        task_id,
        "completed",
        result,
    )

    return {
        "task_id":
            task_id,
        "version":
            VERSION,
        **result,
    }


# ============================================================
# EXECUTE
# ============================================================

@app.post("/v1/execute")
def execute_endpoint(
    request: ExecuteRequest,
):
    task_id = create_task(
        request.objective
        or request.action
    )

    result = execute_registered_action(
        task_id,
        request.action,
        request.objective,
    )

    update_task(
        task_id,
        "completed",
        result,
    )

    return {
        "task_id":
            task_id,
        **result,
    }


# ============================================================
# TASK
# ============================================================

@app.get("/v1/tasks/{task_id}")
def task_endpoint(
    task_id: str,
):
    result = get_task(
        task_id
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    return result


# ============================================================
# MEMORY
# ============================================================

@app.get("/v1/memory")
def memory_endpoint(
    q: str = "",
):
    if q:
        memories = search_memory(q)

    else:
        connection = db()

        rows = connection.execute(
            """
            SELECT *
            FROM memories
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()

        connection.close()

        memories = [
            dict(row)
            for row in rows
        ]

    return {
        "count":
            len(memories),
        "memories":
            memories,
    }


# ============================================================
# GENOMES
# ============================================================

@app.get("/v1/genomes")
def genomes_endpoint():
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM genomes
        ORDER BY created_at DESC
        LIMIT 20
        """
    ).fetchall()

    connection.close()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["genome"] = json.loads(
                item.pop(
                    "genome_json"
                )
            )
        except Exception:
            item["genome"] = item.pop(
                "genome_json",
                None,
            )

        result.append(item)

    return {
        "count":
            len(result),
        "genomes":
            result,
    }


# ============================================================
# EVIDENCE
# ============================================================

@app.get("/v1/evidence")
def evidence_endpoint(
    task_id: str = "",
):
    connection = db()

    if task_id:
        rows = connection.execute(
            """
            SELECT *
            FROM evidence
            WHERE task_id = ?
            ORDER BY retrieved_at DESC
            LIMIT 100
            """,
            (task_id,),
        ).fetchall()

    else:
        rows = connection.execute(
            """
            SELECT *
            FROM evidence
            ORDER BY retrieved_at DESC
            LIMIT 100
            """
        ).fetchall()

    connection.close()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["metadata"] = json.loads(
                item.pop(
                    "metadata_json"
                )
            )
        except Exception:
            item["metadata"] = {}

        result.append(item)

    return {
        "count":
            len(result),
        "evidence":
            result,
    }


# ============================================================
# RESEARCH HISTORY
# ============================================================

@app.get("/v1/research")
def research_history():
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM research
        ORDER BY created_at DESC
        LIMIT 30
        """
    ).fetchall()

    connection.close()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["evidence"] = json.loads(
                item.pop(
                    "evidence_json"
                )
            )
        except Exception:
            item["evidence"] = []

        result.append(item)

    return {
        "count":
            len(result),
        "research":
            result,
    }


# ============================================================
# SOURCES
# ============================================================

@app.get("/v1/sources")
def sources_endpoint(
    task_id: str = "",
):
    connection = db()

    if task_id:
        rows = connection.execute(
            """
            SELECT *
            FROM sources
            WHERE task_id = ?
            ORDER BY retrieved_at DESC
            LIMIT 100
            """,
            (task_id,),
        ).fetchall()

    else:
        rows = connection.execute(
            """
            SELECT *
            FROM sources
            ORDER BY retrieved_at DESC
            LIMIT 100
            """
        ).fetchall()

    connection.close()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["metadata"] = json.loads(
                item.pop(
                    "metadata_json"
                )
            )
        except Exception:
            item["metadata"] = {}

        result.append(item)

    return {
        "count":
            len(result),
        "sources":
            result,
    }


# ============================================================
# FAILURES
# ============================================================

@app.get("/v1/failures")
def failures_endpoint():
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM failures
        ORDER BY created_at DESC
        LIMIT 30
        """
    ).fetchall()

    connection.close()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["recovery"] = json.loads(
                item.pop(
                    "recovery_json"
                )
            )
        except Exception:
            item["recovery"] = {}

        result.append(item)

    return {
        "count":
            len(result),
        "failures":
            result,
    }


# ============================================================
# AUDIT
# ============================================================

@app.get("/v1/audit")
def audit_endpoint():
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM events
        ORDER BY created_at DESC
        LIMIT 100
        """
    ).fetchall()

    connection.close()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["payload"] = json.loads(
                item.pop(
                    "payload_json"
                )
            )
        except Exception:
            item["payload"] = {}

        result.append(item)

    return {
        "count":
            len(result),
        "events":
            result,
    }


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/v1/research/diagnostics")
def research_diagnostics(
    q: str = "AI agent reliability autonomous task execution",
):
    try:
        sources, diagnostics = discover_sources(q)

        return {
            "version":
                VERSION,
            "query":
                q,
            "normalized_query":
                research_query_terms(q),
            "providers":
                diagnostics,
            "sources_found":
                len(sources),
            "sources":
                sources,
        }

    except Exception as exc:
        return {
            "version":
                VERSION,
            "status":
                "failed",
            "error":
                str(exc),
        }


# ============================================================
# ROOT UI
# ============================================================

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>

body {
    font-family: Arial, sans-serif;
    max-width: 950px;
    margin: auto;
    padding: 18px;
    background: #080d13;
    color: #f8fafc;
}

.card {
    background: #111827;
    border-radius: 16px;
    padding: 20px;
    margin-bottom: 18px;
}

textarea {
    width: 100%;
    min-height: 180px;
    box-sizing: border-box;
    padding: 14px;
    border-radius: 12px;
    border: 1px solid #334155;
    background: #020617;
    color: white;
    font-size: 15px;
}

button {
    padding: 13px 20px;
    margin-top: 12px;
    border: 0;
    border-radius: 10px;
    cursor: pointer;
    font-weight: bold;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: #020617;
    padding: 15px;
    border-radius: 12px;
    overflow-x: auto;
}

.badge {
    display: inline-block;
    padding: 6px 10px;
    border-radius: 999px;
    background: #1e293b;
    margin: 3px;
}

</style>
</head>

<body>

<div class="card">

<h1>♾️ AI Infinity</h1>

<p>
<span class="badge">TARGET-2.2</span>
<span class="badge">Multi-Source Research</span>
<span class="badge">Evidence Gate</span>
<span class="badge">Verification</span>
<span class="badge">Memory</span>
<span class="badge">Genome</span>
</p>

<p>
Intent → Discover → Evidence → Reason → Verify → Learn → Evolve
</p>

</div>

<div class="card">

<h2>Run Objective</h2>

<textarea id="objective"
placeholder="Research what AI agent systems need to become reliable autonomous task executors. Collect multiple public sources, separate evidence-supported facts from assumptions, verify the evidence, save the research to memory, and create an Intelligence Genome."></textarea>

<br>

<button onclick="runObjective()">
Run AI Infinity
</button>

</div>

<div class="card">

<h2>Result</h2>

<pre id="result">Ready.</pre>

</div>

<script>

async function runObjective() {

    const objective =
        document
        .getElementById("objective")
        .value.trim();

    if (!objective) {
        return;
    }

    document
    .getElementById("result")
    .textContent =
        "♾️ AI Infinity is working...";

    try {

        const response =
            await fetch(
                "/v1/run",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body:
                        JSON.stringify({
                            objective:
                                objective,
                            research:
                                true,
                            verify:
                                true,
                            remember:
                                true
                        })
                }
            );

        const data =
            await response.json();

        document
        .getElementById("result")
        .textContent =
            JSON.stringify(
                data,
                null,
                2
            );

    } catch (error) {

        document
        .getElementById("result")
        .textContent =
            String(error);

    }
}

</script>

</body>
</html>
"""


@app.get(
    "/",
    response_class=HTMLResponse,
)
def root():
    return HTML


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():
    init_db()

    log_event(
        None,
        "system_start",
        {
            "service":
                SERVICE,
            "version":
                VERSION,
            "architecture":
                "Intent → Discover → Evidence → Reason → Verify → Learn → Evolve",
        },
    )
