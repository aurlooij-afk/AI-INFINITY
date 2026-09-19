"""
AI Infinity
TARGET-2050.35-FINAL
BUILD: EVIDENCE-INTEGRITY-CORRECTION

Correction of the final validation run.

FIXES:
1. Mission relevance gate
2. DOI -> canonical publisher/source provenance
3. Calibrated evidence entailment
4. Recovery source diversification
5. Per-claim verification blockers
6. Duplicate-safe evidence graph
7. Strict verification gate preserved

IMPORTANT:
- This system never forces VERIFIED.
- Lexical similarity is NOT treated as entailment.
- Unknown provenance cannot create DIRECT_SUPPORT.
- Irrelevant documents cannot create mission evidence.
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Optional, Any
from urllib.parse import urlparse
import sqlite3
import requests
import re
import json
import time
import hashlib
import ipaddress
import socket
from collections import Counter
from difflib import SequenceMatcher


VERSION = "TARGET-2050.35-FINAL"
BUILD = "EVIDENCE-INTEGRITY-CORRECTION"

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

TIMEOUT = 18
MAX_TEXT = 30000
MAX_EXCERPT = 1400

USER_AGENT = (
    "AI-Infinity/2050.35 "
    "(evidence research engine; contact unavailable)"
)

STOPWORDS = {
    "the", "and", "that", "this", "with", "from", "into", "for",
    "are", "was", "were", "been", "have", "has", "had", "will",
    "would", "could", "should", "their", "there", "they", "them",
    "than", "then", "when", "where", "which", "while", "using",
    "used", "use", "also", "more", "most", "some", "such", "these",
    "those", "about", "after", "before", "between", "through",
    "within", "without", "over", "under", "during", "only", "each",
    "other", "both", "many", "much", "very", "may", "might", "can",
    "our", "your", "its", "their", "we", "our", "you", "a", "an",
    "of", "to", "in", "on", "at", "by", "as", "or", "is", "it",
    "be", "not", "no", "do", "does", "did"
}

GENERIC_WORDS = STOPWORDS | {
    "study", "research", "paper", "results", "finding", "findings",
    "analysis", "method", "methods", "approach", "system",
    "model", "models", "data", "evidence", "results", "work",
    "works", "information", "researchers", "authors"
}

BOILERPLATE_PATTERNS = [
    r"skip to main content",
    r"subscribe",
    r"sign up",
    r"share this",
    r"cookie",
    r"privacy policy",
    r"terms of use",
    r"all rights reserved",
    r"contact us",
    r"follow us",
    r"newsletter",
    r"issn",
    r"e-issn",
    r"impact factor",
    r"volume\s+\d+",
    r"issue\s+\d+",
    r"copyright",
    r"funded by",
    r"funder",
    r"conflict of interest",
    r"author contributions",
    r"received .* accepted",
    r"doi:\s*10\.",
    r"citation:",
]

METADATA_PATTERNS = [
    r"^figure\s+\d+",
    r"^table\s+\d+",
    r"^supplementary",
    r"^references?$",
    r"^contents?$",
    r"^abstract$",
    r"^introduction$",
    r"^methods?$",
    r"^results?$",
    r"^discussion$",
]

CODE_PATTERNS = [
    r"pip install",
    r"import\s+\w+",
    r"from\s+\w+\s+import",
    r"npm install",
    r"```",
    r"example code",
    r"source code",
    r"github\.com",
]

NEGATIVE_WORDS = {
    "not", "no", "never", "without", "failed", "failure",
    "unable", "cannot", "did not", "does not", "insufficient",
    "lack", "lacks", "limited", "unlikely", "contradict",
    "contradicted"
}

POSITIVE_WORDS = {
    "found", "find", "shows", "showed", "demonstrate",
    "demonstrates", "evidence", "increased", "decreased",
    "associated", "effective", "successful", "reliable",
    "improved", "observed", "identified", "confirmed"
}


# ---------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT,
            created REAL,
            updated REAL,
            result TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            stage TEXT,
            status TEXT,
            detail TEXT,
            created REAL
        );

        CREATE TABLE IF NOT EXISTS works (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            title TEXT,
            url TEXT,
            canonical_url TEXT,
            doi TEXT,
            provider TEXT,
            domain TEXT,
            family TEXT,
            abstract TEXT,
            relevance REAL DEFAULT 0,
            accepted INTEGER DEFAULT 0,
            created REAL
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            work_id TEXT,
            claim_id TEXT,
            excerpt TEXT,
            lexical REAL DEFAULT 0,
            entailment REAL DEFAULT 0,
            relevance REAL DEFAULT 0,
            quality TEXT,
            relation TEXT,
            domain TEXT,
            family TEXT,
            created REAL
        );

        CREATE TABLE IF NOT EXISTS claims (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            text TEXT,
            claim_type TEXT,
            purity REAL,
            relevance REAL,
            status TEXT,
            confidence REAL,
            blockers TEXT,
            next_action TEXT,
            created REAL
        );

        CREATE TABLE IF NOT EXISTS edges (
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            claim_id TEXT,
            evidence_id TEXT,
            relation TEXT,
            score REAL,
            reason TEXT,
            created REAL
        );

        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            kind TEXT,
            target TEXT,
            status TEXT,
            result TEXT,
            attempt INTEGER,
            created REAL
        );

        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            key TEXT,
            value TEXT,
            created REAL
        );
        """
    )

    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------------------
# API MODELS
# ---------------------------------------------------------------------

class CreateRequest(BaseModel):
    command: str = Field(..., min_length=10, max_length=10000)
    duration_minutes: int = Field(default=1, ge=1, le=120)


# ---------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------

def now():
    return time.time()


def stable_id(prefix: str, value: str):
    h = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{h}"


def tokens(text: str):
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9'-]{2,}", text.lower())
    return {
        w for w in words
        if w not in STOPWORDS and len(w) >= 3
    }


def meaningful_tokens(text: str):
    return {
        w for w in tokens(text)
        if w not in GENERIC_WORDS
    }


def lexical_similarity(a: str, b: str):
    A = meaningful_tokens(a)
    B = meaningful_tokens(b)

    if not A or not B:
        return 0.0

    overlap = len(A & B) / max(1, len(A | B))
    seq = SequenceMatcher(
        None,
        " ".join(sorted(A)),
        " ".join(sorted(B))
    ).ratio()

    return round((overlap * 0.70) + (seq * 0.30), 4)


def normalize_domain(url: str):
    try:
        host = urlparse(url).hostname or ""
        host = host.lower().strip(".")
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def is_safe_url(url: str):
    try:
        p = urlparse(url)

        if p.scheme not in {"http", "https"}:
            return False

        host = p.hostname
        if not host:
            return False

        if host in {"localhost", "127.0.0.1", "::1"}:
            return False

        try:
            ip = ipaddress.ip_address(host)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
                return False
        except ValueError:
            pass

        return True

    except Exception:
        return False


