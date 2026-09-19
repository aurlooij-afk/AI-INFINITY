"""
AI Infinity
TARGET-2050.39
BUILD: EVIDENCE-INGESTION-RECOVERY-CORE

Goals:
- Robust Crossref + OpenAlex discovery
- DOI canonicalization
- OpenAlex inverted-index abstract reconstruction
- Abstract/title fallback when publisher pages are blocked
- Explicit source diagnostics
- Relevance separated from evidence strength
- Atomic claim extraction
- Evidence graph
- Independence checks
- Contradiction detection
- Strict verification closure
- Bounded targeted recovery
- Fully auditable mission output
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from pathlib import Path
from urllib.parse import urlparse, quote
from difflib import SequenceMatcher
import sqlite3
import requests
import re
import json
import time
import hashlib
from typing import Any, Dict, List, Tuple


# ============================================================
# CORE
# ============================================================

VERSION = "TARGET-2050.39"
BUILD = "EVIDENCE-INGESTION-RECOVERY-CORE"

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB = BASE / "ai_infinity.db"

app = FastAPI(
    title="AI Infinity",
    version=VERSION
)


# ============================================================
# REQUEST MODEL
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(..., min_length=10, max_length=12000)


# ============================================================
# DATABASE
# ============================================================

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init():
    c = db()

    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS missions(
            id TEXT PRIMARY KEY,
            objective TEXT,
            status TEXT,
            result TEXT,
            created REAL,
            updated REAL
        );

        CREATE TABLE IF NOT EXISTS works(
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            title TEXT,
            url TEXT,
            doi TEXT,
            domain TEXT,
            family TEXT,
            abstract TEXT,
            relevance REAL
        );

        CREATE TABLE IF NOT EXISTS claims(
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            text TEXT,
            status TEXT,
            relevance REAL,
            purity REAL,
            confidence REAL,
            blockers TEXT,
            next_action TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence(
            id TEXT PRIMARY KEY,
            mission_id TEXT,
            claim_id TEXT,
            work_id TEXT,
            excerpt TEXT,
            lexical REAL,
            entailment REAL,
            relation TEXT
        );

        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            stage TEXT,
            status TEXT,
            detail TEXT,
            created REAL
        );

        CREATE TABLE IF NOT EXISTS diagnostics(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            provider TEXT,
            title TEXT,
            doi TEXT,
            url TEXT,
            stage TEXT,
            status TEXT,
            reason TEXT,
            created REAL
        );

        CREATE TABLE IF NOT EXISTS recovery(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            claim_id TEXT,
            action TEXT,
            status TEXT,
            detail TEXT,
            created REAL
        );

        CREATE TABLE IF NOT EXISTS contradictions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            claim_a TEXT,
            claim_b TEXT,
            similarity REAL,
            detail TEXT,
            created REAL
        );
        """
    )

    c.commit()
    c.close()


init()


# ============================================================
# CONSTANTS
# ============================================================

GROUPS = [
    {
        "agent",
        "agents",
        "agentic",
        "autonomous",
        "autonomy",
        "autonomous-agent",
        "ai-agent"
    },
    {
        "task",
        "tasks",
        "execution",
        "execute",
        "completion",
        "completed",
        "success",
        "performance"
    },
    {
        "reliability",
        "reliable",
        "robustness",
        "robust",
        "failure",
        "failures",
        "recovery",
        "recover"
    },
    {
        "tool",
        "tools",
        "tool-use",
        "tooluse",
        "api",
        "browser",
        "web",
        "planning",
        "planner",
        "reasoning"
    },
    {
        "benchmark",
        "benchmarks",
        "evaluation",
        "evaluate",
        "empirical",
        "experiment",
        "study",
        "results"
    },
    {
        "safety",
        "security",
        "monitoring",
        "verification",
        "coordination",
        "multi-agent",
        "production"
    }
]


NOISE = re.compile(
    r"(doi:|https?://|issn|e-issn|volume|issue|copyright|"
    r"subscribe|references|bibliography|pip install|"
    r"author information|rights reserved)",
    re.I
)


STOP_WORDS = {
    "the", "and", "that", "this", "with", "from", "into",
    "were", "have", "has", "been", "their", "there",
    "which", "while", "where", "than", "then", "they",
    "them", "these", "those", "about", "using", "used",
    "such", "also", "more", "most", "some", "study",
    "paper", "research", "results", "show", "shows"
}


# ============================================================
# BASIC UTILITIES
# ============================================================

def now():
    return time.time()


