"""
AI Infinity
TARGET-2050.35
BUILD: CLOSED-LOOP-AUTONOMOUS-RESEARCH-REALITY-CORE

Final-stage practical upgrade:
- closed-loop mission controller
- evidence contracts and claim-level provenance
- independent-source verification gate
- contradiction screening
- automatic bounded recovery when verification fails
- action/tool ledger
- measurable reality audit
- persistent SQLite state when the host storage permits it
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
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


VERSION = "TARGET-2050.35"
BUILD = "FINAL-CLAIM-ATOMICITY-ENTAILMENT-PROVENANCE-CORE"

BASE = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity"))
BASE.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE / "ai_infinity.db"

MAX_WORKERS = 6
TIMEOUT = 15
MAX_TEXT = 80000
RESULTS = 8

pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
db_lock = threading.Lock()

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Closed-loop evidence-first autonomous research engine",
)


# ============================================================
# DATABASE
# ============================================================

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
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                created REAL NOT NULL,
                updated REAL NOT NULL,
                result TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                created REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS works (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                title TEXT,
                url TEXT,
                doi TEXT,
                provider TEXT,
                domain TEXT,
                family TEXT,
                text TEXT,
                quality REAL DEFAULT 0,
                relevance REAL DEFAULT 0,
                work_key TEXT,
                authors TEXT DEFAULT '',
                created REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                work_id INTEGER NOT NULL,
                claim_id INTEGER NOT NULL,
                excerpt TEXT NOT NULL,
                locator TEXT,
                score REAL DEFAULT 0,
                relation TEXT,
                quality TEXT,
                created REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                claim TEXT NOT NULL,
                status TEXT DEFAULT 'INSUFFICIENT',
                purity REAL DEFAULT 0,
                confidence REAL DEFAULT 0,
                claim_type TEXT DEFAULT 'empirical',
                entailment REAL DEFAULT 0,
                evidence_count INTEGER DEFAULT 0,
                independent_works INTEGER DEFAULT 0,
                independent_domains INTEGER DEFAULT 0,
                independent_families INTEGER DEFAULT 0,
                contradiction_count INTEGER DEFAULT 0,
                created REAL NOT NULL
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
                created REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                target TEXT,
                status TEXT NOT NULL,
                result TEXT,
                attempt INTEGER DEFAULT 1,
                created REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audits (
                mission_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                key TEXT,
                value TEXT,
                created REAL NOT NULL
            );
            """
        )

        # Lightweight schema migration so existing 2050.34 deployments can
        # upgrade without deleting their database.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(claims)").fetchall()}
        for name, typ, default in [
            ("claim_type", "TEXT", "'empirical'"),
            ("entailment", "REAL", "0"),
            ("evidence_count", "INTEGER", "0"),
            ("independent_works", "INTEGER", "0"),
            ("independent_domains", "INTEGER", "0"),
            ("independent_families", "INTEGER", "0"),
            ("contradiction_count", "INTEGER", "0"),
        ]:
            if name not in existing:
                conn.execute(f"ALTER TABLE claims ADD COLUMN {name} {typ} DEFAULT {default}")

        existing_work = {row[1] for row in conn.execute("PRAGMA table_info(works)").fetchall()}
        if "authors" not in existing_work:
            conn.execute("ALTER TABLE works ADD COLUMN authors TEXT DEFAULT ''")

        conn.execute("CREATE INDEX IF NOT EXISTS ix_work_identity ON works(mission_id,work_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_claim_identity ON claims(mission_id,claim)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_evidence_identity ON evidence(mission_id,claim_id,work_id,excerpt)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_edge_identity ON edges(mission_id,source_type,source_id,relation,target_type,target_id)")
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
# CORE HELPERS
# ============================================================

def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def tokens(text: str) -> set:
    stop = {
        "the", "and", "for", "with", "from", "that", "this",
        "are", "was", "were", "has", "have", "been", "into",
        "their", "they", "about", "than", "also", "using",
        "used", "can", "may", "more", "will", "not", "but",
        "its", "our", "between", "which", "such",
    }
    return {
        word
        for word in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
        if word not in stop
    }


def similarity(a: str, b: str) -> float:
    aa = tokens(a)
    bb = tokens(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


def domain_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def source_family(domain: str) -> str:
    known = {
        "arxiv.org": "arxiv",
        "nature.com": "nature",
        "science.org": "science",
        "acm.org": "acm",
        "ieee.org": "ieee",
        "openreview.net": "openreview",
        "sciencedirect.com": "sciencedirect",
        "springer.com": "springer",
    }
    return known.get(domain, domain or "unknown")


def safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname

        if parsed.scheme not in {"http", "https"} or not host:
            return False

        if host.lower() in {
            "localhost",
            "localhost.localdomain",
            "metadata.google.internal",
            "instance-data",
        }:
            return False

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
        response = requests.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": "AI-Infinity/2050.35"},
        )

        content_type = response.headers.get("content-type", "").lower()

        if response.status_code >= 400:
            return None

        if "text" not in content_type and "html" not in content_type:
            return None

        text = re.sub(
            r"<(script|style)\b[^>]*>.*?</\1>",
            " ",
            response.text,
            flags=re.I | re.S,
        )
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        return clean(text)[:MAX_TEXT]

    except Exception:
        return None


