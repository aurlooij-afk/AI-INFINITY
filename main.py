"""
AI Infinity
TARGET-2050.28
BUILD: RELEVANCE-GATED-CORROBORATION-CORE

Focus:
- Mission-specific source relevance
- Cross-provider discovery
- Clean scholarly evidence
- Strong claim extraction
- Claim normalization
- Independent corroboration
- Conservative contradiction detection
- Counter-evidence expansion
- Evidence quality telemetry
- Async missions
- SQLite persistence
- Mobile dashboard

Free-first. No API keys required.
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, quote
from collections import defaultdict
import sqlite3
import threading
import requests
import re
import html
import json
import time
import uuid
import hashlib
import math
import xml.etree.ElementTree as ET


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version="TARGET-2050.28"
)

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB = BASE / "ai_infinity.db"

EXECUTOR = ThreadPoolExecutor(max_workers=3)

HTTP_TIMEOUT = 12
MAX_TEXT = 30000
MAX_SOURCES = 28
MAX_CLAIMS = 100

LOCK = threading.Lock()


# ============================================================
# DATABASE
# ============================================================

def db():
    con = sqlite3.connect(DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()

    con.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            mission_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            created_at REAL,
            updated_at REAL
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            kind TEXT,
            content TEXT,
            created_at REAL
        )
    """)

    con.commit()
    con.close()


init_db()


# ============================================================
# MODELS
# ============================================================

class MissionRequest(BaseModel):
    objective: str = Field(..., min_length=5, max_length=10000)


# ============================================================
# BASIC UTILITIES
# ============================================================

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
    "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "this", "that", "these", "those", "it", "its", "their", "they",
    "we", "our", "can", "may", "not", "than", "into", "through",
    "about", "using", "used", "use", "such", "also", "more", "most",
    "which", "when", "where", "how", "what", "why", "all", "both",
    "between", "based", "study", "paper", "research", "system"
}

BAD_PHRASES = {
    "view pdf",
    "download pdf",
    "cite as",
    "submission history",
    "subjects:",
    "table of contents",
    "references",
    "bibliography",
    "figure ",
    "table ",
    "authors:",
    "copyright",
    "license",
    "loading",
    "sign in",
    "cookie",
    "accept cookies",
    "related articles",
    "research questions",
    "chapter "
}

RESEARCH_MARKERS = {
    "experiment", "evaluation", "evaluated", "benchmark",
    "result", "results", "performance", "success", "failure",
    "accuracy", "reliability", "deployment", "real-world",
    "real world", "task", "agent", "agents", "human",
    "intervention", "oversight", "monitoring", "limitation",
    "failure rate", "success rate", "error", "robustness",
    "replication", "empirical", "observed", "measured"
}

POSITIVE_MARKERS = {
    "improved", "increase", "increased", "higher", "better",
    "successful", "success", "effective", "robust", "reliable",
    "outperformed", "reduced failure", "improved performance"
}

NEGATIVE_MARKERS = {
    "failed", "failure", "unreliable", "limitation", "limited",
    "degraded", "degradation", "error", "unsafe", "unstable",
    "poor", "worse", "declined", "unable", "risk"
}


def clean_text(text):
    if not text:
        return ""

    text = html.unescape(str(text))
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)

    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_word(w):
    w = w.lower().strip()

    replacements = {
        "agents": "agent",
        "agentic": "agent",
        "reliability": "reliable",
        "reliably": "reliable",
        "failures": "failure",
        "failed": "failure",
        "evaluations": "evaluation",
        "evaluated": "evaluation",
        "benchmarks": "benchmark",
        "tasks": "task",
        "deployments": "deployment",
        "interventions": "intervention",
        "oversight": "oversight"
    }

    return replacements.get(w, w)


def tokens(text):
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", text.lower())

    out = set()

    for w in words:
        w = normalize_word(w)

        if w not in STOPWORDS:
            out.add(w)

    return out


def token_similarity(a, b):
    A = tokens(a)
    B = tokens(b)

    if not A or not B:
        return 0.0

    return len(A & B) / max(1, len(A | B))


def sentence_split(text):
    text = clean_text(text)

    return [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", text)
        if len(s.strip()) >= 50
    ]


def canonical_doi(value):
    if not value:
        return ""

    s = str(value).strip().lower()

    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)

    return s.strip().rstrip(".")


def domain_of(url):
    if not url:
        return ""

    try:
        host = urlparse(url).netloc.lower()
        host = host.split("@")[-1].split(":")[0]

        if host.startswith("www."):
            host = host[4:]

        return host
    except Exception:
        return ""


def independent_domain(domain):
    if not domain:
        return ""

    parts = domain.split(".")

    if len(parts) <= 2:
        return domain

    if parts[-2] in {"co", "org", "ac", "gov"}:
        return ".".join(parts[-3:])

    return ".".join(parts[-2:])