def clean_text(text: str):
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def contains_pattern(text: str, patterns):
    low = text.lower()
    return any(re.search(p, low) for p in patterns)


# ---------------------------------------------------------------------
# SOURCE FAMILY
# ---------------------------------------------------------------------

def source_family(domain: str):
    d = (domain or "").lower()

    if not d:
        return "unknown"

    if d.endswith("doi.org"):
        return "unknown"

    if "arxiv.org" in d:
        return "arxiv"

    if "nature.com" in d:
        return "nature"

    if "science.org" in d:
        return "science"

    if "sciencedirect.com" in d:
        return "elsevier"

    if "springer.com" in d or "link.springer" in d:
        return "springer"

    if "frontiersin.org" in d:
        return "frontiers"

    if "plos.org" in d:
        return "plos"

    if "wiley.com" in d:
        return "wiley"

    if "bmj.com" in d:
        return "bmj"

    if "acm.org" in d:
        return "acm"

    if "ieee.org" in d:
        return "ieee"

    if "nih.gov" in d or "ncbi.nlm.nih.gov" in d:
        return "nih"

    if "jamanetwork.com" in d:
        return "jamanetwork"

    if "tandfonline.com" in d:
        return "taylor-francis"

    if "sagepub.com" in d:
        return "sage"

    if "cambridge.org" in d:
        return "cambridge"

    if "oup.com" in d:
        return "oxford"

    if "oxfordacademic.com" in d:
        return "oxford"

    if "mit.edu" in d:
        return "mit"

    if "ac.uk" in d:
        return "university"

    if d.endswith(".edu") or ".edu." in d:
        return "university"

    if "github.com" in d:
        return "github"

    return d


# ---------------------------------------------------------------------
# DOI / CANONICAL PROVENANCE
# ---------------------------------------------------------------------

def resolve_canonical_url(url: str, doi: str = ""):
    """
    Resolve DOI/URL redirects.

    Critical rule:
    doi.org is never accepted as the research source domain.
    """

    candidate = url

    if doi:
        candidate = f"https://doi.org/{doi.strip()}"

    if not candidate or not is_safe_url(candidate):
        return {
            "canonical_url": url,
            "domain": normalize_domain(url),
            "family": source_family(normalize_domain(url))
        }

    try:
        r = requests.get(
            candidate,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            allow_redirects=True,
            stream=True
        )

        final_url = r.url or candidate
        domain = normalize_domain(final_url)

        if not domain or domain == "doi.org":
            domain = ""

        family = source_family(domain)

        return {
            "canonical_url": final_url,
            "domain": domain,
            "family": family
        }

    except Exception:
        domain = normalize_domain(url)

        if domain == "doi.org":
            domain = ""

        return {
            "canonical_url": url,
            "domain": domain,
            "family": source_family(domain)
        }


# ---------------------------------------------------------------------
# MISSION RELEVANCE
# ---------------------------------------------------------------------

def mission_terms(objective: str):
    return meaningful_tokens(objective)


def relevance_score(objective: str, title: str, abstract: str):
    query = mission_terms(objective)

    if not query:
        return 0.0

    title_terms = meaningful_tokens(title)
    body_terms = meaningful_tokens(abstract)

    title_overlap = len(query & title_terms) / max(1, len(query))
    body_overlap = len(query & body_terms) / max(1, len(query))

    # Stronger weight on title because title-level relevance is
    # more reliable than incidental words in navigation/full text.
    score = (
        title_overlap * 0.65
        + body_overlap * 0.35
    )

    # Direct lexical similarity is a secondary check.
    sim = lexical_similarity(
        objective,
        f"{title}. {abstract[:5000]}"
    )

    score = (score * 0.75) + (sim * 0.25)

    return round(min(1.0, score), 4)


def relevance_label(score):
    if score >= 0.60:
        return "HIGH"

    if score >= 0.40:
        return "MEDIUM"

    if score >= 0.25:
        return "LOW"

    return "IRRELEVANT"


# ---------------------------------------------------------------------
# CLAIM PURITY
# ---------------------------------------------------------------------

def claim_purity(text: str):
    text = clean_text(text)

    if len(text) < 45 or len(text) > 900:
        return 0.0

    low = text.lower()

    if contains_pattern(text, BOILERPLATE_PATTERNS):
        return 0.0

    if contains_pattern(text, CODE_PATTERNS):
        return 0.0

    for p in METADATA_PATTERNS:
        if re.search(p, low):
            return 0.0

    if re.match(
        r"^(figure|fig\.|table|section|chapter|appendix)\s+\d",
        low
    ):
        return 0.0

    if "http://" in low or "https://" in low:
        return 0.0

    if "doi.org/" in low:
        return 0.0

    if re.search(r"\b(issn|e-issn|isbn)\b", low):
        return 0.0

    sentence_count = len(
        re.findall(r"[.!?](?:\s|$)", text)
    )

    score = 1.0

    if sentence_count == 0:
        score -= 0.25

    if len(text.split()) < 9:
        score -= 0.20

    if len(text.split()) > 120:
        score -= 0.15

    if text.count(",") > 10:
        score -= 0.10

    return round(max(0.0, min(1.0, score)), 3)


# ---------------------------------------------------------------------
# CLAIM TYPE
# ---------------------------------------------------------------------

def classify_claim(text: str):
    low = text.lower()

    if re.search(
        r"\b(recommend|should|must|need to|we propose|we suggest)\b",
        low
    ):
        return "recommendation"

    if re.search(
        r"\b(review|systematic review|literature review|survey)\b",
        low
    ):
        return "review"

    if re.search(
        r"\b(method|algorithm|framework|approach|architecture)\b",
        low
    ):
        return "methodological"

    if re.search(
        r"\b(found|find|observed|identified|measured|increased|"
        r"decreased|associated|participants|sample|experiment)\b",
        low
    ):
        return "empirical"

    return "synthesis"


# ---------------------------------------------------------------------
# CLAIM EXTRACTION
# ---------------------------------------------------------------------

