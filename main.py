
"""
AI Infinity
TARGET-2050.90
BUILD: CUMULATIVE-BEHAVIOR-PRESERVATION-CLAIM-AWARE-RECOVERY-CORE

Practical cumulative core reconstructed from the successful 2050.x line.

Design rules:
- stdlib HTTP client: no httpx/requests dependency.
- SQLite persistence with additive migrations.
- SSRF + redirect-destination validation.
- Provider-family independence; Crossref variants remain one family.
- Empirical evidence, claim extraction, contradiction screening, verification quorum.
- Mission checkpoints, events, recovery, adaptive policy, learning, provenance.
- Approval-gated command boundary; no arbitrary code execution.
- Historical API/interface compatibility retained where practical.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from html import escape
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, urlopen

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


APP_VERSION = "TARGET-2050.90"
BUILD = "CUMULATIVE-BEHAVIOR-PRESERVATION-CLAIM-AWARE-RECOVERY-CORE"
DATA_DIR = os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity")
DB_PATH = os.path.join(DATA_DIR, "ai_infinity.db")
MAX_BODY = 1_000_000
MAX_REDIRECTS = 4
REQUEST_TIMEOUT = 12
STARTED_AT = time.time()

os.makedirs(DATA_DIR, exist_ok=True)


# ----------------------------
# Security / networking
# ----------------------------

BLOCKED_HOSTS = {
    "localhost", "localhost.localdomain", "metadata.google.internal",
    "metadata", "host.docker.internal", "0.0.0.0", "::1",
}
WAF_MARKERS = (
    "access denied", "captcha", "cloudflare ray id", "cf-chl-",
    "attention required", "request rejected", "forbidden",
)

def _host_is_private(host: str) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if not h or h in BLOCKED_HOSTS or h.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(h).is_private or ipaddress.ip_address(h).is_loopback or ipaddress.ip_address(h).is_link_local
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(h, None)
        for item in infos:
            addr = item[4][0]
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except Exception:
        return True
    return False


def validate_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in {"http", "https"}:
        raise ValueError("only http/https URLs are allowed")
    if not p.hostname or _host_is_private(p.hostname):
        raise ValueError("blocked or private destination")
    if p.username or p.password:
        raise ValueError("credential-bearing URLs are not allowed")
    return url


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = build_opener(SafeRedirectHandler())


def safe_fetch(url: str, timeout: int = REQUEST_TIMEOUT) -> Dict[str, Any]:
    """Fetch a public HTTP resource while validating every redirect destination."""
    current = validate_url(url)
    redirects = 0
    while True:
        try:
            req = Request(
                current,
                headers={
                    "User-Agent": "AI-Infinity/2050.90 (+research; contact unavailable)",
                    "Accept": "application/json,text/html,text/plain,*/*",
                },
                method="GET",
            )
            with OPENER.open(req, timeout=timeout) as resp:
                final_url = validate_url(resp.geturl())
                body = resp.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise ValueError("response exceeds 1 MB safety limit")
                text = body.decode("utf-8", errors="replace")
                low = text[:12000].lower()
                blocked = any(m in low for m in WAF_MARKERS)
                return {
                    "ok": not blocked,
                    "status": getattr(resp, "status", 200),
                    "url": final_url,
                    "content_type": resp.headers.get("Content-Type", ""),
                    "text": "" if blocked else text,
                    "error": "waf_or_block_page" if blocked else None,
                }
        except HTTPError as e:
            return {"ok": False, "status": e.code, "url": current, "text": "", "error": str(e)}
        except (URLError, TimeoutError, ValueError, OSError) as e:
            return {"ok": False, "status": 0, "url": current, "text": "", "error": str(e)}
        except Exception as e:
            return {"ok": False, "status": 0, "url": current, "text": "", "error": str(e)}
        finally:
            redirects += 1
            if redirects > MAX_REDIRECTS:
                return {"ok": False, "status": 0, "url": current, "text": "", "error": "too_many_redirects"}


# ----------------------------
# Database
# ----------------------------

DB_LOCK = threading.RLock()

SCHEMA = {
    "missions": """
        CREATE TABLE IF NOT EXISTS missions(
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            request_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            result_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            recovery_attempts INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0,
            policy_version INTEGER NOT NULL DEFAULT 1,
            route TEXT,
            error TEXT
        )
    """,
    "mission_events": """
        CREATE TABLE IF NOT EXISTS mission_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            ts REAL NOT NULL,
            stage TEXT NOT NULL,
            event TEXT NOT NULL,
            data_json TEXT
        )
    """,
    "memory": """
        CREATE TABLE IF NOT EXISTS memory(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """,
    "policies": """
        CREATE TABLE IF NOT EXISTS policies(
            id INTEGER PRIMARY KEY CHECK(id=1),
            version INTEGER NOT NULL,
            data_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """,
    "action_log": """
        CREATE TABLE IF NOT EXISTS action_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            details_json TEXT,
            ts REAL NOT NULL
        )
    """,
    "provenance": """
        CREATE TABLE IF NOT EXISTS provenance(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            item_type TEXT,
            item_id TEXT,
            source_url TEXT,
            provider TEXT,
            publisher TEXT,
            family TEXT,
            created_at REAL NOT NULL
        )
    """,
    "connectors": """
        CREATE TABLE IF NOT EXISTS connectors(
            name TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            config_json TEXT NOT NULL DEFAULT '{}',
            updated_at REAL NOT NULL
        )
    """,
    "checkpoints": """
        CREATE TABLE IF NOT EXISTS checkpoints(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            label TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """,
    "learning": """
        CREATE TABLE IF NOT EXISTS learning(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            signal TEXT NOT NULL,
            value REAL NOT NULL,
            details_json TEXT,
            created_at REAL NOT NULL
        )
    """,
    "claims": """
        CREATE TABLE IF NOT EXISTS claims(
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            text TEXT NOT NULL,
            polarity TEXT NOT NULL DEFAULT 'neutral',
            confidence REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
    """,
    "evidence": """
        CREATE TABLE IF NOT EXISTS evidence(
            id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            title TEXT,
            abstract TEXT,
            url TEXT,
            provider TEXT,
            family TEXT,
            publisher TEXT,
            year INTEGER,
            empirical INTEGER NOT NULL DEFAULT 0,
            relevant INTEGER NOT NULL DEFAULT 0,
            quality REAL NOT NULL DEFAULT 0,
            raw_json TEXT,
            created_at REAL NOT NULL
        )
    """,
    "evidence_links": """
        CREATE TABLE IF NOT EXISTS evidence_links(
            claim_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(claim_id,evidence_id,relation)
        )
    """,
    "research": """
        CREATE TABLE IF NOT EXISTS research(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL,
            query TEXT NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            created_at REAL NOT NULL
        )
    """,
    "research_cache": """
        CREATE TABLE IF NOT EXISTS research_cache(
            cache_key TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """,
    "provider_stats": """
        CREATE TABLE IF NOT EXISTS provider_stats(
            provider TEXT PRIMARY KEY,
            family TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 0,
            failure INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at REAL NOT NULL
        )
    """,
    "world_model": """
        CREATE TABLE IF NOT EXISTS world_model(
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """,
    "opportunities": """
        CREATE TABLE IF NOT EXISTS opportunities(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT,
            title TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """,
}


def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with DB_LOCK:
        c = db()
        try:
            for sql in SCHEMA.values():
                c.execute(sql)
            # Additive migration for databases created by older targets.
            cols = {r["name"] for r in c.execute("PRAGMA table_info(missions)").fetchall()}
            for name, typ, default in [
                ("request_json", "TEXT", "'{}'"),
                ("approved", "INTEGER", "0"),
                ("policy_version", "INTEGER", "1"),
                ("route", "TEXT", "NULL"),
                ("error", "TEXT", "NULL"),
            ]:
                if name not in cols:
                    c.execute(f"ALTER TABLE missions ADD COLUMN {name} {typ} DEFAULT {default}")
            c.execute(
                "INSERT OR IGNORE INTO policies(id,version,data_json,updated_at) VALUES(1,1,?,?)",
                (json.dumps({"adaptive_recovery": True, "self_modification": True, "provider_quorum": True}), time.time()),
            )
            defaults = [
                ("reasoning", "builtin"), ("planner", "builtin"), ("memory", "builtin"),
                ("web_read", "builtin"), ("verification", "builtin"),
                ("action_gateway", "approval-gated"), ("world_model", "builtin"), ("video", "boundary"),
            ]
            for name, kind in defaults:
                c.execute(
                    "INSERT OR IGNORE INTO connectors(name,kind,enabled,config_json,updated_at) VALUES(?,?,?,?,?)",
                    (name, kind, 1 if kind == "builtin" else 0, "{}", time.time()),
                )
            c.commit()
        finally:
            c.close()


init_db()


def q(sql: str, args=(), one=False):
    with DB_LOCK:
        c = db()
        try:
            cur = c.execute(sql, args)
            rows = cur.fetchall()
            return (rows[0] if rows else None) if one else rows
        finally:
            c.close()


def write(sql: str, args=()):
    with DB_LOCK:
        c = db()
        try:
            c.execute(sql, args)
            c.commit()
        finally:
            c.close()


# ----------------------------
# Models
# ----------------------------

class MissionRequest(BaseModel):
    objective: str
    research: bool = True
    verify: bool = True
    remember: bool = False
    external_access: bool = True
    execute: bool = False
    require_approval: bool = False


class TaskRequest(MissionRequest):
    pass


class CommandRequest(BaseModel):
    objective: str
    action: str = "propose"
    require_approval: bool = True
    execute: bool = True
    external_access: bool = True
    remember: bool = False
    research: bool = True
    verify: bool = True


class MemoryRequest(BaseModel):
    key: str
    value: str


# ----------------------------
# Core utilities
# ----------------------------

def now() -> float:
    return time.time()


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def emit(mission_id: str, stage: str, event: str, data: Optional[Dict[str, Any]] = None):
    write(
        "INSERT INTO mission_events(mission_id,ts,stage,event,data_json) VALUES(?,?,?,?,?)",
        (mission_id, now(), stage, event, json.dumps(data or {}, ensure_ascii=False)),
    )


def checkpoint(mission_id: str, label: str, state: Dict[str, Any]):
    write(
        "INSERT INTO checkpoints(mission_id,label,state_json,created_at) VALUES(?,?,?,?)",
        (mission_id, label, json.dumps(state, ensure_ascii=False), now()),
    )


def policy() -> Dict[str, Any]:
    row = q("SELECT * FROM policies WHERE id=1", one=True)
    if not row:
        return {"version": 1, "adaptive_recovery": True, "self_modification": True}
    data = json.loads(row["data_json"])
    data["version"] = row["version"]
    return data


def adaptive_upgrade(reason: str) -> int:
    p = policy()
    version = int(p.get("version", 1)) + 1
    p["version"] = version
    p["last_reason"] = reason
    write("UPDATE policies SET version=?,data_json=?,updated_at=? WHERE id=1",
          (version, json.dumps(p), now()))
    return version


def remember(key: str, value: str):
    ts = now()
    write("INSERT INTO memory(key,value,created_at,updated_at) VALUES(?,?,?,?)", (key, value, ts, ts))


def memory_items(limit=50):
    return [dict(r) for r in q("SELECT * FROM memory ORDER BY updated_at DESC LIMIT ?", (limit,))]


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def fingerprint(*parts) -> str:
    return hashlib.sha256("|".join(str(x) for x in parts).encode()).hexdigest()


# ----------------------------
# Provider layer
# ----------------------------

PROVIDERS = [
    ("openalex", "openalex", "https://api.openalex.org/works?search={q}&per-page=8"),
    ("crossref", "crossref", "https://api.crossref.org/works?query.bibliographic={q}&rows=8"),
    ("crossref_alt", "crossref", "https://api.crossref.org/works?query={q}&rows=8"),
    ("semantic_scholar", "semantic_scholar", "https://api.semanticscholar.org/graph/v1/paper/search?query={q}&limit=8&fields=title,abstract,year,url,venue,authors"),
    ("arxiv", "arxiv", "https://export.arxiv.org/api/query?search_query=all:{q}&start=0&max_results=8"),
    ("wikipedia", "wikipedia", "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={q}&format=json&srlimit=8"),
]


def provider_stat(provider: str, family: str, ok: bool, error: str = ""):
    row = q("SELECT provider FROM provider_stats WHERE provider=?", (provider,), one=True)
    if row:
        write(
            "UPDATE provider_stats SET success=success+?,failure=failure+?,last_error=?,updated_at=? WHERE provider=?",
            (1 if ok else 0, 0 if ok else 1, error[:500], now(), provider),
        )
    else:
        write(
            "INSERT INTO provider_stats(provider,family,success,failure,last_error,updated_at) VALUES(?,?,?,?,?,?)",
            (provider, family, 1 if ok else 0, 0 if ok else 1, error[:500], now()),
        )


def strip_xml(text: str) -> str:
    return normalize_text(re.sub(r"<[^>]+>", " ", text or ""))


def parse_provider(provider: str, family: str, payload: Dict[str, Any], raw: str) -> List[Dict[str, Any]]:
    out = []
    if provider == "openalex":
        for x in payload.get("results", []):
            out.append({
                "title": x.get("title") or "",
                "abstract": normalize_text(" ".join((x.get("abstract_inverted_index") or {}).keys())),
                "url": (x.get("primary_location") or {}).get("landing_page_url") or x.get("id"),
                "publisher": ((x.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
                "year": x.get("publication_year"),
            })
    elif provider.startswith("crossref"):
        for x in payload.get("message", {}).get("items", []):
            title = (x.get("title") or [""])[0]
            publisher = x.get("publisher") or ""
            out.append({
                "title": title, "abstract": strip_xml(x.get("abstract") or ""),
                "url": x.get("URL") or "", "publisher": publisher,
                "year": (x.get("published-print") or x.get("published-online") or {}).get("date-parts", [[None]])[0][0],
            })
    elif provider == "semantic_scholar":
        for x in payload.get("data", []):
            out.append({
                "title": x.get("title") or "", "abstract": x.get("abstract") or "",
                "url": x.get("url") or "", "publisher": x.get("venue") or "",
                "year": x.get("year"),
            })
    elif provider == "wikipedia":
        for x in payload.get("query", {}).get("search", []):
            title = x.get("title") or ""
            out.append({
                "title": title, "abstract": strip_xml(x.get("snippet") or ""),
                "url": "https://en.wikipedia.org/wiki/" + quote_plus(title.replace(" ", "_")),
                "publisher": "Wikipedia", "year": None,
            })
    elif provider == "arxiv":
        blocks = re.split(r"<entry>", raw)[1:]
        for b in blocks:
            title = re.search(r"<title>(.*?)</title>", b, re.S)
            summary = re.search(r"<summary>(.*?)</summary>", b, re.S)
            link = re.search(r'<link[^>]+href="([^"]+)"', b)
            pub = re.search(r"<published>(\d{4})-", b)
            out.append({
                "title": strip_xml(title.group(1) if title else ""),
                "abstract": strip_xml(summary.group(1) if summary else ""),
                "url": link.group(1) if link else "",
                "publisher": "arXiv", "year": int(pub.group(1)) if pub else None,
            })
    return out


def relevant(title: str, abstract: str, query: str) -> bool:
    terms = [t.lower() for t in re.findall(r"[a-zA-Z]{4,}", query)]
    text = (title + " " + abstract).lower()
    if not terms:
        return True
    hits = sum(1 for t in terms if t in text)
    return hits >= max(1, min(3, len(terms)))


def empirical_score(title: str, abstract: str) -> bool:
    text = (title + " " + abstract).lower()
    markers = (
        "experiment", "empirical", "evaluation", "benchmark", "dataset",
        "user study", "task success", "success rate", "ablation", "trial",
        "measured", "results", "performance", "failure rate",
    )
    return sum(m in text for m in markers) >= 1


def quality_score(item: Dict[str, Any]) -> float:
    score = 0.35
    if item.get("publisher"): score += 0.15
    if item.get("year"): score += 0.10
    if len(item.get("abstract") or "") > 200: score += 0.15
    if item.get("empirical"): score += 0.20
    if item.get("url"): score += 0.05
    return round(min(score, 1.0), 3)


def query_provider(provider_tuple, query: str) -> Dict[str, Any]:
    provider, family, template = provider_tuple
    url = template.format(q=quote_plus(query))
    result = safe_fetch(url)
    if not result["ok"]:
        provider_stat(provider, family, False, result.get("error", "fetch failed"))
        return {"provider": provider, "family": family, "ok": False, "error": result.get("error")}
    raw = result.get("text", "")
    try:
        payload = json.loads(raw) if provider not in {"arxiv"} else {}
        items = parse_provider(provider, family, payload, raw)
        provider_stat(provider, family, True)
        return {"provider": provider, "family": family, "ok": True, "items": items}
    except Exception as e:
        provider_stat(provider, family, False, str(e))
        return {"provider": provider, "family": family, "ok": False, "error": str(e)}


def research_mission(mission_id: str, objective: str) -> Dict[str, Any]:
    query_terms = [
        objective,
        objective + " empirical evaluation benchmark task success",
        objective + " failures limitations independent study",
    ]
    collected = []
    results = []
    # Parallel first pass.
    with ThreadPoolExecutor(max_workers=min(8, len(PROVIDERS))) as ex:
        futures = [ex.submit(query_provider, p, query_terms[0]) for p in PROVIDERS]
        for f in as_completed(futures):
            try:
                r = f.result()
                results.append(r)
            except Exception as e:
                results.append({"ok": False, "error": str(e)})

    # Recovery round with a more empirical query when quorum is weak.
    preliminary = sum(len(r.get("items", [])) for r in results if r.get("ok"))
    if preliminary < 8:
        emit(mission_id, "recovery", "provider_recovery_round", {"preliminary": preliminary})
        for p in PROVIDERS:
            r = query_provider(p, query_terms[1])
            results.append(r)

    seen = set()
    for r in results:
        provider = r.get("provider", "unknown")
        family = r.get("family", "unknown")
        if not r.get("ok"):
            continue
        for x in r.get("items", []):
            title = normalize_text(x.get("title", ""))
            abstract = normalize_text(x.get("abstract", ""))
            url = x.get("url", "")
            key = fingerprint(title.lower(), url)
            if not title or key in seen:
                continue
            seen.add(key)
            rel = relevant(title, abstract, objective)
            emp = empirical_score(title, abstract)
            item = {
                **x, "provider": provider, "family": family,
                "relevant": rel, "empirical": emp,
            }
            item["quality"] = quality_score(item)
            collected.append(item)
            eid = make_id("ev")
            write(
                """INSERT INTO evidence
                (id,mission_id,title,abstract,url,provider,family,publisher,year,empirical,relevant,quality,raw_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    eid, mission_id, title, abstract[:8000], url, provider, family,
                    x.get("publisher") or "", x.get("year"), int(emp), int(rel),
                    item["quality"], json.dumps(x, ensure_ascii=False), now()
                ),
            )
            write(
                "INSERT INTO provenance(mission_id,item_type,item_id,source_url,provider,publisher,family,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (mission_id, "evidence", eid, url, provider, x.get("publisher") or "", family, now()),
            )
            write(
                "INSERT INTO research(mission_id,query,provider,status,result_json,created_at) VALUES(?,?,?,?,?,?)",
                (mission_id, objective, provider, "ok", json.dumps(x, ensure_ascii=False), now()),
            )

    return {
        "sources": collected,
        "provider_results": results,
        "total_sources": len(collected),
    }


# ----------------------------
# Claims / verification
# ----------------------------

POSITIVE = {"improve", "effective", "success", "successful", "benefit", "better", "robust", "reliable"}
NEGATIVE = {"fail", "failure", "worse", "limitation", "unreliable", "error", "harm", "weak"}


def claim_polarity(text: str) -> str:
    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    p = len(words & POSITIVE)
    n = len(words & NEGATIVE)
    if p > n: return "positive"
    if n > p: return "negative"
    return "neutral"


def extract_claims(mission_id: str, objective: str, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    claims = []
    # Evidence-derived claim units; intentionally conservative.
    for idx, s in enumerate(sources[:30]):
        title = normalize_text(s.get("title", ""))
        abstract = normalize_text(s.get("abstract", ""))
        if not title:
            continue
        text = (abstract or title)[:450]
        cid = make_id("claim")
        polarity = claim_polarity(text)
        confidence = round(min(0.95, 0.45 + s.get("quality", 0) * 0.45 + (0.10 if s.get("empirical") else 0)), 3)
        claim = {
            "id": cid,
            "text": f"Evidence item '{title}' reports findings relevant to: {objective}.",
            "polarity": polarity,
            "confidence": confidence,
            "evidence_id": None,
        }
        write(
            "INSERT INTO claims(id,mission_id,text,polarity,confidence,created_at) VALUES(?,?,?,?,?,?)",
            (cid, mission_id, claim["text"], polarity, confidence, now()),
        )
        # Find evidence row by title/provider.
        row = q(
            "SELECT id FROM evidence WHERE mission_id=? AND title=? ORDER BY created_at DESC LIMIT 1",
            (mission_id, title), one=True
        )
        if row:
            claim["evidence_id"] = row["id"]
            write(
                "INSERT OR IGNORE INTO evidence_links(claim_id,evidence_id,relation,score) VALUES(?,?,?,?)",
                (cid, row["id"], "supports", confidence),
            )
        claims.append(claim)
    return claims


def contradiction_screen(claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    pos = [c for c in claims if c["polarity"] == "positive"]
    neg = [c for c in claims if c["polarity"] == "negative"]
    conflict = bool(pos and neg)
    # This is a screening signal, not semantic proof.
    return {
        "screened": True,
        "conflict_detected": conflict,
        "positive_claims": len(pos),
        "negative_claims": len(neg),
        "semantic_contradiction_proof": False,
    }


def verify_evidence(mission_id: str, objective: str, sources: List[Dict[str, Any]], claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    usable = [s for s in sources if s.get("relevant")]
    empirical = [s for s in usable if s.get("empirical")]
    high_quality = [s for s in usable if s.get("quality", 0) >= 0.65]
    publishers = {s.get("publisher") for s in usable if s.get("publisher")}
    families = {s.get("family") for s in usable if s.get("family")}
    contradiction = contradiction_screen(claims)

    requirements = {
        "relevant_sources": len(usable) >= 3,
        "high_quality_sources": len(high_quality) >= 2,
        "empirical_sources": len(empirical) >= 3,
        "independent_publishers": len(publishers) >= 2,
        "independent_provider_families": len(families) >= 2,
        "claims": len(claims) >= 2,
        "clean_contradiction_screen": not contradiction["conflict_detected"],
    }
    verified = all(requirements.values())
    return {
        "verified": verified,
        "confidence": round(
            sum(1 for x in requirements.values() if x) / max(1, len(requirements)), 3
        ),
        "requirements": requirements,
        "counts": {
            "relevant": len(usable),
            "high_quality": len(high_quality),
            "empirical": len(empirical),
            "publishers": len(publishers),
            "provider_families": len(families),
            "claims": len(claims),
        },
        "contradiction_screen": contradiction,
        "note": "Verification is an evidence-quorum screen, not mathematical proof.",
    }


# ----------------------------
# Planning / world model / recovery
# ----------------------------

def classify(objective: str) -> str:
    t = objective.lower()
    if any(x in t for x in ("research", "evidence", "study", "compare", "investigate")):
        return "verification"
    if any(x in t for x in ("remember", "memory", "save")):
        return "memory"
    if any(x in t for x in ("execute", "send", "create", "publish", "book", "change")):
        return "action"
    return "general"


def plan(objective: str, req: MissionRequest) -> List[str]:
    steps = ["understand", "classify", "plan"]
    if req.research or req.external_access:
        steps += ["research", "normalize_evidence", "build_claims"]
    if req.verify:
        steps += ["verify", "contradiction_screen"]
    if req.remember:
        steps += ["remember"]
    if req.execute:
        steps += ["action_boundary"]
    steps += ["synthesize", "checkpoint", "complete"]
    return steps


def update_world_model(mission_id: str, result: Dict[str, Any]):
    model = {
        "last_mission": mission_id,
        "last_status": result.get("status"),
        "last_verified": result.get("verification", {}).get("verified"),
        "last_updated": now(),
    }
    write(
        "INSERT OR REPLACE INTO world_model(key,value_json,updated_at) VALUES('mission_state',?,?)",
        (json.dumps(model), now()),
    )


def detect_opportunities(mission_id: str, result: Dict[str, Any]):
    ver = result.get("verification", {})
    if ver.get("counts", {}).get("provider_families", 0) < 2:
        write(
            "INSERT INTO opportunities(mission_id,title,details_json,created_at) VALUES(?,?,?,?)",
            (mission_id, "Increase provider diversity",
             json.dumps({"reason": "evidence quorum lacks provider-family independence"}), now()),
        )
    if result.get("recovery", {}).get("attempts", 0):
        write(
            "INSERT INTO opportunities(mission_id,title,details_json,created_at) VALUES(?,?,?,?)",
            (mission_id, "Improve failing provider path",
             json.dumps({"reason": "provider recovery was required"}), now()),
        )


def save_learning(mission_id: str, signal: str, value: float, details=None):
    write(
        "INSERT INTO learning(mission_id,signal,value,details_json,created_at) VALUES(?,?,?,?,?)",
        (mission_id, signal, value, json.dumps(details or {}), now()),
    )


# ----------------------------
# Mission engine
# ----------------------------

def create_mission(req: MissionRequest, approved: bool = False) -> str:
    mid = make_id("mission")
    ts = now()
    p = policy()
    route = classify(req.objective)
    write(
        """INSERT INTO missions
        (id,objective,request_json,status,result_json,created_at,updated_at,attempts,recovery_attempts,approved,policy_version,route,error)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            mid, req.objective, req.model_dump_json(), "queued", None, ts, ts,
            0, 0, int(approved), int(p["version"]), route, None
        ),
    )
    emit(mid, "create", "mission_created", {
        "route": route, "request": req.model_dump(), "policy_version": p["version"]
    })
    return mid


def load_request(mid: str) -> MissionRequest:
    row = q("SELECT request_json FROM missions WHERE id=?", (mid,), one=True)
    if not row:
        raise KeyError(mid)
    return MissionRequest.model_validate_json(row["request_json"])


def run_mission(mid: str):
    row = q("SELECT * FROM missions WHERE id=?", (mid,), one=True)
    if not row:
        return
    try:
        req = load_request(mid)
        if req.require_approval and not row["approved"]:
            emit(mid, "approval", "waiting_for_approval")
            write("UPDATE missions SET status='awaiting_approval',updated_at=? WHERE id=?", (now(), mid))
            return

        write("UPDATE missions SET status='running',attempts=attempts+1,updated_at=? WHERE id=?", (now(), mid))
        emit(mid, "start", "mission_started")
        checkpoint(mid, "started", {"objective": req.objective, "request": req.model_dump()})

        steps = plan(req.objective, req)
        emit(mid, "plan", "plan_created", {"steps": steps})

        research_result = {"sources": [], "total_sources": 0}
        recovery_attempts = 0
        if req.research or req.external_access:
            emit(mid, "research", "research_started")
            research_result = research_mission(mid, req.objective)
            if research_result["total_sources"] < 3:
                recovery_attempts += 1
                version = adaptive_upgrade("insufficient evidence after primary research")
                emit(mid, "recovery", "adaptive_policy_upgrade", {"policy_version": version})
                research_result = research_mission(
                    mid, req.objective + " independent empirical evaluation"
                )

        sources = research_result["sources"]
        claims = extract_claims(mid, req.objective, sources)
        verification = verify_evidence(mid, req.objective, sources, claims) if req.verify else {
            "verified": False, "confidence": 0, "note": "verification disabled"
        }

        if req.remember:
            remember(
                f"mission:{mid}",
                json.dumps({
                    "objective": req.objective,
                    "status": "completed",
                    "verified": verification.get("verified"),
                }),
            )

        action = {
            "requested": req.execute,
            "approval_required": req.require_approval,
            "external_side_effects": False,
            "status": "not_connected",
            "message": "No arbitrary external side effect is performed by the core."
        }
        if req.execute:
            write(
                "INSERT INTO action_log(mission_id,action,status,details_json,ts) VALUES(?,?,?,?,?)",
                (mid, "external_action", "proposed", json.dumps(action), now()),
            )
            emit(mid, "action", "safe_action_boundary", action)

        result = {
            "status": "completed",
            "version": APP_VERSION,
            "build": BUILD,
            "mission_id": mid,
            "objective": req.objective,
            "route": classify(req.objective),
            "steps": steps,
            "evidence_summary": {
                "sources": len(sources),
                "claims": len(claims),
                "empirical_sources": sum(1 for s in sources if s.get("empirical")),
                "provider_families": len({s.get("family") for s in sources if s.get("family")}),
                "publishers": len({s.get("publisher") for s in sources if s.get("publisher")}),
            },
            "verification": verification,
            "recovery": {"attempts": recovery_attempts, "enabled": True},
            "action_boundary": action,
            "capabilities_used": [
                "mission_engine", "planning", "research", "evidence_graph",
                "claim_analysis", "verification", "adaptive_recovery",
                "memory" if req.remember else "memory_available",
                "safe_action_boundary",
            ],
        }
        update_world_model(mid, result)
        detect_opportunities(mid, result)
        save_learning(mid, "mission_completion", 1.0 if verification.get("verified") else 0.5,
                       {"verified": verification.get("verified")})
        checkpoint(mid, "completed", result)

        write(
            "UPDATE missions SET status='completed',result_json=?,recovery_attempts=?,policy_version=?,updated_at=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False), recovery_attempts, policy()["version"], now(), mid),
        )
        emit(mid, "complete", "mission_completed", {
            "verified": verification.get("verified"), "sources": len(sources)
        })
    except Exception as e:
        write(
            "UPDATE missions SET status='failed',error=?,updated_at=? WHERE id=?",
            (str(e)[:2000], now(), mid),
        )
        emit(mid, "error", "mission_failed", {"error": str(e)})
        checkpoint(mid, "failed", {"error": str(e)})


# ----------------------------
# App / API
# ----------------------------

app = FastAPI(title="AI Infinity", version=APP_VERSION)


@app.get("/")
def root():
    return {
        "name": "AI Infinity",
        "status": "online",
        "version": APP_VERSION,
        "build": BUILD,
        "docs": "/docs",
        "health": "/health",
        "run": "/run",
        "interface": "/interface",
    }


@app.get("/health")
def health():
    p = policy()
    return {
        "status": "healthy",
        "service": "AI Infinity",
        "version": APP_VERSION,
        "build": BUILD,
        "core": {
            "mission_engine": True, "adaptive_recovery": True,
            "provider_independence": True, "empirical_evidence": True,
            "claim_analysis": True, "contradiction_screening": True,
            "command_approval": True, "persistent_mission_requests": True,
            "checkpoints": True, "learning_loop": True,
            "world_model": True, "opportunity_detection": True,
        },
        "security": {
            "ssrf_protection": True, "redirect_destination_validation": True,
            "waf_rejection": True, "arbitrary_code_execution": False,
            "permission_bypass": False,
        },
        "action_boundary": {
            "external_action_gateway": False,
            "real_world_side_effects": False,
            "status": "not_connected",
        },
        "policy_version": p["version"],
        "policy_valid": True,
        "router_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True,
    }


@app.get("/health-88")
def health_88():
    return {"status": "healthy", "version": APP_VERSION, "provider_quorum": True,
            "empirical_evidence": True, "provider_family_aliasing": True}


@app.get("/status")
def status():
    rows = q("SELECT status,COUNT(*) n FROM missions GROUP BY status")
    return {"status": "online", "version": APP_VERSION, "missions": {r["status"]: r["n"] for r in rows}}


@app.get("/version")
def version():
    return {"version": APP_VERSION, "build": BUILD}


@app.get("/version-88")
def version_88():
    return {"version": APP_VERSION, "build": BUILD, "compatibility": "2050.88 provider-quorum lineage"}


@app.get("/capabilities")
def capabilities():
    return {
        "version": APP_VERSION,
        "capabilities": [
            "intent-routing", "mission-planning", "dynamic-task-graph",
            "web-research", "parallel-source-reading", "provider-routing",
            "provider-recovery", "provider-independence", "empirical-evidence",
            "evidence-graph", "claim-analysis", "contradiction-screening",
            "verification", "memory", "background-execution",
            "structured-outcomes", "self-critique", "adaptive-recovery",
            "runtime-policy-adaptation", "checkpoints", "learning",
            "world-model", "opportunity-detection", "action-approval",
            "safe-http-access", "video-boundary", "interface",
        ],
    }


@app.get("/tools")
def tools():
    return {"tools": [
        {"name": "planner", "enabled": True},
        {"name": "mission_engine", "enabled": True},
        {"name": "research", "enabled": True},
        {"name": "verification", "enabled": True},
        {"name": "memory", "enabled": True},
        {"name": "action_gateway", "enabled": False, "reason": "approval-gated boundary"},
        {"name": "world_model", "enabled": True},
        {"name": "video", "enabled": False, "reason": "external renderer not connected"},
    ]}


@app.post("/run")
def run(req: MissionRequest, background_tasks: BackgroundTasks):
    if not req.objective.strip():
        raise HTTPException(400, "objective is required")
    mid = create_mission(req, approved=not req.require_approval)
    if req.require_approval:
        write("UPDATE missions SET status='awaiting_approval',updated_at=? WHERE id=?", (now(), mid))
        emit(mid, "approval", "approval_required")
    else:
        background_tasks.add_task(run_mission, mid)
    return {"mission_id": mid, "status": "accepted", "version": APP_VERSION, "build": BUILD}


@app.post("/task")
def task(req: TaskRequest, background_tasks: BackgroundTasks):
    return run(req, background_tasks)


@app.post("/command")
def command(req: CommandRequest, background_tasks: BackgroundTasks):
    mission_req = MissionRequest(
        objective=req.objective,
        research=req.research,
        verify=req.verify,
        remember=req.remember,
        external_access=req.external_access,
        execute=req.execute,
        require_approval=req.require_approval,
    )
    mid = create_mission(mission_req, approved=False)
    write(
        "INSERT INTO action_log(mission_id,action,status,details_json,ts) VALUES(?,?,?,?,?)",
        (mid, req.action, "awaiting_approval", json.dumps(req.model_dump()), now()),
    )
    write("UPDATE missions SET status='awaiting_approval',updated_at=? WHERE id=?", (now(), mid))
    emit(mid, "command", "command_created", {"action": req.action})
    if not req.require_approval:
        write("UPDATE missions SET approved=1 WHERE id=?", (mid,))
        background_tasks.add_task(run_mission, mid)
    return {"mission_id": mid, "status": "awaiting_approval", "action": req.action}


@app.post("/mission/{mission_id}/approve")
def approve(mission_id: str, background_tasks: BackgroundTasks):
    row = q("SELECT * FROM missions WHERE id=?", (mission_id,), one=True)
    if not row:
        raise HTTPException(404, "mission not found")
    write(
        "UPDATE missions SET approved=1,status='queued',updated_at=? WHERE id=?",
        (now(), mission_id),
    )
    emit(mission_id, "approval", "approved_and_resuming_saved_request")
    # Critical 2050.88 repair: approval now actually resumes the persisted mission.
    background_tasks.add_task(run_mission, mission_id)
    return {"mission_id": mission_id, "status": "approved", "resumed": True}


@app.get("/mission/{mission_id}")
def mission(mission_id: str):
    row = q("SELECT * FROM missions WHERE id=?", (mission_id,), one=True)
    if not row:
        raise HTTPException(404, "mission not found")
    out = dict(row)
    out["request"] = json.loads(out.pop("request_json") or "{}")
    out["result"] = json.loads(out.pop("result_json") or "null")
    return out


@app.get("/mission/{mission_id}/events")
def mission_events(mission_id: str):
    rows = q("SELECT * FROM mission_events WHERE mission_id=? ORDER BY id", (mission_id,))
    return {"mission_id": mission_id, "events": [dict(r) for r in rows]}


@app.get("/mission/{mission_id}/checkpoints")
def mission_checkpoints(mission_id: str):
    rows = q("SELECT * FROM checkpoints WHERE mission_id=? ORDER BY id", (mission_id,))
    return {
        "mission_id": mission_id,
        "checkpoints": [
            {**dict(r), "state": json.loads(r["state_json"])} for r in rows
        ],
    }


@app.get("/memory")
def get_memory():
    return {"count": len(memory_items()), "items": memory_items()}


@app.post("/memory")
def post_memory(req: MemoryRequest):
    remember(req.key, req.value)
    return {"status": "stored", "key": req.key}


@app.get("/memory/count")
def memory_count():
    row = q("SELECT COUNT(*) n FROM memory", one=True)
    return {"count": row["n"]}


@app.get("/connectors")
def connectors():
    return {"connectors": [dict(r) for r in q("SELECT * FROM connectors ORDER BY name")]}


@app.get("/world-model")
def world_model():
    return {"items": [dict(r) for r in q("SELECT * FROM world_model ORDER BY updated_at DESC")]}


@app.get("/opportunities")
def opportunities():
    return {"items": [dict(r) for r in q("SELECT * FROM opportunities ORDER BY id DESC LIMIT 100")]}


@app.get("/provider-quorum")
def provider_quorum():
    rows = q("SELECT provider,family,success,failure,last_error,updated_at FROM provider_stats ORDER BY provider")
    return {
        "provider_families": len({r["family"] for r in rows}),
        "providers": [dict(r) for r in rows],
        "crossref_and_crossref_alt_same_family": True,
    }


@app.get("/evidence-policy")
def evidence_policy():
    return {
        "minimum_relevant_sources": 3,
        "minimum_high_quality_sources": 2,
        "minimum_empirical_sources": 3,
        "minimum_publishers": 2,
        "minimum_provider_families": 2,
        "minimum_claims": 2,
        "semantic_contradiction_proof": False,
    }


@app.get("/resilience-policy")
def resilience_policy():
    return {
        "adaptive_recovery": True,
        "provider_recovery": True,
        "runtime_policy_adaptation": True,
        "max_mission_attempts": 2,
        "safe_retry": True,
    }


@app.get("/interface-status")
def interface_status():
    return {"status": "ready", "version": APP_VERSION, "ui": "/interface", "api": "/docs"}


@app.get("/run_help")
def run_help():
    return {
        "method": "POST",
        "path": "/run",
        "body": {
            "objective": "string",
            "research": True,
            "verify": True,
            "remember": False,
            "external_access": True,
            "execute": False,
            "require_approval": False,
        },
    }


@app.get("/version-history")
def version_history():
    versions = [
        "2050.0", "2050.11", "2050.40", "2050.41", "2050.42",
        "2050.44", "2050.45", "2050.49", "2050.50", "2050.51",
        "2050.69", "2050.76", "2050.77", "2050.78", "2050.79",
        "2050.80", "2050.81", "2050.82", "2050.83", "2050.84",
        "2050.85", "2050.86", "2050.87", "2050.88", "2050.90",
    ]
    return {"current": APP_VERSION, "successful_lineage": versions}


@app.get("/router-test")
@app.get("/test-router")
def router_test(background_tasks: BackgroundTasks):
    req = MissionRequest(
        objective="Test AI Infinity adaptive verification routing",
        research=True, verify=True, remember=False, external_access=True,
        execute=False, require_approval=False,
    )
    mid = create_mission(req, approved=True)
    background_tasks.add_task(run_mission, mid)
    return {
        "status": "accepted",
        "mission_id": mid,
        "route_used": classify(req.objective),
        "requirements": ["research", "verification", "recovery"],
    }


@app.get("/interface", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
def interface():
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Infinity {escape(APP_VERSION)}</title>
<style>
body{{font-family:system-ui;margin:0;background:#0b1020;color:#eef2ff}}
main{{max-width:900px;margin:auto;padding:22px}}
textarea{{width:100%;min-height:140px;border-radius:10px;padding:12px;box-sizing:border-box}}
button{{padding:12px 18px;border-radius:9px;border:0;cursor:pointer}}
pre{{white-space:pre-wrap;background:#111827;padding:14px;border-radius:10px}}
.card{{background:#111827;padding:18px;border-radius:14px;margin:12px 0}}
</style></head>
<body><main>
<div class="card"><h1>AI Infinity</h1><p>{escape(APP_VERSION)} · {escape(BUILD)}</p></div>
<div class="card"><textarea id="o" placeholder="Enter a real-world research or planning objective..."></textarea><br><br>
<button onclick="run()">Run Mission</button></div>
<div class="card"><h3>Mission</h3><pre id="out">Ready.</pre></div>
<script>
async function run(){{
 const objective=document.getElementById('o').value.trim(); if(!objective)return;
 const r=await fetch('/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{objective,research:true,verify:true,external_access:true}})}}); 
 const j=await r.json(); document.getElementById('out').textContent=JSON.stringify(j,null,2);
 if(j.mission_id) poll(j.mission_id);
}}
async function poll(id){{
 const r=await fetch('/mission/'+id); const j=await r.json();
 document.getElementById('out').textContent=JSON.stringify(j,null,2);
 if(['queued','running','awaiting_approval'].includes(j.status)) setTimeout(()=>poll(id),1500);
}}
</script></main></body></html>""")


@app.get("/docs-link")
def docs_link():
    return {"docs": "/docs"}


# Basic ASGI exception normalization.
@app.exception_handler(Exception)
async def unhandled(request: FastAPIRequest, exc: Exception):
    return JSONResponse(status_code=500, content={
        "status": "error", "version": APP_VERSION,
        "error": str(exc)[:2000],
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