def source_id(item):
    doi = canonical_doi(item.get("doi"))

    if doi:
        return "doi:" + doi

    arxiv_id = item.get("arxiv_id")

    if arxiv_id:
        return "arxiv:" + arxiv_id

    pmid = item.get("pmid")

    if pmid:
        return "pmid:" + str(pmid)

    url = item.get("url", "")

    return "url:" + hashlib.sha256(url.encode()).hexdigest()[:20]


# ============================================================
# MISSION RELEVANCE
# ============================================================

SYNONYMS = {
    "autonomous": {"autonomous", "agentic", "agent"},
    "agent": {"agent", "agents", "agentic"},
    "reliability": {
        "reliability", "reliable", "robustness", "failure",
        "failures", "error", "success"
    },
    "real": {
        "real", "realworld", "deployment", "production",
        "operational", "practical"
    },
    "task": {
        "task", "execution", "action", "workflow", "operation"
    },
    "human": {
        "human", "oversight", "intervention", "supervision",
        "monitoring"
    },
    "evaluation": {
        "evaluation", "benchmark", "experiment", "empirical",
        "measurement", "study"
    }
}


def mission_concepts(objective):
    raw = tokens(objective)

    concepts = set(raw)

    for key, vals in SYNONYMS.items():
        if raw & vals:
            concepts.add(key)

    return concepts


def relevance_score(objective, title, abstract="", body=""):
    mission = mission_concepts(objective)

    title_tokens = tokens(title)
    abstract_tokens = tokens(abstract)
    body_tokens = tokens(body[:12000])

    if not mission:
        return 0.0

    title_hits = len(mission & title_tokens)
    abstract_hits = len(mission & abstract_tokens)
    body_hits = len(mission & body_tokens)

    score = (
        min(1.0, title_hits / 5) * 0.55 +
        min(1.0, abstract_hits / 8) * 0.30 +
        min(1.0, body_hits / 12) * 0.15
    )

    return round(score, 3)


def strong_mission_match(objective, title, abstract, body):
    score = relevance_score(objective, title, abstract, body)

    mission = mission_concepts(objective)

    combined = tokens(title + " " + abstract)

    hits = len(mission & combined)

    return score >= 0.20 and hits >= 2


# ============================================================
# SOURCE QUALITY / FILTERING
# ============================================================

ARTIFACT_TERMS = {
    "supplementary",
    "supplement",
    "correction",
    "erratum",
    "retraction",
    "editorial",
    "decision",
    "response",
    "commentary",
    "dataset",
    "data paper",
    "protocol"
}


def artifact(text):
    low = (text or "").lower()

    return any(x in low for x in ARTIFACT_TERMS)


def quality_score(item):
    q = 0.55

    if item.get("abstract"):
        q += 0.08

    if item.get("body"):
        q += 0.12

    if item.get("doi"):
        q += 0.06

    if item.get("provider") in {
        "openalex", "semantic_scholar", "crossref",
        "europe_pmc", "arxiv"
    }:
        q += 0.05

    if item.get("tier") == "FULL_TEXT":
        q += 0.08

    return min(0.95, round(q, 2))


# ============================================================
# HTTP
# ============================================================

def get(url, params=None):
    try:
        r = requests.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT,
            headers={
                "User-Agent": "AI-Infinity/2050.28 research-engine"
            }
        )

        if r.status_code >= 400:
            return None

        return r

    except Exception:
        return None


# ============================================================
# OPENALEX
# ============================================================

def search_openalex(query):
    r = get(
        "https://api.openalex.org/works",
        {
            "search": query,
            "per-page": 8
        }
    )

    if not r:
        return []

    try:
        data = r.json()
    except Exception:
        return []

    out = []

    for w in data.get("results", []):
        title = clean_text(w.get("title", ""))

        abstract = ""

        inv = w.get("abstract_inverted_index") or {}

        if inv:
            words = []

            for word, positions in inv.items():
                for p in positions:
                    words.append((p, word))

            words.sort()

            abstract = " ".join(x[1] for x in words)

        doi = canonical_doi(w.get("doi"))

        url = (
            w.get("primary_location", {}) or {}
        ).get("landing_page_url") or w.get("id", "")

        out.append({
            "provider": "openalex",
            "title": title,
            "abstract": clean_text(abstract),
            "doi": doi,
            "url": url,
            "year": w.get("publication_year"),
            "type": w.get("type")
        })

    return out


# ============================================================
# SEMANTIC SCHOLAR
# ============================================================

def search_semantic(query):
    r = get(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        {
            "query": query,
            "limit": 8,
            "fields": "title,abstract,year,url,externalIds,openAccessPdf"
        }
    )

    if not r:
        return []

    try:
        data = r.json()
    except Exception:
        return []

    out = []

    for p in data.get("data", []):
        ids = p.get("externalIds") or {}

        out.append({
            "provider": "semantic_scholar",
            "title": clean_text(p.get("title", "")),
            "abstract": clean_text(p.get("abstract", "")),
            "doi": canonical_doi(ids.get("DOI")),
            "url": p.get("url", ""),
            "pdf": (p.get("openAccessPdf") or {}).get("url", ""),
            "year": p.get("year")
        })

    return out


# ============================================================
# CROSSREF
# ============================================================

def search_crossref(query):
    r = get(
        "https://api.crossref.org/works",
        {
            "query.bibliographic": query,
            "rows": 8
        }
    )

    if not r:
        return []

    try:
        data = r.json()
    except Exception:
        return []

    out = []

    for w in data.get("message", {}).get("items", []):
        title = clean_text(
            " ".join(w.get("title") or [])
        )

        abstract = clean_text(
            w.get("abstract", "")
        )

        typ = str(w.get("type", "")).lower()

        if typ not in {
            "journal-article",
            "proceedings-article",
            "article"
        }:
            continue

        if artifact(title + " " + abstract):
            continue

        out.append({
            "provider": "crossref",
            "title": title,
            "abstract": abstract,
            "doi": canonical_doi(w.get("DOI")),
            "url": w.get("URL", ""),
            "year": (
                (w.get("published-print") or {}).get("date-parts", [[None]])[0][0]
            )
        })

    return out


# ============================================================
# EUROPE PMC
# ============================================================

def search_europe_pmc(query):
    r = get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        {
            "query": query,
            "format": "json",
            "pageSize": 8
        }
    )

    if not r:
        return []

    try:
        data = r.json()
    except Exception:
        return []

    out = []

    for p in data.get("resultList", {}).get("result", []):
        out.append({
            "provider": "europe_pmc",
            "title": clean_text(p.get("title", "")),
            "abstract": clean_text(p.get("abstractText", "")),
            "doi": canonical_doi(p.get("doi")),
            "pmid": p.get("pmid"),
            "url": (
                "https://europepmc.org/article/MED/"
                + str(p.get("pmid"))
                if p.get("pmid") else ""
            ),
            "year": p.get("pubYear")
        })

    return out


# ============================================================
# ARXIV
# ============================================================

def search_arxiv(query):
    url = (
        "https://export.arxiv.org/api/query"
        "?search_query=all:"
        + quote(query)
        + "&start=0&max_results=8"
    )

    r = get(url)

    if not r:
        return []

    try:
        root = ET.fromstring(r.text)
    except Exception:
        return []

    ns = {
        "a": "http://www.w3.org/2005/Atom"
    }

    out = []

    for e in root.findall("a:entry", ns):
        title = clean_text(
            e.findtext("a:title", "", ns)
        )

        abstract = clean_text(
            e.findtext("a:summary", "", ns)
        )

        entry = e.findtext("a:id", "", ns)

        m = re.search(
            r"arxiv\.org/abs/([^?#]+)",
            entry
        )

        if not m:
            continue

        aid = m.group(1)

        out.append({
            "provider": "arxiv",
            "title": title,
            "abstract": abstract,
            "arxiv_id": aid,
            "url": "https://arxiv.org/abs/" + aid,
            "pdf": "https://arxiv.org/pdf/" + aid + ".pdf"
        })

    return out


# ============================================================
# PDF / HTML RECOVERY
# ============================================================

def extract_pdf(url):
    if not url:
        return ""

    try:
        from pypdf import PdfReader

        r = get(url)

        if not r:
            return ""

        tmp = BASE / ("pdf_" + uuid.uuid4().hex + ".pdf")
        tmp.write_bytes(r.content)

        reader = PdfReader(str(tmp))

        pages = []

        for page in reader.pages[:15]:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pass

        try:
            tmp.unlink()
        except Exception:
            pass

        return clean_text(" ".join(pages))[:MAX_TEXT]

    except Exception:
        return ""