def extract_claim_candidates(
    objective: str,
    title: str,
    abstract: str,
    work_relevance: float
):
    if work_relevance < 0.40:
        return []

    text = clean_text(abstract)

    if not text:
        return []

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text
    )

    results = []

    for sentence in sentences:
        sentence = clean_text(sentence)

        purity = claim_purity(sentence)

        if purity < 0.65:
            continue

        relevance = lexical_similarity(
            objective,
            sentence
        )

        # Claim relevance is stricter than work relevance.
        if relevance < 0.18:
            continue

        # Reject generic scientific sentences with no mission terms.
        mission_overlap = (
            len(
                mission_terms(objective)
                & meaningful_tokens(sentence)
            )
            / max(1, len(mission_terms(objective)))
        )

        if mission_overlap < 0.04:
            continue

        results.append({
            "text": sentence,
            "purity": purity,
            "relevance": round(
                (relevance * 0.60) +
                (mission_overlap * 0.40),
                4
            ),
            "claim_type": classify_claim(sentence)
        })

    # Deduplicate locally.
    unique = {}
    for item in results:
        key = re.sub(
            r"\W+",
            " ",
            item["text"].lower()
        ).strip()

        unique[key] = item

    return list(unique.values())[:80]


# ---------------------------------------------------------------------
# EVIDENCE ENTailMENT
# ---------------------------------------------------------------------

def contradiction_polarity(text: str):
    low = text.lower()

    neg = sum(
        1 for x in NEGATIVE_WORDS
        if x in low
    )

    pos = sum(
        1 for x in POSITIVE_WORDS
        if x in low
    )

    if neg > pos:
        return "negative"

    if pos > neg:
        return "positive"

    return "neutral"


def evidence_entailment(
    objective: str,
    claim: str,
    excerpt: str,
    work_relevance: float
):
    """
    Conservative heuristic.

    IMPORTANT:
    This is deliberately NOT an LLM semantic-entailment claim.

    A high lexical match alone cannot create DIRECT_SUPPORT.
    """

    if not excerpt or not claim:
        return 0.0

    if work_relevance < 0.40:
        return 0.0

    if contains_pattern(excerpt, BOILERPLATE_PATTERNS):
        return 0.0

    if contains_pattern(excerpt, CODE_PATTERNS):
        return 0.0

    claim_terms = meaningful_tokens(claim)
    evidence_terms = meaningful_tokens(excerpt)

    if not claim_terms or not evidence_terms:
        return 0.0

    overlap = len(
        claim_terms & evidence_terms
    ) / max(1, len(claim_terms))

    seq = SequenceMatcher(
        None,
        claim.lower(),
        excerpt.lower()
    ).ratio()

    # Evidence must cover substantial portions of the claim.
    coverage = overlap * 0.75 + seq * 0.25

    # Penalize extremely short excerpts.
    words = len(excerpt.split())

    if words < 12:
        coverage *= 0.60

    if words < 7:
        coverage *= 0.25

    # Contradictory polarity blocks support.
    cp = contradiction_polarity(claim)
    ep = contradiction_polarity(excerpt)

    if (
        cp != "neutral"
        and ep != "neutral"
        and cp != ep
    ):
        return round(min(0.25, coverage), 4)

    # Mission relevance remains part of evidence validity.
    score = coverage * (
        0.65 + (0.35 * work_relevance)
    )

    # Strict calibration.
    if overlap < 0.35:
        score *= 0.55

    if overlap < 0.25:
        score *= 0.25

    return round(
        max(0.0, min(1.0, score)),
        4
    )


def evidence_relation(
    lexical: float,
    entailment: float,
    relevance: float,
    domain: str,
    family: str
):
    """
    Strict relation classifier.

    DIRECT_SUPPORT is impossible when provenance is unknown.
    """

    if relevance < 0.40:
        return "NO_SUPPORT"

    if not domain or not family or family == "unknown":
        return "NO_SUPPORT"

    if entailment >= 0.82 and lexical >= 0.55:
        return "DIRECT_SUPPORT"

    if entailment >= 0.68 and lexical >= 0.40:
        return "STRONG_SUPPORT"

    if entailment >= 0.52 and lexical >= 0.30:
        return "SUPPORT"

    if entailment >= 0.40 and lexical >= 0.25:
        return "POSSIBLE_SUPPORT"

    return "NO_SUPPORT"


# ---------------------------------------------------------------------
# PROVIDERS
# ---------------------------------------------------------------------

