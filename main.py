"""
AI Infinity
TARGET-2050.14
BUILD: EVIDENCE-QUALITY + CLAIM-GROUNDING

Preserves the TARGET-2050.x single-file FastAPI runtime:
- SQLite persistence
- research / evidence collection
- provenance
- memory / genomes
- safe registered execution
- controlled execution graph
- free-first governor
- fail-closed behavior

Adds:
- clean query generation
- source/claim separation
- substantive claim extraction
- source independence scoring
- evidence relation classification
- claim-level verification
- matched counter-evidence
- contradiction clusters
- automatic next-cycle research actions
- stronger evidence gate
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


VERSION = "TARGET-2050.14"
EVIDENCE_VERSION = "EVIDENCE-INTEGRITY-4.0"
BASE = Path(os.getenv("AI_INFINITY_HOME", "/tmp/ai-infinity"))
DB_PATH = BASE / "ai_infinity.db"
for d in (BASE, BASE / "artifacts", BASE / "work", BASE / "logs"):
    d.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = int(os.getenv("AI_INFINITY_HTTP_TIMEOUT", "18"))
MAX_SOURCE_BYTES = int(os.getenv("AI_INFINITY_MAX_SOURCE_BYTES", "140000"))
MAX_DISCOVERY_PER_QUERY = int(os.getenv("AI_INFINITY_DISCOVERY_PER_QUERY", "5"))
MAX_COLLECTED_SOURCES = int(os.getenv("AI_INFINITY_MAX_SOURCES", "16"))
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_MODEL = os.getenv("HF_MODEL", "openai/gpt-oss-120b:cheapest")
USER_AGENT = f"AI-Infinity/{VERSION}"

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Evidence-first intelligence fabric with claim-grounded verification."
)


class RunRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=12000)
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


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS tasks(
      id TEXT PRIMARY KEY, objective TEXT, status TEXT,
      result_json TEXT, created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS memories(
      id TEXT PRIMARY KEY, kind TEXT, content TEXT,
      verified INTEGER, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS genomes(
      id TEXT PRIMARY KEY, task_id TEXT, genome_json TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS evidence(
      id TEXT PRIMARY KEY, task_id TEXT, source_url TEXT,
      source_title TEXT, domain TEXT, claim TEXT, excerpt TEXT,
      content_hash TEXT, verification_status TEXT, relevance REAL,
      metadata_json TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS research(
      id TEXT PRIMARY KEY, task_id TEXT, question TEXT,
      status TEXT, report_json TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS events(
      id TEXT PRIMARY KEY, task_id TEXT, event_type TEXT,
      data_json TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS failures(
      id TEXT PRIMARY KEY, task_id TEXT, stage TEXT,
      error TEXT, recovery_json TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS executions(
      id TEXT PRIMARY KEY, task_id TEXT, action TEXT,
      status TEXT, result_json TEXT, created_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_evidence_task ON evidence(task_id);
    CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
    CREATE INDEX IF NOT EXISTS idx_memory_kind ON memories(kind);
    """)
    c.commit()
    c.close()


init_db()


def event(task_id: Optional[str], event_type: str, data: Any) -> None:
    c = db()
    c.execute(
        "INSERT INTO events VALUES(?,?,?,?,?)",
        (uid("event"), task_id, event_type, dump(data), now_iso())
    )
    c.commit()
    c.close()


def save_task(task_id: str, objective: str, status: str, result: Any = None) -> None:
    t = now_iso()
    c = db()
    c.execute("""
        INSERT INTO tasks(id,objective,status,result_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          status=excluded.status,
          result_json=excluded.result_json,
          updated_at=excluded.updated_at
    """, (task_id, objective, status, dump(result) if result is not None else None, t, t))
    c.commit()
    c.close()


def save_memory(content: str, kind: str, verified: bool) -> str:
    mid = uid("memory")
    c = db()
    c.execute(
        "INSERT INTO memories VALUES(?,?,?,?,?)",
        (mid, kind, content, int(verified), now_iso())
    )
    c.commit()
    c.close()
    return mid


def save_genome(task_id: str, genome: Dict[str, Any]) -> str:
    gid = uid("genome")
    c = db()
    c.execute(
        "INSERT INTO genomes VALUES(?,?,?,?)",
        (gid, task_id, dump(genome), now_iso())
    )
    c.commit()
    c.close()
    return gid


def save_research(task_id: str, question: str, status: str, report: Dict[str, Any]) -> str:
    rid = uid("research")
    c = db()
    c.execute(
        "INSERT INTO research VALUES(?,?,?,?,?,?)",
        (rid, task_id, question, status, dump(report), now_iso())
    )
    c.commit()
    c.close()
    return rid


# ---------------------------------------------------------------------------
# Text / identity
# ---------------------------------------------------------------------------

STOP = set("""
a an and are as at be been being by for from had has have how i if in into is
it its me more most of on or our that the their them there these they this to
was were what when where which who why will with you your about become need
needed use using used than then can could should would may might do does did
not only all any each other such through based per very real world make made
research study studies paper papers article articles evidence source sources
""".split())

RESEARCH_TERMS = {
    "agent","agents","autonomous","autonomy","task","tasks","execution",
    "reliable","reliability","planning","planner","tool","tools","verification",
    "verify","safety","security","memory","evaluation","benchmark","failure",
    "monitoring","control","reasoning","workflow","multi-agent","agentic",
    "alignment","evidence","provenance","grounding","recovery","robust",
    "uncertainty","performance","experiment","results","limitations","risk"
}

POSITIVE = {
    "improve","improved","improves","effective","reliable","reliability",
    "success","successful","safe","safety","verified","verification","robust",
    "accurate","supports","supported","works","validated","valid","strengthen"
}

NEGATIVE = {
    "fail","fails","failed","failure","unreliable","unsafe","risk","risks",
    "limitation","limitations","cannot","unable","error","errors","incorrect",
    "inaccurate","harm","harms","degrades","weak","uncertain","poor","worse"
}

NOISE_PHRASES = (
    "create account","log in","sign up","donate","view pdf","submission history",
    "cite this","html version","export bibtex","navigation","cookie","menu",
    "privacy policy","terms of use","feedback","register","forgot password"
)


def tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", (text or "").lower())
    return {w for w in words if w not in STOP}


def similarity(a: str, b: str) -> float:
    A, B = tokens(a), tokens(b)
    if not A or not B:
        return 0.0
    return len(A & B) / max(1, len(A | B))


def keyword_hits(text: str) -> set:
    low = (text or "").lower()
    return {x for x in RESEARCH_TERMS if x in low}


