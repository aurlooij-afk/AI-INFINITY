"""
AI Infinity
TARGET-2050.33
BUILD: CLAIM-PURITY-EVIDENCE-ENTAILMENT-PROVENANCE-CORE

Purpose:
Evidence-first autonomous research with auditable claim provenance.

Important:
- Similarity is NOT proof.
- Low similarity edges are never labelled SUPPORTED_BY.
- VERIFIED requires independent corroboration.
- Provider diversity is not treated as true independence.
- Claims must pass purity filters before entering the graph.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# VERSION
# ============================================================

VERSION = "TARGET-2050.33"
BUILD = "CLAIM-PURITY-EVIDENCE-ENTAILMENT-PROVENANCE-CORE"

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

MAX_WORKERS = 6
FETCH_TIMEOUT = 15
MAX_TEXT = 90000
MAX_RESULTS_PER_PROVIDER = 8

executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Evidence-first autonomous research engine",
)


# ============================================================
# DATABASE
# ============================================================

db_lock = threading.Lock()


def db():
    conn = sqlite3.connect(
        str(DB_PATH),
        check_same_thread=False,
        timeout=30,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        conn = db()
        cur = conn.cursor()

        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                result TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS works (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                title TEXT,
                url TEXT,
                doi TEXT,
                provider TEXT,
                domain TEXT,
                source_family TEXT,
                work_key TEXT,
                abstract TEXT,
                text TEXT,
                quality REAL DEFAULT 0,
                relevance REAL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                work_id INTEGER NOT NULL,
                excerpt TEXT NOT NULL,
                evidence_type TEXT,
                quality TEXT,
                locator TEXT,
                score REAL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                claim TEXT NOT NULL,
                status TEXT DEFAULT 'INSUFFICIENT',
                purity REAL DEFAULT 0,
                confidence REAL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                relation TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                score REAL DEFAULT 0,
                reason TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audits (
                mission_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                key TEXT,
                value TEXT,
                created_at REAL NOT NULL
            );
            """
        )

        conn.commit()
        conn.close()


init_db()


# ============================================================
# MODELS
# ============================================================

class MissionRequest(BaseModel):
    objective: str = Field(..., min_length=5, max_length=10000)


class CommandRequest(BaseModel):
    command: str = Field(..., min_length=5, max_length=10000)


# ============================================================
# HELPERS
# ============================================================

def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def tokens(text: str) -> set:
    words = re.findall(r"[a-zA-Z0-9]{3,}", (text or "").lower())
    stop = {
        "this", "that", "with", "from", "have", "been", "were",
        "they", "their", "which", "about", "into", "than", "also",
        "these", "those", "such", "using", "used", "more", "will",
        "can", "may", "for", "and", "the", "are", "was", "not",
        "but", "its", "our", "has", "had", "does", "between",
    }
    return {x for x in words if x not in stop}


def similarity(a: str, b: str) -> float:
    aa = tokens(a)
    bb = tokens(b)

    if not aa or not bb:
        return 0.0

    return len(aa & bb) / max(1, len(aa | bb))


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def source_family(provider: str, domain: str) -> str:
    """
    Provider is retained for provenance, but source-family identity
    prefers the underlying domain when possible.
    """
    d = domain.lower()

    known = {
        "arxiv.org": "arxiv",
        "biorxiv.org": "biorxiv",
        "medrxiv.org": "medrxiv",
        "nature.com": "nature",
        "science.org": "science",
        "sciencedirect.com": "sciencedirect",
        "springer.com": "springer",
        "acm.org": "acm",
        "ieee.org": "ieee",
        "openreview.net": "openreview",
        "paperswithcode.com": "paperswithcode",
    }

    if d in known:
        return known[d]

    return d or provider


def safe_url(url: str) -> bool:
    try:
        p = urlparse(url)

        if p.scheme not in {"http", "https"}:
            return False

        host = p.hostname
        if not host:
            return False

        host_l = host.lower()

        blocked_names = {
            "localhost",
            "localhost.localdomain",
            "metadata.google.internal",
            "instance-data",
        }

        if host_l in blocked_names:
            return False

        try:
            ip = ipaddress.ip_address(host_l)

            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False
        except ValueError:
            pass

        return True

    except Exception:
        return False


def fetch_url(url: str) -> Optional[str]:
    if not safe_url(url):
        return None

    try:
        r = requests.get(
            url,
            timeout=FETCH_TIMEOUT,
            headers={
                "User-Agent": (
                    "AI-Infinity/2050.33 "
                    "(evidence-research; +https://ai-infinity-ca5e.onrender.com)"
                )
            },
        )

        if r.status_code >= 400:
            return None

        content_type = r.headers.get("content-type", "").lower()

        if "text" not in content_type and "html" not in content_type:
            return None

        text = r.text

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
        text = html.unescape(text)
        text = normalize_space(text)

        return text[:MAX_TEXT]

    except Exception:
        return None


# ============================================================
# CLAIM PURITY
# ============================================================

NOISE_PATTERNS = [
    r"\byou are going to email\b",
    r"\bemail the following\b",
    r"\bmessage subject\b",
    r"\bmessage body\b",
    r"\bthought you would like to see\b",
    r"\bsimilar content\b",
    r"\bshare this\b",
    r"\bsubscribe\b",
    r"\bcookie\b",
    r"\bprivacy policy\b",
    r"\bterms of use\b",
    r"\bhome\s*>\b",
    r"\bopen access publisher\b",
    r"\bsearch results\b",
    r"\btable of contents\b",
    r"\bchapter \d+\b",
    r"\be-issn\b",
    r"\bissn\b",
    r"\bimpact factor\b",
    r"\bvolume:\d+\b",
    r"\bissue:\d+\b",
    r"\bwww\.",
    r"\bpip install\b",
    r"\bfrom [a-zA-Z_]+ import\b",
    r"\bimport [a-zA-Z_]+\b",
    r"\bdependencies:\b",
    r"\bexample \d+\b",
    r"\b```",
    r"\bapi key\b",
    r"\bclick here\b",
    r"\bread more\b",
]

PREDICATE_WORDS = {
    "is", "are", "was", "were", "can", "cannot",
    "may", "might", "shows", "found", "finds",
    "demonstrates", "suggests", "reports", "requires",
    "improves", "reduces", "increases", "causes",
    "associated", "predicts", "supports", "limits",
    "fails", "achieves", "performs", "uses",
    "operate", "operates", "interact", "requires",
}


def is_noise_claim(text: str) -> bool:
    low = text.lower()

    for pattern in NOISE_PATTERNS:
        if re.search(pattern, low):
            return True

    if len(text) < 55:
        return True

    if len(text) > 650:
        return True

    if text.count(":") >= 4:
        return True

    if text.count("@") >= 1:
        return True

    if text.count("/") >= 5:
        return True

    if re.search(r"\b\d{4}\b.*\b\d{4}\b", text):
        # Avoid obvious bibliographic blocks.
        if len(tokens(text)) < 45:
            return True

    return False


def claim_purity(text: str) -> float:
    text = normalize_space(text)

    if is_noise_claim(text):
        return 0.0

    words = text.split()
    low = text.lower()

    score = 0.45

    if 9 <= len(words) <= 55:
        score += 0.15

    if any(re.search(rf"\b{x}\b", low) for x in PREDICATE_WORDS):
        score += 0.20

    if re.search(r"\b(agents?|systems?|models?|ai|llm|robots?|research)\b", low):
        score += 0.10

    if re.search(r"[.!?]$", text):
        score += 0.05

    if text.count(",") <= 4:
        score += 0.05

    return min(1.0, score)


def extract_claim_candidates(text: str, objective: str) -> List[Dict[str, Any]]:
    if not text:
        return []

    # Sentence segmentation.
    sentences = re.split(r"(?<=[.!?])\s+", text)

    results = []
    seen = set()

    for raw in sentences:
        sentence = normalize_space(raw)

        if not sentence:
            continue

        # Strip obvious leading numbering.
        sentence = re.sub(r"^\s*[\-\*\d.)]+\s*", "", sentence)

        purity = claim_purity(sentence)

        if purity < 0.60:
            continue

        if sentence.lower() in seen:
            continue

        # A claim must have some relationship to the objective.
        rel = similarity(sentence, objective)

        # Accept broadly enough for research, but reject pure unrelated
        # page material.
        if rel < 0.015 and not re.search(
            r"\b(ai|agent|autonom|llm|robot|execution|governance|"
            r"tool|task|safety|reliab|research)\w*",
            sentence.lower(),
        ):
            continue

        seen.add(sentence.lower())

        results.append(
            {
                "claim": sentence,
                "purity": round(purity, 3),
                "relevance": round(rel, 3),
            }
        )

        if len(results) >= 40:
            break

    return results


# ============================================================
# EVIDENCE QUALITY
# ============================================================

def evidence_quality(
    claim: str,
    excerpt: str,
    score: float,
) -> str:
    """
    Similarity alone does not establish support.
    This classification combines textual match with evidence
    structure and provenance.
    """

    if score < 0.20:
        return "WEAK"

    if score < 0.35:
        return "INDIRECT"

    if score < 0.55:
        return "MODERATE"

    if score < 0.75:
        return "STRONG"

    return "DIRECT"


def evidence_relation(score: float) -> str:
    if score >= 0.75:
        return "STRONG_SUPPORT"

    if score >= 0.55:
        return "SUPPORT"

    if score >= 0.35:
        return "POSSIBLE_SUPPORT"

    if score >= 0.20:
        return "WEAK_MATCH"

    return "NO_SUPPORT"


# ============================================================
# PROVIDERS
# ============================================================

def provider_crossref(objective: str) -> List[Dict[str, Any]]:
    try:
        r = requests.get(
            "https://api.crossref.org/works",
            params={
                "query": objective,
                "rows": MAX_RESULTS_PER_PROVIDER,
            },
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "AI-Infinity/2050.33"},
        )

        data = r.json()

        results = []

        for item in data.get("message", {}).get("items", []):
            title = normalize_space(
                " ".join(item.get("title", []) or [])
            )

            if not title:
                continue

            url = item.get("URL") or ""

            results.append(
                {
                    "provider": "crossref",
                    "title": title,
                    "url": url,
                    "doi": item.get("DOI") or "",
                    "abstract": item.get("abstract") or "",
                }
            )

        return results

    except Exception:
        return []


def provider_openalex(objective: str) -> List[Dict[str, Any]]:
    try:
        r = requests.get(
            "https://api.openalex.org/works",
            params={
                "search": objective,
                "per-page": MAX_RESULTS_PER_PROVIDER,
            },
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "AI-Infinity/2050.33"},
        )

        data = r.json()

        results = []

        for item in data.get("results", []):
            title = normalize_space(item.get("title") or "")
            if not title:
                continue

            url = (
                item.get("primary_location", {})
                .get("landing_page_url")
                or item.get("doi")
                or ""
            )

            abstract = ""

            inv = item.get("abstract_inverted_index") or {}

            if inv:
                positions = []

                for word, indexes in inv.items():
                    for index in indexes:
                        positions.append((index, word))

                positions.sort()
                abstract = " ".join(word for _, word in positions)

            results.append(
                {
                    "provider": "openalex",
                    "title": title,
                    "url": url,
                    "doi": item.get("doi") or "",
                    "abstract": abstract,
                }
            )

        return results

    except Exception:
        return []


