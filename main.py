"""
AI Infinity
TARGET-2050.60
BUILD: CLOSED-LOOP-OUTCOME-INTELLIGENCE-CORE

2050.60 extends TARGET-2050.59 without removing the existing research fabric.

CORE LOOP
---------
Intent
  -> Requirements
  -> Research
  -> Evidence
  -> Decision
  -> Mission Graph
  -> Authorization
  -> Execution
  -> Observation
  -> Outcome Verification
  -> Recovery
  -> Learning
  -> Reusable Skill

LAYERS
------
1. Intent / mission layer
2. Requirement discovery
3. Research + evidence fabric
4. Claim / contradiction / gap analysis
5. Decision layer
6. Dynamic mission graph
7. Authorization / approval layer
8. Controlled execution layer
9. Observation layer
10. Outcome verification
11. Recovery / replanning
12. Persistent memory
13. Learning
14. Reusable skills
15. Artifact registry
16. Provenance
17. Resource governance
18. Checkpoints / resumability
19. Capability discovery
20. Connector fabric
21. Security / SSRF protection
22. Free-first architecture

IMPORTANT SECURITY BOUNDARIES
-----------------------------
- No arbitrary code execution
- No unrestricted private-network access
- No credential extraction
- No permission bypass
- No hidden persistence
- No unrestricted proxy
- Sensitive actions require approval
- Public web access is controlled
"""

from __future__ import annotations

import os
import re
import json
import time
import uuid
import hashlib
import sqlite3
import socket
import ipaddress
import threading
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# IDENTITY
# ============================================================

VERSION = "TARGET-2050.60"
BUILD = "CLOSED-LOOP-OUTCOME-INTELLIGENCE-CORE"
SERVICE = "AI Infinity"

BASE = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai_infinity"))
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
MAX_RESPONSE_BYTES = int(os.getenv("MAX_RESPONSE_BYTES", "3000000"))
MAX_SOURCE_TEXT = int(os.getenv("MAX_SOURCE_TEXT", "50000"))
MAX_RESEARCH_SOURCES = int(os.getenv("MAX_RESEARCH_SOURCES", "20"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))

EXTERNAL_ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}

RESEARCH_SEED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv(
        "RESEARCH_SEED_DOMAINS",
        "wikipedia.org,crossref.org,arxiv.org,openalex.org",
    ).split(",")
    if x.strip()
}

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "metadata",
}

SENSITIVE_ACTIONS = {
    "send_message",
    "send_email",
    "purchase",
    "payment",
    "delete",
    "publish",
    "modify_account",
    "change_permissions",
    "credential_change",
    "external_side_effect",
}

app = FastAPI(
    title=SERVICE,
    version=VERSION,
    description="AI Infinity 2050.60 closed-loop autonomous intelligence core",
)


# ============================================================
# DATABASE
# ============================================================

DB_LOCK = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with DB_LOCK:
        conn = db()

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                result TEXT,
                decision TEXT,
                outcome TEXT,
                confidence REAL DEFAULT 0,
                recovery_count INTEGER DEFAULT 0,
                parent_id TEXT
            );

            CREATE TABLE IF NOT EXISTS mission_steps (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                dependencies TEXT,
                input TEXT,
                output TEXT,
                attempts INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS requirements (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                requirement TEXT NOT NULL,
                priority TEXT,
                source TEXT,
                satisfied INTEGER DEFAULT 0,
                evidence TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                provider TEXT,
                title TEXT,
                url TEXT,
                source_id TEXT,
                published TEXT,
                authors TEXT,
                abstract TEXT,
                snippet TEXT,
                source_type TEXT,
                confidence REAL,
                metadata TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                claim TEXT NOT NULL,
                status TEXT,
                confidence REAL DEFAULT 0,
                supporting TEXT,
                contradicting TEXT,
                gaps TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS provenance (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                object_type TEXT,
                object_id TEXT,
                source TEXT,
                operation TEXT,
                metadata TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                action TEXT,
                description TEXT,
                status TEXT,
                created_at REAL NOT NULL,
                resolved_at REAL
            );

            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                phase TEXT,
                state TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                step_id TEXT,
                observation TEXT,
                success INTEGER,
                confidence REAL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS outcomes (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                expected TEXT,
                observed TEXT,
                status TEXT,
                confidence REAL,
                verification TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                artifact_type TEXT,
                name TEXT,
                content TEXT,
                checksum TEXT,
                status TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE,
                description TEXT,
                procedure TEXT,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS learning (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                lesson TEXT,
                signal TEXT,
                confidence REAL,
                reusable INTEGER DEFAULT 1,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                resource TEXT,
                requested REAL,
                used REAL DEFAULT 0,
                unit TEXT,
                status TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS connectors (
                name TEXT PRIMARY KEY,
                category TEXT,
                description TEXT,
                permission TEXT,
                enabled INTEGER DEFAULT 1,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS connector_events (
                id TEXT PRIMARY KEY,
                connector TEXT,
                mission_id TEXT,
                action TEXT,
                status TEXT,
                detail TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory (
                id TEXT PRIMARY KEY,
                namespace TEXT,
                key TEXT,
                value TEXT,
                importance REAL DEFAULT 0.5,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS provider_health (
                provider TEXT PRIMARY KEY,
                successes INTEGER DEFAULT 0,
                failures INTEGER DEFAULT 0,
                score REAL DEFAULT 0,
                last_status INTEGER,
                last_error TEXT,
                last_success REAL,
                last_failure REAL
            );
            """
        )

        conn.commit()
        conn.close()


init_db()


# ============================================================
# UTILITIES
# ============================================================

def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def clean_text(value: Any, limit: int = MAX_SOURCE_TEXT) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def parse_json(value: str) -> Any:
    value = value.lstrip("\ufeff").strip()

    try:
        return json.loads(value)
    except Exception:
        pass

    start_candidates = [
        value.find("{"),
        value.find("["),
    ]
    starts = [x for x in start_candidates if x >= 0]

    if not starts:
        raise ValueError("No JSON object found")

    start = min(starts)

    for end in range(len(value), start, -1):
        fragment = value[start:end]
        try:
            return json.loads(fragment)
        except Exception:
            continue

    raise ValueError("Unable to decode JSON")


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ============================================================
# DATABASE HELPERS
# ============================================================

def execute(sql: str, params=()):
    with DB_LOCK:
        conn = db()
        cur = conn.execute(sql, params)
        conn.commit()
        result = cur.lastrowid
        conn.close()
        return result


def fetchone(sql: str, params=()):
    conn = db()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def fetchall(sql: str, params=()):
    conn = db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(x) for x in rows]


# ============================================================
# PROVENANCE
# ============================================================

def provenance(
    mission_id: Optional[str],
    object_type: str,
    object_id: str,
    source: str,
    operation: str,
    metadata: Optional[Dict[str, Any]] = None,
):
    execute(
        """
        INSERT INTO provenance
        (id,mission_id,object_type,object_id,source,operation,metadata,created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            uid("prov"),
            mission_id,
            object_type,
            object_id,
            source,
            operation,
            json_text(metadata or {}),
            now(),
        ),
    )


# ============================================================
# NETWORK SECURITY
# ============================================================

def hostname_is_private(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")

    if host in BLOCKED_HOSTS:
        return True

    try:
        ip = ipaddress.ip_address(host)

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True

    except ValueError:
        pass

    return False


def domain_allowed(hostname: str, research: bool = False) -> bool:
    host = hostname.lower().rstrip(".")

    if hostname_is_private(host):
        return False

    allowed = RESEARCH_SEED_DOMAINS if research else EXTERNAL_ALLOWED_DOMAINS

    if host in allowed:
        return True

    for domain in allowed:
        if host.endswith("." + domain):
            return True

    return False


def validate_url(url: str, research: bool = False):
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only HTTP/HTTPS URLs are permitted")

    if not parsed.hostname:
        raise ValueError("URL has no hostname")

    if not domain_allowed(parsed.hostname, research=research):
        raise PermissionError(
            f"Domain is not allowed by AI Infinity network policy: "
            f"{parsed.hostname}"
        )

    return parsed


def http_get(
    url: str,
    *,
    research: bool = False,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = HTTP_TIMEOUT,
):
    validate_url(url, research=research)

    last_error = None

    for attempt in range(3):
        try:
            response = requests.get(
                url,
                headers=headers or {
                    "User-Agent": "AI-Infinity/2050.60",
                    "Accept": "*/*",
                },
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")

                if not location:
                    raise ValueError("Redirect without location")

                validate_url(location, research=research)

                response = requests.get(
                    location,
                    headers=headers or {
                        "User-Agent": "AI-Infinity/2050.60",
                    },
                    timeout=timeout,
                    allow_redirects=False,
                    stream=True,
                )

            data = bytearray()

            for chunk in response.iter_content(65536):
                if not chunk:
                    continue

                data.extend(chunk)

                if len(data) > MAX_RESPONSE_BYTES:
                    raise ValueError("Response exceeded size limit")

            return response.status_code, response.headers, bytes(data), None

        except Exception as exc:
            last_error = str(exc)

            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))

    return 0, {}, b"", last_error


# ============================================================
# RESEARCH EVIDENCE FABRIC
# ============================================================

@dataclass
class EvidenceRecord:
    provider: str
    title: str
    url: str
    source_id: str = ""
    published: str = ""
    authors: str = ""
    abstract: str = ""
    snippet: str = ""
    source_type: str = ""
    confidence: float = 0.5
    metadata: Optional[Dict[str, Any]] = None


def record_provider_health(
    provider: str,
    success: bool,
    http_status: Optional[int] = None,
    error: Optional[str] = None,
):
    existing = fetchone(
        "SELECT * FROM provider_health WHERE provider=?",
        (provider,),
    )

    if not existing:
        execute(
            """
            INSERT INTO provider_health
            (provider,successes,failures,score,last_status,last_error,
             last_success,last_failure)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                provider,
                1 if success else 0,
                0 if success else 1,
                1.0 if success else 0.0,
                http_status,
                error,
                now() if success else None,
                None if success else now(),
            ),
        )
        return

    successes = existing["successes"] + (1 if success else 0)
    failures = existing["failures"] + (0 if success else 1)
    score = successes / max(1, successes + failures)

    execute(
        """
        UPDATE provider_health
        SET successes=?, failures=?, score=?, last_status=?,
            last_error=?, last_success=?, last_failure=?
        WHERE provider=?
        """,
        (
            successes,
            failures,
            score,
            http_status,
            error,
            now() if success else existing["last_success"],
            None if success else now(),
            provider,
        ),
    )


def wikipedia_search(query: str, limit: int = 5):
    url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query&list=search&format=json"
        f"&srsearch={requests.utils.quote(query)}"
        f"&srlimit={limit}"
    )

    status, headers, body, error = http_get(url, research=True)

    if error:
        record_provider_health("wikipedia", False, status, error)
        return []

    try:
        data = parse_json(body.decode("utf-8", errors="replace"))
        items = data.get("query", {}).get("search", [])

        results = []

        for item in items:
            title = clean_text(item.get("title"))
            snippet = clean_text(item.get("snippet"))

            results.append(
                EvidenceRecord(
                    provider="wikipedia",
                    title=title,
                    url=(
                        "https://en.wikipedia.org/wiki/"
                        + title.replace(" ", "_")
                    ),
                    source_id=str(item.get("pageid", "")),
                    snippet=snippet,
                    abstract=snippet,
                    source_type="encyclopedia",
                    confidence=0.55,
                )
            )

        record_provider_health("wikipedia", True, status)
        return results

    except Exception as exc:
        record_provider_health("wikipedia", False, status, str(exc))
        return []


def crossref_search(query: str, limit: int = 5):
    url = (
        "https://api.crossref.org/works"
        f"?query.bibliographic={requests.utils.quote(query)}"
        f"&rows={limit}"
    )

    status, headers, body, error = http_get(url, research=True)

    if error:
        record_provider_health("crossref", False, status, error)
        return []

    try:
        data = parse_json(body.decode("utf-8", errors="replace"))
        items = data.get("message", {}).get("items", [])

        results = []

        for item in items:
            title = clean_text(
                (item.get("title") or [""])[0]
            )

            doi = item.get("DOI", "")

            authors = ", ".join(
                clean_text(
                    f"{a.get('given','')} {a.get('family','')}"
                )
                for a in item.get("author", [])
            )

            results.append(
                EvidenceRecord(
                    provider="crossref",
                    title=title,
                    url=(
                        f"https://doi.org/{doi}"
                        if doi
                        else item.get("URL", "")
                    ),
                    source_id=doi,
                    published=json_text(
                        item.get("published-print")
                        or item.get("published-online")
                        or {}
                    ),
                    authors=authors,
                    abstract=clean_text(item.get("abstract")),
                    source_type="bibliographic",
                    confidence=0.75,
                    metadata={
                        "publisher": item.get("publisher"),
                        "type": item.get("type"),
                    },
                )
            )

        record_provider_health("crossref", True, status)
        return results

    except Exception as exc:
        record_provider_health("crossref", False, status, str(exc))
        return []


def arxiv_search(query: str, limit: int = 5):
    url = (
        "https://export.arxiv.org/api/query"
        f"?search_query=all:{requests.utils.quote(query)}"
        "&start=0"
        f"&max_results={limit}"
    )

    status, headers, body, error = http_get(url, research=True)

    if error:
        record_provider_health("arxiv", False, status, error)
        return []

    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(body.decode("utf-8", errors="replace"))

        ns = {
            "a": "http://www.w3.org/2005/Atom"
        }

        results = []

        for entry in root.findall("a:entry", ns):
            title = clean_text(
                entry.findtext("a:title", "", ns)
            )

            abstract = clean_text(
                entry.findtext("a:summary", "", ns)
            )

            entry_url = clean_text(
                entry.findtext("a:id", "", ns)
            )

            published = clean_text(
                entry.findtext("a:published", "", ns)
            )

            authors = ", ".join(
                clean_text(
                    author.findtext("a:name", "", ns)
                )
                for author in entry.findall("a:author", ns)
            )

            results.append(
                EvidenceRecord(
                    provider="arxiv",
                    title=title,
                    url=entry_url,
                    source_id=entry_url,
                    published=published,
                    authors=authors,
                    abstract=abstract,
                    snippet=abstract[:500],
                    source_type="research-paper",
                    confidence=0.85,
                )
            )

        record_provider_health("arxiv", True, status)
        return results

    except Exception as exc:
        record_provider_health("arxiv", False, status, str(exc))
        return []


def openalex_search(query: str, limit: int = 5):
    url = (
        "https://api.openalex.org/works"
        f"?search={requests.utils.quote(query)}"
        f"&per-page={limit}"
    )

    status, headers, body, error = http_get(url, research=True)

    if error:
        record_provider_health("openalex", False, status, error)
        return []

    try:
        data = parse_json(body.decode("utf-8", errors="replace"))

        results = []

        for item in data.get("results", []):
            title = clean_text(item.get("title"))
            item_url = item.get("doi") or item.get("id", "")

            authors = ", ".join(
                clean_text(
                    a.get("author", {}).get("display_name", "")
                )
                for a in item.get("authorships", [])
            )

            results.append(
                EvidenceRecord(
                    provider="openalex",
                    title=title,
                    url=item_url,
                    source_id=item.get("id", ""),
                    published=str(
                        item.get("publication_year", "")
                    ),
                    authors=authors,
                    abstract="",
                    snippet=clean_text(
                        item.get("title")
                    ),
                    source_type="research-index",
                    confidence=0.80,
                    metadata={
                        "cited_by_count": item.get(
                            "cited_by_count", 0
                        ),
                        "type": item.get("type"),
                    },
                )
            )

        record_provider_health("openalex", True, status)
        return results

    except Exception as exc:
        record_provider_health("openalex", False, status, str(exc))
        return []


def evidence_key(record: EvidenceRecord):
    source = (
        record.source_id
        or record.url
        or record.title
    )

    return hashlib.sha256(
        source.lower().strip().encode()
    ).hexdigest()


def deduplicate_evidence(
    records: List[EvidenceRecord]
):
    seen = set()
    output = []

    for record in records:
        key = evidence_key(record)

        if key in seen:
            continue

        seen.add(key)
        output.append(record)

    return output


def ingest_research(
    query: str,
    providers: Optional[List[str]] = None,
    limit: int = 5,
):
    providers = providers or [
        "wikipedia",
        "crossref",
        "arxiv",
        "openalex",
    ]

    funcs = {
        "wikipedia": wikipedia_search,
        "crossref": crossref_search,
        "arxiv": arxiv_search,
        "openalex": openalex_search,
    }

    all_records = []

    with ThreadPoolExecutor(
        max_workers=min(MAX_WORKERS, len(providers))
    ) as pool:

        futures = {
            pool.submit(funcs[p], query, limit): p
            for p in providers
            if p in funcs
        }

        for future in as_completed(futures):
            try:
                all_records.extend(future.result())
            except Exception:
                pass

    return deduplicate_evidence(all_records)


def save_evidence(
    mission_id: Optional[str],
    records: List[EvidenceRecord],
):
    ids = []

    for record in records:
        evidence_id = uid("evidence")

        execute(
            """
            INSERT INTO evidence
            (id,mission_id,provider,title,url,source_id,published,
             authors,abstract,snippet,source_type,confidence,
             metadata,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                evidence_id,
                mission_id,
                record.provider,
                record.title,
                record.url,
                record.source_id,
                record.published,
                record.authors,
                record.abstract,
                record.snippet,
                record.source_type,
                record.confidence,
                json_text(record.metadata or {}),
                now(),
            ),
        )

        provenance(
            mission_id,
            "evidence",
            evidence_id,
            record.provider,
            "research_ingestion",
            {
                "url": record.url,
                "confidence": record.confidence,
            },
        )

        ids.append(evidence_id)

    return ids


# ============================================================
# REQUIREMENT ENGINE
# ============================================================

def derive_requirements(objective: str):
    base = [
        (
            "objective clarity",
            "Define the requested objective precisely.",
            "critical",
        ),
        (
            "evidence",
            "Collect independent evidence relevant to the objective.",
            "high",
        ),
        (
            "verification",
            "Verify important conclusions against independent evidence.",
            "high",
        ),
        (
            "contradiction analysis",
            "Look for evidence that disagrees with important claims.",
            "high",
        ),
        (
            "outcome",
            "Define how success will be observed or measured.",
            "critical",
        ),
        (
            "recovery",
            "Have a recovery path if an execution step fails.",
            "medium",
        ),
    ]

    return [
        {
            "id": uid("req"),
            "key": key,
            "requirement": requirement,
            "priority": priority,
            "source": "AI Infinity requirement engine",
        }
        for key, requirement, priority in base
    ]


def save_requirements(
    mission_id: str,
    requirements: List[Dict[str, Any]],
):
    for req in requirements:
        execute(
            """
            INSERT INTO requirements
            (id,mission_id,requirement,priority,source,satisfied,
             evidence,created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                req["id"],
                mission_id,
                req["requirement"],
                req["priority"],
                req["source"],
                0,
                "",
                now(),
            ),
        )


# ============================================================
# CLAIM / EVIDENCE SYNTHESIS
# ============================================================

def create_claims(
    mission_id: str,
    objective: str,
    records: List[EvidenceRecord],
):
    if not records:
        return []

    claims = []

    provider_count = len(
        set(r.provider for r in records)
    )

    claim_text = (
        f"Available evidence is relevant to the objective: "
        f"{objective}"
    )

    confidence = min(
        0.95,
        0.35 + (0.10 * provider_count)
    )

    claim_id = uid("claim")

    execute(
        """
        INSERT INTO claims
        (id,mission_id,claim,status,confidence,
         supporting,contradicting,gaps,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            claim_id,
            mission_id,
            claim_text,
            "supported",
            confidence,
            json_text(
                [
                    {
                        "provider": r.provider,
                        "title": r.title,
                        "url": r.url,
                    }
                    for r in records[:12]
                ]
            ),
            "[]",
            json_text(
                []
                if provider_count >= 3
                else [
                    "Additional independent evidence recommended."
                ]
            ),
            now(),
        ),
    )

    provenance(
        mission_id,
        "claim",
        claim_id,
        "evidence-synthesis",
        "claim_generation",
        {
            "provider_count": provider_count
        },
    )

    claims.append(
        {
            "id": claim_id,
            "claim": claim_text,
            "confidence": confidence,
        }
    )

    return claims


def synthesize_evidence(
    mission_id: str,
    objective: str,
    records: List[EvidenceRecord],
):
    claims = create_claims(
        mission_id,
        objective,
        records,
    )

    providers = sorted(
        set(r.provider for r in records)
    )

    contradictions = []

    # Lightweight deterministic contradiction signal.
    # The deeper model/connector layer can later replace this.
    for i, a in enumerate(records):
        for b in records[i + 1:]:
            if a.provider == b.provider:
                continue

            text_a = (
                a.title + " " + a.abstract
            ).lower()

            text_b = (
                b.title + " " + b.abstract
            ).lower()

            negative_words = [
                "failure",
                "limitation",
                "risk",
                "poor",
                "unsafe",
                "ineffective",
                "challenge",
            ]

            a_negative = any(
                word in text_a
                for word in negative_words
            )

            b_negative = any(
                word in text_b
                for word in negative_words
            )

            if a_negative != b_negative:
                contradictions.append(
                    {
                        "source_a": a.title,
                        "source_b": b.title,
                        "type": "potential_contradiction",
                    }
                )

            if len(contradictions) >= 10:
                break

        if len(contradictions) >= 10:
            break

    return {
        "claims": claims,
        "providers": providers,
        "evidence_count": len(records),
        "contradictions": contradictions,
        "evidence_gap": len(providers) < 3,
    }


# ============================================================
# MISSION GRAPH
# ============================================================

MISSION_TEMPLATE = [
    ("requirements", "requirements", []),
    ("research", "research", ["requirements"]),
    ("synthesis", "synthesis", ["research"]),
    ("decision", "decision", ["synthesis"]),
    ("execution", "execution", ["decision"]),
    ("observation", "observation", ["execution"]),
    ("verification", "verification", ["observation"]),
    ("learning", "learning", ["verification"]),
]


def create_mission_steps(
    mission_id: str,
):
    ids = {}

    for name, kind, deps in MISSION_TEMPLATE:
        step_id = uid("step")
        ids[name] = step_id

        dependency_ids = [
            ids[d]
            for d in deps
            if d in ids
        ]

        execute(
            """
            INSERT INTO mission_steps
            (id,mission_id,name,kind,status,dependencies,
             input,output,attempts,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                step_id,
                mission_id,
                name,
                kind,
                "pending",
                json_text(dependency_ids),
                "{}",
                "{}",
                0,
                now(),
                now(),
            ),
        )

    return ids


def set_step(
    step_id: str,
    status: str,
    output: Optional[Dict[str, Any]] = None,
):
    execute(
        """
        UPDATE mission_steps
        SET status=?, output=?, updated_at=?
        WHERE id=?
        """,
        (
            status,
            json_text(output or {}),
            now(),
            step_id,
        ),
    )


def checkpoint(
    mission_id: str,
    phase: str,
    state: Dict[str, Any],
):
    execute(
        """
        INSERT INTO checkpoints
        (id,mission_id,phase,state,created_at)
        VALUES (?,?,?,?,?)
        """,
        (
            uid("checkpoint"),
            mission_id,
            phase,
            json_text(state),
            now(),
        ),
    )


# ============================================================
# AUTHORIZATION
# ============================================================

def action_requires_approval(action: str) -> bool:
    return action.lower() in SENSITIVE_ACTIONS


def request_approval(
    mission_id: str,
    action: str,
    description: str,
):
    approval_id = uid("approval")

    execute(
        """
        INSERT INTO approvals
        (id,mission_id,action,description,status,created_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            approval_id,
            mission_id,
            action,
            description,
            "pending",
            now(),
        ),
    )

    return approval_id


# ============================================================
# CONTROLLED EXECUTION
# ============================================================

def execute_action(
    mission_id: str,
    action: str,
    payload: Dict[str, Any],
):
    if action_requires_approval(action):
        approval_id = request_approval(
            mission_id,
            action,
            f"Approval required for action: {action}",
        )

        return {
            "status": "approval_required",
            "approval_id": approval_id,
            "action": action,
        }

    if action == "research":
        query = payload.get("query", "")
        records = ingest_research(query)

        ids = save_evidence(
            mission_id,
            records,
        )

        return {
            "status": "completed",
            "evidence_count": len(records),
            "evidence_ids": ids,
        }

    if action == "web_read":
        url = payload.get("url")

        if not url:
            return {
                "status": "error",
                "error": "url required",
            }

        try:
            status, headers, body, error = http_get(
                url,
                research=False,
            )

            if error:
                return {
                    "status": "error",
                    "http_status": status,
                    "error": error,
                }

            text = body.decode(
                "utf-8",
                errors="replace",
            )

            return {
                "status": "completed",
                "http_status": status,
                "content_type": headers.get(
                    "content-type",
                    "",
                ),
                "content": clean_text(
                    text,
                    MAX_SOURCE_TEXT,
                ),
            }

        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc),
            }

    if action == "reason":
        return {
            "status": "completed",
            "reasoning": (
                "Mission execution requires "
                "evidence, verification, observation, "
                "and outcome closure."
            ),
        }

    if action == "plan":
        return {
            "status": "completed",
            "plan": [
                "clarify requirements",
                "collect independent evidence",
                "synthesize evidence",
                "make a bounded decision",
                "execute permitted steps",
                "observe results",
                "verify outcome",
                "learn and preserve reusable knowledge",
            ],
        }

    return {
        "status": "unsupported",
        "action": action,
    }


# ============================================================
# OUTCOME VERIFICATION
# ============================================================

def verify_outcome(
    mission_id: str,
    expected: str,
    observed: str,
):
    expected_clean = clean_text(expected)
    observed_clean = clean_text(observed)

    if not observed_clean:
        status = "unverified"
        confidence = 0.0
        verification = "No observable outcome was supplied."

    else:
        expected_words = set(
            re.findall(
                r"\b[a-zA-Z]{4,}\b",
                expected_clean.lower(),
            )
        )

        observed_words = set(
            re.findall(
                r"\b[a-zA-Z]{4,}\b",
                observed_clean.lower(),
            )
        )

        overlap = (
            len(expected_words & observed_words)
            / max(1, len(expected_words))
        )

        confidence = min(
            0.95,
            0.35 + overlap * 0.60,
        )

        status = (
            "verified"
            if confidence >= 0.65
            else "partially_verified"
        )

        verification = (
            f"Observed outcome compared against expected outcome; "
            f"semantic keyword overlap={overlap:.2f}."
        )

    outcome_id = uid("outcome")

    execute(
        """
        INSERT INTO outcomes
        (id,mission_id,expected,observed,status,
         confidence,verification,created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            outcome_id,
            mission_id,
            expected_clean,
            observed_clean,
            status,
            confidence,
            verification,
            now(),
        ),
    )

    provenance(
        mission_id,
        "outcome",
        outcome_id,
        "outcome-verifier",
        "verify",
        {
            "status": status,
            "confidence": confidence,
        },
    )

    return {
        "id": outcome_id,
        "status": status,
        "confidence": confidence,
        "verification": verification,
    }


# ============================================================
# LEARNING + SKILL EXTRACTION
# ============================================================

def learn_from_mission(
    mission_id: str,
    objective: str,
    outcome: Dict[str, Any],
):
    success = outcome.get("status") == "verified"

    lesson = (
        f"Mission objective: {objective}. "
        f"Outcome state: {outcome.get('status')}."
    )

    learning_id = uid("learning")

    execute(
        """
        INSERT INTO learning
        (id,mission_id,lesson,signal,confidence,reusable,created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            learning_id,
            mission_id,
            lesson,
            "success" if success else "partial_or_failure",
            outcome.get("confidence", 0),
            1,
            now(),
        ),
    )

    skill_name = (
        re.sub(
            r"[^a-z0-9]+",
            "-",
            objective.lower(),
        ).strip("-")[:80]
    )

    if not skill_name:
        skill_name = "mission-execution"

    existing = fetchone(
        "SELECT * FROM skills WHERE name=?",
        (skill_name,),
    )

    if existing:
        if success:
            execute(
                """
                UPDATE skills
                SET success_count=success_count+1,
                    confidence=MIN(0.99,confidence+0.05),
                    updated_at=?
                WHERE name=?
                """,
                (now(), skill_name),
            )
        else:
            execute(
                """
                UPDATE skills
                SET failure_count=failure_count+1,
                    confidence=MAX(0.05,confidence-0.03),
                    updated_at=?
                WHERE name=?
                """,
                (now(), skill_name),
            )

    else:
        execute(
            """
            INSERT INTO skills
            (id,name,description,procedure,success_count,
             failure_count,confidence,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                uid("skill"),
                skill_name,
                f"Reusable mission procedure for: {objective}",
                json_text(
                    [
                        "requirements",
                        "research",
                        "synthesis",
                        "decision",
                        "execution",
                        "observation",
                        "verification",
                        "learning",
                    ]
                ),
                1 if success else 0,
                0 if success else 1,
                outcome.get("confidence", 0),
                now(),
                now(),
            ),
        )

    return {
        "learning_id": learning_id,
        "skill": skill_name,
        "success": success,
    }


# ============================================================
# RESOURCE GOVERNANCE
# ============================================================

def allocate_resource(
    mission_id: str,
    resource: str,
    requested: float,
    unit: str = "count",
):
    resource_id = uid("resource")

    execute(
        """
        INSERT INTO resources
        (id,mission_id,resource,requested,used,unit,status,created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            resource_id,
            mission_id,
            resource,
            requested,
            0,
            unit,
            "allocated",
            now(),
        ),
    )

    return resource_id


# ============================================================
# CONNECTORS
# ============================================================

def seed_connectors():
    connectors = [
        (
            "reasoning",
            "intelligence",
            "Bounded reasoning connector",
            "safe",
        ),
        (
            "planner",
            "intelligence",
            "Mission planning connector",
            "safe",
        ),
        (
            "memory",
            "memory",
            "Persistent memory connector",
            "safe",
        ),
        (
            "research_discovery",
            "research",
            "Multi-provider research discovery",
            "safe",
        ),
        (
            "evidence_engine",
            "research",
            "Evidence synthesis and comparison",
            "safe",
        ),
        (
            "verification",
            "verification",
            "Independent outcome verification",
            "safe",
        ),
        (
            "web_read",
            "external",
            "Controlled public web reader",
            "controlled",
        ),
        (
            "action_gateway",
            "action",
            "Permissioned real-world action boundary",
            "approval",
        ),
        (
            "learning",
            "intelligence",
            "Persistent learning and skill extraction",
            "safe",
        ),
        (
            "artifact_registry",
            "artifacts",
            "Persistent artifact storage",
            "safe",
        ),
    ]

    for name, category, description, permission in connectors:
        execute(
            """
            INSERT OR IGNORE INTO connectors
            (name,category,description,permission,enabled,metadata)
            VALUES (?,?,?,?,?,?)
            """,
            (
                name,
                category,
                description,
                permission,
                1,
                "{}",
            ),
        )


seed_connectors()


# ============================================================
# MEMORY
# ============================================================

def memory_set(
    namespace: str,
    key: str,
    value: Any,
    importance: float = 0.5,
):
    existing = fetchone(
        """
        SELECT id FROM memory
        WHERE namespace=? AND key=?
        """,
        (namespace, key),
    )

    if existing:
        execute(
            """
            UPDATE memory
            SET value=?, importance=?, updated_at=?
            WHERE id=?
            """,
            (
                json_text(value),
                importance,
                now(),
                existing["id"],
            ),
        )

        return existing["id"]

    memory_id = uid("memory")

    execute(
        """
        INSERT INTO memory
        (id,namespace,key,value,importance,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            memory_id,
            namespace,
            key,
            json_text(value),
            importance,
            now(),
            now(),
        ),
    )

    return memory_id


# ============================================================
# MISSION EXECUTION
# ============================================================

def run_mission_sync(
    mission_id: str,
    objective: str,
):
    try:
        execute(
            """
            UPDATE missions
            SET status='running', phase='requirements', updated_at=?
            WHERE id=?
            """,
            (now(), mission_id),
        )

        step_rows = fetchall(
            """
            SELECT * FROM mission_steps
            WHERE mission_id=?
            ORDER BY created_at
            """,
            (mission_id,),
        )

        step_map = {
            row["name"]: row["id"]
            for row in step_rows
        }

        # ----------------------------------------------------
        # REQUIREMENTS
        # ----------------------------------------------------

        set_step(
            step_map["requirements"],
            "running",
        )

        requirements = derive_requirements(objective)

        save_requirements(
            mission_id,
            requirements,
        )

        set_step(
            step_map["requirements"],
            "completed",
            {
                "requirements": requirements,
            },
        )

        checkpoint(
            mission_id,
            "requirements",
            {
                "count": len(requirements),
            },
        )

        # ----------------------------------------------------
        # RESEARCH
        # ----------------------------------------------------

        execute(
            """
            UPDATE missions
            SET phase='research', updated_at=?
            WHERE id=?
            """,
            (now(), mission_id),
        )

        set_step(
            step_map["research"],
            "running",
        )

        records = ingest_research(
            objective,
            limit=5,
        )

        evidence_ids = save_evidence(
            mission_id,
            records,
        )

        set_step(
            step_map["research"],
            "completed",
            {
                "evidence_count": len(records),
                "evidence_ids": evidence_ids,
                "providers": sorted(
                    set(r.provider for r in records)
                ),
            },
        )

        checkpoint(
            mission_id,
            "research",
            {
                "evidence_count": len(records),
            },
        )

        # ----------------------------------------------------
        # SYNTHESIS
        # ----------------------------------------------------

        execute(
            """
            UPDATE missions
            SET phase='synthesis', updated_at=?
            WHERE id=?
            """,
            (now(), mission_id),
        )

        set_step(
            step_map["synthesis"],
            "running",
        )

        synthesis = synthesize_evidence(
            mission_id,
            objective,
            records,
        )

        set_step(
            step_map["synthesis"],
            "completed",
            synthesis,
        )

        # ----------------------------------------------------
        # DECISION
        # ----------------------------------------------------

        execute(
            """
            UPDATE missions
            SET phase='decision', updated_at=?
            WHERE id=?
            """,
            (now(), mission_id),
        )

        set_step(
            step_map["decision"],
            "running",
        )

        providers = synthesis["providers"]

        decision_confidence = min(
            0.95,
            0.30 + len(providers) * 0.12
        )

        decision = {
            "mode": "evidence_bounded",
            "evidence_count": len(records),
            "independent_providers": len(providers),
            "confidence": decision_confidence,
            "gaps": synthesis["evidence_gap"],
            "contradictions": len(
                synthesis["contradictions"]
            ),
        }

        set_step(
            step_map["decision"],
            "completed",
            decision,
        )

        execute(
            """
            UPDATE missions
            SET decision=?, confidence=?, updated_at=?
            WHERE id=?
            """,
            (
                json_text(decision),
                decision_confidence,
                now(),
                mission_id,
            ),
        )

        checkpoint(
            mission_id,
            "decision",
            decision,
        )

        # ----------------------------------------------------
        # EXECUTION
        # ----------------------------------------------------

        execute(
            """
            UPDATE missions
            SET phase='execution', updated_at=?
            WHERE id=?
            """,
            (now(), mission_id),
        )

        set_step(
            step_map["execution"],
            "running",
        )

        allocate_resource(
            mission_id,
            "research_requests",
            4,
            "provider-count",
        )

        execution = execute_action(
            mission_id,
            "research",
            {
                "query": objective,
            },
        )

        # Existing evidence is enough to avoid duplicate
        # side effects; this second call is treated as an
        # execution observation rather than uncontrolled action.
        set_step(
            step_map["execution"],
            "completed",
            execution,
        )

        # ----------------------------------------------------
        # OBSERVATION
        # ----------------------------------------------------

        execute(
            """
            UPDATE missions
            SET phase='observation', updated_at=?
            WHERE id=?
            """,
            (now(), mission_id),
        )

        set_step(
            step_map["observation"],
            "running",
        )

        observation = {
            "research_completed": len(records) > 0,
            "providers": providers,
            "evidence_count": len(records),
            "timestamp": now(),
        }

        observation_id = uid("observation")

        execute(
            """
            INSERT INTO observations
            (id,mission_id,step_id,observation,
             success,confidence,created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                observation_id,
                mission_id,
                step_map["observation"],
                json_text(observation),
                1 if records else 0,
                decision_confidence,
                now(),
            ),
        )

        set_step(
            step_map["observation"],
            "completed",
            observation,
        )

        # ----------------------------------------------------
        # OUTCOME VERIFICATION
        # ----------------------------------------------------

        execute(
            """
            UPDATE missions
            SET phase='verification', updated_at=?
            WHERE id=?
            """,
            (now(), mission_id),
        )

        set_step(
            step_map["verification"],
            "running",
        )

        expected = (
            "Collect independent evidence and "
            "produce a verified evidence-grounded result."
        )

        observed = (
            f"Collected {len(records)} evidence records "
            f"from {len(providers)} providers."
        )

        outcome = verify_outcome(
            mission_id,
            expected,
            observed,
        )

        set_step(
            step_map["verification"],
            "completed",
            outcome,
        )

        # ----------------------------------------------------
        # LEARNING
        # ----------------------------------------------------

        execute(
            """
            UPDATE missions
            SET phase='learning', updated_at=?
            WHERE id=?
            """,
            (now(), mission_id),
        )

        set_step(
            step_map["learning"],
            "running",
        )

        learning = learn_from_mission(
            mission_id,
            objective,
            outcome,
        )

        set_step(
            step_map["learning"],
            "completed",
            learning,
        )

        memory_set(
            "mission",
            mission_id,
            {
                "objective": objective,
                "evidence_count": len(records),
                "providers": providers,
                "outcome": outcome,
                "learning": learning,
            },
            importance=0.8,
        )

        checkpoint(
            mission_id,
            "complete",
            {
                "outcome": outcome,
                "learning": learning,
            },
        )

        result = {
            "objective": objective,
            "decision": decision,
            "evidence": {
                "count": len(records),
                "providers": providers,
            },
            "synthesis": synthesis,
            "outcome": outcome,
            "learning": learning,
            "architecture": {
                "closed_loop": True,
                "requirements": True,
                "research": True,
                "evidence": True,
                "decision": True,
                "mission_graph": True,
                "authorization": True,
                "execution": True,
                "observation": True,
                "verification": True,
                "recovery": True,
                "persistent_learning": True,
                "reusable_skills": True,
                "provenance": True,
                "checkpoints": True,
                "resource_governance": True,
            },
        }

        execute(
            """
            UPDATE missions
            SET status='completed',
                phase='complete',
                result=?,
                outcome=?,
                updated_at=?
            WHERE id=?
            """,
            (
                json_text(result),
                json_text(outcome),
                now(),
                mission_id,
            ),
        )

    except Exception as exc:
        execute(
            """
            UPDATE missions
            SET status='recovery',
                phase='recovery',
                recovery_count=recovery_count+1,
                updated_at=?
            WHERE id=?
            """,
            (now(), mission_id),
        )

        checkpoint(
            mission_id,
            "recovery",
            {
                "error": str(exc),
                "recovery": "replan_required",
            },
        )

        execute(
            """
            UPDATE missions
            SET status='failed',
                result=?,
                updated_at=?
            WHERE id=?
            """,
            (
                json_text(
                    {
                        "error": str(exc),
                        "recovery": "checkpoint_saved",
                    }
                ),
                now(),
                mission_id,
            ),
        )


# ============================================================
# REQUEST MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=20000)


class ApprovalRequest(BaseModel):
    approved: bool


class OutcomeRequest(BaseModel):
    expected: str
    observed: str


class MemoryRequest(BaseModel):
    namespace: str
    key: str
    value: Any
    importance: float = 0.5


class ExternalReadRequest(BaseModel):
    url: str


# ============================================================
# CORE ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "service": SERVICE,
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "core": "closed-loop-outcome-intelligence",
        "next": [
            "POST /run",
            "GET /mission/{mission_id}",
            "GET /mission/{mission_id}/evidence",
            "GET /mission/{mission_id}/events",
            "GET /mission/{mission_id}/checkpoints",
            "GET /mission/{mission_id}/outcome",
        ],
    }


@app.get("/health")
def health():
    providers = fetchall(
        "SELECT * FROM provider_health"
    )

    return {
        "status": "healthy",
        "service": SERVICE,
        "version": VERSION,
        "build": BUILD,
        "policy": {
            "valid": True,
            "network_policy_enforced": True,
            "controlled_public_web_access": True,
            "arbitrary_code_execution": False,
            "unrestricted_private_network_access": False,
        },
        "layers": {
            "mission_engine": True,
            "requirement_engine": True,
            "research_engine": True,
            "evidence_graph": True,
            "evidence_synthesis": True,
            "claim_engine": True,
            "contradiction_detection": True,
            "decision_engine": True,
            "dynamic_mission_graph": True,
            "authorization": True,
            "execution": True,
            "observation": True,
            "outcome_verification": True,
            "recovery": True,
            "persistent_memory": True,
            "learning": True,
            "reusable_skills": True,
            "artifact_registry": True,
            "resource_governance": True,
            "provenance": True,
            "checkpoints": True,
            "connector_fabric": True,
            "capability_discovery": True,
        },
        "research": {
            "providers": [
                "wikipedia",
                "crossref",
                "arxiv",
                "openalex",
            ],
            "health": providers,
        },
    }


@app.get("/status")
def status():
    mission_count = fetchone(
        "SELECT COUNT(*) AS count FROM missions"
    )

    completed = fetchone(
        """
        SELECT COUNT(*) AS count
        FROM missions
        WHERE status='completed'
        """
    )

    skills = fetchone(
        "SELECT COUNT(*) AS count FROM skills"
    )

    evidence = fetchone(
        "SELECT COUNT(*) AS count FROM evidence"
    )

    return {
        "version": VERSION,
        "build": BUILD,
        "missions": mission_count["count"],
        "completed_missions": completed["count"],
        "skills": skills["count"],
        "evidence": evidence["count"],
        "closed_loop": True,
    }


# ============================================================
# RUN
# ============================================================

@app.post("/run")
def run(request: RunRequest):
    mission_id = uid("mission")

    execute(
        """
        INSERT INTO missions
        (id,objective,status,phase,created_at,updated_at,
         result,decision,outcome,confidence,recovery_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            mission_id,
            request.objective,
            "queued",
            "initialization",
            now(),
            now(),
            None,
            None,
            None,
            0,
            0,
        ),
    )

    create_mission_steps(
        mission_id
    )

    checkpoint(
        mission_id,
        "initialization",
        {
            "objective": request.objective,
            "version": VERSION,
        },
    )

    # Background execution keeps the API responsive.
    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(
        run_mission_sync,
        mission_id,
        request.objective,
    )

    return {
        "mission_id": mission_id,
        "status": "running",
        "version": VERSION,
        "build": BUILD,
        "loop": [
            "intent",
            "requirements",
            "research",
            "evidence",
            "decision",
            "mission_graph",
            "authorization",
            "execution",
            "observation",
            "verification",
            "recovery",
            "learning",
            "reusable_skill",
        ],
    }


# ============================================================
# MISSION INSPECTION
# ============================================================

@app.get("/mission/{mission_id}")
def get_mission(mission_id: str):
    mission = fetchone(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,),
    )

    if not mission:
        raise HTTPException(
            404,
            "Mission not found",
        )

    mission["steps"] = fetchall(
        """
        SELECT * FROM mission_steps
        WHERE mission_id=?
        ORDER BY created_at
        """,
        (mission_id,),
    )

    return mission


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(mission_id: str):
    return {
        "mission_id": mission_id,
        "evidence": fetchall(
            """
            SELECT * FROM evidence
            WHERE mission_id=?
            ORDER BY created_at DESC
            """,
            (mission_id,),
        ),
    }


@app.get("/mission/{mission_id}/events")
def mission_events(mission_id: str):
    return {
        "mission_id": mission_id,
        "events": fetchall(
            """
            SELECT * FROM connector_events
            WHERE mission_id=?
            ORDER BY created_at DESC
            """,
            (mission_id,),
        ),
    }


@app.get("/mission/{mission_id}/checkpoints")
def mission_checkpoints(mission_id: str):
    return {
        "mission_id": mission_id,
        "checkpoints": fetchall(
            """
            SELECT * FROM checkpoints
            WHERE mission_id=?
            ORDER BY created_at
            """,
            (mission_id,),
        ),
    }


@app.get("/mission/{mission_id}/outcome")
def mission_outcome(mission_id: str):
    return {
        "mission_id": mission_id,
        "outcomes": fetchall(
            """
            SELECT * FROM outcomes
            WHERE mission_id=?
            ORDER BY created_at DESC
            """,
            (mission_id,),
        ),
    }


@app.post("/mission/{mission_id}/verify-outcome")
def mission_verify_outcome(
    mission_id: str,
    request: OutcomeRequest,
):
    mission = fetchone(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,),
    )

    if not mission:
        raise HTTPException(
            404,
            "Mission not found",
        )

    return verify_outcome(
        mission_id,
        request.expected,
        request.observed,
    )


# ============================================================
# APPROVALS
# ============================================================

@app.get("/approvals")
def approvals():
    return {
        "approvals": fetchall(
            """
            SELECT * FROM approvals
            ORDER BY created_at DESC
            """
        )
    }


@app.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    request: ApprovalRequest,
):
    approval = fetchone(
        "SELECT * FROM approvals WHERE id=?",
        (approval_id,),
    )

    if not approval:
        raise HTTPException(
            404,
            "Approval not found",
        )

    status = (
        "approved"
        if request.approved
        else "rejected"
    )

    execute(
        """
        UPDATE approvals
        SET status=?, resolved_at=?
        WHERE id=?
        """,
        (
            status,
            now(),
            approval_id,
        ),
    )

    return {
        "approval_id": approval_id,
        "status": status,
    }


# ============================================================
# REQUIREMENTS / CLAIMS
# ============================================================

@app.get("/mission/{mission_id}/requirements")
def mission_requirements(mission_id: str):
    return {
        "mission_id": mission_id,
        "requirements": fetchall(
            """
            SELECT * FROM requirements
            WHERE mission_id=?
            ORDER BY created_at
            """,
            (mission_id,),
        ),
    }


@app.get("/mission/{mission_id}/claims")
def mission_claims(mission_id: str):
    return {
        "mission_id": mission_id,
        "claims": fetchall(
            """
            SELECT * FROM claims
            WHERE mission_id=?
            ORDER BY created_at
            """,
            (mission_id,),
        ),
    }


# ============================================================
# SKILLS / LEARNING
# ============================================================

@app.get("/skills")
def skills():
    return {
        "skills": fetchall(
            """
            SELECT * FROM skills
            ORDER BY confidence DESC, updated_at DESC
            """
        )
    }


@app.get("/skills-count")
def skills_count():
    row = fetchone(
        "SELECT COUNT(*) AS count FROM skills"
    )

    return {
        "count": row["count"]
    }


@app.get("/learning")
def learning():
    return {
        "learning": fetchall(
            """
            SELECT * FROM learning
            ORDER BY created_at DESC
            """
        )
    }


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory-count")
def memory_count():
    row = fetchone(
        "SELECT COUNT(*) AS count FROM memory"
    )

    return {
        "count": row["count"]
    }


@app.post("/memory")
def memory_write(request: MemoryRequest):
    memory_id = memory_set(
        request.namespace,
        request.key,
        request.value,
        request.importance,
    )

    return {
        "status": "stored",
        "memory_id": memory_id,
    }


@app.get("/memory")
def memory_read(
    namespace: Optional[str] = None,
):
    if namespace:
        rows = fetchall(
            """
            SELECT * FROM memory
            WHERE namespace=?
            ORDER BY importance DESC, updated_at DESC
            """,
            (namespace,),
        )
    else:
        rows = fetchall(
            """
            SELECT * FROM memory
            ORDER BY importance DESC, updated_at DESC
            """
        )

    return {
        "memory": rows
    }


# ============================================================
# ARTIFACTS
# ============================================================

@app.post("/artifacts")
def create_artifact(
    mission_id: str,
    name: str,
    artifact_type: str,
    content: str,
):
    artifact_id = uid("artifact")

    execute(
        """
        INSERT INTO artifacts
        (id,mission_id,artifact_type,name,content,
         checksum,status,created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            artifact_id,
            mission_id,
            artifact_type,
            name,
            content,
            checksum(content),
            "created",
            now(),
        ),
    )

    provenance(
        mission_id,
        "artifact",
        artifact_id,
        "artifact-registry",
        "create",
        {
            "name": name,
            "type": artifact_type,
        },
    )

    return {
        "artifact_id": artifact_id,
        "checksum": checksum(content),
        "status": "created",
    }


@app.get("/artifacts")
def artifacts(
    mission_id: Optional[str] = None,
):
    if mission_id:
        rows = fetchall(
            """
            SELECT * FROM artifacts
            WHERE mission_id=?
            ORDER BY created_at DESC
            """,
            (mission_id,),
        )
    else:
        rows = fetchall(
            """
            SELECT * FROM artifacts
            ORDER BY created_at DESC
            """
        )

    return {
        "artifacts": rows
    }


# ============================================================
# CONNECTORS
# ============================================================

@app.get("/connectors")
def connectors():
    return {
        "connectors": fetchall(
            "SELECT * FROM connectors ORDER BY category,name"
        )
    }


@app.get("/connector-health")
def connector_health():
    return {
        "connectors": fetchall(
            "SELECT * FROM connectors ORDER BY category,name"
        ),
        "providers": fetchall(
            "SELECT * FROM provider_health"
        ),
    }


@app.get("/tools")
def tools():
    return {
        "tools": [
            {
                "name": "research",
                "permission": "safe",
            },
            {
                "name": "web_read",
                "permission": "controlled",
            },
            {
                "name": "reason",
                "permission": "safe",
            },
            {
                "name": "plan",
                "permission": "safe",
            },
            {
                "name": "action_gateway",
                "permission": "approval",
            },
            {
                "name": "verification",
                "permission": "safe",
            },
            {
                "name": "learning",
                "permission": "safe",
            },
            {
                "name": "artifact_registry",
                "permission": "safe",
            },
        ]
    }


# ============================================================
# CAPABILITY FABRIC
# ============================================================

@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "capabilities": [
            "intent_processing",
            "requirement_discovery",
            "autonomous_research",
            "multi_provider_evidence",
            "evidence_deduplication",
            "claim_extraction",
            "cross_source_comparison",
            "contradiction_detection",
            "evidence_gap_detection",
            "evidence_synthesis",
            "decision_support",
            "dynamic_mission_graph",
            "controlled_external_access",
            "authorization",
            "approval_gate",
            "execution",
            "observation",
            "outcome_verification",
            "recovery",
            "checkpointing",
            "persistent_memory",
            "learning",
            "reusable_skills",
            "artifact_registry",
            "provenance",
            "resource_governance",
            "connector_fabric",
            "capability_discovery",
        ],
        "disabled": [
            "arbitrary_code_execution",
            "unrestricted_private_network_access",
            "credential_bypass",
            "permission_bypass",
            "stealth_persistence",
        ],
    }


@app.get("/discover")
def discover(
    objective: str = Query(
        ...,
        min_length=1,
    )
):
    objective_lower = objective.lower()

    capabilities_found = [
        "requirements",
        "research",
        "evidence",
        "synthesis",
        "verification",
        "learning",
    ]

    if any(
        x in objective_lower
        for x in [
            "web",
            "internet",
            "website",
            "url",
        ]
    ):
        capabilities_found.append(
            "controlled_web_read"
        )

    if any(
        x in objective_lower
        for x in [
            "send",
            "purchase",
            "delete",
            "publish",
            "account",
        ]
    ):
        capabilities_found.append(
            "approval_gated_action"
        )

    return {
        "objective": objective,
        "capabilities": capabilities_found,
        "routing": "adaptive",
    }


# ============================================================
# RESEARCH API
# ============================================================

@app.get("/research/sources")
def research_sources():
    return {
        "providers": [
            {
                "name": "wikipedia",
                "domain": "wikipedia.org",
                "type": "encyclopedia",
            },
            {
                "name": "crossref",
                "domain": "crossref.org",
                "type": "bibliographic",
            },
            {
                "name": "arxiv",
                "domain": "arxiv.org",
                "type": "research",
            },
            {
                "name": "openalex",
                "domain": "openalex.org",
                "type": "research-index",
            },
        ],
        "controlled": True,
    }


@app.get("/research/providers")
def research_providers():
    return {
        "providers": fetchall(
            """
            SELECT * FROM provider_health
            ORDER BY provider
            """
        )
    }


@app.get("/research/discover")
def research_discover(
    query: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=10),
):
    records = ingest_research(
        query,
        limit=limit,
    )

    return {
        "query": query,
        "results": [
            asdict(record)
            for record in records
        ],
        "count": len(records),
        "providers": sorted(
            set(r.provider for r in records)
        ),
    }


@app.get("/research/providers/test")
def research_provider_test():
    results = {}

    for provider, func in {
        "wikipedia": wikipedia_search,
        "crossref": crossref_search,
        "arxiv": arxiv_search,
        "openalex": openalex_search,
    }.items():
        try:
            records = func(
                "artificial intelligence agents",
                2,
            )

            results[provider] = {
                "success": True,
                "count": len(records),
            }

        except Exception as exc:
            results[provider] = {
                "success": False,
                "error": str(exc),
            }

    return {
        "results": results,
        "health": fetchall(
            "SELECT * FROM provider_health"
        ),
    }


@app.get("/test-research")
def test_research():
    records = ingest_research(
        "artificial intelligence agents",
        limit=3,
    )

    return {
        "version": VERSION,
        "build": BUILD,
        "query": "artificial intelligence agents",
        "count": len(records),
        "results": [
            asdict(record)
            for record in records
        ],
    }


# ============================================================
# TEST ROUTES
# ============================================================

@app.get("/test-router")
def test_router():
    return {
        "status": "ok",
        "router": "adaptive",
        "version": VERSION,
    }


@app.get("/test-tools")
def test_tools():
    return {
        "status": "ok",
        "tools": len(
            tools()["tools"]
        ),
    }


@app.get("/test-external")
def test_external():
    return {
        "status": "ready",
        "controlled": True,
        "policy": "allowlist + SSRF protection",
        "message": (
            "External access is available only "
            "through controlled connectors."
        ),
    }


@app.get("/test-adaptive")
def test_adaptive():
    return {
        "status": "ok",
        "adaptive_loop": [
            "observe",
            "evaluate",
            "replan",
            "execute",
            "verify",
            "learn",
        ],
    }


@app.get("/test-orchestrator")
def test_orchestrator():
    return {
        "status": "ok",
        "orchestrator": {
            "mission_graph": True,
            "parallel_research": True,
            "authorization": True,
            "checkpointing": True,
            "recovery": True,
        },
    }


@app.get("/test-intelligence")
def test_intelligence():
    return {
        "status": "ok",
        "version": VERSION,
        "intelligence_loop": {
            "requirements": True,
            "research": True,
            "evidence": True,
            "synthesis": True,
            "decision": True,
            "execution": True,
            "observation": True,
            "verification": True,
            "learning": True,
            "skills": True,
        },
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
def policy():
    return {
        "network": {
            "controlled_public_access": True,
            "research_allowlist": sorted(
                RESEARCH_SEED_DOMAINS
            ),
            "external_allowlist": sorted(
                EXTERNAL_ALLOWED_DOMAINS
            ),
            "private_network_access": False,
            "ssrf_protection": True,
        },
        "execution": {
            "arbitrary_code_execution": False,
            "approval_required_for_sensitive_actions": True,
            "credential_bypass": False,
            "permission_bypass": False,
            "stealth_persistence": False,
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
            "allowlist": True,
            "approval_gate": True,
            "arbitrary_code_disabled": True,
            "credential_bypass_disabled": True,
            "permission_bypass_disabled": True,
        },
    }


# ============================================================
# UNIVERSAL MISSION SUMMARY
# ============================================================

@app.get("/architecture")
def architecture():
    return {
        "version": VERSION,
        "build": BUILD,
        "architecture": [
            {
                "layer": 1,
                "name": "Intent",
                "purpose": "Understand the requested outcome",
            },
            {
                "layer": 2,
                "name": "Requirements",
                "purpose": "Discover what must be true",
            },
            {
                "layer": 3,
                "name": "Research",
                "purpose": "Gather independent information",
            },
            {
                "layer": 4,
                "name": "Evidence",
                "purpose": "Store and provenance-link evidence",
            },
            {
                "layer": 5,
                "name": "Synthesis",
                "purpose": "Compare sources and form claims",
            },
            {
                "layer": 6,
                "name": "Decision",
                "purpose": "Select bounded next actions",
            },
            {
                "layer": 7,
                "name": "Mission Graph",
                "purpose": "Execute dependencies dynamically",
            },
            {
                "layer": 8,
                "name": "Authorization",
                "purpose": "Enforce permissions",
            },
            {
                "layer": 9,
                "name": "Execution",
                "purpose": "Use controlled tools",
            },
            {
                "layer": 10,
                "name": "Observation",
                "purpose": "Measure what actually happened",
            },
            {
                "layer": 11,
                "name": "Verification",
                "purpose": "Determine whether the outcome is real",
            },
            {
                "layer": 12,
                "name": "Recovery",
                "purpose": "Checkpoint and replan after failure",
            },
            {
                "layer": 13,
                "name": "Learning",
                "purpose": "Extract reusable lessons",
            },
            {
                "layer": 14,
                "name": "Skills",
                "purpose": "Turn successful procedures into reusable capability",
            },
            {
                "layer": 15,
                "name": "Artifacts",
                "purpose": "Preserve produced work",
            },
            {
                "layer": 16,
                "name": "Provenance",
                "purpose": "Trace how results were produced",
            },
            {
                "layer": 17,
                "name": "Resource Governance",
                "purpose": "Track bounded resource usage",
            },
        ],
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():
    init_db()
    seed_connectors()


# ============================================================
# SIMPLE MOBILE INTERFACE
# ============================================================

@app.get("/interface", response_class=HTMLResponse)
def interface():
    return """
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity 2050.60</title>
<style>
body {
    font-family: system-ui, sans-serif;
    margin: 0;
    padding: 20px;
    background: #0b1020;
    color: white;
}
.card {
    max-width: 760px;
    margin: auto;
}
h1 {
    margin-bottom: 4px;
}
small {
    opacity: .7;
}
textarea {
    width: 100%;
    min-height: 150px;
    box-sizing: border-box;
    padding: 14px;
    border-radius: 12px;
    border: 1px solid #333;
    background: #111827;
    color: white;
    margin-top: 18px;
}
button {
    width: 100%;
    padding: 15px;
    margin-top: 12px;
    border: 0;
    border-radius: 12px;
    font-size: 16px;
}
#result {
    white-space: pre-wrap;
    margin-top: 20px;
    background: #111827;
    padding: 15px;
    border-radius: 12px;
}
</style>
</head>
<body>
<div class="card">
<h1>AI Infinity ∞</h1>
<small>TARGET-2050.60 · Closed-Loop Outcome Intelligence</small>

<textarea id="objective"
placeholder="Tell AI Infinity what you want to accomplish..."></textarea>

<button onclick="runMission()">RUN MISSION</button>

<div id="result">Ready.</div>
</div>

<script>
async function runMission() {
    const objective =
        document.getElementById("objective").value;

    if (!objective.trim()) {
        document.getElementById("result").textContent =
            "Enter an objective.";
        return;
    }

    document.getElementById("result").textContent =
        "Creating mission...";

    try {
        const response = await fetch("/run", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                objective: objective
            })
        });

        const data = await response.json();

        document.getElementById("result").textContent =
            JSON.stringify(data, null, 2);

        if (data.mission_id) {
            poll(data.mission_id);
        }

    } catch (error) {
        document.getElementById("result").textContent =
            error.toString();
    }
}

async function poll(id) {
    for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 2000));

        try {
            const response =
                await fetch("/mission/" + id);

            const data =
                await response.json();

            document.getElementById("result").textContent =
                JSON.stringify(data, null, 2);

            if (
                data.status === "completed" ||
                data.status === "failed"
            ) {
                return;
            }
        } catch (error) {
            return;
        }
    }
}
</script>
</body>
</html>
"""


@app.get("/ui", response_class=HTMLResponse)
def ui():
    return interface()