# ============================================================
# CLAIM PURITY
# ============================================================

NOISE_PATTERNS = [
    r"you are going to email", r"similar content", r"subscribe",
    r"privacy policy", r"terms of use", r"home\s*>", r"table of contents",
    r"e-?issn", r"impact factor", r"pip install", r"from [\w.]+ import",
    r"^import ", r"```", r"click here", r"read more", r"how to cite",
    r"keywords?\b", r"references?\b", r"bibliography", r"citation",
    r"doi\s*[:/]|https?://|www\.", r"©\s*\d{4}", r"volume\s*\d+",
    r"issue\s*\d+", r"pages?\s*\d+[-–]\d+", r"open access",
]

META_CLAIM_PATTERNS = [
    r"^(our|this|the)\s+(analysis|paper|article|study|review|work)\b",
    r"\bwe\s+(argue|propose|present|discuss|draw on|conclude|show)\b",
    r"\bthis\s+(paper|article|study|review)\s+(argues|proposes|presents|discusses)\b",
]

PREDICATES = {
    "is", "are", "was", "were", "can", "cannot", "may", "might", "shows",
    "found", "finds", "demonstrates", "suggests", "reports", "requires",
    "improves", "reduces", "increases", "causes", "associated", "predicts",
    "supports", "limits", "fails", "achieves", "performs", "uses", "operate",
    "operates", "interact", "completed", "succeeded", "failed", "varies",
    "depends", "correlates", "generalizes", "outperforms", "degrades",
}


def claim_type(text: str) -> str:
    low = clean(text).lower()
    if any(re.search(p, low) for p in META_CLAIM_PATTERNS):
        return "synthesis"
    if re.search(r"\b(method|methodology|framework|approach|algorithm|benchmark|evaluation)\b", low):
        return "methodological"
    if re.search(r"\b(recommend|should|ought|governance|policy|proportionate|risk management)\b", low):
        return "recommendation"
    if re.search(r"\b(review|survey|literature)\b", low):
        return "review"
    return "empirical"