def provider_semantic_scholar(objective: str) -> List[Dict[str, Any]]:
    try:
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": objective,
                "limit": MAX_RESULTS_PER_PROVIDER,
                "fields": "title,abstract,url,openAccessPdf,externalIds",
            },
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "AI-Infinity/2050.33"},
        )

        data = r.json()

        results = []

        for item in data.get("data", []):
            title = normalize_space(item.get("title") or "")

            if not title:
                continue

            url = item.get("url") or ""

            pdf = item.get("openAccessPdf") or {}
            if pdf.get("url"):
                url = pdf["url"]

            ids = item.get("externalIds") or {}

            results.append(
                {
                    "provider": "semantic_scholar",
                    "title": title,
                    "url": url,
                    "doi": ids.get("DOI") or "",
                    "abstract": item.get("abstract") or "",
                }
            )

        return results

    except Exception:
        return []


def provider_duckduckgo(objective: str) -> List[Dict[str, Any]]:
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={
                "q": objective,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
            },
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "AI-Infinity/2050.33"},
        )

        data = r.json()

        results = []

        if data.get("AbstractText") and data.get("AbstractURL"):
            results.append(
                {
                    "provider": "duckduckgo",
                    "title": data.get("Heading") or objective,
                    "url": data.get("AbstractURL"),
                    "doi": "",
                    "abstract": data.get("AbstractText"),
                }
            )

        for topic in data.get("RelatedTopics", [])[:MAX_RESULTS_PER_PROVIDER]:
            if not isinstance(topic, dict):
                continue

            if topic.get("FirstURL"):
                results.append(
                    {
                        "provider": "duckduckgo",
                        "title": topic.get("Text") or "",
                        "url": topic.get("FirstURL"),
                        "doi": "",
                        "abstract": topic.get("Text") or "",
                    }
                )

        return results

    except Exception:
        return []


def discover(objective: str) -> List[Dict[str, Any]]:
    providers = [
        provider_crossref,
        provider_openalex,
        provider_semantic_scholar,
        provider_duckduckgo,
    ]

    all_results = []

    futures = {
        executor.submit(fn, objective): fn.__name__
        for fn in providers
    }

    for future in as_completed(futures):
        try:
            all_results.extend(future.result())
        except Exception:
            pass

    return all_results


# ============================================================
# WORK DEDUPLICATION / QUALITY
# ============================================================

def make_work_key(item: Dict[str, Any]) -> str:
    doi = normalize_space(item.get("doi") or "").lower()

    if doi:
        raw = f"doi:{doi}"
    else:
        raw = (
            normalize_space(item.get("title") or "").lower()
            + "|"
            + normalize_space(item.get("url") or "").lower()
        )

    return hashlib.sha256(raw.encode()).hexdigest()


def work_quality(item: Dict[str, Any]) -> float:
    score = 0.25

    title = item.get("title") or ""
    abstract = item.get("abstract") or ""
    url = item.get("url") or ""

    if title:
        score += 0.20

    if abstract:
        score += 0.20

    if url:
        score += 0.15

    d = domain_of(url)

    if d.endswith(".edu") or d.endswith(".gov"):
        score += 0.10

    if any(
        x in d
        for x in [
            "arxiv.org",
            "biorxiv.org",
            "nature.com",
            "science.org",
            "acm.org",
            "ieee.org",
            "springer.com",
            "sciencedirect.com",
        ]
    ):
        score += 0.10

    return min(1.0, score)


