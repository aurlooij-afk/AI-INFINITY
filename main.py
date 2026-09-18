import ast
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY — TARGET-2.1
# Evidence-Gated Research Fabric
#
# Pipeline:
# Intent
#   -> Discovery
#   -> Fetch
#   -> Extract
#   -> Hash / Provenance
#   -> Evidence DB
#   -> Independent checks
#   -> Evidence gate
#   -> AI synthesis
#   -> Verification
#   -> Memory
#   -> Intelligence Genome
#
# Safety:
# - no arbitrary shell execution
# - no automatic spending
# - no automatic self-modification
# - registered actions only
# - research cannot invent evidence
# ============================================================

VERSION = "TARGET-2.1.0"
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
MAX_SOURCE_CHARS = 14000
MAX_SEARCH_RESULTS = 12

USER_AGENT = (
    "AI-Infinity/2.1 "
    "(evidence research; respectful public web retrieval)"
)

app = FastAPI(
    title=SERVICE,
    version=VERSION,
    description="AI Infinity TARGET-2.1 Evidence-Gated Intelligence Fabric",
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


def normalize_url(url: str) -> str:
    url = html.unescape(url.strip())

    if url.startswith("//"):
        url = "https:" + url

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return ""

    if not parsed.netloc:
        return ""

    # Remove common tracking parameters.
    query = parse_qs(parsed.query)

    blocked = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
    }

    clean = {
        k: v
        for k, v in query.items()
        if k.lower() not in blocked
    }

    query_string = "&".join(
        f"{quote_plus(k)}={quote_plus(v[0])}"
        for k, v in sorted(clean.items())
    )

    result = (
        f"{parsed.scheme.lower()}://"
        f"{parsed.netloc.lower()}"
        f"{parsed.path or '/'}"
    )

    if query_string:
        result += "?" + query_string

    return result


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def is_probably_html(response: requests.Response) -> bool:
    content_type = response.headers.get(
        "content-type",
        "",
    ).lower()

    if "text/html" in content_type:
        return True

    if "application/xhtml" in content_type:
        return True

    return False


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
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
# LOGGING
# ============================================================

def log_event(
    task_id: Optional[str],
    event_type: str,
    payload: Any,
) -> None:

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
) -> None:

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
) -> None:

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


def get_task(task_id: str) -> Optional[Dict[str, Any]]:

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
) -> str:

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
) -> List[Dict[str, Any]]:

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
# INTENT
# ============================================================

def classify_intent(
    objective: str,
) -> Dict[str, Any]:

    text = objective.lower()

    domains = []

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

    for domain, terms in keyword_map.items():
        if any(term in text for term in terms):
            domains.append(domain)

    if not domains:
        domains.append("general")

    research_intent = any(
        term in text
        for term in [
            "research",
            "investigate",
            "sources",
            "evidence",
            "study",
            "literature",
        ]
    )

    return {
        "type": "research" if research_intent else "goal",
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
) -> Dict[str, Any]:

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
# DREAM / COUNTERFACTUALS
# ============================================================

def dream_strategies() -> List[Dict[str, str]]:

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


def build_counterfactuals(
    strategies: List[Dict[str, str]],
) -> List[Dict[str, Any]]:

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

def create_temporary_minds(
    domains: List[str],
) -> List[Dict[str, Any]]:

    roles = [
        ("researcher", "research"),
        ("analyst", "analysis"),
        ("critic", "verification"),
        ("engineer", "software"),
    ]

    minds = []

    for name, role in roles:
        if role in domains or role in (
            "analysis",
            "verification",
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
# WEB HTML EXTRACTION
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

        text = re.sub(
            r"\s+",
            " ",
            data,
        ).strip()

        if not text:
            return

        if self.in_title:
            self.title_parts.append(text)

        self.parts.append(text)

    def text(self) -> str:

        return " ".join(self.parts)

    def title(self) -> str:

        return " ".join(
            self.title_parts
        ).strip()


class LinkExtractor(HTMLParser):

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):

        if tag.lower() != "a":
            return

        data = dict(attrs)
        href = data.get("href")

        if href:
            self.links.append(href)


def extract_html(
    raw: str,
) -> Dict[str, Any]:

    parser = TextExtractor()

    try:
        parser.feed(raw)
    except Exception:
        pass

    text = parser.text()

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return {
        "title": parser.title()[:500],
        "text": text,
    }


# ============================================================
# SEARCH DISCOVERY
# ============================================================

def discover_duckduckgo(
    query: str,
) -> List[Dict[str, Any]]:

    url = (
        "https://html.duckduckgo.com/html/"
        "?q="
        + quote_plus(query)
    )

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        parser = LinkExtractor()

        parser.feed(response.text)

        results = []

        for href in parser.links:

            href = html.unescape(href)

            if "uddg=" in href:
                parsed = urlparse(href)
                params = parse_qs(
                    parsed.query
                )

                if params.get("uddg"):
                    href = params["uddg"][0]

            normalized = normalize_url(href)

            if not normalized:
                continue

            domain = domain_of(normalized)

            if domain in {
                "duckduckgo.com",
                "html.duckduckgo.com",
            }:
                continue

            results.append(
                {
                    "url": normalized,
                    "domain": domain,
                    "method": "duckduckgo",
                }
            )

            if len(results) >= MAX_SEARCH_RESULTS:
                break

        return results

    except Exception:
        return []


def discover_wikipedia(
    query: str,
) -> List[Dict[str, Any]]:

    url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query"
        "&list=search"
        "&format=json"
        "&utf8=1"
        "&srlimit=5"
        "&srsearch="
        + quote_plus(query)
    )

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        data = response.json()

        results = []

        for item in data.get(
            "query",
            {}
        ).get(
            "search",
            []
        ):

            title = item.get(
                "title",
                "",
            )

            if not title:
                continue

            page_url = (
                "https://en.wikipedia.org/wiki/"
                + quote_plus(
                    title.replace(
                        " ",
                        "_",
                    )
                )
            )

            results.append(
                {
                    "url": normalize_url(
                        page_url
                    ),
                    "domain":
                        "en.wikipedia.org",
                    "title": title,
                    "method":
                        "wikipedia_api",
                }
            )

        return results

    except Exception:
        return []


def discover_arxiv(
    query: str,
) -> List[Dict[str, Any]]:

    url = (
        "https://export.arxiv.org/api/query"
        "?search_query=all:"
        + quote_plus(query)
        + "&start=0&max_results=5"
    )

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        parser = LinkExtractor()

        parser.feed(response.text)

        results = []

        for href in parser.links:

            normalized = normalize_url(
                href
            )

            if (
                normalized
                and "arxiv.org" in domain_of(
                    normalized
                )
            ):
                results.append(
                    {
                        "url": normalized,
                        "domain":
                            domain_of(normalized),
                        "method":
                            "arxiv_api",
                    }
                )

        return results[:5]

    except Exception:
        return []


def discover_sources(
    query: str,
) -> List[Dict[str, Any]]:

    discovered = []

    # General web
    discovered.extend(
        discover_duckduckgo(query)
    )

    # Structured public knowledge
    discovered.extend(
        discover_wikipedia(query)
    )

    # Scholarly source
    discovered.extend(
        discover_arxiv(query)
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

    return list(unique.values())[
        :MAX_RESEARCH_SOURCES * 2
    ]


# ============================================================
# SOURCE FETCHING
# ============================================================

def fetch_source(
    task_id: str,
    source: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    url = normalize_url(
        source.get("url", "")
    )

    if not url:
        return None

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept":
                    "text/html,text/plain,"
                    "application/xhtml+xml",
            },
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        final_url = normalize_url(
            response.url
        ) or url

        status = response.status_code

        raw = response.text[:500000]

        if status >= 400:
            return None

        if is_probably_html(response):
            extracted = extract_html(raw)
            title = (
                extracted["title"]
                or source.get("title")
                or final_url
            )
            text = extracted["text"]
        else:
            title = (
                source.get("title")
                or final_url
            )
            text = raw

        text = text[:MAX_SOURCE_CHARS]

        if len(text.strip()) < 120:
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
                response.headers.get(
                    "content-type",
                    "",
                ),
            "content_length":
                len(text),
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
            "metadata": metadata,
        }

    except Exception as exc:

        log_event(
            task_id,
            "source_fetch_failed",
            {
                "url": url,
                "error": str(exc),
            },
        )

        return None