def claim_purity(text: str) -> float:
    text = clean(text)
    low = text.lower()
    words = text.split()
    if len(text) < 55 or len(text) > 650:
        return 0.0
    if any(re.search(pattern, low) for pattern in NOISE_PATTERNS):
        return 0.0
    if any(re.search(pattern, low) for pattern in META_CLAIM_PATTERNS):
        return 0.0
    if "@" in text or text.count("/") >= 3 or text.count(":") >= 4:
        return 0.0
    if re.search(r"\b(doi|isbn|issn|author|affiliation|keywords?)\b", low):
        return 0.0
    if sum(c.isdigit() for c in text) > max(12, len(text) // 8):
        return 0.0
    score = 0.45
    if 9 <= len(words) <= 55:
        score += 0.15
    if any(re.search(rf"\b{re.escape(word)}\b", low) for word in PREDICATES):
        score += 0.20
    if re.search(r"\b(ai|agent|autonom|llm|robot|execution|tool|task|safety|reliab|research)\w*", low):
        score += 0.10
    if re.search(r"[.!?]$", text):
        score += 0.05
    if text.count(",") <= 4:
        score += 0.05
    return min(1.0, score)


def atomicize(sentence: str):
    """Prefer one proposition per claim; prevents compound metadata-like claims."""
    sentence = clean(sentence)
    parts = re.split(r"\s*;\s*", sentence)
    out = []
    for part in parts:
        part = clean(part)
        if len(part) >= 55:
            out.append(part)
    return out[:3]


def extract_claims(text: str, objective: str):
    results = []
    seen = set()
    for raw in re.split(r"(?<=[.!?])\s+", text or ""):
        sentence = clean(re.sub(r"^\s*[-*0-9.)]+\s*", "", raw))
        for candidate in atomicize(sentence):
            purity = claim_purity(candidate)
            if purity < 0.60:
                continue
            key = re.sub(r"\W+", " ", candidate.lower()).strip()
            if key in seen:
                continue
            if similarity(candidate, objective) < 0.015 and not re.search(
                r"\b(ai|agent|autonom|llm|robot|execution|tool|task|safety|reliab|research)\w*",
                candidate.lower(),
            ):
                continue
            seen.add(key)
            results.append((candidate, purity, claim_type(candidate)))
            if len(results) >= 35:
                return results
    return results


def content_tokens(text: str) -> set:
    return tokens(text) - {"can", "may", "might", "could", "would", "should"}


def evidence_entailment(claim: str, excerpt: str):
    """Deterministic evidence entailment heuristic; conservative by design."""
    c = content_tokens(claim)
    e = content_tokens(excerpt)
    if not c or not e:
        return 0.0, "NO_ENTAILMENT"
    coverage = len(c & e) / max(1, len(c))
    precision = len(c & e) / max(1, len(e))
    base = (0.72 * coverage) + (0.28 * min(1.0, precision * 2.0))
    nums_c = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", claim))
    nums_e = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", excerpt))
    if nums_c and not nums_c.issubset(nums_e):
        base *= 0.55
    neg_c = bool(re.search(r"\b(not|no|cannot|fails|unable|rarely|never)\b", claim.lower()))
    neg_e = bool(re.search(r"\b(not|no|cannot|fails|unable|rarely|never)\b", excerpt.lower()))
    if neg_c != neg_e:
        base *= 0.35
    if coverage >= 0.82 and precision >= 0.20:
        verdict = "ENTAILS"
    elif coverage >= 0.58:
        verdict = "PARTIAL"
    else:
        verdict = "NO_ENTAILMENT"
    return round(min(1.0, base), 4), verdict


# ============================================================
# PROVIDERS
# ============================================================

def discover_crossref(objective: str):
    try:
        data = requests.get(
            "https://api.crossref.org/works",
            params={"query": objective, "rows": RESULTS},
            timeout=TIMEOUT,
        ).json()

        result = []

        for item in data.get("message", {}).get("items", []):
            title = clean(" ".join(item.get("title", []) or []))
            if title:
                result.append(
                    {
                        "provider": "crossref",
                        "title": title,
                        "url": item.get("URL", ""),
                        "doi": item.get("DOI", ""),
                    }
                )

        return result

    except Exception:
        return []


def discover_openalex(objective: str):
    try:
        data = requests.get(
            "https://api.openalex.org/works",
            params={"search": objective, "per-page": RESULTS},
            timeout=TIMEOUT,
        ).json()

        result = []

        for item in data.get("results", []):
            title = clean(item.get("title", ""))
            url = (
                (item.get("primary_location") or {}).get("landing_page_url")
                or item.get("doi")
                or ""
            )

            if title:
                result.append(
                    {
                        "provider": "openalex",
                        "title": title,
                        "url": url,
                        "doi": item.get("doi", ""),
                    }
                )

        return result

    except Exception:
        return []


def discover_semantic_scholar(objective: str):
    try:
        data = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": objective,
                "limit": RESULTS,
                "fields": "title,abstract,url,openAccessPdf,externalIds",
            },
            timeout=TIMEOUT,
        ).json()

        result = []

        for item in data.get("data", []):
            title = clean(item.get("title", ""))
            url = (
                (item.get("openAccessPdf") or {}).get("url")
                or item.get("url", "")
            )

            if title:
                result.append(
                    {
                        "provider": "semantic_scholar",
                        "title": title,
                        "url": url,
                        "doi": (item.get("externalIds") or {}).get(
                            "DOI", ""
                        ),
                        "abstract": item.get("abstract") or "",
                    }
                )

        return result

    except Exception:
        return []


def discover_sources(objective: str):
    raw = (
        discover_crossref(objective)
        + discover_openalex(objective)
        + discover_semantic_scholar(objective)
    )

    accepted = []
    seen = set()

    for item in raw:
        url = item.get("url") or ""
        dom = domain_of(url)

        work_key = (
            (item.get("doi") or "").lower()
            or hashlib.sha256(
                (
                    clean(item.get("title", "")).lower()
                    + dom
                ).encode()
            ).hexdigest()
        )

        if work_key in seen:
            continue

        seen.add(work_key)

        relevance = similarity(
            objective,
            item.get("title", "")
            + " "
            + item.get("abstract", ""),
        )

        if relevance < 0.01:
            continue

        item.update(
            {
                "domain": dom,
                "family": source_family(dom),
                "relevance": round(relevance, 4),
                "quality": round(
                    min(
                        1.0,
                        0.4
                        + 0.1 * bool(item.get("doi"))
                        + 0.2 * bool(url),
                    ),
                    3,
                ),
                "work_key": work_key,
            }
        )

        accepted.append(item)

    return raw, accepted


# ============================================================
# EVENT / ACTION LEDGER
# ============================================================

def event(mission_id, stage, status, detail):
    with db_lock:
        conn = db()
        conn.execute(
            """
            INSERT INTO events
            (mission_id,stage,status,detail,created)
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


def action(
    mission_id,
    kind,
    target,
    status,
    result,
    attempt,
):
    with db_lock:
        conn = db()
        conn.execute(
            """
            INSERT INTO actions
            (mission_id,kind,target,status,result,attempt,created)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                mission_id,
                kind,
                target,
                status,
                result,
                attempt,
                now(),
            ),
        )
        conn.commit()
        conn.close()