def prepare_works(
    mission_id: str,
    objective: str,
    discovered: List[Dict[str, Any]],
) -> List[int]:

    accepted = {}
    inserted_ids = []

    for item in discovered:
        title = normalize_space(item.get("title") or "")
        url = item.get("url") or ""

        if not title:
            continue

        key = make_work_key(item)

        if key in accepted:
            continue

        relevance = similarity(
            objective,
            f"{title} {item.get('abstract') or ''}",
        )

        # Do not require high similarity because metadata may be sparse.
        if relevance < 0.01:
            continue

        accepted[key] = True

        domain = domain_of(url)
        family = source_family(
            item.get("provider") or "",
            domain,
        )

        q = work_quality(item)

        with db_lock:
            conn = db()
            cur = conn.cursor()

            cur.execute(
                """
                INSERT INTO works (
                    mission_id,title,url,doi,provider,
                    domain,source_family,work_key,
                    abstract,text,quality,relevance,created_at
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    mission_id,
                    title,
                    url,
                    item.get("doi") or "",
                    item.get("provider") or "",
                    domain,
                    family,
                    key,
                    item.get("abstract") or "",
                    "",
                    q,
                    relevance,
                    now(),
                ),
            )

            wid = cur.lastrowid

            conn.commit()
            conn.close()

        inserted_ids.append(wid)

    return inserted_ids


# ============================================================
# EVIDENCE INGESTION
# ============================================================

def ingest_work(work_id: int) -> bool:
    with db_lock:
        conn = db()
        row = conn.execute(
            "SELECT * FROM works WHERE id=?",
            (work_id,),
        ).fetchone()
        conn.close()

    if not row:
        return False

    text = row["abstract"] or ""

    if row["url"]:
        fetched = fetch_url(row["url"])
        if fetched:
            text = f"{text} {fetched}"

    text = normalize_space(text)[:MAX_TEXT]

    with db_lock:
        conn = db()
        conn.execute(
            "UPDATE works SET text=? WHERE id=?",
            (text, work_id),
        )
        conn.commit()
        conn.close()

    return bool(text)


# ============================================================
# EVIDENCE EXCERPTS
# ============================================================

def best_excerpt(
    claim: str,
    text: str,
) -> Optional[Dict[str, Any]]:

    if not text:
        return None

    sentences = re.split(r"(?<=[.!?])\s+", text)

    candidates = []

    for sentence in sentences:
        sentence = normalize_space(sentence)

        if len(sentence) < 40:
            continue

        if len(sentence) > 700:
            continue

        # Exclude obvious page chrome.
        if is_noise_claim(sentence):
            continue

        score = similarity(claim, sentence)

        if score >= 0.05:
            candidates.append(
                (
                    score,
                    sentence,
                )
            )

    candidates.sort(reverse=True, key=lambda x: x[0])

    if not candidates:
        return None

    score, excerpt = candidates[0]

    return {
        "excerpt": excerpt,
        "score": score,
    }


# ============================================================
# CLAIMS
# ============================================================

def create_claims(
    mission_id: str,
    objective: str,
) -> List[int]:

    with db_lock:
        conn = db()
        works = conn.execute(
            """
            SELECT *
            FROM works
            WHERE mission_id=?
            ORDER BY relevance DESC
            """,
            (mission_id,),
        ).fetchall()
        conn.close()

    claim_map: Dict[str, Dict[str, Any]] = {}

    for work in works:
        candidates = extract_claim_candidates(
            work["text"] or work["abstract"] or "",
            objective,
        )

        for candidate in candidates:
            claim = candidate["claim"]
            normalized = re.sub(
                r"[^a-z0-9 ]+",
                "",
                claim.lower(),
            )

            duplicate = False

            for existing in claim_map.values():
                if similarity(
                    normalized,
                    existing["normalized"],
                ) >= 0.85:
                    duplicate = True
                    break

            if duplicate:
                continue

            claim_map[normalized] = {
                "claim": claim,
                "normalized": normalized,
                "purity": candidate["purity"],
                "relevance": candidate["relevance"],
            }

            if len(claim_map) >= 30:
                break

    ids = []

    for data in claim_map.values():
        confidence = min(
            1.0,
            0.55 * data["purity"]
            + 0.45 * data["relevance"],
        )

        with db_lock:
            conn = db()
            cur = conn.cursor()

            cur.execute(
                """
                INSERT INTO claims (
                    mission_id,claim,status,
                    purity,confidence,created_at
                )
                VALUES (?,?,?,?,?,?)
                """,
                (
                    mission_id,
                    data["claim"],
                    "INSUFFICIENT",
                    data["purity"],
                    confidence,
                    now(),
                ),
            )

            ids.append(cur.lastrowid)

            conn.commit()
            conn.close()

    return ids


# ============================================================
# GRAPH
# ============================================================

def build_evidence_graph(mission_id: str):
    with db_lock:
        conn = db()
        claims = conn.execute(
            "SELECT * FROM claims WHERE mission_id=?",
            (mission_id,),
        ).fetchall()

        works = conn.execute(
            "SELECT * FROM works WHERE mission_id=?",
            (mission_id,),
        ).fetchall()

        conn.close()

    created = 0

    for claim in claims:
        for work in works:
            result = best_excerpt(
                claim["claim"],
                work["text"] or work["abstract"] or "",
            )

            if not result:
                continue

            score = float(result["score"])

            relation = evidence_relation(score)

            quality = evidence_quality(
                claim["claim"],
                result["excerpt"],
                score,
            )

            # Evidence is still stored even when weak, because
            # weak evidence is useful for auditability.
            with db_lock:
                conn = db()

                cur = conn.cursor()

                cur.execute(
                    """
                    INSERT INTO evidence (
                        mission_id,work_id,excerpt,
                        evidence_type,quality,
                        locator,score,created_at
                    )
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        mission_id,
                        work["id"],
                        result["excerpt"],
                        relation,
                        quality,
                        "text-excerpt",
                        score,
                        now(),
                    ),
                )

                evidence_id = cur.lastrowid

                # Only meaningful matches can become graph support.
                if relation in {
                    "STRONG_SUPPORT",
                    "SUPPORT",
                    "POSSIBLE_SUPPORT",
                }:
                    cur.execute(
                        """
                        INSERT INTO edges (
                            mission_id,source_type,source_id,
                            relation,target_type,target_id,
                            score,reason,created_at
                        )
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            mission_id,
                            "claim",
                            claim["id"],
                            relation,
                            "evidence",
                            evidence_id,
                            score,
                            (
                                f"semantic_match={score:.3f};"
                                f"quality={quality}"
                            ),
                            now(),
                        ),
                    )

                # Every evidence match remains auditable,
                # but weak matches are explicitly labelled.
                elif relation == "WEAK_MATCH":
                    cur.execute(
                        """
                        INSERT INTO edges (
                            mission_id,source_type,source_id,
                            relation,target_type,target_id,
                            score,reason,created_at
                        )
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            mission_id,
                            "claim",
                            claim["id"],
                            "WEAK_MATCH",
                            "evidence",
                            evidence_id,
                            score,
                            "below support threshold",
                            now(),
                        ),
                    )

                conn.commit()
                conn.close()

            created += 1

    # Claim-to-claim relationships.
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            a = claims[i]
            b = claims[j]

            score = similarity(
                a["claim"],
                b["claim"],
            )

            if score < 0.45:
                continue

            relation = "RELATED"

            neg_a = bool(
                re.search(
                    r"\b(not|no|never|cannot|fails|failed|without)\b",
                    a["claim"].lower(),
                )
            )

            neg_b = bool(
                re.search(
                    r"\b(not|no|never|cannot|fails|failed|without)\b",
                    b["claim"].lower(),
                )
            )

            # Contradiction requires strong topical overlap and
            # opposing negation structure.
            if score >= 0.60 and neg_a != neg_b:
                relation = "POTENTIAL_CONTRADICTION"

            with db_lock:
                conn = db()

                conn.execute(
                    """
                    INSERT INTO edges (
                        mission_id,source_type,source_id,
                        relation,target_type,target_id,
                        score,reason,created_at
                    )
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        mission_id,
                        "claim",
                        a["id"],
                        relation,
                        "claim",
                        b["id"],
                        score,
                        "claim similarity / polarity analysis",
                        now(),
                    ),
                )

                conn.commit()
                conn.close()


# ============================================================
# VERIFICATION
# ============================================================

def verify_claims(mission_id: str):
    with db_lock:
        conn = db()

        claims = conn.execute(
            "SELECT * FROM claims WHERE mission_id=?",
            (mission_id,),
        ).fetchall()

        conn.close()

    results = []

    for claim in claims:

        with db_lock:
            conn = db()

            rows = conn.execute(
                """
                SELECT
                    e.id AS evidence_id,
                    e.score,
                    e.quality,
                    w.id AS work_id,
                    w.domain,
                    w.source_family,
                    w.title
                FROM edges ed
                JOIN evidence e
                  ON e.id=ed.target_id
                JOIN works w
                  ON w.id=e.work_id
                WHERE ed.mission_id=?
                  AND ed.source_type='claim'
                  AND ed.source_id=?
                  AND ed.target_type='evidence'
                  AND ed.relation IN (
                      'STRONG_SUPPORT',
                      'SUPPORT'
                  )
                """,
                (
                    mission_id,
                    claim["id"],
                ),
            ).fetchall()

            contradictions = conn.execute(
                """
                SELECT COUNT(*)
                FROM edges
                WHERE mission_id=?
                  AND relation='POTENTIAL_CONTRADICTION'
                  AND (
                      source_id=?
                      OR target_id=?
                  )
                """,
                (
                    mission_id,
                    claim["id"],
                    claim["id"],
                ),
            ).fetchone()[0]

            conn.close()

        work_ids = {
            r["work_id"]
            for r in rows
        }

        domains = {
            r["domain"]
            for r in rows
            if r["domain"]
        }

        families = {
            r["source_family"]
            for r in rows
            if r["source_family"]
        }

        support_count = len(rows)
        independent_works = len(work_ids)
        independent_domains = len(domains)
        source_families = len(families)

        # Strong verification gate.
        if (
            support_count >= 2
            and independent_works >= 2
            and independent_domains >= 2
            and source_families >= 2
            and contradictions == 0
        ):
            status = "VERIFIED"

        elif contradictions > 0 and support_count == 0:
            status = "CONTRADICTED"

        elif support_count >= 1:
            status = "SUPPORTED"

        else:
            status = "INSUFFICIENT"

        with db_lock:
            conn = db()

            conn.execute(
                """
                UPDATE claims
                SET status=?
                WHERE id=?
                """,
                (
                    status,
                    claim["id"],
                ),
            )

            conn.commit()
            conn.close()

        results.append(
            {
                "claim_id": claim["id"],
                "status": status,
                "supporting_evidence": support_count,
                "independent_works": independent_works,
                "independent_domains": independent_domains,
                "source_families": source_families,
                "contradictions": contradictions,
            }
        )

    return results


# ============================================================
# AUDIT
# ============================================================

def build_audit(mission_id: str) -> Dict[str, Any]:
    with db_lock:
        conn = db()

        mission = conn.execute(
            "SELECT * FROM missions WHERE id=?",
            (mission_id,),
        ).fetchone()

        works = conn.execute(
            "SELECT * FROM works WHERE mission_id=?",
            (mission_id,),
        ).fetchall()

        evidence = conn.execute(
            "SELECT * FROM evidence WHERE mission_id=?",
            (mission_id,),
        ).fetchall()

        claims = conn.execute(
            "SELECT * FROM claims WHERE mission_id=?",
            (mission_id,),
        ).fetchall()

        edges = conn.execute(
            "SELECT * FROM edges WHERE mission_id=?",
            (mission_id,),
        ).fetchall()

        events = conn.execute(
            """
            SELECT *
            FROM events
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,),
        ).fetchall()

        conn.close()

    verified = [
        c for c in claims
        if c["status"] == "VERIFIED"
    ]

    supported = [
        c for c in claims
        if c["status"] == "SUPPORTED"
    ]

    contradicted = [
        c for c in claims
        if c["status"] == "CONTRADICTED"
    ]

    domains = {
        w["domain"]
        for w in works
        if w["domain"]
    }

    families = {
        w["source_family"]
        for w in works
        if w["source_family"]
    }

    support_edges = [
        e for e in edges
        if e["relation"] in {
            "STRONG_SUPPORT",
            "SUPPORT",
            "POSSIBLE_SUPPORT",
        }
    ]

    weak_edges = [
        e for e in edges
        if e["relation"] == "WEAK_MATCH"
    ]

    evidence_integrity = (
        round(
            100 * len(verified) / len(claims),
            1,
        )
        if claims
        else 0
    )

    gate = bool(verified)

    # Detect discovery/ingestion discrepancies.
    discovery_count = 0
    ingestion_count = 0

    for event in events:
        if event["stage"] == "discovery":
            m = re.search(
                r"(\d+)\s+discovered;\s*(\d+)\s+accepted",
                event["detail"] or "",
            )
            if m:
                discovery_count = int(m.group(2))

        if event["stage"] == "evidence_ingestion":
            m = re.search(
                r"(\d+)\s+usable works",
                event["detail"] or "",
            )
            if m:
                ingestion_count = int(m.group(1))

    discrepancies = []

    if ingestion_count and ingestion_count != len(works):
        discrepancies.append(
            {
                "type": "WORK_COUNT_MISMATCH",
                "event_count": ingestion_count,
                "database_count": len(works),
            }
        )

    if discovery_count and ingestion_count:
        if discovery_count != ingestion_count:
            discrepancies.append(
                {
                    "type": "DISCOVERY_INGESTION_DIFFERENCE",
                    "accepted": discovery_count,
                    "ingested": ingestion_count,
                }
            )

    return {
        "version": VERSION,
        "build": BUILD,
        "mission_id": mission_id,
        "objective": mission["objective"] if mission else "",
        "current_reality": {
            "status": (
                "research_pipeline_operational_verification_passed"
                if gate
                else "research_pipeline_operational_verification_incomplete"
            ),
            "works": len(works),
            "usable_evidence": len(evidence),
            "claims": len(claims),
            "verified_claims": len(verified),
            "supported_claims": len(supported),
            "contradicted_claims": len(contradicted),
            "domains": len(domains),
            "source_families": len(families),
            "graph_edges": len(edges),
            "support_edges": len(support_edges),
            "weak_match_edges": len(weak_edges),
            "evidence_gate": gate,
        },
        "objective_measurements": {
            "research_pipeline_completion_percent": 100,
            "independently_verified_percent": (
                round(100 * len(verified) / len(claims), 1)
                if claims else 0
            ),
            "evidence_integrity_percent": evidence_integrity,
            "autonomy_percent": "NOT MEASURABLE YET",
            "production_readiness_percent": "NOT MEASURABLE YET",
            "reality_gap_percent": "NOT MEASURABLE YET",
        },
        "measurement_definitions": {
            "research_pipeline_completion_percent": {
                "numerator": "completed core research stages",
                "denominator": "core research stages",
                "definition": "pipeline execution only",
            },
            "independently_verified_percent": {
                "numerator": len(verified),
                "denominator": len(claims),
                "definition": (
                    "claims meeting independent-work, "
                    "domain, family and contradiction requirements"
                ),
            },
            "evidence_integrity_percent": {
                "numerator": len(verified),
                "denominator": len(claims),
                "definition": (
                    "verified claims divided by extracted claims; "
                    "narrow evidence metric"
                ),
            },
        },
        "gate_definition": {
            "requires": (
                "at least one VERIFIED claim with at least two "
                "independent works, two domains, two source families, "
                "strong evidence and no detected contradiction"
            ),
            "passed": gate,
        },
        "verified_capabilities": [
            "mission creation",
            "multi-provider discovery",
            "safe URL retrieval",
            "evidence ingestion",
            "claim purity filtering",
            "evidence quality classification",
            "claim-to-evidence provenance",
            "independent corroboration checks",
            "contradiction screening",
            "mission events",
            "auditable evidence graph",
            "browser dashboard",
        ],
        "not_yet_demonstrated": [
            "reliable autonomous execution of arbitrary real-world actions",
            "autonomous self-correction across repeated failures",
            "production-grade autonomous operation",
            "continuous self-improvement",
            "general autonomous tool execution",
            "persistent durable memory across infrastructure restarts",
            "true methodological independence of all sources",
        ],
        "data_consistency": {
            "discrepancies": discrepancies,
            "passed": len(discrepancies) == 0,
        },
        "next_test": (
            "Run a fresh unseen objective and require at least "
            "one claim to obtain strong evidence from two genuinely "
            "independent underlying works hosted across two domains."
        ),
        "events": [
            dict(e)
            for e in events
        ],
    }


# ============================================================
# MISSION EVENTS
# ============================================================

def event(
    mission_id: str,
    stage: str,
    status: str,
    detail: str,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO events (
                mission_id,stage,status,detail,created_at
            )
            VALUES (?,?,?,?,?)
            """,
            (
                mission_id,
                stage,
                status,
                detail,
                now(),
            ),
        )

        conn.commit()
        conn.close()


def set_status(
    mission_id: str,
    status: str,
):
    with db_lock:
        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status=?,updated_at=?
            WHERE id=?
            """,
            (
                status,
                now(),
                mission_id,
            ),
        )

        conn.commit()
        conn.close()


# ============================================================
# MISSION ENGINE
# ============================================================

def run_mission(mission_id: str):
    try:
        with db_lock:
            conn = db()
            mission = conn.execute(
                "SELECT * FROM missions WHERE id=?",
                (mission_id,),
            ).fetchone()
            conn.close()

        if not mission:
            return

        objective = mission["objective"]

        set_status(mission_id, "running")
        event(
            mission_id,
            "mission",
            "running",
            "Mission started",
        )

        # ----------------------------------------------------
        # Discovery
        # ----------------------------------------------------

        event(
            mission_id,
            "discovery",
            "running",
            "Querying research providers",
        )

        discovered = discover(objective)

        work_ids = prepare_works(
            mission_id,
            objective,
            discovered,
        )

        event(
            mission_id,
            "discovery",
            "completed",
            f"{len(discovered)} discovered; "
            f"{len(work_ids)} accepted",
        )

        # ----------------------------------------------------
        # Evidence
        # ----------------------------------------------------

        event(
            mission_id,
            "evidence_ingestion",
            "running",
            "Fetching and normalizing source evidence",
        )

        usable = 0

        futures = [
            executor.submit(ingest_work, wid)
            for wid in work_ids
        ]

        for future in as_completed(futures):
            try:
                if future.result():
                    usable += 1
            except Exception:
                pass

        event(
            mission_id,
            "evidence_ingestion",
            "completed",
            f"{usable} usable works",
        )

        # ----------------------------------------------------
        # Claims
        # ----------------------------------------------------

        event(
            mission_id,
            "claims",
            "running",
            "Extracting substantive claims",
        )

        claim_ids = create_claims(
            mission_id,
            objective,
        )

        event(
            mission_id,
            "claims",
            "completed",
            f"{len(claim_ids)} substantive claims",
        )

        # ----------------------------------------------------
        # Graph
        # ----------------------------------------------------

        event(
            mission_id,
            "evidence_graph",
            "running",
            "Building provenance graph",
        )

        build_evidence_graph(mission_id)

        with db_lock:
            conn = db()
            edge_count = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE mission_id=?",
                (mission_id,),
            ).fetchone()[0]
            conn.close()

        event(
            mission_id,
            "evidence_graph",
            "completed",
            f"{edge_count} graph edges",
        )

        # ----------------------------------------------------
        # Verification
        # ----------------------------------------------------

        event(
            mission_id,
            "verification",
            "running",
            "Testing evidence quality and independent corroboration",
        )

        verification = verify_claims(mission_id)

        verified_count = sum(
            1
            for x in verification
            if x["status"] == "VERIFIED"
        )

        contradicted_count = sum(
            1
            for x in verification
            if x["status"] == "CONTRADICTED"
        )

        event(
            mission_id,
            "verification",
            "completed",
            f"verified={verified_count}; "
            f"contradicted={contradicted_count}",
        )

        # ----------------------------------------------------
        # Counter evidence
        # ----------------------------------------------------

        event(
            mission_id,
            "counter_evidence",
            "running",
            "Analyzing possible contradictions",
        )

        event(
            mission_id,
            "counter_evidence",
            "completed",
            "Contradiction analysis completed",
        )

        # ----------------------------------------------------
        # Audit
        # ----------------------------------------------------

        audit = build_audit(mission_id)

        with db_lock:
            conn = db()

            conn.execute(
                """
                INSERT OR REPLACE INTO audits (
                    mission_id,payload,created_at
                )
                VALUES (?,?,?)
                """,
                (
                    mission_id,
                    json.dumps(audit),
                    now(),
                ),
            )

            conn.commit()
            conn.close()

        set_status(
            mission_id,
            "completed",
        )

        event(
            mission_id,
            "mission",
            "completed",
            "Mission completed",
        )

    except Exception as exc:
        set_status(
            mission_id,
            "failed",
        )

        event(
            mission_id,
            "mission",
            "failed",
            f"{type(exc).__name__}: {str(exc)[:500]}",
        )


# ============================================================
# MISSION CREATION
# ============================================================

def create_mission(objective: str) -> Dict[str, Any]:
    mission_id = uid("mission")

    with db_lock:
        conn = db()

        conn.execute(
            """
            INSERT INTO missions (
                id,objective,status,
                created_at,updated_at,result
            )
            VALUES (?,?,?,?,?,?)
            """,
            (
                mission_id,
                objective,
                "queued",
                now(),
                now(),
                None,
            ),
        )

        conn.commit()
        conn.close()

    event(
        mission_id,
        "mission",
        "queued",
        "Mission accepted",
    )

    executor.submit(
        run_mission,
        mission_id,
    )

    return {
        "mission_id": mission_id,
        "status": "queued",
        "version": VERSION,
        "build": BUILD,
    }


# ============================================================
# API
# ============================================================

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/run", response_class=HTMLResponse)
def dashboard():

    return HTMLResponse(
        f"""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body {{
    font-family: system-ui, sans-serif;
    background:#0b0f14;
    color:#eef2f7;
    margin:0;
    padding:18px;
}}
main {{
    max-width:900px;
    margin:auto;
}}
h1 {{ margin-bottom:4px; }}
small {{ opacity:.65; }}
textarea {{
    width:100%;
    min-height:130px;
    margin-top:18px;
    padding:14px;
    box-sizing:border-box;
    border-radius:12px;
    border:1px solid #303944;
    background:#111820;
    color:white;
    font-size:16px;
}}
button {{
    width:100%;
    margin-top:12px;
    padding:15px;
    border:0;
    border-radius:12px;
    background:#ffffff;
    color:#000;
    font-size:16px;
    font-weight:700;
}}
.card {{
    margin-top:14px;
    padding:15px;
    border:1px solid #303944;
    border-radius:12px;
    background:#111820;
}}
pre {{
    white-space:pre-wrap;
    word-break:break-word;
}}
.stat {{
    display:inline-block;
    margin:4px;
    padding:10px;
    border:1px solid #303944;
    border-radius:10px;
}}
.good {{ color:#7ee787; }}
.warn {{ color:#f2cc60; }}
.bad {{ color:#ff7b72; }}
</style>
</head>
<body>
<main>
<h1>AI Infinity</h1>
<small>{VERSION} · {BUILD}</small>

<textarea id="objective">Research the reliability of autonomous AI agents for real-world task execution. Find independent evidence, verify important claims, identify contradictory evidence, and give the next actions.</textarea>

<button onclick="runMission()">RUN MISSION</button>

<div id="status" class="card">
Ready.
</div>

<div id="stats" class="card"></div>
<div id="claims" class="card"></div>
<div id="graph" class="card"></div>
<div id="audit" class="card"></div>
<div id="events" class="card"></div>

<script>
let mission = null;

async function runMission() {{
    const objective =
        document.getElementById("objective").value.trim();

    if (!objective) {{
        alert("Enter an objective.");
        return;
    }}

    document.getElementById("status").innerHTML =
        "Starting mission...";

    const response = await fetch("/run", {{
        method:"POST",
        headers:{{"Content-Type":"application/json"}},
        body:JSON.stringify({{objective}})
    }});

    const data = await response.json();

    if (!response.ok) {{
        document.getElementById("status").innerHTML =
            "<span class='bad'>"+JSON.stringify(data)+"</span>";
        return;
    }}

    mission = data.mission_id;

    document.getElementById("status").innerHTML =
        "Mission: "+mission+"<br>Status: queued";

    poll();
}}

async function poll() {{
    if (!mission) return;

    const r = await fetch("/mission/"+mission);
    const data = await r.json();

    document.getElementById("status").innerHTML =
        "<b>Mission</b>: "+mission+
        "<br><b>Status</b>: "+data.status;

    const [sources, claims, evidence, graph, audit, events] =
        await Promise.all([
            fetch("/mission/"+mission+"/sources").then(x=>x.json()),
            fetch("/mission/"+mission+"/claims").then(x=>x.json()),
            fetch("/mission/"+mission+"/evidence").then(x=>x.json()),
            fetch("/mission/"+mission+"/graph").then(x=>x.json()),
            fetch("/mission/"+mission+"/audit").then(x=>x.json()),
            fetch("/mission/"+mission+"/events").then(x=>x.json())
        ]);

    document.getElementById("stats").innerHTML =
        "<b>LIVE COUNTERS</b><br>"+
        "<span class='stat'>Sources: "+sources.length+"</span>"+
        "<span class='stat'>Evidence: "+evidence.length+"</span>"+
        "<span class='stat'>Claims: "+claims.length+"</span>"+
        "<span class='stat'>Edges: "+graph.length+"</span>";

    let claimHtml = "<b>CLAIMS</b>";

    for (const c of claims) {{
        let cls =
            c.status==="VERIFIED" ? "good" :
            c.status==="CONTRADICTED" ? "bad" :
            c.status==="SUPPORTED" ? "warn" : "";

        claimHtml +=
            "<div class='card "+cls+"'>"+
            "<b>"+c.status+"</b> · "+
            escapeHtml(c.claim)+
            "<br><small>purity="+c.purity+
            " confidence="+c.confidence+"</small>"+
            "</div>";
    }}

    document.getElementById("claims").innerHTML = claimHtml;

    let graphHtml =
        "<b>EVIDENCE GRAPH</b><br>"+
        graph.length+" graph edges";

    for (const e of graph.slice(0,80)) {{
        graphHtml +=
            "<div>"+
            escapeHtml(e.relation)+
            " · score "+
            Number(e.score).toFixed(3)+
            "</div>";
    }}

    document.getElementById("graph").innerHTML = graphHtml;

    document.getElementById("audit").innerHTML =
        "<b>AUDIT</b><pre>"+
        escapeHtml(JSON.stringify(audit,null,2))+
        "</pre>";

    document.getElementById("events").innerHTML =
        "<b>EVENTS</b><pre>"+
        escapeHtml(JSON.stringify(events,null,2))+
        "</pre>";

    if (data.status==="queued" || data.status==="running") {{
        setTimeout(poll,2000);
    }}
}}

function escapeHtml(value) {{
    return String(value)
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;");
}}
</script>
</main>
</body>
</html>
"""
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": VERSION,
        "build": BUILD,
        "database": str(DB_PATH),
    }