def extract_html(url):
    r = get(url)

    if not r:
        return ""

    text = r.text

    low = text.lower()

    if any(x in low for x in [
        "cloudflare",
        "captcha",
        "access denied",
        "enable javascript",
        "sign in to continue"
    ]):
        return ""

    text = re.sub(
        r"<(script|style|nav|header|footer|aside|form).*?</\1>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(r"<[^>]+>", " ", text)

    text = clean_text(text)

    return text[:MAX_TEXT]


def recover(item):
    body = ""

    if item.get("pdf"):
        body = extract_pdf(item["pdf"])

    if len(body) < 800 and item.get("url"):
        body = extract_html(item["url"])

    if len(body) >= 800:
        item["body"] = body
        item["tier"] = "FULL_TEXT"
    elif len(item.get("abstract", "")) >= 120:
        item["body"] = item["abstract"]
        item["tier"] = "ABSTRACT"
    else:
        item["body"] = ""
        item["tier"] = "METADATA"

    return item


# ============================================================
# CLAIM CLEANING
# ============================================================

def bad_sentence(s):
    low = s.lower().strip()

    if len(s) < 80 or len(s) > 650:
        return True

    if any(x in low for x in BAD_PHRASES):
        return True

    if low.startswith(("abstract ", "keywords ", "references ")):
        return True

    if re.search(r"https?://|www\.", s):
        return True

    if s.count("=") > 3:
        return True

    if len(re.findall(r"\b\d{4}\b", s)) >= 4:
        return True

    if re.search(r"\b[A-Z]\s*=\s*[\w(]", s):
        return True

    return False


def claim_score(sentence, objective):
    if bad_sentence(sentence):
        return 0.0

    low = sentence.lower()

    research_hits = sum(
        1 for x in RESEARCH_MARKERS
        if x in low
    )

    rel = relevance_score(
        objective,
        sentence,
        sentence,
        sentence
    )

    empirical = min(1.0, research_hits / 3)

    return round(
        empirical * 0.45 + rel * 0.55,
        3
    )


def claim_stance(text):
    low = text.lower()

    pos = sum(1 for x in POSITIVE_MARKERS if x in low)
    neg = sum(1 for x in NEGATIVE_MARKERS if x in low)

    if neg > pos and neg >= 2:
        return "negative"

    if pos > neg and pos >= 2:
        return "positive"

    if neg:
        return "limitation"

    return "neutral"


def normalize_claim(text):
    t = text.lower()

    replacements = {
        "failure rate": "failure",
        "task success": "success",
        "success rate": "success",
        "human intervention": "intervention",
        "human oversight": "oversight",
        "real-world": "realworld",
        "real world": "realworld",
        "agents": "agent",
        "agentic": "agent"
    }

    for a, b in replacements.items():
        t = t.replace(a, b)

    return re.sub(r"\s+", " ", t).strip()


def extract_claims(item, objective):
    text = item.get("body", "")

    if not text:
        return []

    claims = []

    for s in sentence_split(text):
        score = claim_score(s, objective)

        if score < 0.23:
            continue

        # A real mission-specific claim should contain at least
        # one meaningful mission concept.
        mission = mission_concepts(objective)
        st = tokens(s)

        if len(mission & st) < 2:
            continue

        claims.append({
            "claim_id": "claim-" + uuid.uuid4().hex[:16],
            "text": s,
            "normalized": normalize_claim(s),
            "source_id": item["source_id"],
            "provider": item["provider"],
            "domain": item["domain"],
            "quality": item["quality"],
            "relevance": score,
            "stance": claim_stance(s),
            "status": "UNCERTAIN",
            "supporting_sources": [],
            "contradicting_sources": [],
            "limiting_sources": [],
            "corroborating_sources": []
        })

        if len(claims) >= 5:
            break

    return claims


# ============================================================
# QUERY PLANNING
# ============================================================

def plan_queries(objective):
    base = clean_text(objective)

    topics = [
        "empirical study",
        "benchmark evaluation",
        "task success failure",
        "real world deployment",
        "reliability limitations",
        "human intervention monitoring",
        "independent replication",
        "systematic evaluation",
        "failure modes",
        "performance evaluation"
    ]

    return [
        f"{base} {x}"
        for x in topics
    ]


# ============================================================
# DISCOVERY
# ============================================================

def discover(objective, queries):
    raw = []

    for i, q in enumerate(queries):
        raw.extend(search_openalex(q))
        raw.extend(search_semantic(q))

        if i < 5:
            raw.extend(search_crossref(q))

        if i < 4:
            raw.extend(search_arxiv(q))

        if i < 3:
            raw.extend(search_europe_pmc(q))

    return raw


def filter_sources(raw, objective):
    best = {}

    for item in raw:
        title = item.get("title", "")
        abstract = item.get("abstract", "")

        if not title:
            continue

        if artifact(title + " " + abstract):
            continue

        score = relevance_score(
            objective,
            title,
            abstract,
            ""
        )

        # Stronger gate than 2050.27.
        if score < 0.12:
            continue

        item["source_id"] = source_id(item)

        old = best.get(item["source_id"])

        if not old or len(abstract) > len(old.get("abstract", "")):
            item["relevance"] = score
            best[item["source_id"]] = item

    return list(best.values())


# ============================================================
# EVIDENCE RECOVERY
# ============================================================

def recover_sources(candidates, objective):
    accepted = []

    for item in candidates:
        item = recover(item)

        score = relevance_score(
            objective,
            item.get("title", ""),
            item.get("abstract", ""),
            item.get("body", "")
        )

        item["relevance"] = score

        # Final body-level gate.
        if score < 0.17:
            continue

        if item.get("tier") == "METADATA":
            continue

        item["domain"] = independent_domain(
            domain_of(item.get("url", ""))
        )

        if not item["domain"]:
            if item.get("provider") == "arxiv":
                item["domain"] = "arxiv.org"
            elif item.get("provider") == "europe_pmc":
                item["domain"] = "europepmc.org"

        item["quality"] = quality_score(item)

        accepted.append(item)

    accepted.sort(
        key=lambda x: (
            x.get("relevance", 0),
            x.get("quality", 0),
            len(x.get("body", ""))
        ),
        reverse=True
    )

    return accepted[:MAX_SOURCES]


# ============================================================
# GRAPH
# ============================================================

def same_proposition(a, b):
    A = tokens(a["normalized"])
    B = tokens(b["normalized"])

    if not A or not B:
        return 0.0

    overlap = len(A & B)

    containment = overlap / min(len(A), len(B))

    jaccard = overlap / max(1, len(A | B))

    return 0.65 * containment + 0.35 * jaccard


def build_graph(claims):
    edges = []

    for i in range(len(claims)):
        a = claims[i]

        for j in range(i + 1, len(claims)):
            b = claims[j]

            if a["source_id"] == b["source_id"]:
                continue

            sim = same_proposition(a, b)

            # Much stronger than the old generic-token matching.
            if sim < 0.48:
                continue

            # Same proposition, independent work.
            if (
                a["stance"] == b["stance"]
                and a["stance"] in {"positive", "negative"}
            ):
                relation = "CORROBORATES"
            elif (
                a["stance"] == "negative"
                and b["stance"] == "positive"
            ) or (
                a["stance"] == "positive"
                and b["stance"] == "negative"
            ):
                relation = "CONTRADICTS"
            elif (
                a["stance"] == "limitation"
                or b["stance"] == "limitation"
            ):
                relation = "LIMITS"
            else:
                continue

            edges.append({
                "from": a["claim_id"],
                "to": b["claim_id"],
                "relation": relation,
                "similarity": round(sim, 3),
                "source_a": a["source_id"],
                "source_b": b["source_id"]
            })

    return edges


# ============================================================
# VERIFICATION
# ============================================================

def verify_claims(claims, edges):
    by_id = {c["claim_id"]: c for c in claims}

    support = defaultdict(set)
    contradiction = defaultdict(set)
    limits = defaultdict(set)

    for e in edges:
        a = by_id.get(e["from"])
        b = by_id.get(e["to"])

        if not a or not b:
            continue

        if e["relation"] == "CORROBORATES":
            support[a["claim_id"]].add(b["source_id"])
            support[b["claim_id"]].add(a["source_id"])

        elif e["relation"] == "CONTRADICTS":
            contradiction[a["claim_id"]].add(b["source_id"])
            contradiction[b["claim_id"]].add(a["source_id"])

        elif e["relation"] == "LIMITS":
            limits[a["claim_id"]].add(b["source_id"])
            limits[b["claim_id"]].add(a["source_id"])

    for c in claims:
        cid = c["claim_id"]

        c["supporting_sources"] = sorted(support[cid])
        c["corroborating_sources"] = sorted(support[cid])
        c["contradicting_sources"] = sorted(contradiction[cid])
        c["limiting_sources"] = sorted(limits[cid])

        source_count = len(support[cid])
        contradiction_count = len(contradiction[cid])

        if source_count >= 2 and contradiction_count == 0:
            c["status"] = "VERIFIED"

        elif contradiction_count >= 2 and contradiction_count > source_count:
            c["status"] = "CONTRADICTED"

        elif limits[cid]:
            c["status"] = "LIMITED"

        else:
            c["status"] = "UNCERTAIN"

    return claims


# ============================================================
# COUNTER EVIDENCE
# ============================================================

def counter_queries(claim):
    text = claim["text"]

    return [
        text + " failure limitation",
        text + " contradictory evidence",
        text + " replication",
        text + " null result",
        text + " limitations",
        text + " independent evaluation"
    ]


def counter_evidence(objective, claims, original_ids):
    ranked = sorted(
        claims,
        key=lambda c: (
            c["relevance"],
            c["quality"]
        ),
        reverse=True
    )[:8]

    found = []
    tested = []

    original_ids = set(original_ids)

    for claim in ranked:
        tested.append(claim["claim_id"])

        for q in counter_queries(claim)[:4]:
            results = []

            results.extend(search_openalex(q))
            results.extend(search_semantic(q))
            results.extend(search_arxiv(q))

            for item in results:
                sid = source_id(item)

                if sid in original_ids:
                    continue

                title = item.get("title", "")
                abstract = item.get("abstract", "")

                rel = relevance_score(
                    objective,
                    title,
                    abstract,
                    ""
                )

                if rel < 0.18:
                    continue

                found.append({
                    "source_id": sid,
                    "title": title,
                    "provider": item.get("provider"),
                    "relevance": rel,
                    "target_claim": claim["claim_id"]
                })

    # Deduplicate.
    unique = {}

    for x in found:
        unique[x["source_id"]] = x

    return {
        "sources": list(unique.values())[:12],
        "claims_tested": tested
    }


# ============================================================
# SYNTHESIS
# ============================================================

def synthesis(objective, claims, edges, counter):
    verified = [
        c for c in claims
        if c["status"] == "VERIFIED"
    ]

    limited = [
        c for c in claims
        if c["status"] == "LIMITED"
    ]

    contradicted = [
        c for c in claims
        if c["status"] == "CONTRADICTED"
    ]

    uncertain = [
        c for c in claims
        if c["status"] == "UNCERTAIN"
    ]

    if verified:
        conclusion = (
            f"The research produced {len(verified)} independently "
            f"corroborated claim(s). These have evidence from multiple "
            f"independent works and no stronger contradictory evidence "
            f"was detected by the current verification rules."
        )
    else:
        conclusion = (
            "The research produced substantial evidence but did not "
            "meet the system's threshold for independent verification. "
            "The result should therefore be treated as evidence-bearing "
            "but not conclusively verified."
        )

    actions = [
        "Prioritize claims with multiple independent works.",
        "Run targeted counter-evidence searches on important claims.",
        "Prefer empirical evaluations and real-world task measurements.",
        "Separate model capability from end-to-end system reliability.",
        "Require independent replication before treating a strong claim as verified."
    ]

    if contradicted:
        actions.insert(
            0,
            "Investigate the contradictory evidence before accepting the affected claims."
        )

    return {
        "conclusion": conclusion,
        "verified_claims": len(verified),
        "limited_claims": len(limited),
        "contradicted_claims": len(contradicted),
        "uncertain_claims": len(uncertain),
        "next_actions": actions
    }


# ============================================================
# TELEMETRY
# ============================================================

def telemetry(sources, claims, edges, counter):
    domains = sorted({
        x.get("domain")
        for x in sources
        if x.get("domain")
    })

    works = sorted({
        x.get("source_id")
        for x in sources
    })

    verified = sum(
        1 for c in claims
        if c["status"] == "VERIFIED"
    )

    contradictions = sum(
        1 for e in edges
        if e["relation"] == "CONTRADICTS"
    )

    corroborations = sum(
        1 for e in edges
        if e["relation"] == "CORROBORATES"
    )

    quality = (
        sum(x.get("quality", 0) for x in sources)
        / max(1, len(sources))
    )

    graph_factor = min(
        1.0,
        len(edges) / max(1, len(claims) * 0.35)
    )

    verification_factor = (
        verified / max(1, len(claims))
    )

    independence_factor = min(
        1.0,
        len(domains) / 6
    )

    strength = (
        quality * 0.35 +
        graph_factor * 0.25 +
        independence_factor * 0.20 +
        verification_factor * 0.20
    )

    return {
        "sources": len(sources),
        "claims": len(claims),
        "verified": verified,
        "edges": len(edges),
        "corroborations": corroborations,
        "contradictions": contradictions,
        "counter_evidence": len(counter.get("sources", [])),
        "domains": len(domains),
        "independent_works": len(works),
        "average_source_quality": round(quality, 3),
        "strength": round(min(0.99, strength), 3),
        "independent_domains": domains
    }


# ============================================================
# MISSION ENGINE
# ============================================================

def run_mission(mission_id, objective):
    started = time.time()

    try:
        queries = plan_queries(objective)

        raw = discover(objective, queries)

        candidates = filter_sources(
            raw,
            objective
        )

        sources = recover_sources(
            candidates,
            objective
        )

        claims = []

        for source in sources:
            claims.extend(
                extract_claims(
                    source,
                    objective
                )
            )

        # Global claim deduplication.
        unique = []

        for claim in claims:
            duplicate = False

            for old in unique:
                if (
                    claim["source_id"] == old["source_id"]
                    and token_similarity(
                        claim["normalized"],
                        old["normalized"]
                    ) >= 0.82
                ):
                    duplicate = True
                    break

            if not duplicate:
                unique.append(claim)

        claims = unique[:MAX_CLAIMS]

        edges = build_graph(claims)

        claims = verify_claims(
            claims,
            edges
        )

        counter = counter_evidence(
            objective,
            claims,
            [x["source_id"] for x in sources]
        )

        summary = synthesis(
            objective,
            claims,
            edges,
            counter
        )

        stats = telemetry(
            sources,
            claims,
            edges,
            counter
        )

        trace = [
            {
                "stage": "query_planning",
                "status": "completed",
                "queries": queries,
                "count": len(queries)
            },
            {
                "stage": "multi_provider_discovery",
                "status": "completed",
                "sources_discovered": len(raw),
                "providers": sorted(
                    set(x.get("provider") for x in raw)
                )
            },
            {
                "stage": "relevance_gate",
                "status": "completed",
                "candidate_works": len(candidates)
            },
            {
                "stage": "evidence_recovery",
                "status": "completed",
                "accepted_sources": len(sources),
                "full_text": sum(
                    x.get("tier") == "FULL_TEXT"
                    for x in sources
                ),
                "abstract": sum(
                    x.get("tier") == "ABSTRACT"
                    for x in sources
                )
            },
            {
                "stage": "claim_extraction",
                "status": "completed",
                "substantive_claims": len(claims)
            },
            {
                "stage": "evidence_graph",
                "status": "completed",
                "nodes": len(sources) + len(claims),
                "edges": len(edges)
            },
            {
                "stage": "verification",
                "status": "completed",
                "verified": sum(
                    c["status"] == "VERIFIED"
                    for c in claims
                ),
                "uncertain": sum(
                    c["status"] == "UNCERTAIN"
                    for c in claims
                ),
                "contradicted": sum(
                    c["status"] == "CONTRADICTED"
                    for c in claims
                ),
                "limited": sum(
                    c["status"] == "LIMITED"
                    for c in claims
                )
            },
            {
                "stage": "counter_evidence",
                "status": "completed",
                "sources": len(counter["sources"]),
                "claims_tested": len(counter["claims_tested"])
            },
            {
                "stage": "synthesis",
                "status": "completed"
            }
        ]

        result = {
            "task_id": mission_id,
            "mission_id": mission_id,
            "status": "completed",
            "version": "TARGET-2050.28",
            "build": "RELEVANCE-GATED-CORROBORATION-CORE",
            "objective": objective,
            "agent_trace": trace,
            "providers": {
                "used": sorted(
                    set(x.get("provider") for x in sources)
                ),
                "count": len(set(
                    x.get("provider")
                    for x in sources
                ))
            },
            "evidence": {
                "count": len(sources),
                "graph_nodes": len(sources) + len(claims),
                "graph_edges": len(edges),
                "relationships": {
                    "CORROBORATES": sum(
                        e["relation"] == "CORROBORATES"
                        for e in edges
                    ),
                    "SUPPORTS": sum(
                        e["relation"] == "SUPPORTS"
                        for e in edges
                    ),
                    "CONTRADICTS": sum(
                        e["relation"] == "CONTRADICTS"
                        for e in edges
                    ),
                    "LIMITS": sum(
                        e["relation"] == "LIMITS"
                        for e in edges
                    )
                },
                "independent_domains": stats["domains"],
                "domains": stats["independent_domains"],
                "independent_works": stats["independent_works"],
                "average_source_quality": stats[
                    "average_source_quality"
                ],
                "evidence_tiers": {
                    "FULL_TEXT": sum(
                        x.get("tier") == "FULL_TEXT"
                        for x in sources
                    ),
                    "ABSTRACT": sum(
                        x.get("tier") == "ABSTRACT"
                        for x in sources
                    )
                }
            },
            "claims": claims,
            "counter_evidence": counter,
            "synthesis": summary,
            "telemetry": stats,
            "duration_seconds": round(
                time.time() - started,
                2
            )
        }

        save_mission(
            mission_id,
            "completed",
            result
        )

        return result

    except Exception as e:
        result = {
            "task_id": mission_id,
            "mission_id": mission_id,
            "status": "failed",
            "version": "TARGET-2050.28",
            "error": str(e)
        }

        save_mission(
            mission_id,
            "failed",
            result
        )

        return result


# ============================================================
# PERSISTENCE
# ============================================================

def save_mission(mission_id, status, result):
    con = db()

    con.execute("""
        UPDATE missions
        SET status = ?, result = ?, updated_at = ?
        WHERE mission_id = ?
    """, (
        status,
        json.dumps(result, ensure_ascii=False),
        time.time(),
        mission_id
    ))

    con.commit()
    con.close()


def worker(mission_id, objective):
    return run_mission(
        mission_id,
        objective
    )


# ============================================================
# API
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "TARGET-2050.28"
    }