def set_status(mission_id, status):
    with db_lock:
        conn = db()
        conn.execute(
            "UPDATE missions SET status=?,updated=? WHERE id=?",
            (status, now(), mission_id),
        )
        conn.commit()
        conn.close()


# ============================================================
# RESEARCH PIPELINE
# ============================================================

def discovery_pass(mission_id, objective, attempt):
    event(
        mission_id,
        "discovery",
        "running",
        f"source pass {attempt}",
    )

    raw, accepted = discover_sources(objective)

    with db_lock:
        conn = db()

        inserted = 0
        for item in accepted:
            exists = conn.execute("SELECT 1 FROM works WHERE mission_id=? AND work_key=? LIMIT 1", (mission_id, item["work_key"])).fetchone()
            if exists:
                continue
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO works
                (mission_id,title,url,doi,provider,domain,family,text,quality,relevance,work_key,authors,created)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (mission_id,item["title"],item["url"],item.get("doi", ""),item["provider"],
                 item["domain"],item["family"],item.get("abstract", ""),item["quality"],
                 item["relevance"],item["work_key"],item.get("authors", ""),now()),
            )
            inserted += cur.rowcount

        conn.commit()
        conn.close()

    event(
        mission_id,
        "discovery",
        "completed",
        f"{len(raw)} discovered; {inserted} new works accepted",
    )

    return len(accepted)