def normalize_title(title: str) -> str:
    t = re.sub(r"\[[^]]+\]", " ", (title or "").lower())
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return re.sub(r"\s+", " ", t)


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def source_family(url: str) -> str:
    d = domain_of(url)
    if "arxiv.org" in d:
        return "preprint"
    if "doi.org" in d or "crossref.org" in d:
        return "doi-resolver"
    if "openalex.org" in d:
        return "bibliographic-index"
    if "wikipedia.org" in d:
        return "encyclopedia"
    return d or "unknown"


def work_identity(item: Dict[str, Any]) -> str:
    doi = str(item.get("doi") or "").lower()
    doi = doi.replace("https://doi.org/", "").replace("doi:", "").strip()
    if doi:
        return "doi:" + doi
    title = normalize_title(str(item.get("title", "")))
    if title:
        return "title:" + hashlib.sha1(title.encode()).hexdigest()[:20]
    return "unknown:" + hashlib.sha1(dump(item).encode()).hexdigest()[:20]


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = []
    for part in parts:
        s = part.strip()
        low = s.lower()
        if len(s) < 35:
            continue
        if any(n in low for n in NOISE_PHRASES) and len(s) < 190:
            continue
        if s.count("|") >= 3:
            continue
        kept.append(s)
    return (" ".join(kept) if kept else text)[:MAX_SOURCE_BYTES]


# ---------------------------------------------------------------------------
# Research query engine
# ---------------------------------------------------------------------------

def research_anchor(question: str) -> str:
    clean = re.sub(r"\s+", " ", (question or "").strip())
    clean = re.sub(r"[,:;]+", " ", clean)
    words = clean.split()
    if len(words) > 24:
        words = words[:24]
    return " ".join(words).strip(" .")


def clean_query(q: str) -> str:
    q = re.sub(r"\s+", " ", q or "").strip(" ,.;:-")
    q = re.sub(r"\s+,", ",", q)
    q = re.sub(r",\s*(and\s*)?,", " ", q, flags=re.I)
    q = re.sub(r"\b(and\s*){2,}", "and ", q, flags=re.I)
    return q.strip(" ,.;")


def topic_queries(question: str) -> List[str]:
    a = research_anchor(question)
    candidates = [
        f"{a} empirical evidence evaluation",
        f"{a} reliability failure recovery",
        f"{a} independent benchmark real world",
        f"{a} limitations risks safety",
        f"{a} systematic review",
        f"{a} contradictory evidence criticism",
    ]
    out = []
    for q in candidates:
        q = clean_query(q)
        if q and q not in out:
            out.append(q)
    return out


def expand_queries(question: str) -> List[str]:
    a = research_anchor(question)
    candidates = [a] + topic_queries(question)
    out = []
    for q in candidates:
        q = clean_query(q)
        if q and q not in out:
            out.append(q)
    return out[:8]


# ---------------------------------------------------------------------------
# Network research providers
# ---------------------------------------------------------------------------

def fetch_url(url: str) -> Dict[str, Any]:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("unsupported_url")

    r = requests.get(
        url,
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,text/plain,application/json,application/xml,*/*;q=0.2"
        },
        allow_redirects=True,
        stream=True,
    )
    data = bytearray()
    for chunk in r.iter_content(8192):
        data.extend(chunk)
        if len(data) >= MAX_SOURCE_BYTES:
            break

    raw = bytes(data[:MAX_SOURCE_BYTES])
    raw_text = raw.decode("utf-8", errors="replace")
    ct = r.headers.get("content-type", "")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_text, re.I | re.S)
    title = (
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", title_match.group(1))).strip()
        if title_match else r.url
    )

    text = raw_text
    if "html" in ct.lower() or "<html" in raw_text[:1200].lower():
        for tag in ("script", "style", "noscript", "nav", "header", "footer"):
            text = re.sub(rf"(?is)<{tag}.*?</{tag}>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)

    text = clean_text(text)
    return {
        "url": r.url,
        "title": title[:500] or r.url,
        "text": text,
        "hash": hashlib.sha256(raw).hexdigest(),
        "http_status": r.status_code,
        "content_type": ct,
        "content_length": len(raw),
        "retrieved_at": now_iso(),
    }


def arxiv_search(query: str, limit: int) -> List[Dict[str, Any]]:
    url = (
        "https://export.arxiv.org/api/query?search_query=all:"
        + quote(query) + f"&start=0&max_results={limit}"
    )
    r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", r.text, re.S):
        tm = re.search(r"<title>(.*?)</title>", entry, re.S)
        sm = re.search(r"<summary>(.*?)</summary>", entry, re.S)
        lm = re.search(r'<link[^>]+href="([^"]+)"', entry)
        if not (tm and lm):
            continue
        title = re.sub(r"\s+", " ", tm.group(1)).strip()
        url2 = lm.group(1)
        out.append({
            "title": title,
            "url": url2,
            "snippet": re.sub(r"\s+", " ", sm.group(1)).strip() if sm else "",
            "provider": "arxiv",
            "doi": "",
            "work_id": re.search(r"arxiv.org/abs/([^/?#]+)", url2).group(1)
                if re.search(r"arxiv.org/abs/([^/?#]+)", url2) else ""
        })
    return out


def openalex_search(query: str, limit: int) -> List[Dict[str, Any]]:
    url = "https://api.openalex.org/works?search=" + quote(query) + f"&per-page={limit}"
    r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = []
    for item in r.json().get("results", []):
        title = item.get("display_name") or ""
        primary = (item.get("primary_location") or {}).get("landing_page_url") or ""
        doi = item.get("doi") or ""
        url2 = primary or doi
        if not url2:
            continue
        out.append({
            "title": title,
            "url": url2,
            "snippet": "",
            "provider": "openalex",
            "doi": doi,
            "work_id": item.get("id") or ""
        })
    return out


def crossref_search(query: str, limit: int) -> List[Dict[str, Any]]:
    url = (
        "https://api.crossref.org/works?query.bibliographic="
        + quote(query) + f"&rows={limit}"
    )
    r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = []
    for item in r.json().get("message", {}).get("items", []):
        title_list = item.get("title") or []
        title = title_list[0] if title_list else ""
        doi = item.get("DOI") or ""
        url2 = ("https://doi.org/" + doi) if doi else item.get("URL") or ""
        if not url2:
            continue
        out.append({
            "title": title,
            "url": url2,
            "snippet": "",
            "provider": "crossref",
            "doi": doi,
            "work_id": "doi:" + doi.lower() if doi else ""
        })
    return out


def wikipedia_search(query: str, limit: int) -> List[Dict[str, Any]]:
    url = (
        "https://en.wikipedia.org/w/api.php?action=query&list=search"
        "&format=json&srsearch=" + quote(query) + f"&srlimit={limit}"
    )
    r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = []
    for item in r.json().get("query", {}).get("search", []):
        title = item.get("title", "")
        out.append({
            "title": title,
            "url": "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
            "snippet": re.sub(r"<[^>]+>", " ", item.get("snippet", "")),
            "provider": "wikipedia",
            "doi": "",
            "work_id": "wiki:" + normalize_title(title)
        })
    return out


def relevance(question: str, title: str, text: str, url: str) -> float:
    q = tokens(research_anchor(question))
    if not q:
        return 0.0
    tt = tokens(title)
    tx = tokens(text[:50000])
    title_overlap = len(q & tt) / max(1, len(q))
    text_overlap = len(q & tx) / max(1, min(len(q), 12))
    kh = len(keyword_hits(question) & keyword_hits(title + " " + text)) / max(1, len(keyword_hits(question)))
    d = domain_of(url)
    bonus = 0.05 if any(x in d for x in ("doi.org", "arxiv.org")) else 0.02
    if "wikipedia.org" in d:
        bonus -= 0.10
    return round(max(0, min(1, 0.45 * title_overlap + 0.40 * text_overlap + 0.15 * kh + bonus)), 4)


def discover_sources(question: str) -> Dict[str, Any]:
    providers = [
        ("arxiv", arxiv_search),
        ("crossref", crossref_search),
        ("openalex", openalex_search),
        ("wikipedia", wikipedia_search),
    ]
    queries = expand_queries(question)
    items, diagnostics = [], []

    for q in queries:
        for name, fn in providers:
            try:
                batch = fn(q, MAX_DISCOVERY_PER_QUERY)
                for x in batch:
                    x["query"] = q
                    x["provider"] = name
                items.extend(batch)
                diagnostics.append({"query": q, "provider": name, "count": len(batch), "status": "ok"})
            except Exception as exc:
                diagnostics.append({
                    "query": q, "provider": name, "count": 0,
                    "status": "error", "error": str(exc)[:300]
                })

    unique, seen = [], set()
    for x in items:
        key = x.get("work_id") or x.get("url")
        if key and key not in seen:
            seen.add(key)
            unique.append(x)

    return {
        "queries": queries,
        "search_anchor": research_anchor(question),
        "items": unique,
        "diagnostics": diagnostics
    }


# ---------------------------------------------------------------------------
# Evidence collection + independence
# ---------------------------------------------------------------------------

def collect_sources(task_id: str, question: str, discovered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates = []

    for item in discovered:
        if item.get("provider") == "wikipedia":
            continue
        try:
            got = fetch_url(item["url"])
            if got["http_status"] >= 400 or len(got["text"]) < 300:
                continue
            score = relevance(question, got["title"], got["text"], got["url"])
            if score < 0.10:
                continue

            work_id = work_identity({**item, "title": got["title"]})
            got.update({
                "provider": item.get("provider"),
                "query": item.get("query"),
                "relevance": score,
                "source_family": source_family(got["url"]),
                "work_id": work_id,
                "substantive": len(got["text"]) >= 500,
                "evidence_quality": min(1.0, 0.45 + score * 0.45 + min(len(got["text"]) / 50000, 0.10))
            })
            candidates.append(got)
        except Exception:
            continue

    candidates.sort(
        key=lambda x: (x["evidence_quality"], x["relevance"], len(x["text"])),
        reverse=True
    )

    selected, hashes, works = [], set(), set()
    for x in candidates:
        if x["hash"] in hashes or x["work_id"] in works:
            continue
        selected.append(x)
        hashes.add(x["hash"])
        works.add(x["work_id"])
        if len(selected) >= MAX_COLLECTED_SOURCES:
            break

    c = db()
    for x in selected:
        eid = uid("evidence")
        x["evidence_id"] = eid
        c.execute(
            "INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid, task_id, x["url"], x["title"], domain_of(x["url"]), "",
                x["text"][:4000], x["hash"], "unreviewed", x["relevance"],
                dump({
                    "provider": x["provider"],
                    "query": x["query"],
                    "source_family": x["source_family"],
                    "work_id": x["work_id"],
                    "evidence_quality": x["evidence_quality"],
                    "retrieved_at": x["retrieved_at"]
                }),
                now_iso()
            )
        )
    c.commit()
    c.close()
    return selected


# ---------------------------------------------------------------------------
# Claim grounding
# ---------------------------------------------------------------------------

def sentence_candidates(text: str) -> List[str]:
    raw = re.split(r"(?<=[.!?])\s+", clean_text(text))
    out = []
    for s in raw:
        s = re.sub(r"\s+", " ", s).strip()
        low = s.lower()
        if not 65 <= len(s) <= 520:
            continue
        if any(n in low for n in NOISE_PHRASES):
            continue
        alpha = len(re.findall(r"[A-Za-z]", s))
        if alpha < 45:
            continue
        if not (keyword_hits(s) or any(
            w in low for w in (
                "study", "experiment", "results", "evaluation",
                "performance", "method", "benchmark", "finding"
            )
        )):
            continue
        out.append(s)
    return out


def is_substantive_claim(sentence: str, question: str) -> bool:
    low = sentence.lower()
    if "doi" in low and len(sentence) < 120:
        return False
    if sentence.endswith(":"):
        return False
    q_terms = tokens(research_anchor(question))
    overlap = len(q_terms & tokens(sentence)) / max(1, len(q_terms))
    return overlap >= 0.06 or bool(keyword_hits(sentence) & keyword_hits(question))


def extract_claims(question: str, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    claims, seen = [], set()
    anchor_terms = tokens(research_anchor(question))

    for source in sources:
        candidates = sentence_candidates(source["text"])
        scored = []
        for sentence in candidates:
            if not is_substantive_claim(sentence, question):
                continue
            overlap = len(anchor_terms & tokens(sentence)) / max(1, len(anchor_terms))
            score = overlap + 0.05 * len(keyword_hits(sentence) & keyword_hits(question))
            scored.append((score, sentence))

        for _, sentence in sorted(scored, reverse=True)[:8]:
            fingerprint = hashlib.sha1(
                re.sub(r"\W+", " ", sentence.lower()).encode()
            ).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            claims.append({
                "claim_id": uid("claim"),
                "text": sentence,
                "claim_type": "substantive_evidence_claim",
                "origin_evidence_id": source["evidence_id"],
                "origin_work_id": source["work_id"],
                "origin_domain": domain_of(source["url"]),
                "evidence_ids": [source["evidence_id"]],
                "source_urls": [source["url"]],
                "works": [source["work_id"]],
                "domains": [domain_of(source["url"])],
                "source_families": [source["source_family"]],
            })

    return claims[:36]


# ---------------------------------------------------------------------------
# Evidence relation engine
# ---------------------------------------------------------------------------

def polarity(text: str) -> str:
    t = tokens(text)
    p = len(t & POSITIVE)
    n = len(t & NEGATIVE)
    if p >= n + 2:
        return "positive"
    if n >= p + 2:
        return "negative"
    return "mixed"


def has_negation(text: str) -> bool:
    return bool(re.search(
        r"\b(no|not|never|cannot|can't|unable|fails?|failed|without|lack|limited|weak)\b",
        (text or "").lower()
    ))


def relation(claim: str, evidence: str) -> Tuple[str, float]:
    sim = similarity(claim, evidence)
    if sim < 0.055:
        return "irrelevant", sim

    cp = polarity(claim)
    ep = polarity(evidence)
    neg_mismatch = has_negation(claim) != has_negation(evidence)

    if sim >= 0.15 and neg_mismatch:
        return "contradicts", sim
    if sim >= 0.16 and cp != "mixed" and ep != "mixed" and cp != ep:
        return "contradicts", sim
    if sim >= 0.13:
        return "supports", sim
    if sim >= 0.08:
        return "qualifies", sim
    return "weak_support", sim


def verify_claims(claims: List[Dict[str, Any]], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    all_contradictions = []

    for claim in claims:
        support = []
        counter = []
        qualifying = []

        for source in sources:
            rel, sim = relation(claim["text"], source["text"][:50000])
            if rel == "irrelevant":
                continue

            item = {
                "evidence_id": source["evidence_id"],
                "url": source["url"],
                "work_id": source["work_id"],
                "domain": domain_of(source["url"]),
                "source_family": source["source_family"],
                "relation": rel,
                "similarity": round(sim, 4)
            }

            if rel == "supports" or rel == "weak_support":
                support.append(item)
            elif rel == "qualifies":
                qualifying.append(item)
            elif rel == "contradicts":
                counter.append(item)

        # Unique works are what count for corroboration.
        supporting_works = list(dict.fromkeys(x["work_id"] for x in support))
        supporting_domains = list(dict.fromkeys(x["domain"] for x in support))
        counter_works = list(dict.fromkeys(x["work_id"] for x in counter))

        claim["supporting_evidence"] = support[:8]
        claim["counter_evidence"] = counter[:8]
        claim["qualifying_evidence"] = qualifying[:8]
        claim["evidence_ids"] = list(dict.fromkeys(
            [claim["origin_evidence_id"]] +
            [x["evidence_id"] for x in support[:8] + counter[:8] + qualifying[:8]]
        ))
        claim["works"] = list(dict.fromkeys(
            [claim["origin_work_id"]] + supporting_works + counter_works
        ))
        claim["domains"] = list(dict.fromkeys(
            [claim["origin_domain"]] + supporting_domains +
            [x["domain"] for x in counter]
        ))
        claim["source_families"] = list(dict.fromkeys(
            [x["source_family"] for x in support + counter + qualifying]
        ))
        claim["support_count"] = len(support)
        claim["independent_supporting_works"] = len(supporting_works)
        claim["independent_supporting_domains"] = len(supporting_domains)
        claim["counter_evidence_count"] = len(counter)
        claim["counter_evidence_works"] = len(counter_works)

        if len(supporting_works) >= 2 and len(counter) == 0:
            status = "corroborated"
            confidence = min(0.96, 0.72 + 0.08 * min(len(supporting_works), 3))
        elif len(supporting_works) >= 1 and len(counter) == 0:
            status = "source_supported"
            confidence = 0.66
        elif len(supporting_works) >= 1 and len(counter) >= 1:
            status = "contested"
            confidence = 0.45
        elif len(counter) >= 1:
            status = "counter_supported"
            confidence = 0.32
        else:
            status = "insufficient"
            confidence = 0.10

        claim["verification_status"] = status
        claim["confidence"] = round(confidence, 3)

        if counter:
            all_contradictions.append({
                "claim_id": claim["claim_id"],
                "claim": claim["text"],
                "counter_evidence": counter[:6],
                "type": "claim_specific_counter_evidence"
            })

    unsupported = [
        c for c in claims
        if c["verification_status"] == "insufficient"
    ]

    return {
        "claims": claims,
        "unsupported_claims": unsupported,
        "contradictions": all_contradictions,
        "corroborated_claims": sum(
            1 for c in claims if c["verification_status"] == "corroborated"
        ),
        "contested_claims": sum(
            1 for c in claims if c["verification_status"] == "contested"
        )
    }


# ---------------------------------------------------------------------------
# Evidence gate 4.0
# ---------------------------------------------------------------------------

def independence_profile(sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    substantive = [s for s in sources if s.get("substantive")]
    works = {s["work_id"] for s in substantive if s.get("work_id")}
    domains = {domain_of(s["url"]) for s in substantive}
    families = {s.get("source_family") for s in substantive}

    # Provider diversity is deliberately NOT treated as independence.
    providers = {s.get("provider") for s in substantive}

    return {
        "substantive_sources": len(substantive),
        "underlying_works": len(works),
        "domains": len(domains),
        "source_families": len(families),
        "providers": len(providers),
        "independent_domains": sorted(domains),
        "independent_works": sorted(works),
        "source_families_list": sorted(families),
    }


def evidence_gate(sources: List[Dict[str, Any]], verification: Dict[str, Any]) -> Dict[str, Any]:
    profile = independence_profile(sources)
    claims = verification.get("claims", [])

    usable_claims = [
        c for c in claims
        if len(c.get("text", "")) >= 65
        and c.get("verification_status") in {
            "corroborated", "source_supported", "contested"
        }
    ]

    corroborated = [
        c for c in claims if c.get("verification_status") == "corroborated"
    ]

    checks = {
        "minimum_substantive_sources": profile["substantive_sources"] >= 4,
        "minimum_independent_works": profile["underlying_works"] >= 3,
        "minimum_independent_domains": profile["domains"] >= 2,
        "minimum_valid_claims": len(usable_claims) >= 3,
        "minimum_corroborated_claims": len(corroborated) >= 2,
        "no_unsupported_claims": len(verification.get("unsupported_claims", [])) == 0,
        "no_unresolved_claim_conflicts": len([
            c for c in claims if c.get("verification_status") == "contested"
        ]) == 0,
        "no_navigation_noise": all(
            not any(n in c.get("text", "").lower() for n in NOISE_PHRASES)
            for c in claims        ),
    }

    reasons_map = {
        "minimum_substantive_sources": "fewer_than_4_substantive_sources",
        "minimum_independent_works": "fewer_than_3_independent_works",
        "minimum_independent_domains": "fewer_than_2_independent_domains",
        "minimum_valid_claims": "fewer_than_3_valid_claims",
        "minimum_corroborated_claims": "fewer_than_2_corroborated_claims",
        "no_unsupported_claims": "unsupported_claims_present",
        "no_unresolved_claim_conflicts": "claim_conflicts_present",
        "no_navigation_noise": "navigation_noise_present",
    }

    reasons = [reason for key, reason in reasons_map.items() if not checks[key]]
    passed = all(checks.values())

    return {
        "version": EVIDENCE_VERSION,
        "passed": passed,
        "checks": checks,
        "source_count": profile["substantive_sources"],
        "work_count": profile["underlying_works"],
        "domain_count": profile["domains"],
        "source_family_count": profile["source_families"],
        "provider_count": profile["providers"],
        "valid_claim_count": len(usable_claims),
        "corroborated_claim_count": len(corroborated),
        "unsupported_claim_count": len(verification.get("unsupported_claims", [])),
        "contradiction_count": len(verification.get("contradictions", [])),
        "reasons": reasons,
        "independence": profile,
    }


# ---------------------------------------------------------------------------
# Automatic next-cycle research
# ---------------------------------------------------------------------------

def next_cycle_actions(
    question: str,
    sources: List[Dict[str, Any]],
    verification: Dict[str, Any],
    gate: Dict[str, Any]
) -> List[Dict[str, Any]]:
    actions = []

    if gate["checks"]["minimum_independent_domains"] is False:
        actions.append({
            "priority": 1,
            "action": "discover_new_domains",
            "reason": "Current evidence lacks two independent domains.",
            "query": clean_query(f"{research_anchor(question)} independent publisher empirical study")
        })

    if gate["checks"]["minimum_independent_works"] is False:
        actions.append({
            "priority": 2,
            "action": "find_independent_works",
            "reason": "Current evidence does not contain enough distinct underlying works.",
            "query": clean_query(f"{research_anchor(question)} independent empirical benchmark")
        })

    for claim in verification.get("claims", []):
        status = claim.get("verification_status")
        if status == "contested":
            actions.append({
                "priority": 1,
                "action": "resolve_claim_conflict",
                "claim_id": claim["claim_id"],
                "reason": "Claim has claim-specific counter-evidence.",
                "query": clean_query(
                    f"{claim['text']} replication independent study contradictory evidence"
                )
            })
        elif status == "source_supported":
            actions.append({
                "priority": 3,
                "action": "corroborate_claim",
                "claim_id": claim["claim_id"],
                "reason": "Claim currently has support from only one independent work.",
                "query": clean_query(
                    f"{claim['text']} independent evidence benchmark"
                )
            })
        elif status == "insufficient":
            actions.append({
                "priority": 2,
                "action": "ground_claim",
                "claim_id": claim["claim_id"],
                "reason": "No sufficiently matched evidence was found.",
                "query": clean_query(
                    f"{claim['text']} primary source empirical evidence"
                )
            })

    if not actions:
        actions.append({
            "priority": 4,
            "action": "periodic_recheck",
            "reason": "No immediate evidence-integrity defect remains.",
            "query": clean_query(f"{research_anchor(question)} latest independent evidence")
        })

    # Deduplicate exact query/action pairs.
    seen = set()
    out = []
    for item in sorted(actions, key=lambda x: x["priority"]):
        key = (item["action"], item.get("query", ""), item.get("claim_id", ""))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out[:12]


# ---------------------------------------------------------------------------
# Grounded synthesis
# ---------------------------------------------------------------------------

def call_ai(prompt: str) -> Optional[str]:
    if not HF_TOKEN:
        return None
    try:
        r = requests.post(
            "https://router.huggingface.co/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + HF_TOKEN,
                "Content-Type": "application/json"
            },
            json={
                "model": HF_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are AI Infinity's grounded synthesis layer. "
                            "Use only supplied evidence. Never invent facts, "
                            "sources, tool results, permissions, or completed actions. "
                            "Clearly distinguish support, contradiction, and uncertainty."
                        )
                    },
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.10,
                "max_tokens": 2400
            },
            timeout=45
        )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def grounded_analysis(
    question: str,
    sources: List[Dict[str, Any]],
    verification: Dict[str, Any],
    gate: Dict[str, Any],
    actions: List[Dict[str, Any]]
) -> str:
    evidence = [
        {
            "id": s["evidence_id"],
            "work_id": s["work_id"],
            "domain": domain_of(s["url"]),
            "title": s["title"],
            "url": s["url"],
            "excerpt": s["text"][:1200]
        }
        for s in sources
    ]
    claims = [
        {
            "claim_id": c["claim_id"],
            "claim": c["text"],
            "status": c["verification_status"],
            "confidence": c["confidence"],
            "supporting_works": c["independent_supporting_works"],
            "counter_evidence": c["counter_evidence_count"]
        }
        for c in verification["claims"]
    ]

    prompt = (
        f"Question:\n{question}\n\n"
        f"Evidence Gate:\n{dump(gate)}\n\n"
        f"Evidence:\n{dump(evidence)}\n\n"
        f"Claims:\n{dump(claims)}\n\n"
        f"Next actions:\n{dump(actions)}\n\n"
        "Return concise sections: Findings, Supported Claims, Contested Claims, "
        "Uncertainty, Next Actions. Every factual statement must be traceable "
        "to supplied evidence."
    )

    ai = call_ai(prompt)
    if ai:
        return ai

    lines = [
        f"Evidence status: {'PASSED' if gate['passed'] else 'INSUFFICIENT'}."
    ]
    for c in verification["claims"][:10]:
        lines.append(
            f"- [{c['verification_status']}] {c['text']} "
            f"(confidence={c['confidence']})"
        )
    lines.append("Next cycle:")
    for a in actions[:6]:
        lines.append(f"- {a['action']}: {a['reason']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Research pipeline
# ---------------------------------------------------------------------------

def research_pipeline(task_id: str, question: str) -> Dict[str, Any]:
    discovery = discover_sources(question)
    sources = collect_sources(task_id, question, discovery["items"])
    claims = extract_claims(question, sources)
    verification = verify_claims(claims, sources)
    gate = evidence_gate(sources, verification)
    actions = next_cycle_actions(question, sources, verification, gate)
    analysis = grounded_analysis(question, sources, verification, gate, actions)

    # Update stored evidence with claim-level provenance.
    c = db()
    for claim in verification["claims"]:
        for eid in claim["evidence_ids"]:
            c.execute(
                "UPDATE evidence SET claim=?, verification_status=? WHERE id=?",
                (claim["text"], claim["verification_status"], eid)
            )
    c.commit()
    c.close()

    report = {
        "version": VERSION,
        "evidence_integrity_version": EVIDENCE_VERSION,
        "question": question,
        "queries": discovery["queries"],
        "search_anchor": discovery["search_anchor"],
        "query_quality": {
            "count": len(discovery["queries"]),
            "empty_or_malformed": [
                q for q in discovery["queries"]
                if not q or re.search(r",\\s*,|\\band\\s*,", q.lower())
            ]
        },
        "discovery": discovery["diagnostics"],
        "sources": [
            {
                k: s.get(k) for k in (
                    "evidence_id","url","title","provider","query",
                    "relevance","hash","retrieved_at","source_family","work_id",
                    "evidence_quality","substantive"
                )
            }
            for s in sources
        ],
        "claims": verification["claims"],
        "verification": verification,
        "contradictions": verification["contradictions"],
        "evidence_gate": gate,
        "next_cycle": actions,
        "analysis": analysis,
        "status": "completed" if gate["passed"] else "insufficient_evidence",
        "provenance": {
            "query_count": len(discovery["queries"]),
            "source_count": len(sources),
            "underlying_work_count": len({s["work_id"] for s in sources}),
            "domains": sorted({domain_of(s["url"]) for s in sources}),
            "source_families": sorted({s["source_family"] for s in sources}),
            "providers": sorted({s["provider"] for s in sources}),
            "source_hashes": sorted({s["hash"] for s in sources}),
            "claim_count": len(verification["claims"]),
            "gate": EVIDENCE_VERSION
        }
    }

    report["research_strength"] = round(
        min(
            1.0,
            0.25 * min(1, gate["source_count"] / 6) +
            0.25 * min(1, gate["work_count"] / 4) +
            0.20 * min(1, gate["domain_count"] / 3) +
            0.20 * min(1, gate["corroborated_claim_count"] / 4) +
            0.10 * (1 if gate["contradiction_count"] == 0 else 0)
        ),
        3
    )

    report["research_id"] = save_research(
        task_id, question, report["status"], report
    )
    event(task_id, "research_completed", {
        "status": report["status"],
        "gate": gate,
        "next_cycle_count": len(actions)
    })
    return report


# ---------------------------------------------------------------------------
# Safe execution
# ---------------------------------------------------------------------------

def safe_calculator(expression: str) -> Any:
    tree = ast.parse(expression, mode="eval")
    allowed = (
        ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
        ast.USub, ast.UAdd, ast.FloorDiv, ast.Load
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError("calculator_expression_not_allowed")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("calculator_values_must_be_numeric")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if isinstance(node.right, ast.Constant) and abs(float(node.right.value)) > 20:
                raise ValueError("exponent_too_large")
    return eval(compile(tree, "<calculator>", "eval"), {"__builtins__": {}}, {})


SAFE_ACTIONS = {
    "calculator_test": {
        "description": "Evaluate strictly AST-validated arithmetic",
        "external_side_effects": False
    },
    "hash_text": {
        "description": "Create SHA-256 digest",
        "external_side_effects": False
    },
    "validate_python": {
        "description": "Parse Python without executing it",
        "external_side_effects": False
    },
    "create_plan": {
        "description": "Compile a safe execution graph",
        "external_side_effects": False
    },
}


def registered_execute(task_id: str, action: str, args: Dict[str, Any]) -> Any:
    if action not in SAFE_ACTIONS:
        raise ValueError("action_not_registered")

    if action == "calculator_test":
        expr = str(args.get("expression", "2+3*4"))
        return {"expression": expr, "result": safe_calculator(expr)}

    if action == "hash_text":
        text = str(args.get("text", ""))
        return {"sha256": hashlib.sha256(text.encode()).hexdigest()}

    if action == "validate_python":
        source = str(args.get("source", ""))
        ast.parse(source)
        return {"syntax_valid": True, "bytes": len(source.encode())}

    if action == "create_plan":
        objective = str(args.get("objective", ""))
        return build_execution_graph(objective, None)

    raise ValueError("action_not_registered")


def verify_action_result(action: str, args: Dict[str, Any], result: Any) -> Dict[str, Any]:
    checks = []
    if action == "calculator_test":
        checks.append(isinstance(result.get("result"), (int, float)))
    elif action == "hash_text":
        checks.append(bool(result.get("sha256")) and len(result["sha256"]) == 64)
    elif action == "validate_python":
        checks.append(result.get("syntax_valid") is True)
    elif action == "create_plan":
        checks.append(bool(result.get("nodes")))
    return {"passed": all(checks), "checks": checks}


def execute_with_recovery(
    task_id: str, action: str, args: Dict[str, Any], max_attempts: int = 2
) -> Dict[str, Any]:
    attempts = []
    current = dict(args or {})

    for attempt in range(1, min(max_attempts, 3) + 1):
        try:
            started = time.time()
            result = registered_execute(task_id, action, current)
            verification = verify_action_result(action, current, result)
            item = {
                "attempt": attempt,
                "status": "verified" if verification["passed"] else "verification_failed",
                "result": result,
                "verification": verification,
                "latency_ms": round((time.time() - started) * 1000, 2)
            }
            attempts.append(item)

            if verification["passed"]:
                eid = uid("execution")
                c = db()
                c.execute(
                    "INSERT INTO executions VALUES(?,?,?,?,?,?)",
                    (eid, task_id, action, "verified", dump(item), now_iso())
                )
                c.commit()
                c.close()
                return {
                    "status": "verified",
                    "execution_id": eid,
                    "action": action,
                    "attempts": attempts,
                    "safety": {
                        "registered_action_only": True,
                        "external_side_effects": False,
                        "spending": False,
                        "self_modification": False
                    }
                }

            raise ValueError("postcondition_verification_failed")
        except Exception as exc:
            attempts.append({
                "attempt": attempt,
                "status": "failed",
                "error": str(exc)[:500]
            })
            if attempt >= max_attempts:
                break

    eid = uid("execution")
    c = db()
    c.execute(
        "INSERT INTO executions VALUES(?,?,?,?,?,?)",
        (eid, task_id, action, "failed_closed", dump(attempts), now_iso())
    )
    c.commit()
    c.close()
    return {
        "status": "failed_closed",
        "execution_id": eid,
        "action": action,
        "attempts": attempts,
        "safety": {
            "registered_action_only": True,
            "external_side_effects": False,
            "spending": False,
            "self_modification": False
        }
    }


# ---------------------------------------------------------------------------
# Compiler / genome
# ---------------------------------------------------------------------------

def build_execution_graph(
    objective: str, research: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    gate = (research or {}).get("evidence_gate", {})
    verified = bool(gate.get("passed"))

    nodes = [
        {"id": "understand", "type": "intent", "status": "completed", "action": "parse_objective"},
        {"id": "research", "type": "research", "status": "completed" if research else "ready", "action": "collect_claim_grounded_evidence"},
        {"id": "verify", "type": "verification", "status": "completed" if research else "blocked", "action": "verify_claims_and_counter_evidence"},
        {"id": "plan", "type": "planning", "status": "ready" if verified else "blocked", "action": "compile_strategy"},
        {"id": "simulate", "type": "sandbox", "status": "blocked", "action": "dry_run_without_side_effects"},
        {"id": "execute", "type": "execution", "status": "blocked", "action": "registered_safe_action"},
        {"id": "result_verify", "type": "verification", "status": "blocked", "action": "independent_result_check"},
        {"id": "learn", "type": "learning", "status": "ready", "action": "store_reusable_genome"}
    ]
    edges = [
        ["understand", "research"],
        ["research", "verify"],
        ["verify", "plan"],
        ["plan", "simulate"],
        ["simulate", "execute"],
        ["execute", "result_verify"],
        ["result_verify", "learn"]
    ]
    return {
        "version": VERSION,
        "mode": "controlled_execution",
        "objective": objective,
        "verified_research": verified,
        "nodes": nodes,
        "edges": edges,
        "execution_policy": {
            "automatic_external_actions": False,
            "automatic_spending": False,
            "arbitrary_shell": False,
            "self_modification": False,
            "authorized_actions_only": True
        }
    }


def compile_plan(objective: str, research: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    gate = (research or {}).get("evidence_gate", {})
    if not gate.get("passed"):
        return {
            "status": "blocked",
            "reason": "evidence_gate_not_passed",
            "steps": [],
            "requires": ["claim_grounded_verified_evidence"],
            "next_cycle": (research or {}).get("next_cycle", [])
        }

    corroborated = [
        c for c in (research or {}).get("claims", [])
        if c.get("verification_status") == "corroborated"
    ]
    return {
        "status": "compiled",
        "objective": objective,
        "steps": [
            {"step": 1, "name": "Define success criteria", "action": "create_success_criteria"},
            {"step": 2, "name": "Ground plan in verified claims", "action": "ground_plan_in_evidence", "claim_ids": [c["claim_id"] for c in corroborated[:8]]},
            {"step": 3, "name": "Sandbox / dry-run", "action": "simulate_without_external_side_effects"},
            {"step": 4, "name": "Execute registered action only", "action": "registered_execution"},
            {"step": 5, "name": "Verify outcome", "action": "independent_result_check"},
            {"step": 6, "name": "Store reusable intelligence", "action": "create_intelligence_genome"}
        ],
        "evidence_basis": {
            "corroborated_claims": len(corroborated)
        }
    }


def make_genome(
    task_id: str, objective: str, research: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    gate = (research or {}).get("evidence_gate", {})
    passed = bool(gate.get("passed"))
    return {
        "version": VERSION,
        "objective": objective,
        "task_id": task_id,
        "fitness": 1.0 if passed else 0.0,
        "reusable": passed,
        "strategy": {
            "intent": "understand",
            "research": "clean_query_discover_collect",
            "evidence": "source_claim_separation",
            "verification": "claim_level_support_countercheck",
            "independence": "underlying_work_domain_separation",
            "decision": EVIDENCE_VERSION,
            "execution": "registered_actions_only",
            "learning": "preserve_provenance_and_uncertainty"
        },
        "provenance_fields": [
            "queries","source_urls","underlying_work_ids","domains",
            "source_families","hashes","claim_ids","evidence_ids",
            "verification_status","confidence","gate_decision","next_cycle"
        ],
        "evolution": {
            "status": "proposal_only",
            "next_mutations": [
                "publisher identity resolution",
                "stronger semantic contradiction checking",
                "primary-source preference",
                "durable evidence snapshots",
                "benchmark execution reliability"
            ]
        }
    }


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run_infinity(req: RunRequest) -> Dict[str, Any]:
    task_id = uid("mission")
    save_task(task_id, req.objective, "running")
    event(task_id, "started", {"version": VERSION})

    try:
        research = research_pipeline(task_id, req.objective) if req.research else None

        memory_id = None
        if req.remember:
            if research and research["evidence_gate"]["passed"]:
                memory_id = save_memory(
                    dump({
                        "objective": req.objective,
                        "analysis": research["analysis"],
                        "claims": research["claims"],
                        "provenance": research["provenance"]
                    }),
                    "verified_research",
                    True
                )
            else:
                memory_id = save_memory(
                    dump({
                        "objective": req.objective,
                        "research_status": research["status"] if research else "not_requested",
                        "next_cycle": research.get("next_cycle", []) if research else [],
                        "note": "Not stored as verified intelligence because the evidence gate did not pass."
                    }),
                    "research_uncertainty",
                    False
                )

        genome = make_genome(task_id, req.objective, research)
        genome["genome_id"] = save_genome(task_id, genome)

        graph = build_execution_graph(req.objective, research)
        plan = compile_plan(req.objective, research)

        result = {
            "task_id": task_id,
            "status": "completed",
            "version": VERSION,
            "build": "EVIDENCE-QUALITY-CLAIM-GROUNDING",
            "objective": req.objective,
            "intelligence_compiler": {
                "status": "compiled",
                "execution_graph": graph,
                "plan": plan
            },
            "research": research,
            "memory_id": memory_id,
            "intelligence_genome": genome,
            "temporary_minds": [
                {"name": n, "status": "ready"}
                for n in ("researcher", "claim_extractor", "verifier", "counterchecker", "critic", "planner")
            ],
            "world_model": {
                "objective": req.objective,
                "resources": [
                    "local_python","local_filesystem","public_web",
                    "evidence_database","optional_huggingface_model"
                ],
                "constraints": {
                    "free_first": True,
                    "allow_paid": bool(req.allow_paid),
                    "automatic_spending": False,
                    "arbitrary_shell_execution": False,
                    "automatic_self_modification": False,
                    "authorized_actions_only": True,
                    "external_side_effects_default": False
                }
            },
            "evolution": {
                "status": "proposal_only",
                "automatic_deployment": False,
                "improvements": [
                    "clean research queries",
                    "source-claim separation",
                    "independent evidence accounting",
                    "claim-level verification",
                    "claim-specific counter-evidence",
                    "automatic next-cycle research"
                ]
            },
            "safety": {
                "arbitrary_shell_execution": False,
                "automatic_spending": False,
                "automatic_self_modification": False,
                "authorized_actions_only": True,
                "evidence_gate": True
            }
        }

        save_task(task_id, req.objective, "completed", result)
        event(task_id, "completed", {
            "status": "completed",
            "evidence_gate_passed": bool(
                research and research.get("evidence_gate", {}).get("passed")
            )
        })
        return result

    except Exception as exc:
        recovery = [
            "preserve collected evidence",
            "retry failed providers on next cycle",
            "fail closed on missing evidence",
            "never convert unverified text into verified facts",
            "keep external actions disabled"
        ]
        c = db()
        c.execute(
            "INSERT INTO failures VALUES(?,?,?,?,?,?)",
            (uid("failure"), task_id, "run_infinity", str(exc), dump(recovery), now_iso())
        )
        c.commit()
        c.close()
        save_task(task_id, req.objective, "failed", {
            "error": str(exc),
            "recovery": recovery
        })
        raise HTTPException(status_code=500, detail="Task failed safely: " + str(exc))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/")
def home():
    return HTMLResponse(f"""<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Infinity {VERSION}</title>
<style>
body{{font-family:system-ui;max-width:920px;margin:auto;padding:20px;background:#0b1020;color:#fff}}
textarea{{width:100%;min-height:180px;box-sizing:border-box;background:#151b30;color:#fff;border:1px solid #303957;border-radius:12px;padding:14px}}
button{{padding:12px 18px;margin:8px 5px 8px 0;border:0;border-radius:10px}}
pre{{white-space:pre-wrap;background:#151b30;padding:14px;border-radius:12px;overflow:auto}}
</style>
</head>
<body>
<h1>∞ AI Infinity</h1>
<p>{VERSION} · {EVIDENCE_VERSION}</p>
<textarea id="objective" placeholder="Enter your objective..."></textarea><br>
<button onclick="runTask()">Run AI Infinity</button>
<button onclick="calc()">Test Calculator</button>
<pre id="out">Ready.</pre>
<script>
async function runTask(){{
 const objective=document.getElementById('objective').value;
 if(!objective.trim()){{document.getElementById('out').textContent='Enter an objective first.';return;}}
 document.getElementById('out').textContent='Running Evidence → Claims → Verification → Next Cycle...';
 try{{
  const r=await fetch('/v1/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},
  body:JSON.stringify({{objective,research:true,verify:true,remember:true,allow_paid:false}})}});
  document.getElementById('out').textContent=await r.text();
 }}catch(e){{document.getElementById('out').textContent='Request error: '+e;}}
}}
async function calc(){{
 const r=await fetch('/v1/execute',{{method:'POST',headers:{{'Content-Type':'application/json'}},
 body:JSON.stringify({{action:'calculator_test',args:{{expression:'2+3*4'}}}})}});
 document.getElementById('out').textContent=await r.text();
}}
</script>
</body>
</html>""")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "AI Infinity",
        "version": VERSION,
        "evidence_gate": EVIDENCE_VERSION
    }


@app.get("/v1/status")
def status():
    c = db()
    counts = {}
    for table in ("tasks", "memories", "evidence", "genomes", "research", "events"):
        counts[table] = c.execute(
            f"SELECT COUNT(*) AS n FROM {table}"
        ).fetchone()["n"]
    c.close()
    return {
        "service": "AI Infinity",
        "version": VERSION,
        "evidence_gate": EVIDENCE_VERSION,
        "counts": counts,
        "governor": {
            "free_first": True,
            "allow_paid_default": False,
            "automatic_spending": False,
            "external_side_effects_default": False,
            "arbitrary_shell": False
        },
        "compiler": VERSION
    }


@app.post("/v1/run")
def run(request: RunRequest):
    return run_infinity(request)


@app.post("/v1/execute")
def execute(request: ExecuteRequest):
    task_id = uid("task")
    try:
        result = registered_execute(task_id, request.action, request.args)
        verification = verify_action_result(request.action, request.args, result)
        if not verification["passed"]:
            raise ValueError("postcondition_verification_failed")
        return {
            "task_id": task_id,
            "status": "completed",
            "action": request.action,
            "result": result,
            "verification": verification,
            "safety": {
                "registered_action_only": True,
                "external_side_effects": False
            }
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/execute-graph")
def execute_graph(request: ExecuteRequest):
    task_id = uid("task")
    if request.action not in SAFE_ACTIONS:
        raise HTTPException(status_code=400, detail="action_not_registered")
    result = execute_with_recovery(task_id, request.action, request.args, 2)
    return {"task_id": task_id, **result}


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str):
    c = db()
    row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    out = dict(row)
    if out.get("result_json"):
        try:
            out["result"] = json.loads(out["result_json"])
        except Exception:
            pass
    return out


@app.get("/v1/memory")
def list_memory(limit: int = 20):
    c = db()
    rows = c.execute(
        "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
        (min(max(limit, 1), 100),)
    ).fetchall()
    c.close()
    return [dict(x) for x in rows]


@app.post("/v1/memory")
def add_memory(request: MemoryRequest):
    return {
        "memory_id": save_memory(
            request.content, request.kind, request.verified
        )
    }


@app.get("/v1/genomes")
def list_genomes(limit: int = 20):
    c = db()
    rows = c.execute(
        "SELECT * FROM genomes ORDER BY created_at DESC LIMIT ?",
        (min(max(limit, 1), 100),)
    ).fetchall()
    c.close()
    return [dict(x) for x in rows]


@app.post("/v1/genomes")
def add_genome(request: GenomeRequest):
    return {
        "genome_id": save_genome(
            "manual",
            {
                "objective": request.objective,
                "strategy": request.strategy,
                "created_at": now_iso()
            }
        )
    }


@app.get("/v1/research/{research_id}")
def get_research(research_id: str):
    c = db()
    row = c.execute(
        "SELECT * FROM research WHERE id=?", (research_id,)
    ).fetchone()
    c.close()
    if not row:
        raise HTTPException(status_code=404, detail="Research not found")
    out = dict(row)
    try:
        out["report"] = json.loads(out["report_json"])
    except Exception:
        pass
    return out


@app.get("/v1/evidence/{task_id}")
def get_evidence(task_id: str):
    c = db()
    rows = c.execute(
        "SELECT * FROM evidence WHERE task_id=? ORDER BY created_at DESC",
        (task_id,)
    ).fetchall()
    c.close()
    return [dict(x) for x in rows]


@app.get("/v1/next-cycle/{task_id}")
def next_cycle(task_id: str):
    c = db()
    row = c.execute(
        "SELECT report_json FROM research WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
        (task_id,)
    ).fetchone()
    c.close()
    if not row:
        raise HTTPException(status_code=404, detail="Research not found")
    report = json.loads(row["report_json"])
    return {
        "task_id": task_id,
        "version": VERSION,
        "actions": report.get("next_cycle", [])
    }


@app.get("/v1/security/policy")
def security_policy():
    return {
        "default_deny_consequential_actions": True,
        "automatic_spending": False,
        "credential_exfiltration": False,
        "uncontrolled_self_modification": False,
        "arbitrary_shell_execution": False,
        "audit": True
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