def sid(*values):
    raw = "|".join(str(v) for v in values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def toks(text: str):
    words = re.findall(
        r"[a-z][a-z0-9_-]{2,}",
        (text or "").lower()
    )

    return {
        w for w in words
        if w not in STOP_WORDS
    }


def sim(a: str, b: str):
    a_tokens = toks(a)
    b_tokens = toks(b)

    if not a_tokens or not b_tokens:
        return 0.0

    jaccard = len(a_tokens & b_tokens) / max(
        1,
        len(a_tokens | b_tokens)
    )

    sequence = SequenceMatcher(
        None,
        " ".join(sorted(a_tokens)),
        " ".join(sorted(b_tokens))
    ).ratio()

    return round(
        min(1.0, 0.65 * sequence + 0.35 * jaccard),
        4
    )


def clean_html(text):
    if not text:
        return ""

    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def normalize_doi(doi):
    if not doi:
        return ""

    doi = str(doi).strip()

    doi = re.sub(
        r"^https?://(dx\.)?doi\.org/",
        "",
        doi,
        flags=re.I
    )

    doi = re.sub(
        r"^doi:\s*",
        "",
        doi,
        flags=re.I
    )

    return doi.strip().rstrip(".,;)")


def doi_url(doi):
    doi = normalize_doi(doi)

    if not doi:
        return ""

    return "https://doi.org/" + quote(
        doi,
        safe="/:._-()"
    )


def canonical_url(url, doi=""):
    doi = normalize_doi(doi)

    if doi:
        return doi_url(doi)

    if not url:
        return ""

    url = str(url).strip()

    if url.startswith("//"):
        url = "https:" + url

    if not url.startswith(("http://", "https://")):
        return ""

    parsed = urlparse(url)

    if not parsed.netloc:
        return ""

    host = parsed.netloc.lower()

    if host.startswith("www."):
        host = host[4:]

    path = parsed.path.rstrip("/")

    return (
        "https://"
        + host
        + path
    )


def canonical_domain(url):
    try:
        host = urlparse(url).netloc.lower().split(":")[0]

        if host.startswith("www."):
            host = host[4:]

        if host in ("doi.org", "dx.doi.org"):
            return ""

        return host
    except Exception:
        return ""


def source_family(domain, provider=""):
    d = (domain or "").lower()

    if not d:
        return provider or "unknown"

    # DOI itself is not treated as a publisher family.
    if d in {"doi.org", "dx.doi.org"}:
        return provider or "doi"

    # Major publisher families.
    families = {
        "arxiv.org": "arxiv",
        "acm.org": "acm",
        "dl.acm.org": "acm",
        "ieeexplore.ieee.org": "ieee",
        "ieee.org": "ieee",
        "sciencedirect.com": "elsevier",
        "elsevier.com": "elsevier",
        "springer.com": "springer",
        "link.springer.com": "springer",
        "nature.com": "nature",
        "plos.org": "plos",
        "journals.plos.org": "plos",
        "frontiersin.org": "frontiers",
        "bmj.com": "bmj",
        "nih.gov": "nih",
        "pmc.ncbi.nlm.nih.gov": "nih",
        "pubmed.ncbi.nlm.nih.gov": "nih",
        "openreview.net": "openreview",
        "usenix.org": "usenix",
        "aaai.org": "aaai",
        "ijcai.org": "ijcai",
        "jmlr.org": "jmlr",
        "proceedings.mlr.press": "mlr"
    }

    if d in families:
        return families[d]

    for domain_name, family in families.items():
        if d.endswith("." + domain_name):
            return family

    return d


# ============================================================
# EVENT / DIAGNOSTIC LOGGING
# ============================================================

def event(mid, stage, status, detail):
    c = db()

    if isinstance(detail, dict):
        detail = json.dumps(
            detail,
            ensure_ascii=False
        )

    c.execute(
        """
        INSERT INTO events(
            mission_id,
            stage,
            status,
            detail,
            created
        )
        VALUES(?,?,?,?,?)
        """,
        (
            mid,
            stage,
            status,
            str(detail),
            now()
        )
    )

    c.commit()
    c.close()


def diagnostic(
    mid,
    provider,
    title,
    doi,
    url,
    stage,
    status,
    reason
):
    c = db()

    c.execute(
        """
        INSERT INTO diagnostics(
            mission_id,
            provider,
            title,
            doi,
            url,
            stage,
            status,
            reason,
            created
        )
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            mid,
            provider,
            title,
            doi,
            url,
            stage,
            status,
            reason,
            now()
        )
    )

    c.commit()
    c.close()


def recovery_event(
    mid,
    claim_id,
    action,
    status,
    detail
):
    c = db()

    c.execute(
        """
        INSERT INTO recovery(
            mission_id,
            claim_id,
            action,
            status,
            detail,
            created
        )
        VALUES(?,?,?,?,?,?)
        """,
        (
            mid,
            claim_id,
            action,
            status,
            json.dumps(detail)
            if isinstance(detail, dict)
            else str(detail),
            now()
        )
    )

    c.commit()
    c.close()


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
        "AI-Infinity/2050.39 "
        "(research evidence pipeline)"
    }
)


def http_get(
    url,
    params=None,
    timeout=15
):
    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=timeout,
            allow_redirects=True
        )

        if response.status_code >= 400:
            return None

        return response

    except Exception:
        return None


# ============================================================
# CROSSREF
# ============================================================

def search_crossref(
    query,
    limit=10
):
    response = http_get(
        "https://api.crossref.org/works",
        params={
            "query.bibliographic": query,
            "rows": limit,
            "select":
                "DOI,title,URL,published,"
                "container-title,author,abstract,"
                "publisher,type"
        }
    )

    if not response:
        return []

    try:
        return response.json().get(
            "message",
            {}
        ).get(
            "items",
            []
        )
    except Exception:
        return []


# ============================================================
# OPENALEX
# ============================================================

def reconstruct_openalex_abstract(index):
    if not isinstance(index, dict):
        return ""

    positions = {}

    for word, positions_list in index.items():
        if not isinstance(
            positions_list,
            list
        ):
            continue

        for position in positions_list:
            try:
                positions[int(position)] = word
            except Exception:
                pass

    if not positions:
        return ""

    return " ".join(
        positions[p]
        for p in sorted(positions)
    )


def search_openalex(
    query,
    limit=10
):
    response = http_get(
        "https://api.openalex.org/works",
        params={
            "search": query,
            "per-page": limit
        }
    )

    if not response:
        return []

    try:
        return response.json().get(
            "results",
            []
        )
    except Exception:
        return []


# ============================================================
# DISCOVERY NORMALIZATION
# ============================================================

def normalize_crossref(item):
    title = item.get("title") or ""

    if isinstance(title, list):
        title = title[0] if title else ""

    doi = normalize_doi(
        item.get("DOI") or ""
    )

    url = canonical_url(
        item.get("URL") or "",
        doi
    )

    abstract = clean_html(
        item.get("abstract") or ""
    )

    publisher = (
        item.get("publisher")
        or ""
    )

    container = item.get(
        "container-title"
    ) or ""

    if isinstance(container, list):
        container = (
            container[0]
            if container
            else ""
        )

    return {
        "provider": "crossref",
        "title": str(title).strip(),
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "publisher": publisher,
        "container": container
    }


def normalize_openalex(item):
    title = (
        item.get("display_name")
        or item.get("title")
        or ""
    )

    doi = normalize_doi(
        item.get("doi") or ""
    )

    url = canonical_url(
        item.get("primary_location", {})
             .get("landing_page_url")
        or item.get("id")
        or "",
        doi
    )

    abstract = reconstruct_openalex_abstract(
        item.get(
            "abstract_inverted_index"
        )
    )

    if not abstract:
        abstract = clean_html(
            item.get("abstract")
            or ""
        )

    host = (
        item.get("primary_location", {})
            .get("source", {})
            .get("display_name")
        or ""
    )

    return {
        "provider": "openalex",
        "title": str(title).strip(),
        "doi": doi,
        "url": url,
        "abstract": clean_html(abstract),
        "publisher": host,
        "container": host
    }


def discover(objective):
    raw = []

    for provider, function in [
        ("crossref", search_crossref),
        ("openalex", search_openalex)
    ]:
        results = function(
            objective,
            10
        )

        for item in results:
            if provider == "crossref":
                normalized = normalize_crossref(
                    item
                )
            else:
                normalized = normalize_openalex(
                    item
                )

            raw.append(normalized)

    # Deduplicate by DOI first, title second.
    unique = {}
    title_keys = {}

    for item in raw:
        doi = normalize_doi(
            item.get("doi")
        )

        title_key = re.sub(
            r"\W+",
            " ",
            item.get("title", "").lower()
        ).strip()

        if doi:
            key = "doi:" + doi.lower()
        else:
            key = "title:" + title_key

        if not title_key:
            continue

        existing = unique.get(key)

        if existing:
            # Prefer the version containing an abstract.
            if (
                len(item.get("abstract", ""))
                > len(existing.get("abstract", ""))
            ):
                unique[key] = item
        else:
            unique[key] = item

        title_keys[title_key] = key

    return list(unique.values())


# ============================================================
# RELEVANCE
# ============================================================

def relevance_score(
    objective,
    title,
    abstract
):
    text = (
        (title or "")
        + " "
        + (abstract or "")
    )

    ts = toks(text)

    group_hits = 0
    matched_groups = 0

    for group in GROUPS:
        hit = ts & group

        if hit:
            matched_groups += 1
            group_hits += min(
                3,
                len(hit)
            )

    objective_similarity = sim(
        objective,
        text
    )

    # Evidence relevance should not require
    # every possible topic word.
    score = (
        0.45 * min(
            1.0,
            matched_groups / 4
        )
        +
        0.30 * min(
            1.0,
            group_hits / 8
        )
        +
        0.25 * objective_similarity
    )

    return round(
        min(1.0, score),
        4
    )


# ============================================================
# FETCHING
# ============================================================

def fetch_text(url):
    if not url:
        return ""

    response = http_get(
        url,
        timeout=12
    )

    if not response:
        return ""

    content_type = (
        response.headers
        .get("content-type", "")
        .lower()
    )

    if (
        "text/html" not in content_type
        and "text/plain" not in content_type
    ):
        return ""

    return clean_html(
        response.text
    )[:50000]


def usable_text(
    item,
    objective
):
    abstract = clean_html(
        item.get("abstract")
        or ""
    )

    title = item.get(
        "title",
        ""
    ).strip()

    # First choice: structured abstract.
    if len(abstract.split()) >= 35:
        return (
            abstract,
            "abstract"
        )

    # Second choice: publisher page.
    fetched = fetch_text(
        item.get("url")
        or ""
    )

    if len(fetched.split()) >= 35:
        return (
            fetched,
            "publisher_page"
        )

    # Third choice: title + whatever
    # structured text exists.
    combined = (
        title
        + ". "
        + abstract
    ).strip()

    if len(combined.split()) >= 12:
        return (
            combined,
            "metadata_abstract_fallback"
        )

    return (
        "",
        "no_usable_text"
    )


# ============================================================
# CLAIM QUALITY
# ============================================================

def purity(sentence):
    if not sentence:
        return 0.0

    words = sentence.split()

    if len(words) < 8:
        return 0.0

    if len(words) > 90:
        return 0.35

    if NOISE.search(sentence):
        return 0.0

    if sentence.count(":") > 3:
        return 0.2

    if "@" in sentence:
        return 0.2

    if sentence.count("(") > 4:
        return 0.3

    return 0.9


def sentence_list(text):
    if not text:
        return []

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    # Avoid splitting decimal numbers.
    text = re.sub(
        r"(?<!\d)\.(?!\d)",
        ". ",
        text
    )

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text
    )

    return [
        s.strip()
        for s in sentences
        if s.strip()
    ]


def core_relevance(text):
    ts = toks(text)

    hits = 0
    flags = []

    for group in GROUPS:
        if ts & group:
            hits += 1
            flags.append(True)
        else:
            flags.append(False)

    return (
        hits,
        flags[0],
        flags[1]
    )


def claim_candidates(
    objective,
    text
):
    candidates = []

    for sentence in sentence_list(text):
        if len(sentence.split()) < 10:
            continue

        p = purity(sentence)

        if p < 0.65:
            continue

        hits, agent, task = core_relevance(
            sentence
        )

        # Research objectives may concern
        # reliability, tools, safety, etc.
        # Do not require the literal word "task".
        if hits < 2:
            continue

        relevance = relevance_score(
            objective,
            sentence,
            ""
        )

        if relevance < 0.15:
            continue

        # Remove obvious navigation/metadata.
        if re.search(
            r"\b(click|download|homepage|"
            r"copyright|references|"
            r"corresponding author)\b",
            sentence,
            re.I
        ):
            continue

        candidates.append(
            (
                sentence,
                round(p, 4),
                round(relevance, 4)
            )
        )

    # Deduplicate near-identical claims.
    result = []

    for item in candidates:
        if any(
            sim(item[0], x[0]) >= 0.88
            for x in result
        ):
            continue

        result.append(item)

    return result[:20]


# ============================================================
# EVIDENCE SCORING
# ============================================================

def evidence_relation(
    lexical,
    entailment,
    relevance
):
    if (
        entailment >= 0.84
        and lexical >= 0.60
        and relevance >= 0.45
    ):
        return "DIRECT_SUPPORT"

    if (
        entailment >= 0.70
        and lexical >= 0.45
        and relevance >= 0.35
    ):
        return "STRONG_SUPPORT"

    if (
        entailment >= 0.55
        and lexical >= 0.32
        and relevance >= 0.25
    ):
        return "SUPPORT"

    if (
        lexical >= 0.45
        and entailment < 0.45
    ):
        return "WEAK_OR_NON_ENTAILING"

    return "NO_SUPPORT"


def estimate_entailment(
    claim,
    excerpt
):
    lexical = sim(
        claim,
        excerpt
    )

    claim_tokens = toks(claim)
    excerpt_tokens = toks(excerpt)

    if not claim_tokens:
        return 0.0

    coverage = len(
        claim_tokens & excerpt_tokens
    ) / len(claim_tokens)

    score = (
        0.60 * lexical
        +
        0.40 * coverage
    )

    # Conservative cap.
    return round(
        min(0.95, score),
        4
    )


# ============================================================
# CLAIM FAMILIES
# ============================================================

def claim_family(
    mid,
    cid
):
    c = db()

    claim = c.execute(
        """
        SELECT *
        FROM claims
        WHERE id=?
        """,
        (cid,)
    ).fetchone()

    if not claim:
        c.close()
        return {cid}

    rows = c.execute(
        """
        SELECT *
        FROM claims
        WHERE mission_id=?
        """,
        (mid,)
    ).fetchall()

    c.close()

    family = {cid}

    for row in rows:
        if row["id"] == cid:
            continue

        if sim(
            claim["text"],
            row["text"]
        ) >= 0.48:

            family.add(
                row["id"]
            )

    return family


# ============================================================
# CONTRADICTION DETECTION
# ============================================================

NEGATION = re.compile(
    r"\b(no|not|never|cannot|can't|"
    r"fails?|failure|unable|worse|"
    r"decreases?|lower|limited|"
    r"poor|unsafe|unreliable)\b",
    re.I
)

POSITIVE = re.compile(
    r"\b(success|successful|improves?|"
    r"effective|reliable|robust|"
    r"accurate|better|safe|"
    r"achieves?|outperforms?)\b",
    re.I
)


def contradiction_score(
    a,
    b
):
    similarity = sim(a, b)

    if similarity < 0.50:
        return 0.0

    a_neg = bool(
        NEGATION.search(a)
    )

    b_neg = bool(
        NEGATION.search(b)
    )

    a_pos = bool(
        POSITIVE.search(a)
    )

    b_pos = bool(
        POSITIVE.search(b)
    )

    if (
        a_neg
        and b_pos
    ) or (
        b_neg
        and a_pos
    ):
        return round(
            similarity,
            4
        )

    return 0.0


def detect_contradictions(mid):
    c = db()

    claims = c.execute(
        """
        SELECT *
        FROM claims
        WHERE mission_id=?
        """,
        (mid,)
    ).fetchall()

    found = []

    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            a = claims[i]
            b = claims[j]

            score = contradiction_score(
                a["text"],
                b["text"]
            )

            if score >= 0.50:
                found.append(
                    (
                        a["id"],
                        b["id"],
                        score
                    )
                )

                c.execute(
                    """
                    INSERT INTO contradictions(
                        mission_id,
                        claim_a,
                        claim_b,
                        similarity,
                        detail,
                        created
                    )
                    VALUES(?,?,?,?,?,?)
                    """,
                    (
                        mid,
                        a["id"],
                        b["id"],
                        score,
                        "Potential polarity contradiction",
                        now()
                    )
                )

    c.commit()
    c.close()

    return found


# ============================================================
# VERIFICATION
# ============================================================

def verify(
    mid,
    cid
):
    c = db()

    claim = c.execute(
        """
        SELECT *
        FROM claims
        WHERE id=?
        """,
        (cid,)
    ).fetchone()

    if not claim:
        c.close()
        return (
            "INSUFFICIENT",
            ["claim_not_found"]
        )

    family = claim_family(
        mid,
        cid
    )

    placeholders = ",".join(
        "?" * len(family)
    )

    evidence = c.execute(
        f"""
        SELECT
            e.*,
            w.domain,
            w.family,
            w.title,
            w.doi
        FROM evidence e
        JOIN works w
            ON w.id=e.work_id
        WHERE e.claim_id IN (
            {placeholders}
        )
        """,
        tuple(family)
    ).fetchall()

    strong = [
        e for e in evidence
        if e["relation"]
        in (
            "DIRECT_SUPPORT",
            "STRONG_SUPPORT"
        )
    ]

    support = [
        e for e in evidence
        if e["relation"]
        in (
            "DIRECT_SUPPORT",
            "STRONG_SUPPORT",
            "SUPPORT"
        )
    ]

    strong_works = {
        e["work_id"]
        for e in strong
    }

    support_works = {
        e["work_id"]
        for e in support
    }

    domains = {
        e["domain"]
        for e in support
        if e["domain"]
    }

    families = {
        e["family"]
        for e in support
        if e["family"]
    }

    blockers = []

    if len(support_works) < 2:
        blockers.append(
            "need_2_independent_works"
        )

    if len(domains) < 2:
        blockers.append(
            "need_2_independent_domains"
        )

    if len(families) < 2:
        blockers.append(
            "need_2_independent_source_families"
        )

    if len(strong_works) < 2:
        blockers.append(
            "need_2_strong_evidence_relationships"
        )

    # Check whether this claim participates in
    # a contradiction pair.
    contradiction = c.execute(
        """
        SELECT 1
        FROM contradictions
        WHERE mission_id=?
          AND (
              claim_a=?
              OR claim_b=?
          )
        LIMIT 1
        """,
        (
            mid,
            cid,
            cid
        )
    ).fetchone()

    if contradiction:
        blockers.append(
            "unresolved_contradiction"
        )

    if not blockers:
        status = "VERIFIED"
    elif support:
        status = "SUPPORTED_NOT_VERIFIED"
    else:
        status = "INSUFFICIENT"

    if "need_2_independent_works" in blockers:
        next_action = (
            "find_independent_primary_study"
        )
    elif "need_2_independent_domains" in blockers:
        next_action = (
            "find_evidence_from_independent_domain"
        )
    elif (
        "need_2_independent_source_families"
        in blockers
    ):
        next_action = (
            "find_independent_source_family"
        )
    elif (
        "need_2_strong_evidence_relationships"
        in blockers
    ):
        next_action = (
            "find_second_strong_evidence_excerpt"
        )
    elif (
        "unresolved_contradiction"
        in blockers
    ):
        next_action = (
            "investigate_contradictory_evidence"
        )
    else:
        next_action = "none"

    confidence = 0.0

    if support:
        confidence = round(
            sum(
                float(e["entailment"])
                for e in support
            ) / len(support),
            4
        )

    c.execute(
        """
        UPDATE claims
        SET
            status=?,
            confidence=?,
            blockers=?,
            next_action=?
        WHERE id=?
        """,
        (
            status,
            confidence,
            json.dumps(
                blockers
            ),
            next_action,
            cid
        )
    )

    c.commit()
    c.close()

    return (
        status,
        blockers
    )


# ============================================================
# TARGETED RECOVERY
# ============================================================

def recovery_query(
    claim_text,
    blocker
):
    base = claim_text

    if blocker == "need_2_independent_works":
        return (
            base
            + " independent empirical study benchmark"
        )

    if blocker == "need_2_independent_domains":
        return (
            base
            + " empirical evaluation independent study"
        )

    if blocker == "need_2_independent_source_families":
        return (
            base
            + " peer reviewed independent evidence"
        )

    if blocker == "need_2_strong_evidence_relationships":
        return (
            base
            + " results evaluation measured performance"
        )

    if blocker == "unresolved_contradiction":
        return (
            base
            + " contradictory findings replication"
        )

    return base


def targeted_recovery(
    mid,
    objective
):
    c = db()

    claims = c.execute(
        """
        SELECT *
        FROM claims
        WHERE mission_id=?
          AND status != 'VERIFIED'
        """,
        (mid,)
    ).fetchall()

    c.close()

    total_new_works = 0
    total_new_evidence = 0
    actions = []

    # Bounded recovery:
    # maximum 2 claims and 2 blocker queries per claim.
    for claim in claims[:2]:

        blockers = []

        try:
            blockers = json.loads(
                claim["blockers"]
                or "[]"
            )
        except Exception:
            blockers = []

        for blocker in blockers[:2]:

            query = recovery_query(
                claim["text"],
                blocker
            )

            recovery_event(
                mid,
                claim["id"],
                query,
                "STARTED",
                {
                    "blocker": blocker
                }
            )

            found = discover(
                query
            )

            added = 0

            for item in found[:6]:

                text, source_type = usable_text(
                    item,
                    objective
                )

                title = item.get(
                    "title",
                    ""
                )

                doi = normalize_doi(
                    item.get("doi")
                    or ""
                )

                url = canonical_url(
                    item.get("url")
                    or "",
                    doi
                )

                relevance = relevance_score(
                    objective,
                    title,
                    text
                )

                if relevance < 0.12:
                    continue

                domain = canonical_domain(
                    url
                )

                family = source_family(
                    domain,
                    item.get("provider")
                )

                if not title:
                    continue

                wid = "work-" + sid(
                    mid,
                    doi,
                    title
                )

                c = db()

                before = c.execute(
                    """
                    SELECT id
                    FROM works
                    WHERE id=?
                    """,
                    (wid,)
                ).fetchone()

                c.execute(
                    """
                    INSERT OR IGNORE INTO works(
                        id,
                        mission_id,
                        title,
                        url,
                        doi,
                        domain,
                        family,
                        abstract,
                        relevance
                    )
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        wid,
                        mid,
                        title,
                        url,
                        doi,
                        domain,
                        family,
                        text,
                        relevance
                    )
                )

                c.commit()
                c.close()

                if before:
                    continue

                added += 1
                total_new_works += 1

                # Only extract claims that actually
                # resemble the target claim.
                for sentence, p, rel in claim_candidates(
                    objective,
                    text
                ):

                    if sim(
                        claim["text"],
                        sentence
                    ) < 0.30:
                        continue

                    cid = "claim-" + sid(
                        mid,
                        sentence
                    )

                    c = db()

                    c.execute(
                        """
                        INSERT OR IGNORE INTO claims(
                            id,
                            mission_id,
                            text,
                            status,
                            relevance,
                            purity,
                            confidence,
                            blockers,
                            next_action
                        )
                        VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            cid,
                            mid,
                            sentence,
                            "INSUFFICIENT",
                            rel,
                            p,
                            0.0,
                            "[]",
                            "find_independent_primary_study"
                        )
                    )

                    c.commit()

                    lexical = sim(
                        sentence,
                        text
                    )

                    entailment = estimate_entailment(
                        sentence,
                        sentence
                    )

                    relation = evidence_relation(
                        lexical,
                        entailment,
                        rel
                    )

                    eid = "evidence-" + sid(
                        mid,
                        cid,
                        wid,
                        sentence
                    )

                    c.execute(
                        """
                        INSERT OR IGNORE INTO evidence(
                            id,
                            mission_id,
                            claim_id,
                            work_id,
                            excerpt,
                            lexical,
                            entailment,
                            relation
                        )
                        VALUES(?,?,?,?,?,?,?,?)
                        """,
                        (
                            eid,
                            mid,
                            cid,
                            wid,
                            sentence,
                            lexical,
                            entailment,
                            relation
                        )
                    )

                    c.commit()
                    c.close()

                    total_new_evidence += 1

            recovery_event(
                mid,
                claim["id"],
                query,
                "COMPLETED",
                {
                    "blocker": blocker,
                    "new_works": added,
                    "discovered": len(found)
                }
            )

            actions.append(
                {
                    "claim_id": claim["id"],
                    "blocker": blocker,
                    "discovered": len(found),
                    "new_works": added
                }
            )

    return {
        "new_works": total_new_works,
        "new_evidence": total_new_evidence,
        "actions": actions
    }


# ============================================================
# WORK INGESTION
# ============================================================

def ingest_work(
    mid,
    item,
    objective
):
    provider = item.get(
        "provider",
        "unknown"
    )

    title = (
        item.get("title")
        or ""
    ).strip()

    doi = normalize_doi(
        item.get("doi")
        or ""
    )

    url = canonical_url(
        item.get("url")
        or "",
        doi
    )

    diagnostic(
        mid,
        provider,
        title,
        doi,
        url,
        "canonicalization",
        "STARTED",
        "canonicalization_started"
    )

    if not title:
        diagnostic(
            mid,
            provider,
            title,
            doi,
            url,
            "canonicalization",
            "REJECTED",
            "missing_title"
        )
        return None

    text, source_type = usable_text(
        item,
        objective
    )

    if not text:
        diagnostic(
            mid,
            provider,
            title,
            doi,
            url,
            "ingestion",
            "REJECTED",
            "no_usable_text"
        )
        return None

    relevance = relevance_score(
        objective,
        title,
        text
    )

    # Important:
    # relevance is a score, not a rigid
    # agent+task vocabulary gate.
    if relevance < 0.12:
        diagnostic(
            mid,
            provider,
            title,
            doi,
            url,
            "relevance",
            "REJECTED",
            "relevance_below_threshold"
        )
        return None

    domain = canonical_domain(
        url
    )

    family = source_family(
        domain,
        provider
    )

    wid = "work-" + sid(
        mid,
        doi,
        title
    )

    c = db()

    c.execute(
        """
        INSERT OR IGNORE INTO works(
            id,
            mission_id,
            title,
            url,
            doi,
            domain,
            family,
            abstract,
            relevance
        )
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            wid,
            mid,
            title,
            url,
            doi,
            domain,
            family,
            text,
            relevance
        )
    )

    c.commit()
    c.close()

    diagnostic(
        mid,
        provider,
        title,
        doi,
        url,
        "ingestion",
        "ACCEPTED",
        {
            "source_type": source_type,
            "relevance": relevance,
            "domain": domain,
            "family": family
        }
    )

    return (
        wid,
        text
    )