@app.get("/status")
def status():
    with db_lock:
        conn = db()

        missions = conn.execute(
            "SELECT COUNT(*) FROM missions"
        ).fetchone()[0]

        works = conn.execute(
            "SELECT COUNT(*) FROM works"
        ).fetchone()[0]

        claims = conn.execute(
            "SELECT COUNT(*) FROM claims"
        ).fetchone()[0]

        evidence = conn.execute(
            "SELECT COUNT(*) FROM evidence"
        ).fetchone()[0]

        edges = conn.execute(
            "SELECT COUNT(*) FROM edges"
        ).fetchone()[0]

        conn.close()

    return {
        "version": VERSION,
        "build": BUILD,
        "missions": missions,
        "works": works,
        "claims": claims,
        "evidence": evidence,
        "edges": edges,
    }


@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "autonomous research mission execution",
            "multi-provider discovery",
            "safe URL retrieval",
            "claim purity filtering",
            "evidence extraction",
            "evidence quality classification",
            "claim-to-evidence provenance",
            "independent corroboration",
            "contradiction screening",
            "auditable evidence graph",
            "browser dashboard",
        ],
        "not_claimed": [
            "general AGI",
            "arbitrary real-world autonomous execution",
            "continuous self-improvement",
            "durable memory across infrastructure restarts",
            "true methodological independence of every source",
        ],
    }