# ============================================================
# EVIDENCE STORAGE
# ============================================================

def save_evidence(
    task_id: str,
    source: Dict[str, Any],
    excerpt: str,
    claim: str = "",
) -> str:

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


def evidence_for_task(
    task_id: str,
) -> List[Dict[str, Any]]:

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

    results = []

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

        results.append(item)

    return results


# ============================================================
# EVIDENCE GATE
# ============================================================

def evidence_gate(
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:

    valid = []

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

    domains = sorted(
        {
            domain_of(
                item["source_url"]
            )
            for item in valid
        }
    )

    if not valid:

        return {
            "passed": False,
            "status": "insufficient_evidence",
            "reason":
                "No valid collected evidence exists.",
            "evidence_count": 0,
            "independent_domains": 0,
        }

    return {
        "passed": True,
        "status": "evidence_available",
        "reason":
            "Collected evidence passed structural validation.",
        "evidence_count": len(valid),
        "independent_domains": len(domains),
        "domains": domains,
    }


# ============================================================
# LLM
# ============================================================

def call_hf(
    prompt: str,
    model: Optional[str] = None,
) -> Optional[str]:

    if not HF_TOKEN:
        return None

    selected_model = (
        model or HF_MODEL
    )

    payload = {
        "model": selected_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the reasoning layer of AI Infinity. "
                    "Never invent sources, URLs, citations, "
                    "experiments or evidence. "
                    "Only describe a claim as evidence-supported "
                    "when supplied evidence directly supports it."
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

        message = choices[0].get(
            "message",
            {},
        )

        content = message.get(
            "content"
        )

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
) -> str:

    packet = []

    for index, item in enumerate(
        evidence,
        start=1,
    ):

        packet.append(
            f"""
SOURCE {index}
Evidence ID: {item['id']}
Title: {item['source_title']}
URL: {item['source_url']}
Domain: {domain_of(item['source_url'])}
Hash: {item['content_hash']}

EXCERPT:
{item['excerpt']}
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
) -> Dict[str, Any]:

    gate = evidence_gate(
        evidence
    )

    # CRITICAL TARGET-2.1 RULE:
    # Never manufacture an evidence-backed answer.
    if not gate["passed"]:

        return {
            "status":
                "insufficient_evidence",
            "summary":
                "No evidence-supported synthesis was produced "
                "because no valid collected sources passed "
                "the evidence gate.",
            "evidence_supported_facts": [],
            "assumptions": [
                "Further source collection is required."
            ],
            "uncertainty": 1.0,
            "confidence": 0.0,
            "verification": {
                "verified": False,
                "reason":
                    "Evidence gate failed.",
            },
        }

    packet = build_evidence_packet(
        question,
        evidence,
    )

    prompt = f"""
Research question:
{question}

Use ONLY the collected evidence below.

{packet}

Return a structured research report with:

1. Summary
2. Evidence-supported facts
3. Assumptions / interpretations
4. Contradictions or uncertainty
5. Evidence IDs supporting each important fact
6. Confidence from 0 to 1

Rules:
- Never invent a source.
- Never invent an Evidence ID.
- Do not treat an assumption as a fact.
- If evidence is insufficient for a claim, say so.
- Prefer agreement across independent sources.
"""

    answer = call_hf(prompt)

    if not answer:

        # Deterministic fallback.
        return {
            "status":
                "evidence_collected_ai_unavailable",
            "summary":
                "Evidence was successfully collected, "
                "but the AI synthesis provider was unavailable.",
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
                    "Evidence was collected and structurally verified.",
            },
        }

    return {
        "status": "completed",
        "summary": answer,
        "evidence_supported_facts":
            "See source-mapped synthesis above.",
        "assumptions":
            "See explicit assumptions in synthesis.",
        "uncertainty":
            max(
                0.0,
                1.0 -
                min(
                    1.0,
                    len(evidence) / 5.0,
                ),
            ),
        "confidence":
            min(
                0.95,
                0.55 +
                min(
                    0.35,
                    len(evidence) * 0.06,
                ),
            ),
        "verification": {
            "verified": True,
            "reason":
                "Synthesis was generated only after "
                "the evidence gate passed.",
        },
    }


# ============================================================
# RESEARCH ENGINE
# ============================================================

def run_research(
    task_id: str,
    question: str,
) -> Dict[str, Any]:

    research_id = uid("research")

    log_event(
        task_id,
        "research_started",
        {
            "question": question,
            "version": VERSION,
        },
    )

    # --------------------------------------------------------
    # STEP 1 — DISCOVERY
    # --------------------------------------------------------

    discovered = discover_sources(
        question
    )

    log_event(
        task_id,
        "sources_discovered",
        {
            "count": len(discovered),
            "sources": discovered,
        },
    )

    # --------------------------------------------------------
    # STEP 2 — FETCH
    # --------------------------------------------------------

    fetched = []

    for source in discovered:

        if len(fetched) >= MAX_RESEARCH_SOURCES:
            break

        result = fetch_source(
            task_id,
            source,
        )

        if result:
            fetched.append(result)

    # --------------------------------------------------------
    # STEP 3 — EVIDENCE EXTRACTION
    # --------------------------------------------------------

    evidence_ids = []

    for source in fetched:

        text = source["text"]

        # Take representative evidence from the
        # beginning and middle of the retrieved document.
        excerpt = text[:4500]

        evidence_id = save_evidence(
            task_id,
            source,
            excerpt,
        )

        evidence_ids.append(
            evidence_id
        )

    evidence = evidence_for_task(
        task_id
    )

    # --------------------------------------------------------
    # STEP 4 — EVIDENCE GATE
    # --------------------------------------------------------

    gate = evidence_gate(
        evidence
    )

    log_event(
        task_id,
        "evidence_gate",
        gate,
    )

    # --------------------------------------------------------
    # STEP 5 — SYNTHESIS
    # --------------------------------------------------------

    synthesis = synthesize_research(
        question,
        evidence,
    )

    # --------------------------------------------------------
    # STEP 6 — PERSIST
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # STEP 7 — MEMORY
    # --------------------------------------------------------

    memory_id = save_memory(
        content=(
            f"AI Infinity research result.\n"
            f"Question: {question}\n"
            f"Research ID: {research_id}\n"
            f"Sources discovered: {len(discovered)}\n"
            f"Sources fetched: {len(fetched)}\n"
            f"Evidence records: {len(evidence)}\n"
            f"Evidence gate: {gate['status']}\n"
            f"Summary:\n"
            f"{synthesis.get('summary', '')}"
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
            "task_id": task_id,
            "research_id": research_id,
            "sources": [
                item["source_url"]
                for item in evidence
            ],
            "evidence_gate": gate,
        },
    )

    return {
        "research_id": research_id,
        "question": question,
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
        "evidence_gate": gate,
        "evidence": evidence,
        "analysis": synthesis,
        "memory_id": memory_id,
        "verification":
            synthesis.get(
                "verification",
                {
                    "verified": False
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


def safe_calculate(
    expression: str,
) -> Any:

    if len(expression) > 200:
        raise ValueError(
            "Expression too long"
        )

    tree = ast.parse(
        expression,
        mode="eval",
    )

    def evaluate(
        node: ast.AST,
    ) -> Any:

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
            f"Expression node not allowed: "
            f"{type(node).__name__}"
        )

    return evaluate(tree)


def create_calculator_artifact(
    task_id: str,
) -> Dict[str, Any]:

    directory = (
        ARTIFACTS / task_id
    )

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = (
        directory /
        "simple_calculator.py"
    )

    source = r'''"""
AI Infinity safe calculator.

Supported:
+ - * / %
parentheses
"""

import ast
import math


_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}


def evaluate(expression: str):

    if len(expression) > 200:
        raise ValueError("Expression too long")

    tree = ast.parse(expression, mode="eval")

    def walk(node):

        if isinstance(node, ast.Expression):
            return walk(node.body)

        if isinstance(node, ast.Constant):

            if isinstance(node.value, bool):
                raise ValueError(
                    "Boolean values are not allowed"
                )

            if isinstance(node.value, (int, float)):

                if not math.isfinite(float(node.value)):
                    raise ValueError(
                        "Non-finite number"
                    )

                return node.value

            raise ValueError(
                "Only numeric constants are allowed"
            )

        if isinstance(node, ast.BinOp):

            operation = _ALLOWED_BINOPS.get(
                type(node.op)
            )

            if operation is None:
                raise ValueError(
                    "Operator not allowed"
                )

            return operation(
                walk(node.left),
                walk(node.right),
            )

        if isinstance(node, ast.UnaryOp):

            operation = _ALLOWED_UNARYOPS.get(
                type(node.op)
            )

            if operation is None:
                raise ValueError(
                    "Unary operator not allowed"
                )

            return operation(
                walk(node.operand)
            )

        raise ValueError(
            f"Expression node not allowed: "
            f"{type(node).__name__}"
        )

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
        "status": "completed",
        "filename": path.name,
        "path": str(path),
        "bytes": len(data),
        "sha256":
            hashlib.sha256(
                data
            ).hexdigest(),
    }


def verify_calculator(
    artifact: Dict[str, Any],
) -> Dict[str, Any]:

    path = Path(
        artifact["path"]
    )

    if not path.exists():
        return {
            "verified": False,
            "status": "failed",
            "reason":
                "File does not exist",
        }

    source = path.read_text(
        encoding="utf-8"
    )

    try:
        compile(
            source,
            str(path),
            "exec",
        )
        syntax_ok = True
        syntax_error = None

    except SyntaxError as exc:
        syntax_ok = False
        syntax_error = str(exc)

    if not syntax_ok:

        return {
            "verified": False,
            "status": "failed",
            "syntax_ok": False,
            "syntax_error": syntax_error,
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

        functional_ok = (
            process.returncode == 0
            and output == "14"
        )

        return {
            "verified":
                syntax_ok
                and functional_ok,
            "status":
                "verified"
                if functional_ok
                else "failed",
            "syntax_ok": syntax_ok,
            "functional_test": {
                "expression":
                    "2+3*4",
                "expected": 14,
                "actual":
                    output,
                "returncode":
                    process.returncode,
                "passed":
                    functional_ok,
            },
        }

    except Exception as exc:

        return {
            "verified": False,
            "status": "failed",
            "syntax_ok": syntax_ok,
            "error": str(exc),
        }


# ============================================================
# REGISTERED EXECUTION
# ============================================================

def execute_registered_action(
    task_id: str,
    action: str,
    objective: str = "",
) -> Dict[str, Any]:

    allowed = {
        "create_calculator",
    }

    if action not in allowed:

        result = {
            "status": "rejected",
            "reason":
                "Action is not registered.",
            "allowed_actions":
                sorted(allowed),
        }

        log_execution(
            task_id,
            action,
            "rejected",
            result,
        )

        return result

    if action == "create_calculator":

        artifact = create_calculator_artifact(
            task_id
        )

        verification = verify_calculator(
            artifact
        )

        result = {
            "status":
                "completed"
                if verification["verified"]
                else "failed",
            "action": action,
            "artifact": artifact,
            "verification":
                verification,
        }

        log_execution(
            task_id,
            action,
            result["status"],
            result,
        )

        return result

    raise ValueError(
        "Unexpected registered action"
    )


# ============================================================
# FAILURE / SELF-HEALING
# ============================================================

def diagnose_failure(
    task_id: str,
    stage: str,
    error: str,
) -> Dict[str, Any]:

    recovery = {
        "diagnosis": error,
        "actions": [
            "inspect_failure",
            "retry_safe_stage",
            "collect_more_evidence",
            "use_deterministic_fallback",
            "preserve_failed_state",
        ],
        "automatic_deployment": False,
        "automatic_self_modification": False,
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
            uid("failure"),
            task_id,
            stage,
            error,
            json_dump(recovery),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()

    return recovery


# ============================================================
# INTELLIGENCE COMPILER
# ============================================================

def compile_intelligence(
    objective: str,
    intent: Dict[str, Any],
    world_model: Dict[str, Any],
    strategies: List[Dict[str, str]],
    counterfactuals: List[Dict[str, Any]],
    minds: List[Dict[str, Any]],
) -> Dict[str, Any]:

    return {
        "objective": objective,
        "stages": [
            "understand_intent",
            "construct_world_model",
            "discover_resources",
            "generate_strategies",
            "collect_evidence",
            "reason",
            "execute_authorized_action",
            "verify",
            "learn",
            "create_genome",
        ],
        "intent": intent,
        "world_model": world_model,
        "strategies": strategies,
        "counterfactuals":
            counterfactuals,
        "temporary_minds": minds,
    }


# ============================================================
# GENOME
# ============================================================

def create_genome(
    task_id: str,
    objective: str,
    intent: Dict[str, Any],
    strategies: List[Dict[str, str]],
    minds: List[Dict[str, Any]],
    execution: Any,
    verification: Any,
    research: Any,
) -> Dict[str, Any]:

    genome_id = uid("genome")

    verified = bool(
        verification
        and verification.get(
            "verified",
            False,
        )
    )

    research_verified = bool(
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

    if verified:
        fitness += 0.5

    if research_verified:
        fitness += 0.5

    genome = {
        "id": genome_id,
        "task_id": task_id,
        "objective": objective,
        "intent": intent,
        "strategies": strategies,
        "temporary_minds": minds,
        "execution": execution,
        "verification": verification,
        "research": research,
        "fitness": fitness,
        "reusable":
            fitness >= 0.5,
        "safety": {
            "automatic_deployment": False,
            "automatic_self_modification": False,
        },
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


def propose_evolution(
    genome: Dict[str, Any],
) -> Dict[str, Any]:

    return {
        "status":
            "proposal_only",
        "automatic_deployment":
            False,
        "proposals": [
            "Improve source discovery diversity.",
            "Add stronger claim-to-evidence mapping.",
            "Add contradiction detection.",
            "Add persistent provenance.",
            "Add independent verification.",
            "Benchmark research reliability.",
        ],
    }


# ============================================================
# MASTER LOOP
# ============================================================

def run_infinity(
    objective: str,
    research: bool = True,
    verify: bool = True,
    remember: bool = True,
) -> Dict[str, Any]:

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

        compiler = compile_intelligence(
            objective,
            intent,
            world_model,
            strategies,
            counterfactuals,
            minds,
        )

        log_event(
            task_id,
            "intelligence_compiled",
            compiler,
        )

        research_result = None

        should_research = (
            research
            and (
                intent["type"]
                == "research"
                or "research"
                in intent["domains"]
                or "investigate"
                in objective.lower()
                or "sources"
                in objective.lower()
                or "evidence"
                in objective.lower()
                or "analyze"
                in objective.lower()
            )
        )

        if should_research:

            research_result = run_research(
                task_id,
                objective,
            )

        execution_result = None

        calculator_keywords = [
            "simple_calculator.py",
            "calculator",
            "2+3*4",
            "safe calculator",
        ]

        if any(
            keyword in objective.lower()
            for keyword in calculator_keywords
        ):

            execution_result = (
                execute_registered_action(
                    task_id,
                    "create_calculator",
                    objective,
                )
            )

        verification_result = None

        if execution_result:

            verification_result = (
                execution_result.get(
                    "verification"
                )
            )

        elif research_result:

            verification_result = (
                research_result.get(
                    "verification"
                )
            )

        memory_id = None

        if remember:

            if research_result:

                memory_confidence = (
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

                memory_type = (
                    "verified_research"
                    if research_result
                    .get(
                        "evidence_gate",
                        {},
                    )
                    .get(
                        "passed",
                        False,
                    )
                    else
                    "research_insufficient_evidence"
                )

            else:

                memory_confidence = (
                    1.0
                    if verification_result
                    and verification_result.get(
                        "verified",
                        False,
                    )
                    else 0.0
                )

                memory_type = (
                    "verified"
                    if memory_confidence > 0
                    else "unverified"
                )

            memory_id = save_memory(
                content=(
                    f"AI Infinity task.\n"
                    f"Objective: {objective}\n"
                    f"Task: {task_id}\n"
                    f"Research:\n"
                    f"{json_dump(research_result)}\n"
                    f"Verification:\n"
                    f"{json_dump(verification_result)}"
                ),
                memory_type=memory_type,
                confidence=memory_confidence,
                provenance={
                    "task_id": task_id,
                    "version": VERSION,
                },
            )

        genome = create_genome(
            task_id=task_id,
            objective=objective,
            intent=intent,
            strategies=strategies,
            minds=minds,
            execution=execution_result,
            verification=verification_result,
            research=research_result,
        )

        evolution = propose_evolution(
            genome
        )

        final_result = {
            "task_id": task_id,
            "status": "completed