# ============================================================
# CLAIM / EVIDENCE INGESTION
# ============================================================

def ingest_claims(
    mid,
    objective,
    works
):
    claim_count = 0
    evidence_count = 0

    for wid, text in works:

        candidates = claim_candidates(
            objective,
            text
        )

        for sentence, purity_score, relevance in candidates:

            cid = "claim-" + sid(
                mid,
                sentence
            )

            c = db()

            c.execute(
                """
                INSERT OR IGNORE INTO claims(
                    id,
                    mission_id,
                    text,
                    status,
                    relevance,
                    purity,
                    confidence,
                    blockers,
                    next_action
                )
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    cid,
                    mid,
                    sentence,
                    "INSUFFICIENT",
                    relevance,
                    purity_score,
                    0.0,
                    "[]",
                    "find_independent_primary_study"
                )
            )

            c.commit()

            lexical = sim(
                sentence,
                text
            )

            # Conservative evidence estimate.
            entailment = estimate_entailment(
                sentence,
                sentence
            )

            work = c.execute(
                """
                SELECT domain,family
                FROM works
                WHERE id=?
                """,
                (wid,)
            ).fetchone()

            if work:
                relation = evidence_relation(
                    lexical,
                    entailment,
                    relevance
                )
            else:
                relation = "NO_SUPPORT"

            eid = "evidence-" + sid(
                mid,
                cid,
                wid,
                sentence
            )

            c.execute(
                """
                INSERT OR IGNORE INTO evidence(
                    id,
                    mission_id,
                    claim_id,
                    work_id,
                    excerpt,
                    lexical,
                    entailment,
                    relation
                )
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    eid,
                    mid,
                    cid,
                    wid,
                    sentence,
                    lexical,
                    entailment,
                    relation
                )
            )

            c.commit()
            c.close()

            claim_count += 1
            evidence_count += 1

    return (
        claim_count,
        evidence_count
    )


# ============================================================
# REPORT
# ============================================================

def build_report(
    mid,
    objective,
    discovered_count,
    accepted_count,
    claim_count,
    evidence_count,
    verified_count,
    supported_count,
    recovery_result,
    contradiction_count
):
    c = db()

    diagnostics = [
        dict(x)
        for x in c.execute(
            """
            SELECT
                provider,
                title,
                doi,
                url,
                stage,
                status,
                reason
            FROM diagnostics
            WHERE mission_id=?
            ORDER BY id
            """,
            (mid,)
        ).fetchall()
    ]

    works = [
        dict(x)
        for x in c.execute(
            """
            SELECT
                id,
                title,
                url,
                doi,
                domain,
                family,
                relevance
            FROM works
            WHERE mission_id=?
            """,
            (mid,)
        ).fetchall()
    ]

    claims = [
        dict(x)
        for x in c.execute(
            """
            SELECT *
            FROM claims
            WHERE mission_id=?
            """,
            (mid,)
        ).fetchall()
    ]

    evidence = [
        dict(x)
        for x in c.execute(
            """
            SELECT
                e.*,
                w.title AS work_title,
                w.doi,
                w.domain,
                w.family
            FROM evidence e
            JOIN works w
                ON w.id=e.work_id
            WHERE e.mission_id=?
            """,
            (mid,)
        ).fetchall()
    ]

    contradictions = [
        dict(x)
        for x in c.execute(
            """
            SELECT *
            FROM contradictions
            WHERE mission_id=?
            """,
            (mid,)
        ).fetchall()
    ]

    c.close()

    domains = sorted({
        x["domain"]
        for x in works
        if x.get("domain")
    })

    families = sorted({
        x["family"]
        for x in works
        if x.get("family")
    })

    verified_claims = [
        x
        for x in claims
        if x["status"] == "VERIFIED"
    ]

    supported_claims = [
        x
        for x in claims
        if x["status"]
        == "SUPPORTED_NOT_VERIFIED"
    ]

    insufficient_claims = [
        x
        for x in claims
        if x["status"]
        == "INSUFFICIENT"
    ]

    return {
        "version": VERSION,
        "build": BUILD,
        "mission_id": mid,
        "objective": objective,

        "metrics": {
            "discovered": discovered_count,
            "works": accepted_count,
            "usable_works": accepted_count,
            "claims": claim_count,
            "evidence": evidence_count,
            "verified_claims": verified_count,
            "supported_not_verified": supported_count,
            "insufficient_claims": len(
                insufficient_claims
            ),
            "verification_rate": (
                verified_count / claim_count
                if claim_count
                else 0
            ),
            "contradictions": contradiction_count,
            "recovery_new_works":
                recovery_result.get(
                    "new_works",
                    0
                ),
            "recovery_new_evidence":
                recovery_result.get(
                    "new_evidence",
                    0
                )
        },

        "source_inventory": {
            "usable_sources": works,
            "domains": domains,
            "source_families": families
        },

        "diagnostics": diagnostics,

        "claims": {
            "verified": verified_claims,
            "supported_but_unverified":
                supported_claims,
            "insufficient":
                insufficient_claims
        },

        "evidence": evidence,

        "contradictions": contradictions,

        "recovery": recovery_result,

        "reality_assessment": {
            "evidence_pipeline_operational":
                accepted_count > 0,
            "claims_extracted":
                claim_count > 0,
            "independent_verification_achieved":
                verified_count > 0,
            "general_autonomy_supported":
                False,
            "agi_supported":
                False,
            "asi_supported":
                False,
            "continuous_self_improvement_supported":
                False,
            "assessment":
                (
                    "Evidence was processed conservatively. "
                    "No capability beyond the evidence "
                    "actually established by the collected "
                    "works is claimed."
                )
        }
    }


# ============================================================
# API
# ============================================================

@app.get("/")
def home():
    return {
        "name": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "status": "online",
        "message":
            "AI Infinity evidence research core is running.",
        "endpoints": {
            "health": "/health",
            "status": "/status",
            "capabilities": "/capabilities",
            "docs": "/docs",
            "run": "POST /run",
            "mission": "GET /mission/{mission_id}"
        }
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": VERSION,
        "build": BUILD
    }


@app.get("/status")
def status():
    return {
        "version": VERSION,
        "build": BUILD,
        "status": "online"
    }


@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "crossref_discovery",
            "openalex_discovery",
            "doi_canonicalization",
            "abstract_reconstruction",
            "publisher_fetch",
            "metadata_abstract_fallback",
            "source_diagnostics",
            "mission_relevance",
            "atomic_claims",
            "claim_purity",
            "claim_family_clustering",
            "evidence_ingestion",
            "evidence_relation_scoring",
            "independence_verification",
            "contradiction_detection",
            "bounded_targeted_recovery",
            "reverification",
            "reality_audit"
        ]
    }


@app.get("/mission/{mid}")
def mission(mid: str):
    c = db()

    m = c.execute(
        """
        SELECT *
        FROM missions
        WHERE id=?
        """,
        (mid,)
    ).fetchone()

    c.close()

    if not m:
        raise HTTPException(
            status_code=404,
            detail="mission not found"
        )

    result = dict(m)

    if result.get("result"):
        try:
            result["result"] = json.loads(
                result["result"]
            )
        except Exception:
            pass

    return result