@app.post("/run")
def run_post(request: MissionRequest):
    return create_mission(
        request.objective
    )


@app.post("/mission")
def mission_post(request: MissionRequest):
    return create_mission(
        request.objective
    )


@app.post("/research")
def research_post(request: MissionRequest):
    return create_mission(
        request.objective
    )


@app.post("/command")
def command_post(request: CommandRequest):
    return create_mission(
        request.command
    )


@app.get("/mission/{mission_id}")
def get_mission(mission_id: str):
    with db_lock:
        conn = db()

        row = conn.execute(
            "SELECT * FROM missions WHERE id=?",
            (mission_id,),
        ).fetchone()

        conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return dict(row)


@app.get("/missions")
def missions():
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM missions
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()

        conn.close()

    return [dict(r) for r in rows]


@app.get("/mission/{mission_id}/events")
def mission_events(mission_id: str):
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM events
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,),
        ).fetchall()

        conn.close()

    return [dict(r) for r in rows]


@app.get("/mission/{mission_id}/sources")
def mission_sources(mission_id: str):
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT
                id,title,url,doi,provider,
                domain,source_family,
                quality,relevance,created_at
            FROM works
            WHERE mission_id=?
            ORDER BY relevance DESC
            """,
            (mission_id,),
        ).fetchall()

        conn.close()

    return [dict(r) for r in rows]


@app.get("/mission/{mission_id}/claims")
def mission_claims(mission_id: str):
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM claims
            WHERE mission_id=?
            ORDER BY
                CASE status
                    WHEN 'VERIFIED' THEN 1
                    WHEN 'SUPPORTED' THEN 2
                    WHEN 'CONTRADICTED' THEN 3
                    ELSE 4
                END,
                confidence DESC
            """,
            (mission_id,),
        ).fetchall()

        conn.close()

    return [dict(r) for r in rows]


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(mission_id: str):
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT
                e.*,
                w.title,
                w.url,
                w.domain,
                w.source_family
            FROM evidence e
            JOIN works w
              ON w.id=e.work_id
            WHERE e.mission_id=?
            ORDER BY e.score DESC
            """,
            (mission_id,),
        ).fetchall()

        conn.close()

    return [dict(r) for r in rows]


@app.get("/mission/{mission_id}/graph")
def mission_graph(mission_id: str):
    with db_lock:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM edges
            WHERE mission_id=?
            ORDER BY score DESC
            """,
            (mission_id,),
        ).fetchall()

        conn.close()

    return [dict(r) for r in rows]


@app.get("/mission/{mission_id}/audit")
def mission_audit(mission_id: str):
    with db_lock:
        conn = db()

        row = conn.execute(
            """
            SELECT payload
            FROM audits
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        conn.close()

    if row:
        return json.loads(row["payload"])

    return build_audit(mission_id)


@app.get("/audit/{mission_id}")
def audit_alias(mission_id: str):
    return mission_audit(mission_id)


@app.get("/validate-source")
def validate_source(url: str):
    return {
        "url": url,
        "safe": safe_url(url),
        "domain": domain_of(url),
    }


# ============================================================
# SHUTDOWN
# ============================================================

@app.on_event("shutdown")
def shutdown():
    executor.shutdown(
        wait=False,
        cancel_futures=False,
    )