@app.get("/status")
def status():
    con = db()

    row = con.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(status='running') AS running,
            SUM(status='completed') AS completed,
            SUM(status='failed') AS failed
        FROM missions
    """).fetchone()

    con.close()

    return {
        "version": "TARGET-2050.28",
        "build": "RELEVANCE-GATED-CORROBORATION-CORE",
        "missions": {
            "total": row["total"] or 0,
            "running": row["running"] or 0,
            "completed": row["completed"] or 0,
            "failed": row["failed"] or 0
        }
    }


@app.get("/missions")
def missions():
    con = db()

    rows = con.execute("""
        SELECT mission_id, objective, status,
               created_at, updated_at
        FROM missions
        ORDER BY created_at DESC
        LIMIT 50
    """).fetchall()

    con.close()

    return [
        dict(x)
        for x in rows
    ]


@app.post("/mission")
def create_mission(req: MissionRequest):
    mission_id = (
        "mission-" +
        uuid.uuid4().hex[:12]
    )

    now = time.time()

    con = db()

    con.execute("""
        INSERT INTO missions
        (mission_id, objective, status,
         result, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        mission_id,
        req.objective,
        "running",
        None,
        now,
        now
    ))

    con.commit()
    con.close()

    EXECUTOR.submit(
        worker,
        mission_id,
        req.objective
    )

    return JSONResponse(
        status_code=202,
        content={
            "mission_id": mission_id,
            "status": "running",
            "version": "TARGET-2050.28"
        }
    )


@app.get("/mission/{mission_id}")
def get_mission(mission_id: str):
    con = db()

    row = con.execute("""
        SELECT *
        FROM missions
        WHERE mission_id = ?
    """, (mission_id,)).fetchone()

    con.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Mission not found"
        )

    result = None

    if row["result"]:
        try:
            result = json.loads(row["result"])
        except Exception:
            result = row["result"]

    return {
        "mission_id": row["mission_id"],
        "objective": row["objective"],
        "status": row["status"],
        "result": result,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"]
    }


# ============================================================
# MOBILE DASHBOARD
# ============================================================

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body{
    margin:0;
    background:#070b12;
    color:#f4f7fb;
    font-family:Arial,sans-serif;
}
main{
    max-width:760px;
    margin:auto;
    padding:28px 18px 80px;
}
h1{
    font-size:32px;
    margin-bottom:6px;
}
.sub{
    color:#9aa7b8;
    margin-bottom:24px;
}
textarea{
    width:100%;
    box-sizing:border-box;
    min-height:150px;
    padding:18px;
    border-radius:18px;
    background:#0d1420;
    color:white;
    border:1px solid #243247;
    font-size:16px;
}
button{
    width:100%;
    margin-top:14px;
    padding:18px;
    border:0;
    border-radius:18px;
    font-size:17px;
    font-weight:bold;
}
.grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:12px;
    margin-top:26px;
}
.card{
    background:#0c131e;
    border:1px solid #1e2b3e;
    border-radius:18px;
    padding:18px;
}
.label{
    color:#9aa7b8;
    font-size:14px;
}
.value{
    font-size:28px;
    font-weight:bold;
    margin-top:8px;
}
pre{
    white-space:pre-wrap;
    word-break:break-word;
    background:#0c131e;
    padding:18px;
    border-radius:18px;
    overflow:auto;
}
</style>
</head>

<body>
<main>

<h1>AI Infinity</h1>
<div class="sub">
TARGET-2050.28 · Evidence Reasoning Core
</div>

<textarea id="objective"
placeholder="Enter a research objective..."></textarea>

<button onclick="start()">
START AUTONOMOUS RESEARCH
</button>

<div class="grid">
<div class="card">
<div class="label">Sources</div>
<div id="sources" class="value">0</div>
</div>

<div class="card">
<div class="label">Claims</div>
<div id="claims" class="value">0</div>
</div>

<div class="card">
<div class="label">Verified</div>
<div id="verified" class="value">0</div>
</div>

<div class="card">
<div class="label">Edges</div>
<div id="edges" class="value">0</div>
</div>

<div class="card">
<div class="label">Domains</div>
<div id="domains" class="value">0</div>
</div>

<div class="card">
<div class="label">Contradictions</div>
<div id="contradictions" class="value">0</div>
</div>

<div class="card">
<div class="label">Counter Evidence</div>
<div id="counter" class="value">0</div>
</div>

<div class="card">
<div class="label">Strength</div>
<div id="strength" class="value">0</div>
</div>
</div>

<h2>Mission Output</h2>
<pre id="output">Ready.</pre>

</main>

<script>

let timer=null;

async function start(){

    const objective =
        document.getElementById("objective").value.trim();

    if(!objective){
        alert("Enter a research objective.");
        return;
    }

    const r=await fetch("/mission",{
        method:"POST",
        headers:{
            "Content-Type":"application/json"
        },
        body:JSON.stringify({
            objective:objective
        })
    });

    const data=await r.json();

    document.getElementById("output")
        .textContent=JSON.stringify(data,null,2);

    if(data.mission_id){
        poll(data.mission_id);
    }
}

async function poll(id){

    if(timer) clearTimeout(timer);

    try{

        const r=await fetch(
            "/mission/"+id
        );

        const data=await r.json();

        document.getElementById("output")
            .textContent=JSON.stringify(
                data.result || data,
                null,
                2
            );

        const result=data.result;

        if(result && result.telemetry){

            const t=result.telemetry;

            document.getElementById("sources")
                .textContent=t.sources || 0;

            document.getElementById("claims")
                .textContent=t.claims || 0;

            document.getElementById("verified")
                .textContent=t.verified || 0;

            document.getElementById("edges")
                .textContent=t.edges || 0;

            document.getElementById("domains")
                .textContent=t.domains || 0;

            document.getElementById("contradictions")
                .textContent=t.contradictions || 0;

            document.getElementById("counter")
                .textContent=t.counter_evidence || 0;

            document.getElementById("strength")
                .textContent=t.strength || 0;
        }

        if(data.status==="running"){
            timer=setTimeout(
                ()=>poll(id),
                4000
            );
        }

    }catch(e){

        timer=setTimeout(
            ()=>poll(id),
            5000
        );
    }
}

</script>
</body>
</html>
"""