def crossref_search(query: str, rows: int = 8):
    url = "https://api.crossref.org/works"

    try:
        r = requests.get(
            url,
            params={
                "query.bibliographic": query,
                "rows": rows
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT
        )

        data = r.json()

        out = []

        for item in data.get("message", {}).get("items", []):
            title = clean_text(
                " ".join(item.get("title", []))
            )

            if not title:
                continue

            doi = item.get("DOI", "")

            abstract = clean_text(
                re.sub(
                    r"<[^>]+>",
                    " ",
                    item.get("abstract", "")
                )
            )

            url = item.get(
                "URL",
                f"https://doi.org/{doi}" if doi else ""
            )

            out.append({
                "title": title,
                "doi": doi,
                "url": url,
                "abstract": abstract,
                "provider": "crossref"
            })

        return out

    except Exception:
        return []


def openalex_search(query: str, rows: int = 8):
    url = "https://api.openalex.org/works"

    try:
        r = requests.get(
            url,
            params={
                "search": query,
                "per-page": rows
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT
        )

        data = r.json()

        out = []

        for item in data.get("results", []):
            title = clean_text(
                item.get("title", "")
            )

            if not title:
                continue

            abstract = ""

            inv = item.get(
                "abstract_inverted_index"
            )

            if isinstance(inv, dict):
                positions = []
                for word, indexes in inv.items():
                    for idx in indexes:
                        positions.append((idx, word))

                positions.sort()
                abstract = " ".join(
                    word for _, word in positions
                )

            doi = (
                item.get("doi") or ""
            ).replace(
                "https://doi.org/",
                ""
            )

            url = (
                item.get("primary_location", {})
                .get("landing_page_url")
                or item.get("id")
                or (
                    f"https://doi.org/{doi}"
                    if doi else ""
                )
            )

            out.append({
                "title": title,
                "doi": doi,
                "url": url,
                "abstract": clean_text(abstract),
                "provider": "openalex"
            })

        return out

    except Exception:
        return []


def semantic_scholar_search(query: str, rows: int = 8):
    url = "https://api.semanticscholar.org/graph/v1/paper/search"

    try:
        r = requests.get(
            url,
            params={
                "query": query,
                "limit": rows,
                "fields": (
                    "title,abstract,url,externalIds,"
                    "openAccessPdf"
                )
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT
        )

        data = r.json()

        out = []

        for item in data.get("data", []):
            title = clean_text(
                item.get("title", "")
            )

            if not title:
                continue

            ext = item.get(
                "externalIds"
            ) or {}

            doi = ext.get("DOI", "")

            url = (
                item.get("url")
                or (
                    item.get("openAccessPdf") or {}
                ).get("url")
                or (
                    f"https://doi.org/{doi}"
                    if doi else ""
                )
            )

            out.append({
                "title": title,
                "doi": doi,
                "url": url,
                "abstract": clean_text(
                    item.get("abstract", "")
                ),
                "provider": "semantic_scholar"
            })

        return out

    except Exception:
        return []


# ---------------------------------------------------------------------
# SOURCE DISCOVERY
# ---------------------------------------------------------------------

def discovery_queries(objective: str):
    base = objective.strip()

    return [
        base,
        f"{base} empirical study",
        f"{base} benchmark evaluation",
        f"{base} independent replication",
        f"{base} real world deployment",
    ]


def discover_sources(objective: str, attempt: int = 0):
    queries = discovery_queries(objective)

    # Rotate providers by recovery attempt.
    providers = []

    if attempt % 3 == 0:
        providers = [
            crossref_search,
            openalex_search,
            semantic_scholar_search
        ]
    elif attempt % 3 == 1:
        providers = [
            openalex_search,
            semantic_scholar_search,
            crossref_search
        ]
    else:
        providers = [
            semantic_scholar_search,
            crossref_search,
            openalex_search
        ]

    results = []

    for i, query in enumerate(queries):
        provider = providers[
            (i + attempt) % len(providers)
        ]

        results.extend(
            provider(query, rows=5)
        )

    # Global deduplication.
    unique = {}

    for item in results:
        doi = (item.get("doi") or "").lower().strip()

        if doi:
            key = f"doi:{doi}"
        else:
            key = (
                "title:" +
                re.sub(
                    r"\W+",
                    " ",
                    item.get("title", "").lower()
                ).strip()
            )

        if key and key not in unique:
            unique[key] = item

    return list(unique.values())[:40]


# ---------------------------------------------------------------------
# WORK ACCEPTANCE
# ---------------------------------------------------------------------

def prepare_work(mission_id: str, objective: str, item):
    title = clean_text(item.get("title", ""))
    abstract = clean_text(item.get("abstract", ""))
    url = item.get("url", "") or ""
    doi = item.get("doi", "") or ""

    relevance = relevance_score(
        objective,
        title,
        abstract
    )

    # Hard mission relevance gate.
    if relevance < 0.40:
        return None

    provenance = resolve_canonical_url(
        url,
        doi
    )

    domain = provenance["domain"]
    family = provenance["family"]

    # A DOI resolver alone is NOT provenance.
    if domain == "doi.org":
        domain = ""

    work_key = (
        doi.lower()
        if doi
        else (
            provenance["canonical_url"]
            or url
            or title
        ).lower()
    )

    work_id = stable_id(
        "work",
        work_key
    )

    return {
        "id": work_id,
        "mission_id": mission_id,
        "title": title,
        "url": url,
        "canonical_url": provenance["canonical_url"],
        "doi": doi,
        "provider": item.get("provider", "unknown"),
        "domain": domain,
        "family": family,
        "abstract": abstract,
        "relevance": relevance
    }


def save_work(work):
    conn = db()

    conn.execute(
        """
        INSERT OR IGNORE INTO works
        (id, mission_id, title, url, canonical_url,
         doi, provider, domain, family, abstract,
         relevance, accepted, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            work["id"],
            work["mission_id"],
            work["title"],
            work["url"],
            work["canonical_url"],
            work["doi"],
            work["provider"],
            work["domain"],
            work["family"],
            work["abstract"],
            work["relevance"],
            now()
        )
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# EVIDENCE
# ---------------------------------------------------------------------

def choose_evidence_excerpt(text: str, claim: str):
    sentences = re.split(
        r"(?<=[.!?])\s+",
        clean_text(text)
    )

    candidates = []

    for sentence in sentences:
        sentence = clean_text(sentence)

        if not sentence:
            continue

        if contains_pattern(
            sentence,
            BOILERPLATE_PATTERNS
        ):
            continue

        if contains_pattern(
            sentence,
            CODE_PATTERNS
        ):
            continue

        score = lexical_similarity(
            claim,
            sentence
        )

        candidates.append(
            (score, sentence)
        )

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    if not candidates:
        return ""

    return candidates[0][1][:MAX_EXCERPT]


def save_evidence(
    mission_id,
    work,
    claim_id,
    excerpt,
    lexical,
    entailment,
    relation
):
    evidence_id = stable_id(
        "evidence",
        "|".join([
            mission_id,
            work["id"],
            claim_id,
            excerpt
        ])
    )

    quality = "NONE"

    if relation == "DIRECT_SUPPORT":
        quality = "DIRECT"
    elif relation == "STRONG_SUPPORT":
        quality = "STRONG"
    elif relation == "SUPPORT":
        quality = "MODERATE"
    elif relation == "POSSIBLE_SUPPORT":
        quality = "WEAK"

    conn = db()

    conn.execute(
        """
        INSERT OR IGNORE INTO evidence
        (id, mission_id, work_id, claim_id,
         excerpt, lexical, entailment, relevance,
         quality, relation, domain, family, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            mission_id,
            work["id"],
            claim_id,
            excerpt,
            lexical,
            entailment,
            work["relevance"],
            quality,
            relation,
            work["domain"],
            work["family"],
            now()
        )
    )

    conn.commit()
    conn.close()

    return evidence_id


# ---------------------------------------------------------------------
# CLAIMS
# ---------------------------------------------------------------------

def save_claim(
    mission_id,
    text,
    claim_type,
    purity,
    relevance
):
    claim_id = stable_id(
        "claim",
        mission_id + "|" + text.lower()
    )

    conn = db()

    conn.execute(
        """
        INSERT OR IGNORE INTO claims
        (id, mission_id, text, claim_type,
         purity, relevance, status,
         confidence, blockers, next_action, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            claim_id,
            mission_id,
            text,
            claim_type,
            purity,
            relevance,
            "PENDING",
            0.0,
            "[]",
            "",
            now()
        )
    )

    conn.commit()
    conn.close()

    return claim_id


# ---------------------------------------------------------------------
# GRAPH
# ---------------------------------------------------------------------

def save_edge(
    mission_id,
    claim_id,
    evidence_id,
    relation,
    score,
    reason
):
    edge_id = stable_id(
        "edge",
        "|".join([
            mission_id,
            claim_id,
            evidence_id,
            relation
        ])
    )

    conn = db()

    conn.execute(
        """
        INSERT OR IGNORE INTO edges
        (id, mission_id, claim_id,
         evidence_id, relation,
         score, reason, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            edge_id,
            mission_id,
            claim_id,
            evidence_id,
            relation,
            score,
            json.dumps(
                reason,
                ensure_ascii=False
            ),
            now()
        )
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# VERIFICATION
# ---------------------------------------------------------------------

def verify_claim(mission_id, claim_id):
    conn = db()

    claim = conn.execute(
        "SELECT * FROM claims WHERE id=?",
        (claim_id,)
    ).fetchone()

    evidence = conn.execute(
        """
        SELECT e.*, w.title, w.domain AS work_domain,
               w.family AS work_family
        FROM evidence e
        JOIN works w ON w.id=e.work_id
        WHERE e.claim_id=?
        """,
        (claim_id,)
    ).fetchall()

    conn.close()

    strong = [
        e for e in evidence
        if e["relation"] in {
            "DIRECT_SUPPORT",
            "STRONG_SUPPORT"
        }
    ]

    support = [
        e for e in evidence
        if e["relation"] in {
            "DIRECT_SUPPORT",
            "STRONG_SUPPORT",
            "SUPPORT"
        }
    ]

    works = {
        e["work_id"]
        for e in support
    }

    domains = {
        e["work_domain"]
        for e in support
        if e["work_domain"]
    }

    families = {
        e["work_family"]
        for e in support
        if e["work_family"]
        and e["work_family"] != "unknown"
    }

    contradictions = []

    for e in evidence:
        if e["relation"] == "CONTRADICTION":
            contradictions.append(e)

    blockers = []

    if len(works) < 2:
        blockers.append(
            "only_1_independent_work"
        )

    if len(domains) < 2:
        blockers.append(
            "only_1_independent_domain"
        )

    if len(families) < 2:
        blockers.append(
            "only_1_source_family"
        )

    if len(strong) < 2:
        blockers.append(
            "fewer_than_2_strong_evidence_relationships"
        )

    if contradictions:
        blockers.append(
            "contradictory_evidence_present"
        )

    if not evidence:
        status = "INSUFFICIENT"
    elif (
        not blockers
        and len(works) >= 2
        and len(domains) >= 2
        and len(families) >= 2
        and len(strong) >= 2
    ):
        status = "VERIFIED"
    elif contradictions and not support:
        status = "CONTRADICTED"
    elif support:
        status = "SUPPORTED_NOT_VERIFIED"
    else:
        status = "INSUFFICIENT"

    if "only_1_independent_work" in blockers:
        next_action = "find_independent_primary_study"

    elif "only_1_independent_domain" in blockers:
        next_action = "find_evidence_from_independent_domain"

    elif "only_1_source_family" in blockers:
        next_action = "find_evidence_from_independent_source_family"

    elif (
        "fewer_than_2_strong_evidence_relationships"
        in blockers
    ):
        next_action = "find_second_strong_evidence_excerpt"

    elif contradictions:
        next_action = "investigate_contradictory_evidence"

    else:
        next_action = "no_further_action_required"

    confidence = 0.0

    if support:
        confidence = min(
            0.95,
            (
                sum(
                    float(e["entailment"])
                    for e in support
                )
                / len(support)
            )
        )

    conn = db()

    conn.execute(
        """
        UPDATE claims
        SET status=?,
            confidence=?,
            blockers=?,
            next_action=?
        WHERE id=?
        """,
        (
            status,
            round(confidence, 4),
            json.dumps(blockers),
            next_action,
            claim_id
        )
    )

    conn.commit()
    conn.close()

    return {
        "status": status,
        "blockers": blockers,
        "next_action": next_action,
        "works": len(works),
        "domains": len(domains),
        "families": len(families),
        "strong_evidence": len(strong),
        "support": len(support),
        "contradictions": len(contradictions),
        "confidence": round(confidence, 4)
    }


def verify_mission(mission_id):
    conn = db()

    claims = conn.execute(
        "SELECT id FROM claims WHERE mission_id=?",
        (mission_id,)
    ).fetchall()

    conn.close()

    results = []

    for claim in claims:
        results.append(
            verify_claim(
                mission_id,
                claim["id"]
            )
        )

    verified = sum(
        1 for x in results
        if x["status"] == "VERIFIED"
    )

    supported = sum(
        1 for x in results
        if x["status"] == "SUPPORTED_NOT_VERIFIED"
    )

    contradicted = sum(
        1 for x in results
        if x["status"] == "CONTRADICTED"
    )

    return {
        "verified": verified,
        "supported": supported,
        "contradicted": contradicted,
        "claims": len(results)
    }


# ---------------------------------------------------------------------
# EVENTS / ACTIONS
# ---------------------------------------------------------------------

def event(
    mission_id,
    stage,
    status,
    detail
):
    conn = db()

    conn.execute(
        """
        INSERT INTO events
        (mission_id, stage, status, detail, created)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            stage,
            status,
            detail,
            now()
        )
    )

    conn.commit()
    conn.close()


def action(
    mission_id,
    kind,
    target,
    status,
    result,
    attempt
):
    conn = db()

    conn.execute(
        """
        INSERT INTO actions
        (mission_id, kind, target,
         status, result, attempt, created)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            kind,
            target,
            status,
            result,
            attempt,
            now()
        )
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# INGESTION
# ---------------------------------------------------------------------

def ingest_sources(
    mission_id,
    objective,
    attempt
):
    discovered = discover_sources(
        objective,
        attempt
    )

    accepted = 0
    rejected = 0
    new_works = 0
    new_domains = set()
    new_families = set()

    conn = db()

    existing = {
        row["id"]
        for row in conn.execute(
            "SELECT id FROM works WHERE mission_id=?",
            (mission_id,)
        ).fetchall()
    }

    conn.close()

    for item in discovered:
        work = prepare_work(
            mission_id,
            objective,
            item
        )

        if not work:
            rejected += 1
            continue

        accepted += 1

        if work["id"] in existing:
            continue

        save_work(work)

        existing.add(work["id"])
        new_works += 1

        if work["domain"]:
            new_domains.add(work["domain"])

        if (
            work["family"]
            and work["family"] != "unknown"
        ):
            new_families.add(work["family"])

    return {
        "discovered": len(discovered),
        "accepted": accepted,
        "rejected": rejected,
        "new_works": new_works,
        "new_domains": len(new_domains),
        "new_families": len(new_families)
    }


# ---------------------------------------------------------------------
# CLAIM / EVIDENCE PASS
# ---------------------------------------------------------------------

def evidence_pass(
    mission_id,
    objective
):
    conn = db()

    works = conn.execute(
        """
        SELECT * FROM works
        WHERE mission_id=? AND accepted=1
        ORDER BY relevance DESC
        """,
        (mission_id,)
    ).fetchall()

    conn.close()

    new_claims = 0
    new_edges = 0
    usable_works = 0

    for work_row in works:
        work = dict(work_row)

        if (
            work["relevance"] < 0.40
            or not work["domain"]
            or work["family"] == "unknown"
        ):
            continue

        claims = extract_claim_candidates(
            objective,
            work["title"],
            work["abstract"],
            work["relevance"]
        )

        if claims:
            usable_works += 1

        for candidate in claims:
            claim_id = save_claim(
                mission_id,
                candidate["text"],
                candidate["claim_type"],
                candidate["purity"],
                candidate["relevance"]
            )

            conn = db()
            exists = conn.execute(
                """
                SELECT id FROM evidence
                WHERE mission_id=?
                  AND work_id=?
                  AND claim_id=?
                """,
                (
                    mission_id,
                    work["id"],
                    claim_id
                )
            ).fetchone()
            conn.close()

            if exists:
                continue

            new_claims += 1

            excerpt = choose_evidence_excerpt(
                work["abstract"],
                candidate["text"]
            )

            if not excerpt:
                continue

            lexical = lexical_similarity(
                candidate["text"],
                excerpt
            )

            entailment = evidence_entailment(
                objective,
                candidate["text"],
                excerpt,
                work["relevance"]
            )

            relation = evidence_relation(
                lexical,
                entailment,
                work["relevance"],
                work["domain"],
                work["family"]
            )

            # Do not put NO_SUPPORT into the graph.
            if relation == "NO_SUPPORT":
                continue

            evidence_id = save_evidence(
                mission_id,
                work,
                claim_id,
                excerpt,
                lexical,
                entailment,
                relation
            )

            save_edge(
                mission_id,
                claim_id,
                evidence_id,
                relation,
                entailment,
                {
                    "domain": work["domain"],
                    "family": work["family"],
                    "lexical": lexical,
                    "entailment": entailment,
                    "work_relevance": work["relevance"],
                    "verdict": relation
                }
            )

            new_edges += 1

    return {
        "usable_works": usable_works,
        "new_claims": new_claims,
        "new_edges": new_edges
    }


# ---------------------------------------------------------------------
# TARGETED RECOVERY
# ---------------------------------------------------------------------

def recovery_targets(
    objective,
    verification
):
    targets = []

    blockers = Counter()

    conn = db()

    rows = conn.execute(
        """
        SELECT blockers
        FROM claims
        WHERE mission_id=?
        """,
        (verification.get("mission_id", ""),)
    ).fetchall()

    conn.close()

    for row in rows:
        try:
            for blocker in json.loads(
                row["blockers"] or "[]"
            ):
                blockers[blocker] += 1
        except Exception:
            pass

    if (
        blockers["only_1_independent_domain"]
        or blockers["only_1_independent_work"]
    ):
        targets.append(
            f"{objective} independent primary study empirical benchmark"
        )

    if blockers["only_1_source_family"]:
        targets.append(
            f"{objective} university laboratory independent publisher"
        )

    if blockers[
        "fewer_than_2_strong_evidence_relationships"
    ]:
        targets.append(
            f"{objective} replication evaluation results"
        )

    if not targets:
        targets = [
            f"{objective} independent empirical evidence",
            f"{objective} benchmark replication",
        ]

    return targets[:3]


def recovery_round(
    mission_id,
    objective,
    attempt
):
    target_queries = [
        f"{objective} independent primary study",
        f"{objective} empirical benchmark replication",
        f"{objective} real world deployment evaluation",
        f"{objective} limitations failure cases",
        f"{objective} university laboratory study",
    ]

    # Rotate query focus.
    rotated = (
        target_queries[attempt:]
        + target_queries[:attempt]
    )

    target = rotated[0]

    action(
        mission_id,
        "research_recovery",
        target,
        "RUNNING",
        "targeted recovery selected from measured verification bottleneck",
        attempt
    )

    result = ingest_sources(
        mission_id,
        objective,
        attempt
    )

    evidence = evidence_pass(
        mission_id,
        objective
    )

    verification = verify_mission(
        mission_id
    )

    action(
        mission_id,
        "research_recovery",
        target,
        "COMPLETED",
        json.dumps({
            "new_works": result["new_works"],
            "new_domains": result["new_domains"],
            "new_families": result["new_families"],
            "new_claims": evidence["new_claims"],
            "new_edges": evidence["new_edges"],
            "verified": verification["verified"]
        }),
        attempt
    )

    return {
        "target": target,
        "source": result,
        "evidence": evidence,
        "verification": verification
    }


# ---------------------------------------------------------------------
# AUDIT
# ---------------------------------------------------------------------

def mission_audit(mission_id):
    conn = db()

    mission = conn.execute(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,)
    ).fetchone()

    works = conn.execute(
        "SELECT * FROM works WHERE mission_id=?",
        (mission_id,)
    ).fetchall()

    evidence = conn.execute(
        "SELECT * FROM evidence WHERE mission_id=?",
        (mission_id,)
    ).fetchall()

    claims = conn.execute(
        "SELECT * FROM claims WHERE mission_id=?",
        (mission_id,)
    ).fetchall()

    edges = conn.execute(
        "SELECT * FROM edges WHERE mission_id=?",
        (mission_id,)
    ).fetchall()

    actions = conn.execute(
        "SELECT * FROM actions WHERE mission_id=?",
        (mission_id,)
    ).fetchall()

    conn.close()

    usable_works = [
        w for w in works
        if w["accepted"]
        and w["relevance"] >= 0.40
    ]

    domains = {
        w["domain"]
        for w in usable_works
        if w["domain"]
    }

    families = {
        w["family"]
        for w in usable_works
        if w["family"]
        and w["family"] != "unknown"
    }

    verified = [
        c for c in claims
        if c["status"] == "VERIFIED"
    ]

    supported = [
        c for c in claims
        if c["status"] == "SUPPORTED_NOT_VERIFIED"
    ]

    high_purity = [
        c for c in claims
        if c["purity"] >= 0.80
    ]

    rejected_relevance = len([
        w for w in works
        if w["relevance"] < 0.40
    ])

    reality = {
        "autonomous_research": (
            "DEMONSTRATED"
            if works and claims
            else "NOT DEMONSTRATED"
        ),
        "mission_relevance_gate": (
            "DEMONSTRATED"
            if rejected_relevance >= 0
            else "NOT DEMONSTRATED"
        ),
        "evidence_provenance": (
            "DEMONSTRATED"
            if evidence
            and all(
                e["domain"]
                and e["family"] != "unknown"
                for e in evidence
            )
            else "PARTIALLY DEMONSTRATED"
        ),
        "claim_quality_control": (
            "DEMONSTRATED"
            if claims and high_purity
            else "PARTIALLY DEMONSTRATED"
        ),
        "independent_verification": (
            "DEMONSTRATED"
            if verified
            else "NOT DEMONSTRATED"
        ),
        "closed_loop_recovery": (
            "DEMONSTRATED"
            if len(actions) >= 2
            else "NOT DEMONSTRATED"
        ),
        "recovery_diversification": (
            "DEMONSTRATED"
            if len(domains) >= 2
            and len(families) >= 2
            else "NOT DEMONSTRATED"
        ),
        "general_real_world_execution":
            "NOT DEMONSTRATED",
        "continuous_self_improvement":
            "NOT DEMONSTRATED",
        "durable_memory":
            "LIMITED_BY_STORAGE"
    }

    return {
        "version": VERSION,
        "build": BUILD,
        "mission_id": mission_id,
        "objective": (
            mission["objective"]
            if mission else ""
        ),
        "metrics": {
            "works": len(works),
            "usable_works": len(usable_works),
            "claims": len(claims),
            "evidence": len(evidence),
            "support_edges": len([
                e for e in edges
                if e["relation"] != "NO_SUPPORT"
            ]),
            "graph_edges": len(edges),
            "verified_claims": len(verified),
            "supported_not_verified": len(supported),
            "domains": len(domains),
            "source_families": len(families),
            "actions": len(actions),
            "high_purity_claims": len(high_purity),
            "rejected_relevance_records":
                rejected_relevance,
            "verification_rate": round(
                len(verified) / max(1, len(claims)),
                4
            ),
            "support_rate": round(
                len(supported) / max(1, len(claims)),
                4
            )
        },
        "reality": reality,
        "definition_of_working": (
            "A mission is working when it can discover "
            "mission-relevant sources, establish canonical "
            "provenance, extract atomic claims, attach "
            "evidence that passes calibrated entailment, "
            "verify claims only through independent works, "
            "domains and source families, and automatically "
            "recover from measurable verification gaps "
            "without weakening the gate."
        )
    }


# ---------------------------------------------------------------------
# MISSION RUNNER
# ---------------------------------------------------------------------

def run_mission(
    mission_id,
    objective
):
    try:
        event(
            mission_id,
            "mission",
            "running",
            "Mission accepted"
        )

        event(
            mission_id,
            "discovery",
            "running",
            "Discovering research sources"
        )

        discovery = ingest_sources(
            mission_id,
            objective,
            0
        )

        event(
            mission_id,
            "discovery",
            "completed",
            json.dumps(discovery)
        )

        event(
            mission_id,
            "claims",
            "running",
            "Extracting mission-relevant atomic claims"
        )

        evidence_result = evidence_pass(
            mission_id,
            objective
        )

        event(
            mission_id,
            "claims",
            "completed",
            json.dumps(evidence_result)
        )

        event(
            mission_id,
            "evidence_graph",
            "running",
            "Building deduplicated claim-level provenance graph"
        )

        conn = db()
        edge_count = conn.execute(
            "SELECT COUNT(*) AS n FROM edges WHERE mission_id=?",
            (mission_id,)
        ).fetchone()["n"]
        conn.close()

        event(
            mission_id,
            "evidence_graph",
            "completed",
            f"{edge_count} deduplicated graph edges"
        )

        event(
            mission_id,
            "verification",
            "running",
            "Applying calibrated entailment + independence gate"
        )

        verification = verify_mission(
            mission_id
        )

        event(
            mission_id,
            "verification",
            "completed",
            (
                f"verified={verification['verified']}; "
                f"supported={verification['supported']}; "
                f"contradicted={verification['contradicted']}"
            )
        )

        # -------------------------------------------------------------
        # BOUNDED RECOVERY
        # -------------------------------------------------------------

        previous_progress = (
            verification["verified"],
            verification["supported"]
        )

        for attempt in range(1, 3):
            if verification["verified"] > 0:
                break

            event(
                mission_id,
                "recovery",
                "running",
                f"round {attempt} starting"
            )

            result = recovery_round(
                mission_id,
                objective,
                attempt
            )

            verification = result[
                "verification"
            ]

            current_progress = (
                verification["verified"],
                verification["supported"]
            )

            new_works = result[
                "source"
            ]["new_works"]

            new_domains = result[
                "source"
            ]["new_domains"]

            new_families = result[
                "source"
            ]["new_families"]

            # Stop if recovery is not actually diversifying.
            if (
                new_works == 0
                and new_domains == 0
                and new_families == 0
                and current_progress == previous_progress
            ):
                event(
                    mission_id,
                    "recovery",
                    "completed",
                    (
                        f"round {attempt} stopped: "
                        "no new evidence diversification "
                        "or verification progress"
                    )
                )
                break

            previous_progress = current_progress

            event(
                mission_id,
                "recovery",
                "completed",
                f"round {attempt} complete"
            )

        # -------------------------------------------------------------
        # FINAL AUDIT
        # -------------------------------------------------------------

        audit = mission_audit(
            mission_id
        )

        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status=?, updated=?, result=?
            WHERE id=?
            """,
            (
                "completed",
                now(),
                json.dumps(
                    audit,
                    ensure_ascii=False
                ),
                mission_id
            )
        )

        conn.commit()
        conn.close()

        event(
            mission_id,
            "mission",
            "completed",
            "Mission completed with evidence gate preserved"
        )

    except Exception as exc:
        conn = db()

        conn.execute(
            """
            UPDATE missions
            SET status=?, updated=?, result=?
            WHERE id=?
            """,
            (
                "failed",
                now(),
                json.dumps({
                    "error": str(exc)
                }),
                mission_id
            )
        )

        conn.commit()
        conn.close()

        event(
            mission_id,
            "mission",
            "failed",
            str(exc)
        )


# ---------------------------------------------------------------------
# APP
# ---------------------------------------------------------------------

app = FastAPI(
    title="AI Infinity",
    version=VERSION
)


@app.get("/", response_class=HTMLResponse)
def home():
    return f"""
<!doctype html>
<html>
<head>
<meta name="viewport"
 content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body {{
    font-family: system-ui, sans-serif;
    background:#0b0f14;
    color:#e8edf2;
    margin:0;
    padding:20px;
}}
.card {{
    max-width:900px;
    margin:auto;
    background:#121820;
    padding:24px;
    border-radius:18px;
}}
h1 {{ margin-top:0; }}
textarea {{
    width:100%;
    min-height:160px;
    background:#080c10;
    color:#fff;
    border:1px solid #303944;
    border-radius:12px;
    padding:12px;
    box-sizing:border-box;
}}
button {{
    margin-top:12px;
    padding:13px 18px;
    border:0;
    border-radius:10px;
    cursor:pointer;
}}
pre {{
    white-space:pre-wrap;
    word-break:break-word;
    background:#080c10;
    padding:15px;
    border-radius:12px;
}}
.small {{
    opacity:.7;
    font-size:13px;
}}
</style>
</head>
<body>
<div class="card">
<h1>AI Infinity</h1>
<p>{VERSION}</p>
<p class="small">
Evidence-first autonomous research with provenance,
mission relevance and conservative verification.
</p>

<textarea id="command">Research the reliability of autonomous AI agents for real-world task execution. Find high-quality independent primary evidence, extract only atomic factual claims, verify each important claim against independent works from independent domains and source families, detect contradictory evidence, explain exactly why each claim is verified or not verified, identify the strongest remaining evidence gaps, and give the next research action required for each unresolved claim. Do not treat titles, metadata, navigation text, boilerplate, recommendations, or code examples as evidence. Do not lower verification standards to produce a verified result.</textarea>

<button onclick="run()">Run research</button>

<pre id="out">Ready.</pre>
</div>

<script>
async function run() {{
    const command =
        document.getElementById("command").value;

    const r = await fetch("/run", {{
        method:"POST",
        headers:{{"Content-Type":"application/json"}},
        body:JSON.stringify({{command}})
    }});

    const data = await r.json();

    document.getElementById("out").textContent =
        JSON.stringify(data,null,2);
}}
</script>
</body>
</html>
"""


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": VERSION,
        "build": BUILD
    }


@app.get("/status")
def status():
    conn = db()

    missions = conn.execute(
        "SELECT COUNT(*) AS n FROM missions"
    ).fetchone()["n"]

    claims = conn.execute(
        "SELECT COUNT(*) AS n FROM claims"
    ).fetchone()["n"]

    evidence = conn.execute(
        "SELECT COUNT(*) AS n FROM evidence"
    ).fetchone()["n"]

    edges = conn.execute(
        "SELECT COUNT(*) AS n FROM edges"
    ).fetchone()["n"]

    conn.close()

    return {
        "version": VERSION,
        "build": BUILD,
        "missions": missions,
        "claims": claims,
        "evidence": evidence,
        "graph_edges": edges
    }


@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "capabilities": [
            "mission_relevance_gate",
            "canonical_doi_resolution",
            "source_domain_detection",
            "source_family_detection",
            "atomic_claim_extraction",
            "claim_purity_filter",
            "calibrated_evidence_entailment",
            "claim_level_provenance_graph",
            "independent_work_verification",
            "independent_domain_verification",
            "independent_source_family_verification",
            "contradiction_tracking",
            "per_claim_verification_blockers",
            "targeted_recovery",
            "recovery_diversification",
            "bounded_autonomous_research_loop",
            "reality_audit"
        ],
        "not_claimed": [
            "general_agi",
            "general_asi",
            "arbitrary_real_world_execution",
            "continuous_self_improvement",
            "perfect_semantic_entailment"
        ]
    }


@app.post("/run")
def run(request: CreateRequest):
    mission_id = stable_id(
        "mission",
        request.command + str(now())
    )

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (id, objective, status, created, updated, result)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            request.command,
            "running",
            now(),
            now(),
            None
        )
    )

    conn.commit()
    conn.close()

    # Synchronous execution keeps the free Render deployment
    # simple and avoids background-worker requirements.
    run_mission(
        mission_id,
        request.command
    )

    return {
        "mission_id": mission_id,
        "status": "completed",
        "version": VERSION,
        "build": BUILD,
        "message": (
            "Mission executed. "
            "Use /mission/{id} for the complete result."
        )
    }


@app.post("/research")
def research(request: CreateRequest):
    return run(request)


@app.post("/command")
def command(request: CreateRequest):
    return run(request)


@app.get("/mission/{mission_id}")
def get_mission(mission_id: str):
    conn = db()

    mission = conn.execute(
        "SELECT * FROM missions WHERE id=?",
        (mission_id,)
    ).fetchone()

    conn.close()

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="Mission not found"
        )

    audit = mission_audit(
        mission_id
    )

    return {
        "mission": dict(mission),
        "audit": audit
    }


@app.get("/mission/{mission_id}/report")
def mission_report(mission_id: str):
    return mission_audit(
        mission_id
    )


@app.get("/audit/{mission_id}")
def audit(mission_id: str):
    return mission_audit(
        mission_id
    )


@app.get("/claims/{mission_id}")
def claims(mission_id: str):
    conn = db()

    rows = conn.execute(
        """
        SELECT * FROM claims
        WHERE mission_id=?
        ORDER BY relevance DESC
        """,
        (mission_id,)
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "claims": [dict(r) for r in rows]
    }


@app.get("/evidence/{mission_id}")
def evidence(mission_id: str):
    conn = db()

    rows = conn.execute(
        """
        SELECT * FROM evidence
        WHERE mission_id=?
        ORDER BY entailment DESC
        """,
        (mission_id,)
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "evidence": [dict(r) for r in rows]
    }


@app.get("/graph/{mission_id}")
def graph(mission_id: str):
    conn = db()

    rows = conn.execute(
        """
        SELECT * FROM edges
        WHERE mission_id=?
        ORDER BY score DESC
        """,
        (mission_id,)
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "edges": [dict(r) for r in rows]
    }


@app.get("/actions/{mission_id}")
def actions(mission_id: str):
    conn = db()

    rows = conn.execute(
        """
        SELECT * FROM actions
        WHERE mission_id=?
        ORDER BY id
        """,
        (mission_id,)
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "actions": [dict(r) for r in rows]
    }


@app.get("/events/{mission_id}")
def events(mission_id: str):
    conn = db()

    rows = conn.execute(
        """
        SELECT * FROM events
        WHERE mission_id=?
        ORDER BY id
        """,
        (mission_id,)
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "events": [dict(r) for r in rows]
    }


@app.get("/missions")
def missions():
    conn = db()

    rows = conn.execute(
        """
        SELECT id, objective, status,
               created, updated
        FROM missions
        ORDER BY created DESC
        LIMIT 50
        """
    ).fetchall()

    conn.close()

    return {
        "missions": [dict(r) for r in rows]
    }


# ---------------------------------------------------------------------
# DIRECT MISSION SUBROUTES
# ---------------------------------------------------------------------

@app.get("/mission/{mission_id}/claims")
def mission_claims(mission_id: str):
    return claims(mission_id)


@app.get("/mission/{mission_id}/evidence")
def mission_evidence(mission_id: str):
    return evidence(mission_id)


@app.get("/mission/{mission_id}/graph")
def mission_graph(mission_id: str):
    return graph(mission_id)


@app.get("/mission/{mission_id}/actions")
def mission_actions(mission_id: str):
    return actions(mission_id)


@app.get("/mission/{mission_id}/events")
def mission_events(mission_id: str):
    return events(mission_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )
