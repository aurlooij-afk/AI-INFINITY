import ast
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
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

VERSION = "TARGET-2050.29"
BUILD = "CLAIM-EVIDENCE-LINKING-VERIFICATION-CORE"
EVIDENCE_VERSION = "EVIDENCE-INTEGRITY-4.0"

BASE = Path(os.getenv("AI_INFINITY_HOME", "/tmp/ai-infinity"))
DB_PATH = BASE / "ai_infinity.db"
BASE.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = int(os.getenv("AI_INFINITY_HTTP_TIMEOUT", "18"))
MAX_SOURCE_BYTES = int(os.getenv("AI_INFINITY_MAX_SOURCE_BYTES", "180000"))
MAX_DISCOVERY_PER_QUERY = int(os.getenv("AI_INFINITY_DISCOVERY_PER_QUERY", "5"))
MAX_COLLECTED_SOURCES = int(os.getenv("AI_INFINITY_MAX_SOURCES", "12"))
MAX_CLAIMS = int(os.getenv("AI_INFINITY_MAX_CLAIMS", "36"))

USER_AGENT = f"AI-Infinity/{VERSION}"

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Evidence-first autonomous research and execution core",
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


class VerifyRequest(BaseModel):
    claim: str
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    provenance: List[str] = Field(default_factory=list)


# ============================================================
# PERSISTENCE
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    c = db()

    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY,
          objective TEXT,
          status TEXT,
          result_json TEXT,
          created_at TEXT,
          updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS memories(
          id TEXT PRIMARY KEY,
          kind TEXT,
          content TEXT,
          verified INTEGER,
          created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence(
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

        CREATE TABLE IF NOT EXISTS research(
          id TEXT PRIMARY KEY,
          task_id TEXT,
          question TEXT,
          status TEXT,
          report_json TEXT,
          created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS graph_edges(
          id TEXT PRIMARY KEY,
          task_id TEXT,
          from_id TEXT,
          to_id TEXT,
          relationship TEXT,
          confidence REAL,
          reason TEXT,
          metadata_json TEXT,
          created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS events(
          id TEXT PRIMARY KEY,
          task_id TEXT,
          event_type TEXT,
          data_json TEXT,
          created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS failures(
          id TEXT PRIMARY KEY,
          task_id TEXT,
          stage TEXT,
          error TEXT,
          recovery_json TEXT,
          created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_evidence_task
        ON evidence(task_id);

        CREATE INDEX IF NOT EXISTS idx_edges_task
        ON graph_edges(task_id);

        CREATE INDEX IF NOT EXISTS idx_events_task
        ON events(task_id);
        """
    )

    c.commit()
    c.close()


init_db()


def event(task_id: Optional[str], kind: str, data: Any) -> None:
    c = db()

    c.execute(
        "INSERT INTO events VALUES(?,?,?,?,?)",
        (
            uid("event"),
            task_id,
            kind,
            dump(data),
            now_iso(),
        ),
    )

    c.commit()
    c.close()


def save_task(
    task_id: str,
    objective: str,
    status: str,
    result: Any = None,
) -> None:
    t = now_iso()
    c = db()

    c.execute(
        """
        INSERT INTO tasks(
            id,
            objective,
            status,
            result_json,
            created_at,
            updated_at
        )
        VALUES(?,?,?,?,?,?)

        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,
            result_json=excluded.result_json,
            updated_at=excluded.updated_at
        """,
        (
            task_id,
            objective,
            status,
            dump(result) if result is not None else None,
            t,
            t,
        ),
    )

    c.commit()
    c.close()


def save_memory(
    content: str,
    kind: str,
    verified: bool,
) -> str:
    mid = uid("memory")
    c = db()

    c.execute(
        "INSERT INTO memories VALUES(?,?,?,?,?)",
        (
            mid,
            kind,
            content,
            int(verified),
            now_iso(),
        ),
    )

    c.commit()
    c.close()

    return mid


def save_research(
    task_id: str,
    question: str,
    status: str,
    report: Dict[str, Any],
) -> str:
    rid = uid("research")
    c = db()

    c.execute(
        "INSERT INTO research VALUES(?,?,?,?,?,?)",
        (
            rid,
            task_id,
            question,
            status,
            dump(report),
            now_iso(),
        ),
    )

    c.commit()
    c.close()

    return rid


def save_evidence(
    task_id: str,
    source: Dict[str, Any],
) -> None:
    c = db()

    c.execute(
        "INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            source["evidence_id"],
            task_id,
            source["url"],
            source["title"],
            domain_of(source["url"]),
            "",
            source["text"][:1800],
            source["hash"],
            "collected",
            source["relevance"],
            dump(
                {
                    k: source.get(k)
                    for k in (
                        "provider",
                        "query",
                        "source_family",
                        "work_id",
                        "retrieved_at",
                        "http_status",
                    )
                }
            ),
            now_iso(),
        ),
    )

    c.commit()
    c.close()


def save_edge(
    task_id: str,
    edge: Dict[str, Any],
) -> None:
    c = db()

    c.execute(
        "INSERT INTO graph_edges VALUES(?,?,?,?,?,?,?,?,?)",
        (
            edge["edge_id"],
            task_id,
            edge["from_id"],
            edge["to_id"],
            edge["relationship"],
            edge["confidence"],
            edge["reason"],
            dump(edge.get("metadata", {})),
            now_iso(),
        ),
    )

    c.commit()
    c.close()


# ============================================================
# TEXT / EVIDENCE MODEL
# ============================================================

STOP = set(
    """
    a an and are as at be been being by for from had has have
    how i if in into is it its me more most of on or our that
    the their them there these they this to was were what when
    where which who why will with you your about become need
    needed use using used than then can could should would may
    might do does did not only all any each other such through
    based per very real world make made
    """.split()
)

RESEARCH_TERMS = set(
    """
    agent agents autonomous autonomy task tasks execution reliable
    reliability planning planner tool tools verification verify safety
    security memory evaluation benchmark failure monitoring control
    reasoning workflow multi-agent agentic alignment evidence provenance
    grounding recovery robust uncertainty success performance experiment
    study result results method methods intervention human oversight
    deployment real-world
    """.split()
)

NOISE_PHRASES = (
    "article history",
    "compiled",
    "table of contents",
    "copyright",
    "all rights reserved",
    "university of",
    "abstract",
    "references",
    "keywords",
    "figure ",
    "supplementary",
)

LIMIT_WORDS = set(
    """
    limitation limited however although despite caveat only under
    condition conditional depends dependency exception failure
    drawback tradeoff
    """.split()
)

NEG_WORDS = set(
    """
    not no cannot fails failed failure unreliable unsafe worse lower
    decline degrades degraded
    """.split()
)

POS_WORDS = set(
    """
    improves improved effective reliable successful success safe
    verified robust accurate supports supported works working
    """.split()
)


def tokens(text: str) -> set:
    return {
        x
        for x in re.findall(
            r"[a-z0-9][a-z0-9_-]{2,}",
            text.lower(),
        )
        if x not in STOP
    }


def keyword_hits(text: str) -> set:
    return tokens(text) & RESEARCH_TERMS


def normalize_title(title: str) -> str:
    t = re.sub(r"\[[^]]+\]", " ", title.lower())
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return re.sub(r"\s+", " ", t)


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def canonical_url(url: str) -> str:
    try:
        p = urlparse(url)

        return (
            f"{p.scheme.lower()}://"
            f"{p.netloc.lower()}"
            f"{re.sub(r'/{2,}', '/', p.path or '/').rstrip('/') or '/'}"
        )

    except Exception:
        return url


def source_family(url: str) -> str:
    d = domain_of(url)

    if "arxiv.org" in d:
        return "repository"

    if "europepmc.org" in d:
        return "bibliographic_repository"

    if "doi.org" in d:
        return "doi_landing"

    if "crossref.org" in d:
        return "bibliographic_index"

    if "openalex.org" in d:
        return "bibliographic_index"

    return d or "unknown"


def work_identity(item: Dict[str, Any]) -> str:
    doi = (
        str(item.get("doi") or "")
        .lower()
        .replace("https://doi.org/", "")
        .replace("doi:", "")
        .strip()
    )

    if doi:
        return "doi:" + doi

    title = normalize_title(
        str(item.get("title", ""))
    )

    if title:
        return "title:" + hashlib.sha1(
            title.encode()
        ).hexdigest()[:20]

    return str(
        item.get("work_id")
        or uid("work")
    )


def clean_text(text: str) -> str:
    text = re.sub(
        r"<script.*?</script>|<style.*?</style>",
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

    return text[:MAX_SOURCE_BYTES]


def sentence_split(text: str) -> List[str]:
    return [
        re.sub(r"\s+", " ", x).strip()
        for x in re.split(
            r"(?<=[.!?])\s+",
            text,
        )
        if x.strip()
    ]


def valid_claim_sentence(s: str) -> bool:
    low = s.lower()

    if not 70 <= len(s) <= 520:
        return False

    if any(
        p in low
        for p in NOISE_PHRASES
    ):
        return False

    if s.count("|") >= 2:
        return False

    if len(
        re.findall(
            r"[A-Za-z]",
            s,
        )
    ) < 45:
        return False

    words = tokens(s)

    if len(words) < 10:
        return False

    if not (words & RESEARCH_TERMS):
        return False

    if re.match(
        r"^(title|authors?|abstract|introduction|keywords?|references?)\s*[:.-]",
        low,
    ):
        return False

    return True


def claim_quality(
    s: str,
    question: str,
) -> float:
    w = tokens(s)
    q = tokens(question)

    kw = len(
        w & RESEARCH_TERMS
    ) / max(
        1,
        min(8, len(w)),
    )

    overlap = len(
        w & q
    ) / max(
        1,
        min(10, len(q)),
    )

    empirical = (
        0.15
        if any(
            x in s.lower()
            for x in (
                "study",
                "experiment",
                "results",
                "evaluation",
                "benchmark",
                "measured",
                "found",
                "observed",
            )
        )
        else 0.0
    )

    return min(
        1.0,
        0.45 * overlap
        + 0.35 * kw
        + empirical,
    )


def sentence_similarity(
    a: str,
    b: str,
) -> float:
    A = tokens(a)
    B = tokens(b)

    if not A or not B:
        return 0.0

    inter = len(A & B)
    union = len(A | B)

    containment = inter / max(
        1,
        min(len(A), len(B)),
    )

    jaccard = inter / max(
        1,
        union,
    )

    key_inter = len(
        (A & RESEARCH_TERMS)
        & (B & RESEARCH_TERMS)
    )

    key_bonus = min(
        0.25,
        key_inter * 0.035,
    )

    return min(
        1.0,
        0.65 * jaccard
        + 0.35 * containment
        + key_bonus,
    )


def relation_hint(
    claim: str,
    evidence: str,
) -> Tuple[str, float]:
    c = tokens(claim)
    e = tokens(evidence)

    neg_c = bool(c & NEG_WORDS)
    neg_e = bool(e & NEG_WORDS)

    lim_e = bool(e & LIMIT_WORDS)

    sim = sentence_similarity(
        claim,
        evidence,
    )

    if sim < 0.16:
        return "", sim

    if (
        neg_c != neg_e
        and sim >= 0.22
    ):
        return (
            "CONTRADICTS",
            min(
                0.95,
                sim + 0.18,
            ),
        )

    if (
        lim_e
        and sim >= 0.28
    ):
        return (
            "LIMITS",
            min(
                0.92,
                sim + 0.10,
            ),
        )

    return (
        "SUPPORTS",
        sim,
    )


def normalize_numeric(
    text: str,
) -> List[float]:
    vals = []

    for x in re.findall(
        r"(?<![A-Za-z])\d+(?:\.\d+)?%?",
        text,
    ):
        try:
            vals.append(
                float(x.rstrip("%"))
            )
        except ValueError:
            pass

    return vals


# ============================================================
# DISCOVERY
# ============================================================

def http_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    r = requests.get(
        url,
        params=params,
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/json, "
                "text/plain, */*"
            ),
        },
    )

    r.raise_for_status()

    return r.json()


def arxiv_search(
    query: str,
    limit: int,
) -> List[Dict[str, Any]]:
    import xml.etree.ElementTree as ET

    r = requests.get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": limit,
        },
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": USER_AGENT
        },
    )

    r.raise_for_status()

    root = ET.fromstring(r.text)

    ns = {
        "a": "http://www.w3.org/2005/Atom"
    }

    out = []

    for ent in root.findall(
        "a:entry",
        ns,
    ):
        title = (
            ent.findtext(
                "a:title",
                "",
                ns,
            )
            or ""
        ).strip()

        summary = (
            ent.findtext(
                "a:summary",
                "",
                ns,
            )
            or ""
        ).strip()

        link = ""

        for l in ent.findall(
            "a:link",
            ns,
        ):
            if (
                l.attrib.get("type")
                == "text/html"
            ):
                link = l.attrib.get(
                    "href",
                    "",
                )

        if not link:
            idv = (
                ent.findtext(
                    "a:id",
                    "",
                    ns,
                )
                or ""
            )

            link = idv

        doi = ""

        for x in ent.findall(
            "a:link",
            ns,
        ):
            href = x.attrib.get(
                "href",
                "",
            )

            if "doi.org" in href:
                doi = href

        out.append(
            {
                "title": title,
                "summary": summary,
                "url": link,
                "doi": doi,
                "work_id": (
                    "arxiv:"
                    + link.rsplit("/", 1)[-1]
                ),
            }
        )

    return out


def crossref_search(
    query: str,
    limit: int,
) -> List[Dict[str, Any]]:
    data = http_json(
        "https://api.crossref.org/works",
        {
            "query.bibliographic": query,
            "rows": limit,
            "select": (
                "DOI,title,URL,"
                "abstract,published"
            ),
        },
    )

    out = []

    for x in data.get(
        "message",
        {},
    ).get(
        "items",
        [],
    ):
        title = (
            x.get("title")
            or [""]
        )[0]

        doi = x.get(
            "DOI",
            "",
        )

        out.append(
            {
                "title": title,
                "summary": (
                    x.get("abstract")
                    or ""
                ),
                "url": (
                    x.get("URL")
                    or (
                        "https://doi.org/"
                        + doi
                        if doi
                        else ""
                    )
                ),
                "doi": doi,
                "work_id": (
                    "doi:"
                    + doi.lower()
                    if doi
                    else ""
                ),
            }
        )

    return out


def europe_pmc_search(
    query: str,
    limit: int,
) -> List[Dict[str, Any]]:
    data = http_json(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        {
            "query": query,
            "format": "json",
            "pageSize": limit,
            "resultType": "core",
        },
    )

    out = []

    for x in data.get(
        "resultList",
        {},
    ).get(
        "result",
        [],
    ):
        title = x.get(
            "title",
            "",
        )

        pmid = x.get(
            "pmid",
            "",
        )

        doi = x.get(
            "doi",
            "",
        )

        url = (
            "https://europepmc.org/article/MED/"
            + pmid
            if pmid
            else (
                "https://doi.org/"
                + doi
                if doi
                else ""
            )
        )

        out.append(
            {
                "title": title,
                "summary": (
                    x.get("abstractText")
                    or ""
                ),
                "url": url,
                "doi": doi,
                "work_id": (
                    "pmid:"
                    + pmid
                    if pmid
                    else (
                        "doi:"
                        + doi.lower()
                        if doi
                        else ""
                    )
                ),
            }
        )

    return out


def research_anchor(
    question: str,
) -> str:
    words = [
        w
        for w in re.findall(
            r"[A-Za-z][A-Za-z0-9-]{2,}",
            question.lower(),
        )
        if w not in STOP
    ]

    return " ".join(
        dict.fromkeys(words[:22])
    )


def expand_queries(
    question: str,
) -> List[str]:
    a = research_anchor(
        question
    )

    qs = [
        f"{a} empirical study",
        f"{a} benchmark evaluation",
        f"{a} task success failure",
        f"{a} real world deployment",
        f"{a} reliability limitations",
        f"{a} human intervention monitoring",
        f"{a} independent replication",
        f"{a} systematic evaluation",
        f"{a} failure modes",
        f"{a} performance evaluation",
    ]

    return list(
        dict.fromkeys(qs)
    )


def relevance(
    question: str,
    title: str,
    text: str,
) -> float:
    q = tokens(
        research_anchor(question)
    )

    tt = tokens(title)

    tx = tokens(
        text[:60000]
    )

    if not q:
        return 0.0

    title_overlap = len(
        q & tt
    ) / max(
        1,
        len(q),
    )

    text_overlap = len(
        q & tx
    ) / max(
        1,
        min(len(q), 14),
    )

    kh = len(
        keyword_hits(
            title + " " + text
        )
        & keyword_hits(question)
    ) / max(
        1,
        len(
            keyword_hits(question)
        ),
    )

    return round(
        max(
            0,
            min(
                1,
                0.50 * title_overlap
                + 0.35 * text_overlap
                + 0.15 * kh,
            ),
        ),
        4,
    )


def fetch_url(
    url: str,
) -> Dict[str, Any]:
    p = urlparse(url)

    if (
        p.scheme
        not in (
            "http",
            "https",
        )
        or not p.netloc
    ):
        raise ValueError(
            "unsupported_url"
        )

    r = requests.get(
        url,
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,"
                "text/plain,"
                "application/pdf,"
                "*/*"
            ),
        },
        allow_redirects=True,
    )

    raw = r.content[
        :MAX_SOURCE_BYTES
    ]

    decoded = raw.decode(
        "utf-8",
        errors="replace",
    )

    text = clean_text(
        decoded
    )

    title = ""

    m = re.search(
        r"<title[^>]*>(.*?)</title>",
        decoded,
        re.I | re.S,
    )

    if m:
        title = re.sub(
            r"\s+",
            " ",
            re.sub(
                r"<[^>]+>",
                " ",
                m.group(1),
            ),
        ).strip()

    if not title:
        title = (
            urlparse(url)
            .path
            .rsplit("/", 1)[-1]
            or domain_of(url)
        )

    return {
        "url": r.url,
        "title": title[:500],
        "text": text,
        "http_status": r.status_code,
        "hash": hashlib.sha256(
            raw
        ).hexdigest(),
        "retrieved_at": now_iso(),
    }


def discover_sources(
    question: str,
) -> Dict[str, Any]:
    providers = [
        (
            "arxiv",
            arxiv_search,
        ),
        (
            "crossref",
            crossref_search,
        ),
        (
            "europe_pmc",
            europe_pmc_search,
        ),
    ]

    items = []
    diagnostics = []

    queries = expand_queries(
        question
    )

    for q in queries:
        for name, fn in providers:
            try:
                batch = fn(
                    q,
                    MAX_DISCOVERY_PER_QUERY,
                )

                for x in batch:
                    x.update(
                        {
                            "query": q,
                            "provider": name,
                        }
                    )

                items.extend(batch)

                diagnostics.append(
                    {
                        "query": q,
                        "provider": name,
                        "count": len(batch),
                        "status": "ok",
                    }
                )

            except Exception as exc:
                diagnostics.append(
                    {
                        "query": q,
                        "provider": name,
                        "count": 0,
                        "status": "error",
                        "error": str(exc)[:250],
                    }
                )

    unique = []
    seen = set()

    for x in items:
        key = (
            x.get("doi")
            or x.get("work_id")
            or canonical_url(
                x.get("url", "")
            )
        )

        if (
            key
            and key not in seen
            and x.get("url")
        ):
            seen.add(key)
            unique.append(x)

    return {
        "queries": queries,
        "search_anchor": research_anchor(
            question
        ),
        "items": unique,
        "diagnostics": diagnostics,
    }


def collect_sources(
    task_id: str,
    question: str,
    discovered: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    candidates = []

    for item in discovered:
        try:
            got = fetch_url(
                item["url"]
            )

            score = relevance(
                question,
                got["title"],
                got["text"],
            )

            if (
                got["http_status"] >= 400
                or len(got["text"]) < 600
                or score < 0.12
            ):
                continue

            got.update(
                {
                    "provider": item.get(
                        "provider"
                    ),
                    "query": item.get(
                        "query"
                    ),
                    "relevance": score,
                    "source_family": source_family(
                        got["url"]
                    ),
                    "work_id": work_identity(
                        {
                            **item,
                            "title": got["title"],
                        }
                    ),
                    "substantive": True,
                }
            )

            candidates.append(got)

        except Exception:
            continue

    candidates.sort(
        key=lambda x: (
            x["relevance"],
            len(x["text"]),
        ),
        reverse=True,
    )

    selected = []
    hashes = set()
    works = set()

    for x in candidates:
        if (
            x["hash"] in hashes
            or x["work_id"] in works
        ):
            continue

        selected.append(x)

        hashes.add(x["hash"])
        works.add(x["work_id"])

        if (
            len(selected)
            >= MAX_COLLECTED_SOURCES
        ):
            break

    for x in candidates:
        if (
            len(selected)
            >= MAX_COLLECTED_SOURCES
        ):
            break

        if (
            x["hash"] in hashes
            or x["work_id"] in works
        ):
            continue

        selected.append(x)
        hashes.add(x["hash"])
        works.add(x["work_id"])

    for x in selected:
        x["evidence_id"] = uid(
            "evidence"
        )

        save_evidence(
            task_id,
            x,
        )

    return selected


# ============================================================
# CLAIM EXTRACTION
# ============================================================

def extract_claims(
    question: str,
    sources: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    claims = []
    seen = set()

    for s in sources:
        candidates = []

        for sent in sentence_split(
            s["text"]
        ):
            if not valid_claim_sentence(
                sent
            ):
                continue

            q = claim_quality(
                sent,
                question,
            )

            if q < 0.08:
                continue

            fingerprint = hashlib.sha1(
                " ".join(
                    sorted(
                        tokens(sent)
                    )
                ).encode()
            ).hexdigest()

            if fingerprint in seen:
                continue

            seen.add(fingerprint)

            candidates.append(
                (
                    q,
                    sent,
                )
            )

        candidates.sort(
            reverse=True
        )

        for q, sent in candidates[:8]:
            claims.append(
                {
                    "claim_id": uid("claim"),
                    "text": sent,
                    "normalized": " ".join(
                        sorted(
                            tokens(sent)
                        )
                    ),
                    "source_id": s[
                        "evidence_id"
                    ],
                    "source_url": s[
                        "url"
                    ],
                    "work_id": s[
                        "work_id"
                    ],
                    "domain": domain_of(
                        s["url"]
                    ),
                    "source_family": s[
                        "source_family"
                    ],
                    "quality": round(
                        q,
                        4,
                    ),
                    "evidence_ids": [
                        s["evidence_id"]
                    ],
                    "supporting_sources": [],
                    "contradicting_sources": [],
                    "limiting_sources": [],
                    "corroborating_sources": [],
                    "status": "UNCERTAIN",
                    "confidence": 0.0,
                    "edges": [],
                }
            )

    claims.sort(
        key=lambda c: c["quality"],
        reverse=True,
    )

    return claims[:MAX_CLAIMS]


def best_evidence_sentence(
    claim: str,
    source_text: str,
) -> Tuple[str, float]:
    best = (
        "",
        0.0,
    )

    for s in sentence_split(
        source_text
    ):
        if len(s) < 50:
            continue

        sim = sentence_similarity(
            claim,
            s,
        )

        if sim > best[1]:
            best = (
                s,
                sim,
            )

    return best


# ============================================================
# EVIDENCE GRAPH
# ============================================================

def build_evidence_graph(
    task_id: str,
    claims: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:
    by_id = {
        s["evidence_id"]: s
        for s in sources
    }

    edges = []

    rel_counts = {
        "SUPPORTS": 0,
        "CORROBORATES": 0,
        "CONTRADICTS": 0,
        "LIMITS": 0,
    }

    for c in claims:
        own = by_id.get(
            c["source_id"]
        )

        for s in sources:
            if not own:
                continue

            if (
                s["work_id"]
                == c["work_id"]
            ):
                continue

            excerpt, sim = best_evidence_sentence(
                c["text"],
                s["text"],
            )

            if sim < 0.22:
                continue

            rel, conf = relation_hint(
                c["text"],
                excerpt,
            )

            if not rel:
                continue

            # A second independent work
            # explicitly becomes corroborating
            # evidence when the relationship is
            # supportive.
            if rel == "SUPPORTS":
                rel = "CORROBORATES"

            edge = {
                "edge_id": uid("edge"),
                "from_id": c["claim_id"],
                "to_id": s["evidence_id"],
                "relationship": rel,
                "confidence": round(
                    conf,
                    4,
                ),
                "reason": (
                    "Sentence-level semantic "
                    f"overlap={sim:.3f}; "
                    "independent work="
                    f"{s['work_id']}"
                ),
                "metadata": {
                    "excerpt": excerpt[:900],
                    "source_url": s["url"],
                    "work_id": s[
                        "work_id"
                    ],
                },
            }

            edges.append(edge)

            rel_counts[rel] += 1

            save_edge(
                task_id,
                edge,
            )

            c["edges"].append(
                edge["edge_id"]
            )

            if rel == "CORROBORATES":
                c[
                    "corroborating_sources"
                ].append(
                    s["url"]
                )

                c[
                    "supporting_sources"
                ].append(
                    s["url"]
                )

                c[
                    "evidence_ids"
                ].append(
                    s["evidence_id"]
                )

            elif rel == "CONTRADICTS":
                c[
                    "contradicting_sources"
                ].append(
                    s["url"]
                )

            elif rel == "LIMITS":
                c[
                    "limiting_sources"
                ].append(
                    s["url"]
                )

    for c in claims:
        c["evidence_ids"] = list(
            dict.fromkeys(
                c["evidence_ids"]
            )
        )

        c["supporting_sources"] = list(
            dict.fromkeys(
                c["supporting_sources"]
            )
        )

        c[
            "corroborating_sources"
        ] = list(
            dict.fromkeys(
                c[
                    "corroborating_sources"
                ]
            )
        )

        c[
            "contradicting_sources"
        ] = list(
            dict.fromkeys(
                c[
                    "contradicting_sources"
                ]
            )
        )

        c["limiting_sources"] = list(
            dict.fromkeys(
                c["limiting_sources"]
            )
        )

    return {
        "edges": edges,
        "counts": rel_counts,
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_claims(
    claims: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    graph: Dict[str, Any],
) -> Dict[str, Any]:
    by_id = {
        s["evidence_id"]: s
        for s in sources
    }

    verified = []
    uncertain = []
    contradicted = []
    limited = []

    for c in claims:
        works = {
            by_id[e]["work_id"]
            for e in c["evidence_ids"]
            if e in by_id
        }

        corroborating = [
            e
            for e in graph["edges"]
            if (
                e["from_id"]
                == c["claim_id"]
                and e["relationship"]
                == "CORROBORATES"
                and e["confidence"]
                >= 0.30
            )
        ]

        contradictions = [
            e
            for e in graph["edges"]
            if (
                e["from_id"]
                == c["claim_id"]
                and e["relationship"]
                == "CONTRADICTS"
            )
        ]

        limits = [
            e
            for e in graph["edges"]
            if (
                e["from_id"]
                == c["claim_id"]
                and e["relationship"]
                == "LIMITS"
            )
        ]

        if contradictions:
            c["status"] = "CONTRADICTED"
            c["confidence"] = 0.20
            contradicted.append(c)
            continue

        if (
            len(works) >= 2
            and corroborating
        ):
            c["status"] = "VERIFIED"

            c["confidence"] = round(
                min(
                    0.97,
                    0.55
                    + 0.10
                    * len(corroborating)
                    + 0.10
                    * c["quality"],
                ),
                3,
            )

            verified.append(c)
            continue

        if limits:
            c["status"] = "LIMITED"

            c["confidence"] = round(
                min(
                    0.75,
                    0.45
                    + 0.08
                    * len(limits),
                ),
                3,
            )

            limited.append(c)
            continue

        if len(
            c["evidence_ids"]
        ) >= 1:
            c["status"] = "SUPPORTED"

            c["confidence"] = round(
                min(
                    0.68,
                    0.45
                    + 0.12
                    * c["quality"],
                ),
                3,
            )

            uncertain.append(c)
            continue

        c["status"] = "UNCERTAIN"
        c["confidence"] = 0.0

        uncertain.append(c)

    return {
        "claims": claims,
        "verified": len(verified),
        "uncertain": sum(
            1
            for c in claims
            if c["status"]
            == "UNCERTAIN"
        ),
        "supported": sum(
            1
            for c in claims
            if c["status"]
            == "SUPPORTED"
        ),
        "limited": len(limited),
        "contradicted": len(
            contradicted
        ),
        "verified_claims": verified,
    }


# ============================================================
# EVIDENCE GATE
# ============================================================

def evidence_gate(
    sources: List[Dict[str, Any]],
    verification: Dict[str, Any],
    graph: Dict[str, Any],
) -> Dict[str, Any]:
    works = {
        s["work_id"]
        for s in sources
    }

    domains = {
        domain_of(s["url"])
        for s in sources
    }

    families = {
        s["source_family"]
        for s in sources
    }

    valid = [
        c
        for c in verification["claims"]
        if c["status"]
        in (
            "VERIFIED",
            "SUPPORTED",
            "LIMITED",
        )
        and len(c["text"]) >= 70
    ]

    unsupported = [
        c
        for c in verification["claims"]
        if not c["evidence_ids"]
    ]

    checks = {
        "minimum_sources": (
            len(sources) >= 3
        ),
        "independent_works": (
            len(works) >= 3
        ),
        "independent_domains": (
            len(domains) >= 2
        ),
        "independent_source_families": (
            len(families) >= 2
        ),
        "minimum_valid_claims": (
            len(valid) >= 3
        ),
        "evidence_edges_exist": (
            len(graph["edges"]) > 0
        ),
        "verified_claims_exist": (
            verification["verified"]
            > 0
        ),
        "no_unsupported_claims": (
            len(unsupported) == 0
        ),
        "no_contradicted_claims": (
            verification[
                "contradicted"
            ]
            == 0
        ),
    }

    passed = all(
        checks.values()
    )

    reasons = []

    names = {
        "minimum_sources":
            "fewer_than_3_sources",
        "independent_works":
            "fewer_than_3_independent_works",
        "independent_domains":
            "fewer_than_2_domains",
        "independent_source_families":
            "fewer_than_2_source_families",
        "minimum_valid_claims":
            "fewer_than_3_valid_claims",
        "evidence_edges_exist":
            "no_evidence_edges",
        "verified_claims_exist":
            "no_verified_claims",
        "no_unsupported_claims":
            "unsupported_claims_present",
        "no_contradicted_claims":
            "contradicted_claims_present",
    }

    for k, v in checks.items():
        if not v:
            reasons.append(
                names[k]
            )

    return {
        "version": EVIDENCE_VERSION,
        "passed": passed,
        "checks": checks,
        "source_count": len(sources),
        "work_count": len(works),
        "domain_count": len(domains),
        "source_family_count": len(families),
        "valid_claim_count": len(valid),
        "verified_claim_count": (
            verification["verified"]
        ),
        "edge_count": len(
            graph["edges"]
        ),
        "reasons": reasons,
    }


# ============================================================
# SOURCE QUALITY
# ============================================================

def source_quality(
    s: Dict[str, Any],
) -> float:
    fam = s.get(
        "source_family",
        "",
    )

    base = (
        0.85
        if fam
        in (
            "repository",
            "bibliographic_repository",
        )
        else (
            0.78
            if fam
            in (
                "doi_landing",
                "bibliographic_index",
            )
            else 0.65
        )
    )

    return round(
        min(
            0.98,
            base
            + 0.08
            * s.get(
                "relevance",
                0,
            ),
        ),
        3,
    )


# ============================================================
# SYNTHESIS
# ============================================================

def synthesis(
    question: str,
    verification: Dict[str, Any],
    gate: Dict[str, Any],
) -> Dict[str, Any]:
    claims = verification[
        "claims"
    ]

    findings = [
        {
            "claim_id": c[
                "claim_id"
            ],
            "text": c["text"],
            "status": c[
                "status"
            ],
            "confidence": c[
                "confidence"
            ],
            "evidence_ids": c[
                "evidence_ids"
            ],
        }
        for c in claims
        if c["status"]
        in (
            "VERIFIED",
            "SUPPORTED",
            "LIMITED",
        )
    ][:12]

    uncertainties = [
        {
            "claim_id": c[
                "claim_id"
            ],
            "text": c["text"],
            "status": c[
                "status"
            ],
        }
        for c in claims
        if c["status"]
        in (
            "UNCERTAIN",
            "CONTRADICTED",
        )
    ][:12]

    return {
        "status": (
            "evidence_verified"
            if gate["passed"]
            else "evidence_incomplete"
        ),
        "findings": findings,
        "uncertainties": uncertainties,
        "next_actions": [
            (
                "Inspect every VERIFIED "
                "claim's evidence excerpts "
                "before consequential use"
            ),
            (
                "Run targeted follow-up "
                "research for UNCERTAIN "
                "or LIMITED claims"
            ),
            (
                "Preserve contradictory "
                "evidence rather than "
                "deleting it"
            ),
            (
                "Do not treat SUPPORTED "
                "as independently "
                "corroborated"
            ),
        ],
        "rule": (
            "Synthesis is constrained to "
            "claims and evidence produced "
            "by the pipeline."
        ),
    }


# ============================================================
# SAFE EXECUTION
# ============================================================

SAFE_ACTIONS = {
    "calculator_test",
    "hash_text",
    "validate_python",
}


def safe_calculator(
    expression: str,
) -> Any:
    tree = ast.parse(
        expression,
        mode="eval",
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
        ast.FloorDiv,
    )

    for node in ast.walk(tree):
        if not isinstance(
            node,
            allowed,
        ):
            raise ValueError(
                "calculator_expression_not_allowed"
            )

        if (
            isinstance(
                node,
                ast.Constant,
            )
            and not isinstance(
                node.value,
                (int, float),
            )
        ):
            raise ValueError(
                "calculator_values_must_be_numeric"
            )

    return eval(
        compile(
            tree,
            "<calculator>",
            "eval",
        ),
        {
            "__builtins__": {}
        },
        {},
    )


def execute_action(
    action: str,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    if action == "calculator_test":
        return {
            "value": safe_calculator(
                str(
                    args.get(
                        "expression",
                        "2+3*4",
                    )
                )
            )
        }

    if action == "hash_text":
        return {
            "sha256": hashlib.sha256(
                str(
                    args.get(
                        "text",
                        "",
                    )
                ).encode()
            ).hexdigest()
        }

    if action == "validate_python":
        ast.parse(
            str(
                args.get(
                    "source",
                    "",
                )
            )
        )

        return {
            "valid": True
        }

    raise ValueError(
        "action_not_authorized"
    )


# ============================================================
# RESEARCH PIPELINE
# ============================================================

def research_pipeline(
    task_id: str,
    question: str,
) -> Dict[str, Any]:
    discovery = discover_sources(
        question
    )

    sources = collect_sources(
        task_id,
        question,
        discovery["items"],
    )

    claims = extract_claims(
        question,
        sources,
    )

    graph = build_evidence_graph(
        task_id,
        claims,
        sources,
    )

    verification = verify_claims(
        claims,
        sources,
        graph,
    )

    gate = evidence_gate(
        sources,
        verification,
        graph,
    )

    synth = synthesis(
        question,
        verification,
        gate,
    )

    report = {
        "version": VERSION,
        "build": BUILD,
        "question": question,
        "queries": discovery[
            "queries"
        ],
        "search_anchor": discovery[
            "search_anchor"
        ],
        "discovery": discovery[
            "diagnostics"
        ],
        "sources": [
            {
                "evidence_id": s[
                    "evidence_id"
                ],
                "url": s["url"],
                "title": s[
                    "title"
                ],
                "provider": s.get(
                    "provider"
                ),
                "query": s.get(
                    "query"
                ),
                "relevance": s[
                    "relevance"
                ],
                "quality": source_quality(
                    s
                ),
                "source_family": s[
                    "source_family"
                ],
                "work_id": s[
                    "work_id"
                ],
                "domain": domain_of(
                    s["url"]
                ),
            }
            for s in sources
        ],
        "claims": verification[
            "claims"
        ],
        "evidence": {
            "count": len(sources),
            "graph_nodes": (
                len(sources)
                + len(claims)
            ),
            "graph_edges": len(
                graph["edges"]
            ),
            "relationships": graph[
                "counts"
            ],
            "independent_domains": len(
                {
                    domain_of(
                        s["url"]
                    )
                    for s in sources
                }
            ),
            "domains": sorted(
                {
                    domain_of(
                        s["url"]
                    )
                    for s in sources
                }
            ),
            "independent_works": len(
                {
                    s["work_id"]
                    for s in sources
                }
            ),
            "average_source_quality": round(
                sum(
                    source_quality(s)
                    for s in sources
                )
                / max(
                    1,
                    len(sources),
                ),
                3,
            ),
            "evidence_tiers": {
                "FULL_TEXT": len(
                    sources
                )
            },
        },
        "verification": {
            "verified": verification[
                "verified"
            ],
            "supported": verification[
                "supported"
            ],
            "uncertain": verification[
                "uncertain"
            ],
            "contradicted": verification[
                "contradicted"
            ],
            "limited": verification[
                "limited"
            ],
        },
        "evidence_graph": {
            "nodes": [
                {
                    "id": s[
                        "evidence_id"
                    ],
                    "type": "evidence",
                    "work_id": s[
                        "work_id"
                    ],
                }
                for s in sources
            ]
            + [
                {
                    "id": c[
                        "claim_id"
                    ],
                    "type": "claim",
                }
                for c in claims
            ],
            "edges": graph[
                "edges"
            ],
        },
        "evidence_gate": gate,
        "synthesis": synth,
        "status": (
            "completed"
            if gate["passed"]
            else "insufficient_evidence"
        ),
        "provenance": {
            "query_count": len(
                discovery["queries"]
            ),
            "source_count": len(
                sources
            ),
            "underlying_work_count": len(
                {
                    s["work_id"]
                    for s in sources
                }
            ),
            "domains": sorted(
                {
                    domain_of(
                        s["url"]
                    )
                    for s in sources
                }
            ),
            "source_families": sorted(
                {
                    s["source_family"]
                    for s in sources
                }
            ),
            "evidence_gate": (
                EVIDENCE_VERSION
            ),
        },
    }

    rid = save_research(
        task_id,
        question,
        report["status"],
        report,
    )

    report[
        "research_id"
    ] = rid

    event(
        task_id,
        "research_completed",
        {
            "status": report[
                "status"
            ],
            "gate": gate,
            "edges": len(
                graph["edges"]
            ),
            "verified": verification[
                "verified"
            ],
        },
    )

    return report


# ============================================================
# INTELLIGENCE GENOME
# ============================================================

def make_genome(
    task_id: str,
    objective: str,
    research: Optional[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:
    gate = (
        research or {}
    ).get(
        "evidence_gate",
        {},
    )

    return {
        "version": VERSION,
        "task_id": task_id,
        "objective": objective,
        "reusable": bool(
            gate.get(
                "passed"
            )
        ),
        "strategy": {
            "claim_quality":
                "sentence_level",
            "evidence":
                "independent_work_linking",
            "verification":
                "corroboration_required",
            "contradictions":
                "preserve_and_surface",
            "gate":
                "evidence_integrity_4",
        },
        "fitness": (
            1.0
            if gate.get(
                "passed"
            )
            else 0.0
        ),
    }


# ============================================================
# MAIN RUNNER
# ============================================================

def run_infinity(
    req: RunRequest,
) -> Dict[str, Any]:
    task_id = uid(
        "mission"
    )

    save_task(
        task_id,
        req.objective,
        "running",
    )

    event(
        task_id,
        "started",
        {
            "version": VERSION,
            "build": BUILD,
        },
    )

    try:
        research = (
            research_pipeline(
                task_id,
                req.objective,
            )
            if req.research
            else None
        )

        gate = (
            research or {}
        ).get(
            "evidence_gate",
            {},
        )

        memory_id = None

        if req.remember:
            memory_id = save_memory(
                dump(
                    {
                        "objective":
                            req.objective,
                        "research_status":
                            (
                                research
                                or {}
                            ).get(
                                "status",
                                "not_requested",
                            ),
                        "gate": gate,
                        "research_id":
                            (
                                research
                                or {}
                            ).get(
                                "research_id"
                            ),
                    }
                ),
                (
                    "verified_research"
                    if gate.get(
                        "passed"
                    )
                    else "research_uncertainty"
                ),
                bool(
                    gate.get(
                        "passed"
                    )
                ),
            )

        result = {
            "task_id": task_id,
            "mission_id": task_id,
            "status": "completed",
            "version": VERSION,
            "build": BUILD,
            "objective": req.objective,
            "research": research,
            "memory_id": memory_id,
            "intelligence_genome": make_genome(
                task_id,
                req.objective,
                research,
            ),
            "temporary_minds": [
                {
                    "name": x,
                    "status": "ready",
                }
                for x in (
                    "researcher",
                    "planner",
                    "critic",
                    "simulator",
                    "verifier",
                )
            ],
            "safety": {
                "automatic_spending": False,
                "arbitrary_shell_execution": False,
                "automatic_self_modification": False,
                "external_side_effects_default": False,
                "authorized_actions_only": True,
                "evidence_gate": True,
            },
        }

        save_task(
            task_id,
            req.objective,
            "completed",
            result,
        )

        event(
            task_id,
            "completed",
            {
                "gate": gate
            },
        )

        return result

    except Exception as exc:
        recovery = [
            "preserve collected evidence",
            "retry failed providers",
            "fail closed on missing evidence",
            "do not turn unverified text into facts",
        ]

        c = db()

        c.execute(
            "INSERT INTO failures VALUES(?,?,?,?,?,?)",
            (
                uid("failure"),
                task_id,
                "run_infinity",
                str(exc),
                dump(recovery),
                now_iso(),
            ),
        )

        c.commit()
        c.close()

        save_task(
            task_id,
            req.objective,
            "failed",
            {
                "error": str(exc),
                "recovery": recovery,
            },
        )

        event(
            task_id,
            "failed",
            {
                "error": str(exc)
            },
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Task failed: "
                + str(exc)
            ),
        )


# ============================================================
# API
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "evidence_gate":
            EVIDENCE_VERSION,
    }


@app.get("/v1/status")
def status():
    c = db()
    counts = {}

    for table in (
        "tasks",
        "memories",
        "evidence",
        "research",
        "graph_edges",
    ):
        counts[table] = c.execute(
            f"SELECT COUNT(*) n FROM {table}"
        ).fetchone()["n"]

    c.close()

    return {
        "service": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "counts": counts,
        "governor": {
            "free_first": True,
            "automatic_spending": False,
            "external_side_effects_default": False,
        },
        "evidence_gate":
            EVIDENCE_VERSION,
    }


@app.post("/v1/run")
def run(
    request: RunRequest,
):
    return run_infinity(
        request
    )


@app.get("/v1/tasks/{task_id}")
def get_task(
    task_id: str,
):
    c = db()

    row = c.execute(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()

    c.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    out = dict(row)

    if out.get(
        "result_json"
    ):
        try:
            out["result"] = json.loads(
                out.pop(
                    "result_json"
                )
            )
        except Exception:
            pass

    return out


@app.get("/v1/evidence")
def list_evidence(
    task_id: Optional[str] = None,
    limit: int = 100,
):
    c = db()

    lim = min(
        max(limit, 1),
        300,
    )

    if task_id:
        rows = c.execute(
            """
            SELECT *
            FROM evidence
            WHERE task_id=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (
                task_id,
                lim,
            ),
        ).fetchall()
    else:
        rows = c.execute(
            """
            SELECT *
            FROM evidence
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()

    c.close()

    return [
        dict(x)
        for x in rows
    ]


@app.get(
    "/v1/evidence/graph/{task_id}"
)
def evidence_graph(
    task_id: str,
):
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM graph_edges
        WHERE task_id=?
        ORDER BY created_at
        """,
        (task_id,),
    ).fetchall()

    c.close()

    return {
        "task_id": task_id,
        "edges": [
            dict(x)
            for x in rows
        ],
        "count": len(rows),
    }


@app.get(
    "/v1/research/{research_id}"
)
def get_research(
    research_id: str,
):
    c = db()

    row = c.execute(
        """
        SELECT *
        FROM research
        WHERE id=?
        """,
        (research_id,),
    ).fetchone()

    c.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Research not found",
        )

    out = dict(row)

    try:
        out["report"] = json.loads(
            out.pop(
                "report_json"
            )
        )
    except Exception:
        pass

    return out


@app.get("/v1/failures")
def failures(
    limit: int = 50,
):
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM failures
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            min(
                max(limit, 1),
                200,
            ),
        ),
    ).fetchall()

    c.close()

    return [
        dict(x)
        for x in rows
    ]


@app.get("/v1/audit")
def audit(
    limit: int = 100,
):
    c = db()

    rows = c.execute(
        """
        SELECT *
        FROM events
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            min(
                max(limit, 1),
                300,
            ),
        ),
    ).fetchall()

    c.close()

    return [
        dict(x)
        for x in rows
    ]


@app.get("/v1/export")
def export_state():
    c = db()

    tables = {}

    for table in (
        "tasks",
        "memories",
        "evidence",
        "research",
        "graph_edges",
        "events",
        "failures",
    ):
        tables[table] = [
            dict(x)
            for x in c.execute(
                f"SELECT * FROM {table}"
            ).fetchall()
        ]

    c.close()

    return {
        "version": VERSION,
        "build": BUILD,
        "exported_at": now_iso(),
        "tables": tables,
    }


@app.post("/v1/verify")
def verify_endpoint(
    req: VerifyRequest,
):
    usable = [
        e
        for e in req.evidence
        if isinstance(e, dict)
        and e.get("source")
    ]

    result = {
        "claim": req.claim,
        "verified": bool(
            usable
            and req.provenance
        ),
        "evidence_count": len(
            usable
        ),
        "provenance": req.provenance,
        "issues": (
            []
            if usable
            and req.provenance
            else [
                "evidence_and_provenance_required"
            ]
        ),
        "note": (
            "This endpoint checks "
            "supplied evidence state; "
            "it does not manufacture proof."
        ),
    }

    event(
        None,
        "verification.completed",
        result,
    )

    return result


@app.post("/v1/execute")
def execute(
    req: ExecuteRequest,
):
    if req.action not in SAFE_ACTIONS:
        raise HTTPException(
            status_code=403,
            detail="Action not authorized",
        )

    try:
        result = execute_action(
            req.action,
            req.args,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    return {
        "status": "verified",
        "action": req.action,
        "result": result,
        "safety": {
            "registered_action_only": True,
            "external_side_effects": False,
            "spending": False,
            "self_modification": False,
        },
    }


# ============================================================
# WEB UI
# ============================================================

@app.get("/")
def home():
    return HTMLResponse(
        f"""
<!doctype html>
<html>
<head>
<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>
<title>AI Infinity</title>

<style>
body {{
    font-family:system-ui;
    max-width:960px;
    margin:auto;
    padding:20px;
    background:#080d17;
    color:#eef2f7;
}}

textarea {{
    width:100%;
    min-height:150px;
    box-sizing:border-box;
    background:#111827;
    color:#fff;
    border:1px solid #26344b;
    border-radius:14px;
    padding:14px;
}}

button {{
    padding:13px 18px;
    margin:10px 6px 10px 0;
    border:0;
    border-radius:12px;
    font-weight:700;
}}

pre {{
    white-space:pre-wrap;
    background:#111827;
    padding:15px;
    border-radius:14px;
    overflow:auto;
}}

.grid {{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:10px;
}}

.card {{
    background:#111827;
    border:1px solid #26344b;
    border-radius:14px;
    padding:15px;
}}

@media(max-width:650px) {{
    .grid {{
        grid-template-columns:1fr;
    }}
}}
</style>
</head>

<body>

<h1>∞ AI Infinity</h1>

<p>
{VERSION} · {BUILD}
</p>

<textarea
    id="o"
    placeholder="Enter an objective for autonomous research..."
></textarea>

<br>

<button onclick="runTask()">
START AUTONOMOUS RESEARCH
</button>

<div class="grid">

<div class="card">
Evidence graph:
claim → source → relationship
</div>

<div class="card">
Verification:
corroboration required
</div>

</div>

<pre id="out">
Ready.
</pre>

<script>

async function runTask() {{

    const objective =
        document
        .getElementById('o')
        .value
        .trim();

    if (!objective) {{
        out.textContent =
            'Enter an objective.';
        return;
    }}

    out.textContent =
        'Running...';

    try {{

        const r =
            await fetch(
                '/v1/run',
                {{
                    method:'POST',
                    headers:{{
                        'Content-Type':
                            'application/json'
                    }},
                    body:JSON.stringify({{
                        objective,
                        research:true,
                        verify:true,
                        remember:true,
                        allow_paid:false
                    }})
                }}
            );

        out.textContent =
            await r.text();

    }} catch(e) {{

        out.textContent =
            'Request error: ' + e;

    }}
}}

</script>

</body>
</html>
"""
    )


# ============================================================
# LOCAL ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
    )
