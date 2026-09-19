"""
AI Infinity
TARGET-2050.22
BUILD: WEB-CONTROL-CENTER + EVIDENCE-INTEGRITY-CORE

Purpose:
- Give AI Infinity a real browser interface at /
- Preserve the autonomous research backend
- Preserve evidence validation
- Preserve source-quality checks
- Preserve claim verification
- Preserve counter-evidence
- Preserve SQLite persistence
- Provide API + dashboard from one FastAPI application
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

VERSION = "TARGET-2050.22"
BUILD = "WEB-CONTROL-CENTER-EVIDENCE-INTEGRITY-CORE"

APP_NAME = "AI Infinity"

BASE_DIR = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity"))
BASE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "ai_infinity.db"

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
MAX_FETCH_BYTES = 5 * 1024 * 1024

USER_AGENT = (
    "AI-Infinity/2050.22 "
    "(research-and-evidence-validation; +https://ai-infinity-ca5e.onrender.com)"
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="AI Infinity autonomous research and evidence engine",
)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            completed_at REAL,
            result_json TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            url TEXT,
            title TEXT,
            provider TEXT,
            source_key TEXT,
            source_type TEXT,
            quality REAL DEFAULT 0,
            valid INTEGER DEFAULT 0,
            independent INTEGER DEFAULT 0,
            content_preview TEXT,
            rejection_reason TEXT,
            created_at REAL NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            decision TEXT,
            confidence REAL DEFAULT 0,
            supporting_sources INTEGER DEFAULT 0,
            contradicting_sources INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT,
            created_at REAL NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        )
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


def iso(ts: Optional[float] = None) -> str:
    if ts is None:
        ts = now()

    return datetime.fromtimestamp(
        ts, timezone.utc
    ).isoformat()


def safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def log_event(
    mission_id: str,
    event_type: str,
    payload: Any = None,
):
    conn = db()

    conn.execute(
        """
        INSERT INTO events
        (mission_id, event_type, payload, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            mission_id,
            event_type,
            json.dumps(safe_json(payload)),
            now(),
        ),
    )

    conn.commit()
    conn.close()


def normalize_url(url: str) -> str:
    try:
        p = urlparse(url.strip())

        scheme = p.scheme.lower()
        host = (p.hostname or "").lower()

        port = p.port

        if port and not (
            (scheme == "http" and port == 80)
            or (scheme == "https" and port == 443)
        ):
            host = f"{host}:{port}"

        path = p.path or "/"

        return urlunparse(
            (
                scheme,
                host,
                path,
                "",
                p.query,
                "",
            )
        )

    except Exception:
        return url.strip()


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def sha(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8", errors="ignore")
    ).hexdigest()[:24]


# ============================================================
# SSRF / PUBLIC URL VALIDATION
# ============================================================

PRIVATE_HOST_PATTERNS = [
    "localhost",
    "127.",
    "0.0.0.0",
    "::1",
    "169.254.",
    "10.",
    "192.168.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
]


def validate_public_url(url: str) -> tuple[bool, str]:
    try:
        p = urlparse(url)

        if p.scheme not in {"http", "https"}:
            return False, "unsupported_scheme"

        host = (p.hostname or "").lower()

        if not host:
            return False, "missing_host"

        if host in {
            "localhost",
            "localhost.localdomain",
        }:
            return False, "private_host"

        for pattern in PRIVATE_HOST_PATTERNS:
            if host.startswith(pattern):
                return False, "private_host"

        if host.endswith(".local"):
            return False, "local_domain"

        return True, "ok"

    except Exception:
        return False, "invalid_url"


# ============================================================
# CONTENT VALIDATION
# ============================================================

CHALLENGE_PATTERNS = [
    "client challenge",
    "enable javascript",
    "checking your browser",
    "just a moment",
    "cf-chl",
    "cloudflare",
    "captcha",
    "access denied",
    "verify you are human",
    "attention required",
]

LOGIN_PATTERNS = [
    "sign in",
    "log in",
    "login required",
    "create an account",
]

ERROR_PATTERNS = [
    "internal server error",
    "bad gateway",
    "service unavailable",
    "page not found",
]


def looks_like_pdf(content: bytes, content_type: str = "") -> bool:
    return (
        content[:5] == b"%PDF-"
        or "application/pdf" in content_type.lower()
    )


def looks_binary(content: bytes) -> bool:
    if not content:
        return False

    sample = content[:4096]

    if b"\x00" in sample:
        return True

    bad = sum(
        1
        for b in sample
        if b < 9 or (13 < b < 32)
    )

    return bad > len(sample) * 0.12


def detect_page_problem(text: str) -> Optional[str]:
    lowered = text.lower()

    for pattern in CHALLENGE_PATTERNS:
        if pattern in lowered:
            return "challenge_or_block_page"

    for pattern in LOGIN_PATTERNS:
        if pattern in lowered and len(text) < 5000:
            return "login_page"

    for pattern in ERROR_PATTERNS:
        if pattern in lowered:
            return "error_page"

    return None


def validate_evidence_text(text: str) -> tuple[bool, str]:
    if not text:
        return False, "empty_content"

    text = text.strip()

    if len(text) < 250:
        return False, "insufficient_text"

    if text.startswith("%PDF-"):
        return False, "raw_pdf_content"

    if looks_binary(text.encode("utf-8", errors="ignore")):
        return False, "binary_content"

    problem = detect_page_problem(text)

    if problem:
        return False, problem

    words = re.findall(r"\b[A-Za-z]{2,}\b", text)

    if len(words) < 50:
        return False, "too_few_readable_words"

    return True, "valid"


# ============================================================
# PDF SUPPORT
# ============================================================

def extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader

        import io

        reader = PdfReader(io.BytesIO(content))

        pages = []

        for page in reader.pages[:30]:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                continue

        return "\n".join(pages).strip()

    except Exception:
        return ""


# ============================================================
# SOURCE FETCH
# ============================================================

def fetch_url(url: str) -> Dict[str, Any]:
    valid, reason = validate_public_url(url)

    if not valid:
        return {
            "valid": False,
            "url": url,
            "reason": reason,
        }

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/pdf,text/plain;q=0.9,*/*;q=0.5"
                ),
            },
            allow_redirects=True,
            stream=True,
        )

        final_url = response.url

        final_valid, final_reason = validate_public_url(
            final_url
        )

        if not final_valid:
            response.close()

            return {
                "valid": False,
                "url": final_url,
                "reason": "unsafe_redirect:" + final_reason,
                "status_code": response.status_code,
            }

        chunks = []
        total = 0

        for chunk in response.iter_content(65536):
            if not chunk:
                continue

            total += len(chunk)

            if total > MAX_FETCH_BYTES:
                break

            chunks.append(chunk)

        response.close()

        content = b"".join(chunks)

        content_type = response.headers.get(
            "content-type",
            "",
        )

        if looks_like_pdf(content, content_type):
            text = extract_pdf_text(content)

            if not text:
                return {
                    "valid": False,
                    "url": final_url,
                    "reason": "pdf_text_extraction_failed",
                    "status_code": response.status_code,
                    "content_type": content_type,
                }

        elif looks_binary(content):
            return {
                "valid": False,
                "url": final_url,
                "reason": "binary_content",
                "status_code": response.status_code,
                "content_type": content_type,
            }

        else:
            text = content.decode(
                "utf-8",
                errors="replace",
            )

            # Remove obvious scripts/styles for evidence analysis.
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
            ).strip()

        ok, validation_reason = validate_evidence_text(text)

        return {
            "valid": ok,
            "url": final_url,
            "status_code": response.status_code,
            "content_type": content_type,
            "text": text[:100000],
            "reason": validation_reason,
        }

    except requests.RequestException as exc:
        return {
            "valid": False,
            "url": url,
            "reason": "request_error:" + str(exc)[:200],
        }

    except Exception as exc:
        return {
            "valid": False,
            "url": url,
            "reason": "fetch_error:" + str(exc)[:200],
        }


# ============================================================
# PROVIDERS
# ============================================================

def provider_openalex(query: str) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://api.openalex.org/works",
            params={
                "search": query,
                "per-page": 8,
            },
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )

        if response.status_code != 200:
            return []

        data = response.json()

        results = []

        for item in data.get("results", []):
            url = (
                item.get("primary_location", {})
                .get("landing_page_url")
                or item.get("doi")
                or ""
            )

            if not url:
                continue

            results.append(
                {
                    "url": url,
                    "title": item.get("title") or "",
                    "provider": "openalex",
                    "doi": item.get("doi") or "",
                }
            )

        return results

    except Exception:
        return []


def provider_crossref(query: str) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://api.crossref.org/works",
            params={
                "query.bibliographic": query,
                "rows": 8,
            },
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )

        if response.status_code != 200:
            return []

        data = response.json()

        results = []

        for item in (
            data.get("message", {})
            .get("items", [])
        ):
            doi = item.get("DOI")

            if not doi:
                continue

            title_list = item.get("title") or []
            title = title_list[0] if title_list else ""

            results.append(
                {
                    "url": "https://doi.org/" + doi,
                    "title": title,
                    "provider": "crossref",
                    "doi": doi,
                }
            )

        return results

    except Exception:
        return []


def provider_duckduckgo(query: str) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT,
            },
        )

        if response.status_code != 200:
            return []

        html = response.text

        matches = re.findall(
            r'nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.I | re.S,
        )

        results = []

        for url, title in matches[:10]:
            title = re.sub("<[^>]+>", "", title)

            if url.startswith("//"):
                url = "https:" + url

            results.append(
                {
                    "url": url,
                    "title": title.strip(),
                    "provider": "duckduckgo",
                }
            )

        return results

    except Exception:
        return []


def provider_wikipedia(query: str) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": 5,
            },
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )

        if response.status_code != 200:
            return []

        data = response.json()

        results = []

        for item in data.get("query", {}).get("search", []):
            title = item.get("title", "")

            if not title:
                continue

            url = (
                "https://en.wikipedia.org/wiki/"
                + title.replace(" ", "_")
            )

            results.append(
                {
                    "url": url,
                    "title": title,
                    "provider": "wikipedia",
                }
            )

        return results

    except Exception:
        return []


# ============================================================
# SOURCE IDENTITY / DEDUPLICATION
# ============================================================

def source_key(item: Dict[str, Any]) -> str:
    doi = (
        item.get("doi")
        or ""
    ).strip().lower()

    if doi:
        return "doi:" + doi

    title = re.sub(
        r"[^a-z0-9]+",
        " ",
        item.get("title", "").lower(),
    ).strip()

    if title:
        return "title:" + sha(title)

    return "url:" + normalize_url(
        item.get("url", "")
    )


def deduplicate_sources(
    sources: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:

    grouped = {}

    for source in sources:
        key = source_key(source)

        if key not in grouped:
            grouped[key] = source

        else:
            existing = grouped[key]

            # Preserve provider diversity as metadata,
            # but do NOT count duplicate providers as
            # independent evidence.
            providers = set(
                existing.get("providers", [])
            )

            providers.add(
                source.get("provider", "")
            )

            existing["providers"] = sorted(
                p for p in providers if p
            )

    return list(grouped.values())


# ============================================================
# SOURCE QUALITY
# ============================================================

def source_quality(
    source: Dict[str, Any],
    evidence_text: str,
) -> float:

    provider = (
        source.get("provider", "")
        .lower()
    )

    score = 0.40

    if provider == "openalex":
        score += 0.25

    elif provider == "crossref":
        score += 0.25

    elif provider == "wikipedia":
        score += 0.12

    elif provider == "duckduckgo":
        score += 0.05

    if len(evidence_text) >= 2000:
        score += 0.10

    if len(evidence_text) >= 5000:
        score += 0.05

    if source.get("doi"):
        score += 0.08

    return round(min(score, 1.0), 3)


# ============================================================
# CLAIM EXTRACTION
# ============================================================

ASSERTION_WORDS = [
    "shows",
    "found",
    "finds",
    "demonstrates",
    "suggests",
    "improves",
    "reduces",
    "increases",
    "achieved",
    "reported",
    "concluded",
    "results",
    "evidence",
    "failed",
    "failure",
    "limitation",
]


def clean_sentence(sentence: str) -> str:
    sentence = re.sub(
        r"\s+",
        " ",
        sentence,
    ).strip()

    sentence = re.sub(
        r"^[\-\*\d\.\)\s]+",
        "",
        sentence,
    )

    return sentence


def extract_claims(
    evidence_text: str,
    limit: int = 8,
) -> List[str]:

    ok, _ = validate_evidence_text(
        evidence_text
    )

    if not ok:
        return []

    sentences = re.split(
        r"(?<=[.!?])\s+",
        evidence_text,
    )

    claims = []

    for sentence in sentences:
        sentence = clean_sentence(sentence)

        if len(sentence) < 60:
            continue

        if len(sentence) > 600:
            continue

        lowered = sentence.lower()

        if not any(
            word in lowered
            for word in ASSERTION_WORDS
        ):
            continue

        # Avoid navigation and boilerplate.
        if any(
            x in lowered
            for x in [
                "cookie",
                "javascript",
                "subscribe",
                "sign in",
                "privacy policy",
            ]
        ):
            continue

        claims.append(sentence)

        if len(claims) >= limit:
            break

    return claims


# ============================================================
# CLAIM RELATION
# ============================================================

NEGATION_WORDS = [
    "not",
    "no",
    "failed",
    "failure",
    "unable",
    "cannot",
    "could not",
    "did not",
    "does not",
    "weak",
    "limited",
    "limitation",
    "lack",
    "insufficient",
    "contradict",
]


def claim_relation(
    claim: str,
    evidence: str,
) -> str:

    c = claim.lower()
    e = evidence.lower()

    claim_words = set(
        re.findall(r"\b[a-z]{4,}\b", c)
    )

    evidence_words = set(
        re.findall(r"\b[a-z]{4,}\b", e)
    )

    overlap = len(
        claim_words & evidence_words
    )

    if overlap < 3:
        return "neutral"

    negated = any(
        word in e
        for word in NEGATION_WORDS
    )

    if negated:
        return "contradict"

    return "support"


# ============================================================
# RESEARCH
# ============================================================

def build_queries(objective: str) -> List[str]:
    return [
        objective,
        objective + " evidence",
        objective + " limitations",
        objective + " failures",
        objective + " criticism",
        objective + " replication",
    ]


def research_mission(
    mission_id: str,
    objective: str,
) -> Dict[str, Any]:

    log_event(
        mission_id,
        "research_started",
        {"objective": objective},
    )

    all_candidates = []

    queries = build_queries(objective)

    provider_counts = {
        "openalex": 0,
        "crossref": 0,
        "duckduckgo": 0,
        "wikipedia": 0,
    }

    for query in queries:

        providers = [
            provider_openalex,
            provider_crossref,
            provider_duckduckgo,
            provider_wikipedia,
        ]

        for provider in providers:

            results = provider(query)

            provider_name = (
                results[0].get("provider")
                if results
                else None
            )

            if provider_name in provider_counts:
                provider_counts[
                    provider_name
                ] += len(results)

            all_candidates.extend(results)

    unique_sources = deduplicate_sources(
        all_candidates
    )

    accepted = []
    rejected = []

    for source in unique_sources[:60]:

        fetched = fetch_url(
            source.get("url", "")
        )

        source["final_url"] = fetched.get(
            "url",
            source.get("url", ""),
        )

        source["valid"] = bool(
            fetched.get("valid")
        )

        source["reason"] = fetched.get(
            "reason",
            "",
        )

        source["text"] = fetched.get(
            "text",
            "",
        )

        source["quality"] = source_quality(
            source,
            source["text"],
        )

        if source["valid"]:

            accepted.append(source)

        else:

            rejected.append(source)

    # --------------------------------------------------------
    # Independence
    # --------------------------------------------------------

    domains = set()

    for source in accepted:
        domain = domain_of(
            source.get(
                "final_url",
                source.get("url", ""),
            )
        )

        if domain:
            domains.add(domain)

    # --------------------------------------------------------
    # Claims
    # --------------------------------------------------------

    claim_records = {}

    for source in accepted:

        claims = extract_claims(
            source.get("text", "")
        )

        for claim in claims:

            key = re.sub(
                r"\s+",
                " ",
                claim.lower(),
            ).strip()

            if key not in claim_records:
                claim_records[key] = {
                    "claim": claim,
                    "supporting": [],
                    "contradicting": [],
                }

            record = claim_records[key]

            for other in accepted:

                if other is source:
                    continue

                relation = claim_relation(
                    claim,
                    other.get("text", ""),
                )

                if relation == "support":
                    record["supporting"].append(
                        other
                    )

                elif relation == "contradict":
                    record["contradicting"].append(
                        other
                    )

    final_claims = []

    verified_count = 0
    uncertain_count = 0
    insufficient_count = 0

    for record in list(
        claim_records.values()
    )[:20]:

        supporting = record["supporting"]
        contradicting = record[
            "contradicting"
        ]

        support_domains = set(
            domain_of(
                s.get(
                    "final_url",
                    s.get("url", ""),
                )
            )
            for s in supporting
        )

        contradiction_domains = set(
            domain_of(
                s.get(
                    "final_url",
                    s.get("url", ""),
                )
            )
            for s in contradicting
        )

        support_domains.discard("")
        contradiction_domains.discard("")

        if (
            len(support_domains) >= 2
            and len(contradicting) == 0
        ):
            decision = "VERIFIED"
            confidence = 0.85
            verified_count += 1

        elif (
            len(support_domains) >= 1
            and len(contradicting) == 0
        ):
            decision = "UNCERTAIN"
            confidence = 0.60
            uncertain_count += 1

        elif len(contradicting) > 0:
            decision = "INSUFFICIENT"
            confidence = 0.35
            insufficient_count += 1

        else:
            decision = "INSUFFICIENT"
            confidence = 0.25
            insufficient_count += 1

        final_claims.append(
            {
                "claim": record["claim"],
                "decision": decision,
                "confidence": confidence,
                "supporting_sources": len(
                    supporting
                ),
                "contradicting_sources": len(
                    contradicting
                ),
                "support_domains": sorted(
                    support_domains
                ),
                "contradiction_domains": sorted(
                    contradiction_domains
                ),
            }
        )

    # --------------------------------------------------------
    # Persistence
    # --------------------------------------------------------

    conn = db()

    for source in accepted:
        conn.execute(
            """
            INSERT INTO sources
            (
                mission_id,
                url,
                title,
                provider,
                source_key,
                source_type,
                quality,
                valid,
                independent,
                content_preview,
                rejection_reason,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                source.get("final_url")
                or source.get("url"),
                source.get("title", ""),
                source.get("provider", ""),
                source_key(source),
                "web",
                source.get("quality", 0),
                1,
                1,
                source.get("text", "")[:500],
                "",
                now(),
            ),
        )

    for source in rejected:
        conn.execute(
            """
            INSERT INTO sources
            (
                mission_id,
                url,
                title,
                provider,
                source_key,
                source_type,
                quality,
                valid,
                independent,
                content_preview,
                rejection_reason,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.get("final_url")
                or source.get("url"),
                source.get("title", ""),
                source.get("provider", ""),
                source_key(source),
                "web",
                source.get("quality", 0),
                0,
                0,
                "",
                source.get("reason", ""),
                now(),
            ),
        )

    for claim in final_claims:
        conn.execute(
            """
            INSERT INTO claims
            (
                mission_id,
                claim,
                decision,
                confidence,
                supporting_sources,
                contradicting_sources,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                claim["claim"],
                claim["decision"],
                claim["confidence"],
                claim["supporting_sources"],
                claim["contradicting_sources"],
                now(),
            ),
        )

    conn.commit()
    conn.close()

    # --------------------------------------------------------
    # Evaluation
    # --------------------------------------------------------

    contradictions = sum(
        x["contradicting_sources"]
        for x in final_claims
    )

    avg_quality = (
        sum(
            x.get("quality", 0)
            for x in accepted
        )
        / len(accepted)
        if accepted
        else 0
    )

    provider_diversity = len(
        {
            x.get("provider")
            for x in accepted
            if x.get("provider")
        }
    )

    research_strength = round(
        min(
            1.0,
            (
                avg_quality * 0.35
                + min(len(domains) / 5, 1)
                * 0.25
                + min(
                    provider_diversity / 4,
                    1,
                )
                * 0.15
                + min(
                    verified_count / 5,
                    1,
                )
                * 0.25
            ),
        ),
        3,
    )

    if (
        verified_count > 0
        and contradictions == 0
        and len(domains) >= 2
    ):
        overall_decision = "SUPPORTED"

    elif final_claims:
        overall_decision = "INSUFFICIENT"

    else:
        overall_decision = "NO_VALID_EVIDENCE"

    result = {
        "task_id": mission_id,
        "status": "completed",
        "version": VERSION,
        "build": BUILD,
        "objective": objective,
        "research": {
            "queries": queries,
            "provider_counts": provider_counts,
            "candidates": len(all_candidates),
            "unique_sources": len(unique_sources),
            "accepted_sources": len(accepted),
            "rejected_sources": len(rejected),
        },
        "evidence_graph": {
            "nodes": len(accepted),
            "independent_domains": len(domains),
            "domains": sorted(domains),
            "provider_diversity": provider_diversity,
            "average_source_quality": round(
                avg_quality,
                3,
            ),
        },
        "verification": {
            "overall_decision": overall_decision,
            "verified_claims": verified_count,
            "uncertain_claims": uncertain_count,
            "insufficient_claims": insufficient_count,
            "contradictions": contradictions,
            "research_strength": research_strength,
            "claims": final_claims,
        },
        "next_actions": [
            "Review claims marked INSUFFICIENT.",
            "Inspect contradictory evidence.",
            "Increase independent source diversity.",
            "Re-run verification after evidence expansion.",
        ],
        "completed_at": iso(),
    }

    return result


# ============================================================
# MISSION MODELS
# ============================================================

class MissionRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=3,
        max_length=10000,
    )
    research: bool = True
    verify: bool = True
    remember: bool = True


class ResearchRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=3,
        max_length=10000,
    )


class CommandRequest(BaseModel):
    command: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )
    objective: Optional[str] = None
    research: bool = True
    verify: bool = True
    remember: bool = True


# ============================================================
# MISSION EXECUTION
# ============================================================

def create_mission(
    objective: str,
    remember: bool = True,
) -> Dict[str, Any]:

    mission_id = (
        "mission-"
        + uuid.uuid4().hex[:12]
    )

    started = now()

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (
            id,
            objective,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "running",
            started,
        ),
    )

    conn.commit()
    conn.close()

    log_event(
        mission_id,
        "mission_created",
        {"objective": objective},
    )

    try:

        result = research_mission(
            mission_id,
            objective,
        )

        if remember:
            conn = db()

            conn.execute(
                """
                INSERT INTO memory
                (
                    mission_id,
                    content,
                    created_at
                )
                VALUES (?, ?, ?)
                """,
                (
                    mission_id,
                    json.dumps(
                        {
                            "objective": objective,
                            "decision": result.get(
                                "verification",
                                {},
                            ).get(
                                "overall_decision"
                            ),
                        }
                    ),
                    now(),
                ),
            )

            conn.commit()
            conn.close()

        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status = ?,
                completed_at = ?,
                result_json = ?
            WHERE id = ?
            """,
            (
                "completed",
                now(),
                json.dumps(result),
                mission_id,
            ),
        )

        conn.commit()
        conn.close()

        log_event(
            mission_id,
            "mission_completed",
            {
                "status": "completed",
            },
        )

        return result

    except Exception as exc:

        result = {
            "task_id": mission_id,
            "status": "failed",
            "version": VERSION,
            "build": BUILD,
            "error": str(exc),
        }

        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status = ?,
                completed_at = ?,
                result_json = ?
            WHERE id = ?
            """,
            (
                "failed",
                now(),
                json.dumps(result),
                mission_id,
            ),
        )

        conn.commit()
        conn.close()

        log_event(
            mission_id,
            "mission_failed",
            {"error": str(exc)},
        )

        return result


# ============================================================
# WEB DASHBOARD
# ============================================================

HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1.0">

<title>AI Infinity</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    min-height: 100vh;
    font-family:
        Inter,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background:
        radial-gradient(
            circle at top left,
            #172554 0,
            #070b18 35%,
            #03050b 100%
        );

    color: #f8fafc;
}

.container {
    width: min(1100px, 94%);
    margin: auto;
    padding: 28px 0 50px;
}

.header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 20px;
    margin-bottom: 28px;
}

.brand {
    display: flex;
    align-items: center;
    gap: 14px;
}

.logo {
    width: 48px;
    height: 48px;
    border-radius: 16px;

    display: flex;
    align-items: center;
    justify-content: center;

    font-size: 25px;
    font-weight: 800;

    background:
        linear-gradient(
            135deg,
            #38bdf8,
            #6366f1
        );

    box-shadow:
        0 10px 40px rgba(56,189,248,.25);
}

h1 {
    margin: 0;
    font-size: 28px;
}

.subtitle {
    margin-top: 3px;
    color: #94a3b8;
    font-size: 13px;
}

.online {
    display: flex;
    align-items: center;
    gap: 8px;

    padding: 9px 13px;
    border-radius: 999px;

    background: rgba(34,197,94,.10);
    border: 1px solid rgba(34,197,94,.25);

    color: #86efac;
    font-size: 13px;
}

.dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #22c55e;
    box-shadow: 0 0 12px #22c55e;
}

.hero {
    padding: 30px;
    border-radius: 24px;

    background:
        linear-gradient(
            145deg,
            rgba(15,23,42,.92),
            rgba(15,23,42,.60)
        );

    border: 1px solid rgba(148,163,184,.15);

    box-shadow:
        0 30px 100px rgba(0,0,0,.30);
}

.hero h2 {
    margin: 0 0 8px;
    font-size: clamp(25px, 5vw, 42px);
}

.hero p {
    color: #94a3b8;
    max-width: 750px;
    line-height: 1.6;
}

textarea {
    width: 100%;
    min-height: 130px;

    margin-top: 18px;
    padding: 17px;

    resize: vertical;

    border-radius: 16px;
    border: 1px solid #263248;

    background: #070b14;
    color: #f8fafc;

    font-size: 15px;
    outline: none;
}

textarea:focus {
    border-color: #38bdf8;
    box-shadow:
        0 0 0 3px rgba(56,189,248,.10);
}

button {
    margin-top: 14px;

    padding: 13px 20px;

    border: 0;
    border-radius: 13px;

    background:
        linear-gradient(
            135deg,
            #38bdf8,
            #6366f1
        );

    color: white;
    font-weight: 700;
    font-size: 14px;

    cursor: pointer;

    box-shadow:
        0 10px 30px rgba(59,130,246,.20);
}

button:hover {
    transform: translateY(-1px);
}

button:disabled {
    opacity: .55;
    cursor: wait;
}

.grid {
    display: grid;
    grid-template-columns:
        repeat(auto-fit,minmax(180px,1fr));

    gap: 14px;
    margin-top: 18px;
}

.card {
    padding: 20px;
    border-radius: 18px;

    background: rgba(15,23,42,.70);
    border: 1px solid rgba(148,163,184,.12);
}

.card .label {
    color: #64748b;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: .08em;
}

.card .value {
    margin-top: 7px;
    font-size: 21px;
    font-weight: 750;
}

.result {
    margin-top: 18px;
    display: none;

    padding: 22px;

    border-radius: 18px;

    background: #050914;
    border: 1px solid #1e293b;
}

.result pre {
    white-space: pre-wrap;
    word-break: break-word;

    color: #cbd5e1;
    line-height: 1.55;
    font-size: 13px;
}

.section {
    margin-top: 24px;
}

.section h3 {
    margin-bottom: 10px;
}

.api {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.api span {
    padding: 8px 10px;
    border-radius: 9px;

    background: #0b1220;
    border: 1px solid #1e293b;

    color: #94a3b8;
    font-size: 12px;
}

.footer {
    margin-top: 30px;
    text-align: center;

    color: #475569;
    font-size: 12px;
}

.status-ok {
    color: #86efac;
}

</style>
</head>

<body>

<div class="container">

    <header class="header">

        <div class="brand">

            <div class="logo">∞</div>

            <div>
                <h1>AI Infinity</h1>
                <div class="subtitle">
                    Autonomous Research & Evidence Engine
                </div>
            </div>

        </div>

        <div class="online">
            <span class="dot"></span>
            ONLINE
        </div>

    </header>


    <section class="hero">

        <h2>
            Turn an objective into verified intelligence.
        </h2>

        <p>
            AI Infinity researches across public sources,
            validates evidence, detects contradictions,
            evaluates source independence and produces
            a structured mission result.
        </p>

        <textarea
            id="objective"
            placeholder="Tell AI Infinity what you want it to investigate..."
        ></textarea>

        <button id="runBtn" onclick="runMission()">
            🚀 Run Mission
        </button>

    </section>


    <section class="grid">

        <div class="card">
            <div class="label">Version</div>
            <div class="value" id="version">
                TARGET-2050.22
            </div>
        </div>

        <div class="card">
            <div class="label">Engine</div>
            <div class="value status-ok">
                READY
            </div>
        </div>

        <div class="card">
            <div class="label">Evidence</div>
            <div class="value status-ok">
                ENABLED
            </div>
        </div>

        <div class="card">
            <div class="label">Verification</div>
            <div class="value status-ok">
                ENABLED
            </div>
        </div>

    </section>


    <section class="result" id="result">

        <h3>Mission Result</h3>

        <pre id="resultText"></pre>

    </section>


    <section class="section">

        <h3>Core capabilities</h3>

        <div class="api">
            <span>Research</span>
            <span>Evidence Validation</span>
            <span>Source Quality</span>
            <span>Claim Verification</span>
            <span>Counter-Evidence</span>
            <span>Contradiction Detection</span>
            <span>Source Independence</span>
            <span>SQLite Memory</span>
        </div>

    </section>


    <section class="section">

        <h3>API</h3>

        <div class="api">
            <span>/health</span>
            <span>/status</span>
            <span>/capabilities</span>
            <span>/mission</span>
            <span>/missions</span>
            <span>/research</span>
            <span>/run</span>
            <span>/command</span>
        </div>

    </section>


    <div class="footer">
        AI Infinity • TARGET-2050.22 •
        Evidence-integrity architecture
    </div>

</div>


<script>

async function loadStatus() {

    try {

        const response =
            await fetch("/status");

        const data =
            await response.json();

        if (data.version) {
            document.getElementById("version")
                .textContent = data.version;
        }

    } catch (error) {

        console.log(error);

    }

}


async function runMission() {

    const objective =
        document.getElementById(
            "objective"
        ).value.trim();

    if (!objective) {

        alert(
            "Enter an objective first."
        );

        return;
    }

    const button =
        document.getElementById(
            "runBtn"
        );

    const result =
        document.getElementById(
            "result"
        );

    const resultText =
        document.getElementById(
            "resultText"
        );

    button.disabled = true;
    button.textContent =
        "⏳ AI Infinity is working...";

    result.style.display = "block";

    resultText.textContent =
        "Starting mission...\n\n" +
        "Researching sources...\n" +
        "Validating evidence...\n" +
        "Checking independence...\n" +
        "Verifying claims...\n" +
        "Checking counter-evidence...";

    try {

        const response =
            await fetch(
                "/mission",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({
                        objective:
                            objective,

                        research: true,

                        verify: true,

                        remember: true
                    })
                }
            );

        const data =
            await response.json();

        resultText.textContent =
            JSON.stringify(
                data,
                null,
                2
            );

    } catch (error) {

        resultText.textContent =
            "Mission request failed:\n\n"
            + error;

    } finally {

        button.disabled = false;

        button.textContent =
            "🚀 Run Mission";
    }
}


loadStatus();

</script>

</body>
</html>
"""


# ============================================================
# WEB ROUTES
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
def home():
    return HTML_PAGE


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "timestamp": iso(),
    }


@app.get("/status")
def status():
    return {
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "mission_engine": "ready",
        "evidence_integrity": "enabled",
        "source_validation": "enabled",
        "claim_verification": "enabled",
        "countercheck": "enabled",
        "database": "sqlite",
        "web_interface": "enabled",
    }


@app.get("/capabilities")
def capabilities():

    return {
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "builtin_tools": [
            {
                "name": "capabilities",
                "category": "builtin",
                "permission": "safe",
            },
            {
                "name": "health",
                "category": "builtin",
                "permission": "safe",
            },
            {
                "name": "status",
                "category": "builtin",
                "permission": "safe",
            },
            {
                "name": "research",
                "category": "research",
                "permission": "safe",
            },
            {
                "name": "evidence_validation",
                "category": "evidence",
                "permission": "safe",
            },
            {
                "name": "claim_verification",
                "category": "verification",
                "permission": "safe",
            },
            {
                "name": "countercheck",
                "category": "verification",
                "permission": "safe",
            },
            {
                "name": "memory",
                "category": "persistence",
                "permission": "safe",
            },
        ],
        "providers": [
            "OpenAlex",
            "Crossref",
            "DuckDuckGo",
            "Wikipedia",
        ],
        "features": [
            "SSRF protection",
            "source validation",
            "PDF extraction",
            "challenge-page detection",
            "binary-content rejection",
            "source deduplication",
            "source-quality scoring",
            "claim extraction",
            "claim verification",
            "counter-evidence",
            "contradiction detection",
            "independent-domain analysis",
            "SQLite persistence",
            "browser dashboard",
        ],
    }


# ============================================================
# MISSION ROUTES
# ============================================================

@app.post("/mission")
def mission(request: MissionRequest):

    return create_mission(
        request.objective,
        request.remember,
    )


@app.post("/missions")
def missions_create(request: MissionRequest):

    return create_mission(
        request.objective,
        request.remember,
    )


@app.post("/research")
def research(request: ResearchRequest):

    return create_mission(
        request.objective,
        remember=False,
    )


@app.post("/run")
def run(request: MissionRequest):

    return create_mission(
        request.objective,
        request.remember,
    )


@app.post("/command")
def command(request: CommandRequest):

    objective = (
        request.objective
        or request.command
    )

    return create_mission(
        objective,
        request.remember,
    )


# ============================================================
# MISSION LOOKUP
# ============================================================

@app.get("/mission/{mission_id}")
def get_mission(
    mission_id: str,
):

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
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    result = dict(row)

    if result.get("result_json"):
        try:
            result["result"] = json.loads(
                result["result_json"]
            )
        except Exception:
            pass

    return result


@app.get("/missions")
def get_missions():

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            objective,
            status,
            created_at,
            completed_at
        FROM missions
        ORDER BY created_at DESC
        LIMIT 100
        """
    ).fetchall()

    conn.close()

    return {
        "count": len(rows),
        "missions": [
            {
                **dict(row),
                "created_at": iso(
                    row["created_at"]
                ),
                "completed_at": (
                    iso(row["completed_at"])
                    if row["completed_at"]
                    else None
                ),
            }
            for row in rows
        ],
    }


@app.get("/mission/{mission_id}/sources")
def get_sources(
    mission_id: str,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM sources
        WHERE mission_id = ?
        ORDER BY quality DESC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "count": len(rows),
        "sources": [
            dict(row)
            for row in rows
        ],
    }


@app.get("/mission/{mission_id}/claims")
def get_claims(
    mission_id: str,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM claims
        WHERE mission_id = ?
        ORDER BY confidence DESC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "count": len(rows),
        "claims": [
            dict(row)
            for row in rows
        ],
    }


@app.get("/mission/{mission_id}/events")
def get_events(
    mission_id: str,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM events
        WHERE mission_id = ?
        ORDER BY created_at ASC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    events = []

    for row in rows:

        item = dict(row)

        try:
            item["payload"] = json.loads(
                item["payload"]
            )
        except Exception:
            pass

        item["created_at"] = iso(
            item["created_at"]
        )

        events.append(item)

    return {
        "mission_id": mission_id,
        "count": len(events),
        "events": events,
    }


# ============================================================
# SOURCE VALIDATION API
# ============================================================

class SourceRequest(BaseModel):
    url: str


@app.post("/validate-source")
def validate_source(
    request: SourceRequest,
):

    result = fetch_url(
        request.url
    )

    return {
        "version": VERSION,
        "url": request.url,
        "validation": result,
    }


# ============================================================
# ROOT API INFORMATION
# ============================================================

@app.get("/api")
def api_info():

    return {
        "name": APP_NAME,
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "dashboard": "/",
        "health": "/health",
        "status_endpoint": "/status",
        "capabilities": "/capabilities",
        "mission": "POST /mission",
        "research": "POST /research",
        "command": "POST /command",
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():

    init_db()

    print(
        f"{APP_NAME} {VERSION} online"
    )

    print(
        f"Build: {BUILD}"
    )

    print(
        "Dashboard: /"
    )

    print(
        "Evidence integrity: enabled"
    )

    print(
        "Claim verification: enabled"
    )

    print(
        "Countercheck: enabled"
    )


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(
            os.getenv("PORT", "8000")
        ),
    )