def ingest_evidence(mission_id):
    event(
        mission_id,
        "evidence_ingestion",
        "running",
        "Fetching source text",
    )

    with db_lock:
        conn = db()
        rows = conn.execute(
            """
            SELECT id,url
            FROM works
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchall()
        conn.close()

    def fetch_one(row):
        return row["id"], fetch_url(row["url"])

    futures = [
        pool.submit(fetch_one, row)
        for row in rows
    ]

    usable = 0

    with db_lock:
        conn = db()

        for future in as_completed(futures):
            work_id, text = future.result()

            if text:
                conn.execute(
                    "UPDATE works SET text=? WHERE id=?",
                    (text, work_id),
                )
                usable += 1

        conn.commit()
        conn.close()

    event(
        mission_id,
        "evidence_ingestion",
        "completed",
        f"{usable} usable works",
    )

    return usable


def build_claims(mission_id, objective):
    event(mission_id, "claims", "running", "Extracting atomic substantive claims")
    with db_lock:
        conn = db()
        works = conn.execute("SELECT id,text FROM works WHERE mission_id=? AND text!=''", (mission_id,)).fetchall()
        existing = conn.execute("SELECT claim FROM claims WHERE mission_id=?", (mission_id,)).fetchall()
        existing_keys = {re.sub(r"\W+", " ", row["claim"].lower()).strip() for row in existing}
        conn.close()
    created = []
    with db_lock:
        conn = db()
        for work in works:
            for claim, purity, ctype in extract_claims(work["text"], objective):
                key = re.sub(r"\W+", " ", claim.lower()).strip()
                if key in existing_keys:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO claims (mission_id,claim,purity,claim_type,created) VALUES (?,?,?,?,?)",
                    (mission_id, claim, purity, ctype, now()),
                )
                if cur.rowcount:
                    existing_keys.add(key)
                    created.append((claim, purity, cur.lastrowid))
        conn.commit(); conn.close()
    event(mission_id, "claims", "completed", f"{len(created)} new atomic claims")
    return created


# ============================================================
# EVIDENCE CONTRACT
# ============================================================

def evidence_contract(claim, excerpt):
    lexical = similarity(claim, excerpt)
    entailment, verdict = evidence_entailment(claim, excerpt)
    score = round((0.35 * lexical) + (0.65 * entailment), 4)
    if verdict == "ENTAILS" and score >= 0.58:
        relation, quality = "DIRECT_SUPPORT", "DIRECT"
    elif verdict in {"ENTAILS", "PARTIAL"} and score >= 0.42:
        relation, quality = "SUPPORT", "STRONG"
    elif verdict == "PARTIAL" and score >= 0.28:
        relation, quality = "WEAK_MATCH", "MODERATE"
    else:
        relation, quality = "NO_SUPPORT", "WEAK"
    return score, relation, quality, lexical, entailment, verdict


def build_graph(mission_id):
    event(mission_id, "evidence_graph", "running", "Building deduplicated claim-level provenance graph")
    with db_lock:
        conn = db()
        # Rebuild derived provenance deterministically; this prevents recovery rounds
        # from inflating evidence/edge counts. Raw works and claims remain durable.
        conn.execute("DELETE FROM edges WHERE mission_id=?", (mission_id,))
        conn.execute("DELETE FROM evidence WHERE mission_id=?", (mission_id,))
        claims = conn.execute("SELECT id,claim FROM claims WHERE mission_id=?", (mission_id,)).fetchall()
        works = conn.execute("SELECT id,text,domain,family,work_key FROM works WHERE mission_id=? AND text!=''", (mission_id,)).fetchall()
        for claim in claims:
            for work in works:
                sentences = [clean(x) for x in re.split(r"(?<=[.!?])\s+", work["text"] or "") if len(clean(x)) >= 40]
                if not sentences: continue
                ranked = sorted(sentences, key=lambda x: similarity(claim["claim"], x), reverse=True)[:4]
                excerpt = ranked[0]
                score, relation, quality, lexical, entailment, verdict = evidence_contract(claim["claim"], excerpt)
                if relation not in {"DIRECT_SUPPORT", "SUPPORT"}: continue
                already = conn.execute("SELECT id FROM evidence WHERE mission_id=? AND work_id=? AND claim_id=? AND excerpt=? LIMIT 1", (mission_id, work["id"], claim["id"], excerpt[:1200])).fetchone()
                if already:
                    continue
                ec = conn.execute(
                    """INSERT INTO evidence (mission_id,work_id,claim_id,excerpt,locator,score,relation,quality,created) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (mission_id, work["id"], claim["id"], excerpt[:1200], "sentence", score, relation, quality, now()),
                )
                evidence_id = ec.lastrowid
                if evidence_id:
                    conn.execute(
                        """INSERT OR IGNORE INTO edges (mission_id,source_type,source_id,relation,target_type,target_id,score,reason,created) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (mission_id,"claim",claim["id"],relation,"evidence",evidence_id,score,
                         json.dumps({"domain":work["domain"],"family":work["family"],"lexical":lexical,"entailment":entailment,"verdict":verdict}),now()),
                    )
        for i,left in enumerate(claims):
            for right in claims[i+1:]:
                score = similarity(left["claim"], right["claim"])
                if score < 0.62: continue
                ln = bool(re.search(r"\b(not|no|cannot|fails|unable|rarely|never)\b", left["claim"].lower()))
                rn = bool(re.search(r"\b(not|no|cannot|fails|unable|rarely|never)\b", right["claim"].lower()))
                relation = "POTENTIAL_CONTRADICTION" if ln != rn else "RELATED"
                conn.execute(
                    """INSERT OR IGNORE INTO edges (mission_id,source_type,source_id,relation,target_type,target_id,score,reason,created) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (mission_id,"claim",left["id"],relation,"claim",right["id"],score,"polarity + lexical comparison",now()),
                )
                # Symmetric contradiction visibility.
                if relation == "POTENTIAL_CONTRADICTION":
                    conn.execute(
                        """INSERT OR IGNORE INTO edges (mission_id,source_type,source_id,relation,target_type,target_id,score,reason,created) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (mission_id,"claim",right["id"],relation,"claim",left["id"],score,"symmetric contradiction marker",now()),
                    )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM edges WHERE mission_id=?", (mission_id,)).fetchone()[0]
        conn.close()
    event(mission_id,"evidence_graph","completed",f"{count} deduplicated graph edges")
    return count


# ============================================================
# VERIFICATION GATE
# ============================================================

def verify_claims(mission_id):
    event(mission_id, "verification", "running", "Applying conservative entailment + independence gate")
    with db_lock:
        conn = db()
        claims = conn.execute("SELECT * FROM claims WHERE mission_id=?", (mission_id,)).fetchall()
        for claim in claims:
            evidence = conn.execute(
                """SELECT e.score,e.relation,e.quality,w.domain,w.family,w.id,w.work_key FROM evidence e JOIN works w ON w.id=e.work_id WHERE e.claim_id=? AND e.mission_id=? AND e.relation IN ('DIRECT_SUPPORT','SUPPORT')""",
                (claim["id"],mission_id)).fetchall()
            by_work = {x["work_key"]: x for x in evidence if x["work_key"]}
            work_ids = {x["id"] for x in by_work.values()}
            domains = {x["domain"] for x in by_work.values() if x["domain"]}
            families = {x["family"] for x in by_work.values() if x["family"]}
            contradiction_count = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE mission_id=? AND relation='POTENTIAL_CONTRADICTION' AND source_type='claim' AND source_id=?",
                (mission_id,claim["id"])).fetchone()[0]
            entailment = max([float(x["score"]) for x in evidence] + [0.0])
            # Verification is intentionally difficult: two genuinely distinct works,
            # two domains and two publisher/source families, with two strong supports.
            strong_count = sum(1 for x in evidence if float(x["score"]) >= 0.58)
            if len(work_ids) >= 2 and len(domains) >= 2 and len(families) >= 2 and strong_count >= 2 and contradiction_count == 0:
                status = "VERIFIED"
            elif contradiction_count > 0 and not evidence:
                status = "CONTRADICTED"
            elif evidence:
                status = "SUPPORTED"
            else:
                status = "INSUFFICIENT"
            confidence = round(min(0.99, 0.35*entailment + 0.15*min(1,len(work_ids)/2) + 0.15*min(1,len(domains)/2) + 0.15*min(1,len(families)/2) + 0.10*min(1,strong_count/2) + 0.10*claim["purity"]), 3)
            conn.execute(
                """UPDATE claims SET status=?,confidence=?,entailment=?,evidence_count=?,independent_works=?,independent_domains=?,independent_families=?,contradiction_count=? WHERE id=?""",
                (status,confidence,entailment,len(evidence),len(work_ids),len(domains),len(families),contradiction_count,claim["id"]))
        conn.commit()
        rows = conn.execute("SELECT status,COUNT(*) AS n FROM claims WHERE mission_id=? GROUP BY status", (mission_id,)).fetchall()
        conn.close()
    summary={row["status"]:row["n"] for row in rows}
    event(mission_id,"verification","completed",f"verified={summary.get('VERIFIED',0)}; supported={summary.get('SUPPORTED',0)}; contradicted={summary.get('CONTRADICTED',0)}")
    return summary


# ============================================================
# CLOSED-LOOP RECOVERY
# ============================================================

def recovery_pass(mission_id, objective, round_number):
    variants = [
        (
            objective
            + " systematic review benchmark empirical evaluation "
              "limitations failure cases"
        ),
        (
            objective
            + " independent replication evidence real-world deployment "
              "study safety reliability"
        ),
    ]

    query = variants[(round_number - 1) % len(variants)]

    action(
        mission_id,
        "research_recovery",
        query,
        "RUNNING",
        "verification gate not yet satisfied",
        round_number,
    )

    discovery_pass(
        mission_id,
        query,
        round_number + 1,
    )
    ingest_evidence(mission_id)
    build_claims(mission_id, objective)
    build_graph(mission_id)

    action(
        mission_id,
        "research_recovery",
        query,
        "COMPLETED",
        "additional evidence pass completed",
        round_number,
    )


# ============================================================
# REALITY AUDIT
# ============================================================

def build_audit(mission_id):
    with db_lock:
        conn = db()

        def count(query):
            return conn.execute(
                query,
                (mission_id,),
            ).fetchone()[0]

        mission = conn.execute(
            "SELECT * FROM missions WHERE id=?",
            (mission_id,),
        ).fetchone()

        works = count(
            "SELECT COUNT(*) FROM works WHERE mission_id=?"
        )

        usable = count(
            """
            SELECT COUNT(*)
            FROM works
            WHERE mission_id=?
              AND text!=''
            """
        )

        claims = count(
            "SELECT COUNT(*) FROM claims WHERE mission_id=?"
        )

        evidence = count(
            "SELECT COUNT(*) FROM evidence WHERE mission_id=?"
        )

        support = count(
            """
            SELECT COUNT(*)
            FROM evidence
            WHERE mission_id=?
              AND relation IN
                  ('DIRECT_SUPPORT','SUPPORT')
            """
        )

        edges = count(
            "SELECT COUNT(*) FROM edges WHERE mission_id=?"
        )

        verified = count(
            """
            SELECT COUNT(*)
            FROM claims
            WHERE mission_id=?
              AND status='VERIFIED'
            """
        )

        actions = count(
            "SELECT COUNT(*) FROM actions WHERE mission_id=?"
        )

        supported = count(
            "SELECT COUNT(*) FROM claims WHERE mission_id=? AND status='SUPPORTED'"
        )

        contradicted = count(
            "SELECT COUNT(*) FROM claims WHERE mission_id=? AND status='CONTRADICTED'"
        )

        high_purity = count(
            "SELECT COUNT(*) FROM claims WHERE mission_id=? AND purity>=0.75"
        )

        domains = count(
            """
            SELECT COUNT(DISTINCT domain)
            FROM works
            WHERE mission_id=?
              AND domain!=''
            """
        )

        families = count(
            """
            SELECT COUNT(DISTINCT family)
            FROM works
            WHERE mission_id=?
              AND family!=''
            """
        )

        conn.close()

    return {
        "version": VERSION,
        "build": BUILD,
        "mission_id": mission_id,
        "objective": mission["objective"] if mission else None,
        "status": mission["status"] if mission else None,
        "metrics": {
            "works": works,
            "usable_works": usable,
            "claims": claims,
            "evidence": evidence,
            "support_edges": support,
            "graph_edges": edges,
            "verified_claims": verified,
            "domains": domains,
            "source_families": families,
            "actions": actions,
            "supported_claims": supported,
            "contradicted_claims": contradicted,
            "high_purity_claims": high_purity,
        },
        "reality": {
            "autonomous_research": (
                "DEMONSTRATED"
                if usable > 0
                else "NOT DEMONSTRATED"
            ),
            "evidence_provenance": (
                "DEMONSTRATED"
                if evidence > 0 and support > 0
                else "NOT DEMONSTRATED"
            ),
            "claim_quality_control": (
                "DEMONSTRATED"
                if claims > 0 and high_purity == claims
                else "PARTIALLY DEMONSTRATED" if high_purity > 0 else "NOT DEMONSTRATED"
            ),
            "independent_verification": (
                "DEMONSTRATED"
                if verified > 0
                else "NOT DEMONSTRATED"
            ),
            "closed_loop_recovery": (
                "DEMONSTRATED"
                if actions > 0
                else "NOT DEMONSTRATED"
            ),
            "general_real_world_execution": "NOT DEMONSTRATED",
            "continuous_self_improvement": "NOT DEMONSTRATED",
            "durable_memory": "LIMITED_BY_STORAGE",
        },
        "definition_of_working": (
            "A mission is actually working when it completes "
            "discovery, ingestion, claim extraction, provenance "
            "graphing and verification, and when a verification "
            "gate fails it automatically performs a bounded "
            "evidence-recovery pass without weakening the gate."
        ),
    }


# ============================================================
# MISSION CONTROLLER
# ============================================================

def run_mission(mission_id):
    try:
        set_status(mission_id, "running")

        with db_lock:
            conn = db()
            mission = conn.execute(
                "SELECT objective FROM missions WHERE id=?",
                (mission_id,),
            ).fetchone()
            conn.close()

        if not mission:
            return

        objective = mission["objective"]

        event(
            mission_id,
            "mission",
            "running",
            "closed-loop mission started",
        )

        discovery_pass(
            mission_id,
            objective,
            1,
        )

        ingest_evidence(mission_id)
        build_claims(mission_id, objective)
        build_graph(mission_id)

        verification = verify_claims(mission_id)

        # Two bounded recovery rounds. The standards never get lowered.
        for round_number in range(1, 3):
            if verification.get("VERIFIED", 0) > 0:
                break

            event(
                mission_id,
                "recovery",
                "running",
                "verification gate unsatisfied; "
                f"recovery round {round_number}",
            )

            recovery_pass(
                mission_id,
                objective,
                round_number,
            )

            verification = verify_claims(
                mission_id
            )

            event(
                mission_id,
                "recovery",
                "completed",
                f"round {round_number} complete",
            )

        audit = build_audit(mission_id)

        with db_lock:
            conn = db()
            conn.execute(
                """
                INSERT OR REPLACE INTO audits
                (mission_id,payload,created)
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
            "Mission completed with evidence gate preserved",
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
            f"{type(exc).__name__}: {str(exc)[:400]}",
        )


def create_mission(objective: str):
    mission_id = uid("mission")

    with db_lock:
        conn = db()
        conn.execute(
            """
            INSERT INTO missions
            (id,objective,status,created,updated,result)
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

    pool.submit(
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
# DASHBOARD
# ============================================================

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
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
  font-family:system-ui,sans-serif;
  background:#0b0f14;
  color:#eef2f7;
  padding:16px;
  max-width:950px;
  margin:auto;
}}
textarea,button {{
  width:100%;
  box-sizing:border-box;
  padding:14px;
  margin:8px 0;
  border-radius:10px;
}}
textarea {{
  min-height:120px;
  background:#111820;
  color:white;
  border:1px solid #344;
}}
button {{font-weight:700}}
.card {{
  background:#111820;
  border:1px solid #344;
  border-radius:10px;
  padding:12px;
  margin:10px 0;
}}
pre {{white-space:pre-wrap;word-break:break-word}}
</style>
</head>
<body>
<h1>AI Infinity</h1>
<small>{VERSION} · {BUILD}</small>

<textarea id="objective">
Research the reliability of autonomous AI agents for real-world task execution.
Find independent evidence, verify important claims, identify contradictory evidence,
and give the next actions.
</textarea>

<button onclick="runMission()">RUN MISSION</button>

<div id="output" class="card">Ready.</div>

<script>
let missionId = null;

async function runMission() {{
  const response = await fetch("/run", {{
    method:"POST",
    headers:{{"Content-Type":"application/json"}},
    body:JSON.stringify({{objective:objective.value}})
  }});

  const data = await response.json();
  missionId = data.mission_id;
  output.innerHTML = "Mission: " + missionId;
  poll();
}}

async function poll() {{
  if (!missionId) return;

  const mission = await (
    await fetch("/mission/" + missionId)
  ).json();

  const [audit, claims, evidence, graph, actions, events] =
    await Promise.all([
      fetch("/mission/"+missionId+"/audit").then(x=>x.json()),
      fetch("/mission/"+missionId+"/claims").then(x=>x.json()),
      fetch("/mission/"+missionId+"/evidence").then(x=>x.json()),
      fetch("/mission/"+missionId+"/graph").then(x=>x.json()),
      fetch("/mission/"+missionId+"/actions").then(x=>x.json()),
      fetch("/mission/"+missionId+"/events").then(x=>x.json())
    ]);

  output.innerHTML =
    "<b>Status:</b> " + mission.status +
    "<pre>" + JSON.stringify(audit,null,2) + "</pre>" +
    "<b>Claims</b><pre>" +
    JSON.stringify(claims,null,2) +
    "</pre>" +
    "<b>Evidence</b><pre>" +
    JSON.stringify(evidence.slice(0,25),null,2) +
    "</pre>" +
    "<b>Graph</b><pre>" +
    JSON.stringify(graph.slice(0,60),null,2) +
    "</pre>" +
    "<b>Recovery / Actions</b><pre>" +
    JSON.stringify(actions,null,2) +
    "</pre>" +
    "<b>Events</b><pre>" +
    JSON.stringify(events,null,2) +
    "</pre>";

  if (
    mission.status === "queued" ||
    mission.status === "running"
  ) {{
    setTimeout(poll,2000);
  }}
}}
</script>
</body>
</html>
"""
    )