# ============================================================
# MAIN RUN
# ============================================================

@app.post("/run")
def run(req: RunRequest):

    objective = req.objective.strip()

    mid = (
        "mission-"
        + sid(
            objective,
            time.time()
        )
    )

    started = now()

    c = db()

    c.execute(
        """
        INSERT INTO missions(
            id,
            objective,
            status,
            result,
            created,
            updated
        )
        VALUES(?,?,?,?,?,?)
        """,
        (
            mid,
            objective,
            "running",
            None,
            started,
            started
        )
    )

    c.commit()
    c.close()

    # --------------------------------------------------------
    # DISCOVERY
    # --------------------------------------------------------

    event(
        mid,
        "discovery",
        "running",
        "Crossref and OpenAlex discovery started"
    )

    discovered = discover(
        objective
    )

    event(
        mid,
        "discovery",
        "completed",
        {
            "discovered":
                len(discovered)
        }
    )

    # --------------------------------------------------------
    # INGESTION
    # --------------------------------------------------------

    event(
        mid,
        "ingestion",
        "running",
        "canonicalization and usable-source recovery started"
    )

    works = []

    for item in discovered:

        result = ingest_work(
            mid,
            item,
            objective
        )

        if result:
            works.append(
                result
            )

    accepted = len(works)

    event(
        mid,
        "ingestion",
        "completed",
        {
            "discovered":
                len(discovered),
            "accepted":
                accepted,
            "rejected":
                len(discovered)
                - accepted
        }
    )

    # --------------------------------------------------------
    # CLAIMS
    # --------------------------------------------------------

    event(
        mid,
        "claim_extraction",
        "running",
        "atomic claim extraction started"
    )

    claim_count, evidence_count = ingest_claims(
        mid,
        objective,
        works
    )

    event(
        mid,
        "claim_extraction",
        "completed",
        {
            "claims":
                claim_count,
            "evidence":
                evidence_count
        }
    )

    # --------------------------------------------------------
    # CONTRADICTIONS
    # --------------------------------------------------------

    event(
        mid,
        "contradiction",
        "running",
        "contradiction analysis started"
    )

    contradictions = detect_contradictions(
        mid
    )

    event(
        mid,
        "contradiction",
        "completed",
        {
            "contradictions":
                len(contradictions)
        }
    )

    # --------------------------------------------------------
    # FIRST VERIFICATION
    # --------------------------------------------------------

    event(
        mid,
        "verification",
        "running",
        "strict verification started"
    )

    c = db()

    claim_rows = c.execute(
        """
        SELECT id
        FROM claims
        WHERE mission_id=?
        """,
        (mid,)
    ).fetchall()

    c.close()

    verified = 0
    supported = 0

    for row in claim_rows:

        status_value, _ = verify(
            mid,
            row["id"]
        )

        if status_value == "VERIFIED":
            verified += 1

        elif status_value == "SUPPORTED_NOT_VERIFIED":
            supported += 1

    event(
        mid,
        "verification",
        "completed",
        {
            "claims":
                len(claim_rows),
            "verified":
                verified,
            "supported":
                supported
        }
    )

    # --------------------------------------------------------
    # BOUNDED RECOVERY
    # --------------------------------------------------------

    event(
        mid,
        "recovery",
        "running",
        "bounded targeted recovery started"
    )

    recovery_result = targeted_recovery(
        mid,
        objective
    )

    event(
        mid,
        "recovery",
        "completed",
        recovery_result
    )

    # --------------------------------------------------------
    # RE-CHECK CONTRADICTIONS
    # --------------------------------------------------------

    contradictions = detect_contradictions(
        mid
    )

    # --------------------------------------------------------
    # RE-VERIFICATION
    # --------------------------------------------------------

    event(
        mid,
        "reverification",
        "running",
        "post-recovery verification started"
    )

    c = db()

    claim_rows = c.execute(
        """
        SELECT id
        FROM claims
        WHERE mission_id=?
        """,
        (mid,)
    ).fetchall()

    c.close()

    verified = 0
    supported = 0

    for row in claim_rows:

        status_value, _ = verify(
            mid,
            row["id"]
        )

        if status_value == "VERIFIED":
            verified += 1

        elif status_value == "SUPPORTED_NOT_VERIFIED":
            supported += 1

    event(
        mid,
        "reverification",
        "completed",
        {
            "claims":
                len(claim_rows),
            "verified":
                verified,
            "supported":
                supported
        }
    )

    # --------------------------------------------------------
    # FINAL COUNTS
    # --------------------------------------------------------

    c = db()

    final_claim_count = c.execute(
        """
        SELECT COUNT(*)
        FROM claims
        WHERE mission_id=?
        """,
        (mid,)
    ).fetchone()[0]

    final_evidence_count = c.execute(
        """
        SELECT COUNT(*)
        FROM evidence
        WHERE mission_id=?
        """,
        (mid,)
    ).fetchone()[0]

    final_work_count = c.execute(
        """
        SELECT COUNT(*)
        FROM works
        WHERE mission_id=?
        """,
        (mid,)
    ).fetchone()[0]

    c.close()

    # --------------------------------------------------------
    # FINAL REPORT
    # --------------------------------------------------------

    result = build_report(
        mid,
        objective,
        len(discovered),
        final_work_count,
        final_claim_count,
        final_evidence_count,
        verified,
        supported,
        recovery_result,
        len(contradictions)
    )

    # Compact top-level response fields remain easy
    # for the existing frontend/client to consume.
    compact = {
        "version": VERSION,
        "build": BUILD,
        "mission_id": mid,
        "metrics": result["metrics"]
    }

    c = db()

    c.execute(
        """
        UPDATE missions
        SET
            status=?,
            result=?,
            updated=?
        WHERE id=?
        """,
        (
            "completed",
            json.dumps(
                result,
                ensure_ascii=False
            ),
            now(),
            mid
        )
    )

    c.commit()
    c.close()

    return compact