# ============================================================
# BASIC API
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": VERSION,
        "build": BUILD,
    }


@app.get("/status")
def status():
    with db_lock:
        conn = db()

        result = {}

        for table in [
            "missions",
            "works",
            "claims",
            "evidence",
            "edges",
            "actions",
        ]:
            result[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

        conn.close()

    return {
        "version": VERSION,
        "build": BUILD,
        **result,
    }


@app.get("/capabilities")
def capabilities():
    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "closed-loop research",
            "multi-provider discovery",
            "claim purity filtering",
            "claim-level provenance",
            "independent verification gate",
            "contradiction screening",
            "bounded recovery",
            "action ledger",
            "reality audit",
        ],
        "not_claimed": [
            "AGI/ASI",
            "arbitrary real-world execution",
            "self-generated internet",
            "unbounded self-improvement",
        ],
    }


@app.post("/run")
def run_post(request: MissionRequest):
    return create_mission(request.objective)


@app.post("/mission")
def mission_post(request: MissionRequest):
    return create_mission(request.objective)


@app.post("/research")
def research_post(request: MissionRequest):
    return create_mission(request.objective)


@app.post("/command")
def command_post(request: CommandRequest):
    return create_mission(request.command)


@app.get("/missions")
def missions():
    with db_lock:
        conn = db()
        rows = conn.execute(
            """
            SELECT *
            FROM missions
            ORDER BY created DESC
            LIMIT 50
            """
        ).fetchall()
        conn.close()

    return [dict(row) for row in rows]


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

    return [dict(row) for row in rows]


@app.get("/mission/{mission_id}/sources")
def mission_sources(mission_id: str):
    with db_lock:
        conn = db()
        rows = conn.execute(
            """
            SELECT
                id,title,url,doi,provider,
                domain,family,quality,relevance
            FROM works
            WHERE mission_id=?
            ORDER BY relevance DESC
            """,
            (mission_id,),
        ).fetchall()
        conn.close()

    return [dict(row) for row in rows]


@app.get("/mission/{mission_id}/claims")
def mission_claims(mission_id: str):
    with db_lock:
        conn = db()
        rows = conn.execute(
            """
            SELECT *
            FROM claims
            WHERE mission_id=?
            ORDER BY confidence DESC
            """,
            (mission_id,),
        ).fetchall()
        conn.close()

    return [dict(row) for row in rows]


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
                w.family
            FROM evidence e
            JOIN works w
              ON w.id=e.work_id
            WHERE e.mission_id=?
            ORDER BY e.score DESC
            """,
            (mission_id,),
        ).fetchall()
        conn.close()

    return [dict(row) for row in rows]


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

    return [dict(row) for row in rows]


@app.get("/mission/{mission_id}/actions")
def mission_actions(mission_id: str):
    with db_lock:
        conn = db()
        rows = conn.execute(
            """
            SELECT *
            FROM actions
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,),
        ).fetchall()
        conn.close()

    return [dict(row) for row in rows]


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


@app.get("/mission/{mission_id}/report")
def mission_report(mission_id: str):
    audit = mission_audit(mission_id)
    with db_lock:
        conn = db()
        top = conn.execute("SELECT * FROM claims WHERE mission_id=? ORDER BY CASE status WHEN 'VERIFIED' THEN 0 WHEN 'SUPPORTED' THEN 1 ELSE 2 END, confidence DESC LIMIT 20", (mission_id,)).fetchall()
        conn.close()
    return {
        "mission_id": mission_id,
        "audit": audit,
        "decision_rule": "No claim is VERIFIED unless it has strong entailment from at least two distinct works spanning at least two domains and two source families, with no detected contradiction.",
        "claims": [dict(x) for x in top],
    }


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


@app.on_event("shutdown")
def shutdown():
    pool.shutdown(
        wait=False,
        cancel_futures=False,
    )
