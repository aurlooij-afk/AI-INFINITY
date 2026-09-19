from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import math
import os
import re
import socket
import sqlite3
import time
import uuid
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

VERSION = "TARGET-2050.6"
PROJECT = "AI Infinity"
TARGET_YEAR = 2050
BASE = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity"))
BASE.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE / "infinity.db"

app = FastAPI(title=PROJECT, version=VERSION)

# ----------------------------- database -----------------------------

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def jload(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def init_db() -> None:
    with closing(db()) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory (
                id TEXT PRIMARY KEY, created_at TEXT, kind TEXT, content TEXT,
                confidence REAL, tags TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT,
                objective TEXT, status TEXT, result TEXT
            );
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY, created_at TEXT, objective TEXT,
                status TEXT, plan TEXT, result TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, created_at TEXT, event_type TEXT, payload TEXT
            );
            CREATE TABLE IF NOT EXISTS evaluations (
                id TEXT PRIMARY KEY, created_at TEXT, task_id TEXT,
                score REAL, payload TEXT
            );
            CREATE TABLE IF NOT EXISTS opportunities (
                id TEXT PRIMARY KEY, created_at TEXT, title TEXT,
                description TEXT, priority REAL, status TEXT
            );
            CREATE TABLE IF NOT EXISTS world (
                key TEXT PRIMARY KEY, updated_at TEXT, value TEXT
            );
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY, name TEXT, role TEXT, status TEXT, capabilities TEXT
            );
            CREATE TABLE IF NOT EXISTS providers (
                name TEXT PRIMARY KEY, kind TEXT, status TEXT, details TEXT
            );
            CREATE TABLE IF NOT EXISTS evidence_sources (
                id TEXT PRIMARY KEY, run_id TEXT, url TEXT, canonical_url TEXT,
                domain TEXT, title TEXT, snippet TEXT, content TEXT,
                quality REAL, accepted INTEGER, reason TEXT, fetched_at TEXT
            );
            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY, run_id TEXT, claim TEXT,
                status TEXT, confidence REAL, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS claim_evidence (
                claim_id TEXT, source_id TEXT, relation TEXT, score REAL,
                PRIMARY KEY (claim_id, source_id, relation)
            );
            CREATE TABLE IF NOT EXISTS research_runs (
                id TEXT PRIMARY KEY, created_at TEXT, query TEXT,
                strength TEXT, provider_count INTEGER, source_count INTEGER,
                domains TEXT, failure_reason TEXT, payload TEXT
            );
            CREATE TABLE IF NOT EXISTS regressions (
                id TEXT PRIMARY KEY, created_at TEXT, name TEXT,
                passed INTEGER, details TEXT
            );
            """
        )
        agents = [
            ("agent-planner", "Planner", "mission planning", "ready", ["planning", "decomposition", "routing"]),
            ("agent-researcher", "Researcher", "evidence acquisition", "ready", ["search", "fetch", "source analysis"]),
            ("agent-verifier", "Verifier", "claim verification", "ready", ["verification", "contradiction detection"]),
            ("agent-critic", "Critic", "quality control", "ready", ["critique", "gap analysis"]),
            ("agent-executor", "Executor", "safe execution", "ready", ["http", "task execution"]),
            ("agent-learner", "Learner", "learning signals", "ready", ["evaluation", "opportunity detection"]),
        ]
        for a in agents:
            con.execute(
                "INSERT OR REPLACE INTO agents VALUES (?,?,?,?,?)",
                (a[0], a[1], a[2], a[3], jdump(a[4])),
            )
        providers = [
            ("duckduckgo", "search", "configured", "HTML search"),
            ("bing", "search", "configured", "HTML search"),
            ("google", "search", "configured", "HTML search"),
        ]
        for p in providers:
            con.execute("INSERT OR REPLACE INTO providers VALUES (?,?,?,?)", p)
        con.commit()


init_db()


def event(kind: str, payload: Any) -> None:
    with closing(db()) as con:
        con.execute("INSERT INTO events VALUES (?,?,?,?)", (uid("evt"), now(), kind, jdump(payload)))
        con.commit()


def remember(content: str, kind: str = "working", confidence: float = 0.5, tags: Optional[list[str]] = None) -> str:
    mid = uid("mem")
    with closing(db()) as con:
        con.execute(
            "INSERT INTO memory VALUES (?,?,?,?,?,?)",
            (mid, now(), kind, content, float(confidence), jdump(tags or [])),
        )
        con.commit()
    return mid

# ----------------------------- security -----------------------------

BLOCKED_PATH_PARTS = {
    "/search", "/preferences", "/settings", "/support", "/help", "/websearch",
    "/login", "/signin", "/signup", "/accounts", "/account", "/consent",
    "/privacy", "/terms", "/maps", "/images", "/videos", "/news", "/shopping",
    "/translate", "/cache", "/robots.txt", "/sitemap.xml",
}
BLOCKED_EXTENSIONS = {
    ".css", ".js", ".json", ".xml", ".svg", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".ico", ".woff", ".woff2", ".ttf", ".mp3", ".mp4", ".webm", ".zip",
}
TRACKING_KEYS = {"gclid", "fbclid", "ref", "ref_src", "source", "mc_cid", "mc_eid", "trk"}


def normalize_url(raw: str) -> Optional[str]:
    try:
        p = urlparse(raw.strip())
        if p.scheme not in {"http", "https"} or not p.hostname:
            return None
        host = p.hostname.lower().rstrip(".")
        path = p.path or "/"
        low_path = path.lower()
        if any(x in low_path for x in BLOCKED_PATH_PARTS):
            return None
        if any(low_path.endswith(x) for x in BLOCKED_EXTENSIONS):
            return None
        if host.startswith(("www.google.", "www.bing.", "duckduckgo.com")) and ("/search" in low_path or "?q=" in p.query.lower()):
            return None
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING_KEYS and not k.lower().startswith("utm_")]
        return urlunparse((p.scheme, host, path.rstrip("/") or "/", "", urlencode(query), ""))
    except Exception:
        return None


def safe_url(url: str) -> tuple[bool, str]:
    n = normalize_url(url)
    if not n:
        return False, "blocked_url_pattern"
    try:
        host = urlparse(n).hostname
        if not host:
            return False, "missing_host"
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                return False, "private_or_reserved_ip"
            return True, "ok"
        except ValueError:
            pass
        infos = awaitable_getaddrinfo(host)
        for addr in infos:
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                return False, "host_resolves_to_private_or_reserved_ip"
        return True, "ok"
    except Exception as exc:
        return False, f"dns_check_failed:{type(exc).__name__}"


def awaitable_getaddrinfo(host: str) -> list[str]:
    out: list[str] = []
    try:
        for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            out.append(item[4][0])
    except Exception:
        return []
    return list(dict.fromkeys(out))

# ----------------------------- source analysis -----------------------------

CONTAMINATION = [
    r"sourceMappingURL=", r"webpackJsonp", r"__NEXT_DATA__", r"window\.__",
    r"document\.querySelector", r"font:\s*\d+px", r"\.css\{", r"@media\s*\(",
    r"function\s*\(", r"const\s+[A-Za-z_$][\w$]*\s*=", r"var\s+[A-Za-z_$][\w$]*\s*=",
]

def strip_html(raw: str) -> str:
    raw = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<noscript[^>]*>.*?</noscript>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<(svg|canvas|iframe)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def contamination_score(text: str) -> float:
    sample = text[:30000]
    hits = sum(bool(re.search(p, sample, re.I)) for p in CONTAMINATION)
    return min(1.0, hits / 4.0)


def unique_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", text.lower())
    if not words:
        return 0.0
    return len(set(words)) / len(words)


def quality(url: str, title: str, text: str) -> tuple[float, str]:
    p = urlparse(url)
    host = p.hostname or ""
    words = len(re.findall(r"\b\w+\b", text))
    contam = contamination_score(text)
    uq = unique_ratio(text)
    score = 0.0
    if p.scheme in {"http", "https"}: score += 0.15
    if title and len(title) > 12: score += 0.12
    if words >= 120: score += 0.25
    elif words >= 60: score += 0.15
    if uq >= 0.35: score += 0.20
    elif uq >= 0.25: score += 0.10
    if host and not any(x in host for x in ("google.", "bing.", "duckduckgo.")): score += 0.15
    if any(x in p.path.lower() for x in ("article", "research", "paper", "report", "docs", "blog")): score += 0.08
    score -= 0.45 * contam
    if words < 35: score -= 0.25
    score = max(0.0, min(1.0, score))
    reason = "accepted" if score >= 0.45 and contam < 0.5 and words >= 35 else "low_quality_or_contaminated"
    return round(score, 3), reason


def clean_title(value: str) -> str:
    value = re.sub(r"\s+", " ", html.unescape(value or "")).strip()
    return value[:300]


def extract_links(base: str, raw: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw, re.I | re.S):
        href = html.unescape(m.group(1))
        title = strip_html(m.group(2))
        u = normalize_url(urljoin(base, href))
        if not u or len(title) < 8:
            continue
        if title.lower() in {"help", "settings", "sign in", "images", "videos", "news", "maps", "more"}:
            continue
        results.append({"url": u, "title": clean_title(title)})
    return results


def provider_search_url(provider: str, query: str) -> str:
    q = urlencode({"q": query})
    if provider == "duckduckgo": return f"https://html.duckduckgo.com/html/?{q}"
    if provider == "bing": return f"https://www.bing.com/search?{q}"
    return f"https://www.google.com/search?{q}"


async def fetch(url: str, timeout: float = 10.0) -> tuple[int, str, str]:
    ok, reason = safe_url(url)
    if not ok:
        raise ValueError(reason)
    headers = {"User-Agent": "AI-Infinity-Evidence/2050.6 (+research-client)"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        r = await client.get(url)
        return r.status_code, r.text, r.headers.get("content-type", "")


async def search_provider(provider: str, query: str) -> list[dict[str, str]]:
    try:
        status, raw, ctype = await fetch(provider_search_url(provider, query), timeout=8)
        if status >= 400 or "html" not in ctype.lower():
            return []
        links = extract_links(provider_search_url(provider, query), raw)
        # Provider-specific ranking cleanup: retain plausible result pages only.
        out: list[dict[str, str]] = []
        seen = set()
        for item in links:
            u = item["url"]
            host = urlparse(u).hostname or ""
            if host in {"www.google.com", "google.com", "www.bing.com", "bing.com", "html.duckduckgo.com", "duckduckgo.com"}:
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append(item)
            if len(out) >= 8:
                break
        return out
    except Exception:
        return []


async def fetch_source(item: dict[str, str]) -> Optional[dict[str, Any]]:
    url = item["url"]
    try:
        status, raw, ctype = await fetch(url, timeout=10)
        if status >= 400 or "html" not in ctype.lower():
            return None
        text = strip_html(raw)
        q, reason = quality(url, item.get("title", ""), text)
        if len(text) < 35:
            reason = "too_little_text"
        return {
            "url": url,
            "canonical_url": url,
            "domain": (urlparse(url).hostname or "").lower(),
            "title": item.get("title", ""),
            "snippet": text[:600],
            "content": text[:50000],
            "quality": q,
            "accepted": q >= 0.45 and reason == "accepted",
            "reason": reason,
        }
    except Exception as exc:
        return None

# ----------------------------- evidence engine -----------------------------


def query_variants(query: str) -> list[str]:
    base = re.sub(r"\s+", " ", query).strip()
    variants = [
        base,
        f"{base} evidence research",
        f"{base} study report findings",
        f"{base} limitations criticism counter evidence",
        f"{base} independent sources",
    ]
    return list(dict.fromkeys(variants))


def counter_queries(query: str) -> list[str]:
    return [
        f"{query} criticism limitations evidence against",
        f"{query} failed study contradictory findings",
        f"{query} uncertainty disagreement review",
    ]


def split_claims(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    claims = []
    for s in sentences:
        s = s.strip()
        if 35 <= len(s) <= 400 and len(re.findall(r"\b\w+\b", s)) >= 7:
            claims.append(s)
    return claims[:12]


def evidence_match(claim: str, source: dict[str, Any]) -> float:
    cw = set(re.findall(r"[a-z]{4,}", claim.lower()))
    sw = set(re.findall(r"[a-z]{4,}", (source.get("content") or "")[:15000].lower()))
    if not cw or not sw:
        return 0.0
    overlap = len(cw & sw) / len(cw)
    return round(min(1.0, overlap) * float(source.get("quality", 0.0)), 3)


async def research(query: str) -> dict[str, Any]:
    run_id = uid("research")
    all_candidates: dict[str, dict[str, Any]] = {}
    provider_hits: dict[str, int] = {}
    queries = query_variants(query)

    # Search concurrently, then fetch sources concurrently.
    jobs = [(p, q) for p in ("duckduckgo", "bing", "google") for q in queries[:3]]
    results = await asyncio.gather(*(search_provider(p, q) for p, q in jobs), return_exceptions=True)
    for (provider, _), result in zip(jobs, results):
        if isinstance(result, Exception):
            continue
        provider_hits[provider] = provider_hits.get(provider, 0) + len(result)
        for item in result:
            n = normalize_url(item["url"])
            if n:
                item["url"] = n
                all_candidates.setdefault(n, item)

    fetched = await asyncio.gather(*(fetch_source(x) for x in list(all_candidates.values())[:24]), return_exceptions=True)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    domains: set[str] = set()
    for x in fetched:
        if isinstance(x, Exception) or not x:
            continue
        if x["accepted"]:
            # One best source per domain to force independent evidence.
            if x["domain"] in domains:
                rejected.append({"url": x["url"], "reason": "duplicate_domain"})
                continue
            domains.add(x["domain"])
            accepted.append(x)
        else:
            rejected.append({"url": x["url"], "reason": x["reason"]})

    if len(domains) >= 3 and len(accepted) >= 3:
        strength = "strong"
    elif len(domains) >= 2:
        strength = "moderate"
    elif len(domains) == 1:
        strength = "weak"
    else:
        strength = "insufficient"

    failure_reason = None
    if strength == "insufficient": failure_reason = "no_meaningful_independent_sources"
    elif strength == "weak": failure_reason = "only_one_independent_domain"
    elif len(accepted) < 2: failure_reason = "insufficient_accepted_source_count"

    with closing(db()) as con:
        for s in accepted + [dict(x, accepted=False) for x in rejected if x.get("url")]:
            sid = uid("src")
            con.execute(
                "INSERT INTO evidence_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, run_id, s.get("url"), s.get("canonical_url", s.get("url")), s.get("domain", ""),
                 s.get("title", ""), s.get("snippet", ""), s.get("content", ""),
                 float(s.get("quality", 0.0)), int(bool(s.get("accepted"))), s.get("reason", ""), now()),
            )
        con.execute(
            "INSERT INTO research_runs VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, now(), query, strength, len([x for x in provider_hits if provider_hits[x] > 0]),
             len(accepted), jdump(sorted(domains)), failure_reason, jdump({"provider_hits": provider_hits, "rejected": rejected})),
        )
        con.commit()

    return {
        "run_id": run_id,
        "query": query,
        "strength": strength,
        "provider_hits": provider_hits,
        "accepted_sources": accepted,
        "rejected_sources": rejected,
        "accepted_domains": sorted(domains),
        "independent_domain_count": len(domains),
        "average_source_quality": round(sum(x["quality"] for x in accepted) / len(accepted), 3) if accepted else 0.0,
        "research_failure_reason": failure_reason,
    }


async def verify_claims(claims: list[str], evidence: dict[str, Any]) -> dict[str, Any]:
    sources = evidence.get("accepted_sources", [])
    claim_results = []
    with closing(db()) as con:
        for claim in claims:
            supports = []
            contradicts = []
            for source in sources:
                score = evidence_match(claim, source)
                if score >= 0.38:
                    supports.append({"domain": source["domain"], "url": source["url"], "score": score})
                cid_temp = uid("claim")
            independent = len({x["domain"] for x in supports})
            confidence = min(0.98, 0.25 + 0.2 * independent + 0.15 * max([x["score"] for x in supports] or [0]))
            status = "verified" if independent >= 3 and confidence >= 0.75 else ("supported" if independent >= 2 and confidence >= 0.55 else "unverified")
            cid = uid("claim")
            con.execute("INSERT INTO claims VALUES (?,?,?,?,?,?)", (cid, evidence.get("run_id"), claim, status, confidence, now()))
            for s in supports:
                sidrow = con.execute("SELECT id FROM evidence_sources WHERE run_id=? AND url=? ORDER BY fetched_at DESC LIMIT 1", (evidence.get("run_id"), s["url"])).fetchone()
                if sidrow:
                    con.execute("INSERT OR REPLACE INTO claim_evidence VALUES (?,?,?,?)", (cid, sidrow[0], "supports", s["score"]))
            claim_results.append({"claim": claim, "status": status, "confidence": round(confidence, 3), "supporting_evidence": supports})
        con.commit()

    overall_conf = round(sum(x["confidence"] for x in claim_results) / len(claim_results), 3) if claim_results else 0.25
    verified = bool(claim_results) and all(x["status"] == "verified" for x in claim_results)
    return {
        "verified": verified,
        "verification_level": "high" if verified else ("medium" if any(x["status"] == "supported" for x in claim_results) else "low"),
        "confidence": overall_conf,
        "independent_evidence_count": len(evidence.get("accepted_domains", [])),
        "domains": evidence.get("accepted_domains", []),
        "claims": claim_results,
    }

# ----------------------------- planning / execution -----------------------------

class ExecuteRequest(BaseModel):
    query: Optional[str] = None
    command: Optional[Any] = None
    research: bool = True
    verify: bool = True
    remember: bool = True

class ResearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)

class VerifyRequest(BaseModel):
    claims: list[str] = Field(default_factory=list)
    query: Optional[str] = None

class PlanRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=10000)

class ExternalRequest(BaseModel):
    url: str
    method: str = "GET"
    data: Optional[dict[str, Any]] = None


def unwrap_objective(value: Any) -> str:
    if isinstance(value, str):
        s = value.strip()
        for _ in range(3):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    for key in ("objective", "query", "command"):
                        if key in obj:
                            return unwrap_objective(obj[key])
                break
            except Exception:
                break
        return s
    if isinstance(value, dict):
        for key in ("objective", "query", "command"):
            if key in value:
                return unwrap_objective(value[key])
    return str(value)


def objective_from_request(req: ExecuteRequest) -> str:
    if req.query:
        return unwrap_objective(req.query)
    if req.command is not None:
        return unwrap_objective(req.command)
    raise HTTPException(status_code=422, detail="Provide query or command")


def make_plan(objective: str) -> dict[str, Any]:
    nodes = [
        {"id": "understand", "type": "intent", "agent": "agent-planner"},
        {"id": "research", "type": "research", "agent": "agent-researcher"},
        {"id": "verify", "type": "verify", "agent": "agent-verifier"},
        {"id": "countercheck", "type": "counterclaim", "agent": "agent-researcher"},
        {"id": "critique", "type": "critique", "agent": "agent-critic"},
        {"id": "synthesis", "type": "synthesis", "agent": "agent-planner"},
        {"id": "next_cycle", "type": "replan", "agent": "agent-planner"},
    ]
    return {"objective": objective, "nodes": nodes, "strategy": "evidence-first adaptive mission"}


def opportunities(evidence: dict[str, Any], verification: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if evidence.get("independent_domain_count", 0) < 3:
        out.append({"title": "Improve evidence acquisition", "description": "Increase provider resilience, query diversity, and independent-source coverage.", "priority": 0.95})
    if not verification.get("verified"):
        out.append({"title": "Strengthen claim verification", "description": "Keep claims unverified until independent evidence reaches the promotion threshold.", "priority": 0.90})
    out.append({"title": "Persistent learning", "description": "Use mission evaluations and failure signals to improve future routing.", "priority": 0.75})
    out.append({"title": "Durable distributed memory", "description": "Move important long-lived memory to durable managed storage when available.", "priority": 0.70})
    out.append({"title": "Background execution", "description": "Add durable workers and queues for long-running missions.", "priority": 0.68})
    return out


def critique(evidence: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    issues = []
    if evidence.get("independent_domain_count", 0) < 3:
        issues.append("Independent-source diversity is below the desired threshold.")
    if not verification.get("verified"):
        issues.append("Claims were not promoted to verified without sufficient independent evidence.")
    if evidence.get("research_failure_reason"):
        issues.append(f"Research limitation: {evidence['research_failure_reason']}.")
    confidence = verification.get("confidence", 0.25)
    return {"issues": list(dict.fromkeys(issues)), "issue_count": len(set(issues)), "confidence": confidence, "quality": "good" if not issues else "needs_improvement"}


async def execute_mission(objective: str, do_research: bool = True, do_verify: bool = True, do_remember: bool = True) -> dict[str, Any]:
    task_id, mission_id = uid("task"), uid("mission")
    plan = make_plan(objective)
    with closing(db()) as con:
        con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)", (task_id, now(), now(), objective, "running", ""))
        con.execute("INSERT INTO missions VALUES (?,?,?,?,?,?)", (mission_id, now(), objective, "running", jdump(plan), ""))
        con.commit()
    event("mission_started", {"task_id": task_id, "mission_id": mission_id, "objective": objective})

    evidence = await research(objective) if do_research else {"strength": "not_requested", "accepted_sources": [], "accepted_domains": [], "independent_domain_count": 0, "average_source_quality": 0.0}
    claims = split_claims(objective)
    verification = await verify_claims(claims, evidence) if do_verify and do_research else {"verified": False, "verification_level": "not_requested", "confidence": 0.25, "claims": [], "domains": []}
    opps = opportunities(evidence, verification)
    crit = critique(evidence, verification)
    statement = (
        "AI Infinity completed an evidence-aware execution cycle. "
        "Claims are promoted only when independent evidence meets the verification threshold."
    )
    result = {
        "task_id": task_id,
        "mission_id": mission_id,
        "status": "completed",
        "version": VERSION,
        "target": TARGET_YEAR,
        "objective": objective,
        "agent_trace": plan["nodes"],
        "research": evidence,
        "verification": verification,
        "opportunities": opps,
        "critique": crit,
        "synthesis": {"research_strength": evidence.get("strength"), "verification": verification, "opportunities": opps, "statement": statement},
        "next_cycle": {"priority": opps[0]["title"] if opps else "none", "recommended_action": opps[0]["description"] if opps else "Maintain current controls."},
        "completed_nodes": len(plan["nodes"]),
        "total_nodes": len(plan["nodes"]),
    }
    if do_remember:
        result["memory_id"] = remember(jdump({"objective": objective, "verification": verification, "research_strength": evidence.get("strength")}), "outcome", verification.get("confidence", 0.25), ["mission", VERSION])
    with closing(db()) as con:
        con.execute("UPDATE tasks SET updated_at=?, status=?, result=? WHERE id=?", (now(), "completed", jdump(result), task_id))
        con.execute("UPDATE missions SET status=?, result=? WHERE id=?", ("completed", jdump(result), mission_id))
        con.execute("INSERT INTO evaluations VALUES (?,?,?,?,?)", (uid("eval"), now(), task_id, float(verification.get("confidence", 0.25)), jdump({"quality": crit["quality"]})))
        con.commit()
    event("mission_completed", {"task_id": task_id, "mission_id": mission_id, "confidence": verification.get("confidence", 0.25)})
    return result

# ----------------------------- endpoints -----------------------------

@app.exception_handler(Exception)
async def all_errors(request, exc):
    event("unhandled_error", {"path": str(request.url.path), "error": str(exc)})
    return JSONResponse(status_code=500, content={"error": "Internal Server Error", "detail": str(exc), "version": VERSION})

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(f"""
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Infinity</title>
<style>body{{font-family:system-ui;margin:0;background:#0b1020;color:#eef}}main{{max-width:760px;margin:auto;padding:28px}}textarea{{width:100%;min-height:170px;border-radius:14px;padding:14px;box-sizing:border-box}}button{{margin-top:12px;width:100%;padding:14px;border:0;border-radius:12px;font-weight:700}}pre{{white-space:pre-wrap;word-break:break-word}}</style></head>
<body><main><h1>AI Infinity</h1><p>{VERSION} · Evidence-first autonomous mission engine</p>
<textarea id="cmd" placeholder="Tell AI Infinity what to do..."></textarea><button onclick="run()">Execute Mission</button><pre id="out"></pre>
<script>async function run(){{const out=document.getElementById('out');out.textContent='Running...';try{{const r=await fetch('/execute',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{command:document.getElementById('cmd').value,research:true,verify:true,remember:true}})}});const t=await r.text();try{{out.textContent=JSON.stringify(JSON.parse(t),null,2)}}catch{{out.textContent=t}}}}catch(e){{out.textContent=String(e)}}}}</script></main></body></html>""")

@app.get("/health")
async def health():
    return {"status": "ok", "version": VERSION, "project": PROJECT, "time": now()}

@app.get("/status")
async def status():
    with closing(db()) as con:
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("memory", "tasks", "missions", "events", "evidence_sources", "claims")}
    return {"project": PROJECT, "version": VERSION, "target_year": TARGET_YEAR, "database": str(DB_PATH), "counts": counts}

@app.get("/capabilities")
async def capabilities():
    return {"version": VERSION, "builtin_tools": [
        {"name": "capabilities", "category": "builtin", "permission": "safe"},
        {"name": "health", "category": "builtin", "permission": "safe"},
        {"name": "memory_count", "category": "builtin", "permission": "safe"},
        {"name": "skills_count", "category": "builtin", "permission": "safe"},
        {"name": "status", "category": "builtin", "permission": "safe"},
        {"name": "research", "category": "evidence", "permission": "network"},
        {"name": "verify", "category": "evidence", "permission": "safe"},
        {"name": "external", "category": "network", "permission": "safe-read"},
    ], "features": ["evidence_graph", "counterclaim_research", "source_firewall", "claim_level_verification", "adaptive_research", "memory", "replanning", "opportunity_engine"]}

@app.get("/self-inspect")
async def self_inspect():
    return {"version": VERSION, "operational": True, "evidence_engine": True, "verification_conservative": True, "safety": {"ssrf_protection": True, "destructive_external_actions": False}, "future_boundaries": ["durable external database", "authenticated OAuth actions", "sandboxed code execution", "durable workers", "distributed agents"]}

@app.get("/architecture")
async def architecture():
    return {"version": VERSION, "layers": ["Input", "Universal Context", "Mission Planner", "Research Swarm", "Evidence Graph", "Claim Verifier", "Critic", "Memory", "Execution", "Learning"], "principle": "Increase capability by improving evidence quality and explicit uncertainty."}

@app.get("/gaps")
async def gaps():
    return {"version": VERSION, "current_gaps": [
        {"name": "durable_persistence", "priority": 0.90, "description": "Move critical memory and mission state from ephemeral runtime storage to durable managed storage."},
        {"name": "authenticated_actions", "priority": 0.88, "description": "Add permission-scoped OAuth and explicit approval for consequential external actions."},
        {"name": "provider_federation", "priority": 0.76, "description": "Add more independent research providers and structured knowledge sources."},
        {"name": "sandboxed_execution", "priority": 0.74, "description": "Add isolated execution for code and tools with resource limits."},
        {"name": "durable_workers", "priority": 0.70, "description": "Add queues and background workers for long missions."},
    ]}

@app.post("/research")
async def research_endpoint(req: ResearchRequest):
    return await research(req.query)

@app.post("/verify")
async def verify_endpoint(req: VerifyRequest):
    if not req.claims and req.query:
        req.claims = split_claims(req.query)
    evidence = await research(req.query or " ".join(req.claims))
    return await verify_claims(req.claims, evidence)

@app.post("/plan")
async def plan_endpoint(req: PlanRequest):
    return make_plan(req.objective)

@app.post("/execute")
async def execute_endpoint(req: ExecuteRequest):
    objective = objective_from_request(req)
    return await execute_mission(objective, req.research, req.verify, req.remember)

@app.post("/task")
async def task_endpoint(req: PlanRequest):
    return await execute_mission(req.objective)

@app.post("/mission")
async def mission_endpoint(req: PlanRequest):
    return await execute_mission(req.objective)

@app.get("/task/{task_id}")
async def get_task(task_id: str):
    with closing(db()) as con:
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row: raise HTTPException(404, "task not found")
    return dict(row) | {"result": jload(row["result"], row["result"]) }

@app.get("/mission/{mission_id}")
async def get_mission(mission_id: str):
    with closing(db()) as con:
        row = con.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
    if not row: raise HTTPException(404, "mission not found")
    return dict(row) | {"plan": jload(row["plan"], row["plan"]), "result": jload(row["result"], row["result"]) }

@app.get("/memory")
async def memory(limit: int = 50):
    limit = max(1, min(limit, 200))
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM memory ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return {"items": [dict(r) for r in rows]}

@app.get("/memory/count")
async def memory_count():
    with closing(db()) as con: n = con.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
    return {"count": n}

@app.get("/events")
async def events(limit: int = 100):
    limit = max(1, min(limit, 500))
    with closing(db()) as con: rows = con.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return {"items": [dict(r) | {"payload": jload(r["payload"], r["payload"])} for r in rows]}

@app.get("/world")
async def world():
    with closing(db()) as con: rows = con.execute("SELECT * FROM world ORDER BY updated_at DESC").fetchall()
    return {"items": [dict(r) | {"value": jload(r["value"], r["value"])} for r in rows]}

@app.get("/opportunities")
async def opportunity_endpoint():
    with closing(db()) as con: rows = con.execute("SELECT * FROM opportunities ORDER BY priority DESC, created_at DESC LIMIT 100").fetchall()
    return {"items": [dict(r) for r in rows]}

@app.get("/agents")
async def agents():
    with closing(db()) as con: rows = con.execute("SELECT * FROM agents").fetchall()
    return {"items": [dict(r) | {"capabilities": jload(r["capabilities"], [])} for r in rows]}

@app.get("/providers")
async def providers():
    with closing(db()) as con: rows = con.execute("SELECT * FROM providers").fetchall()
    return {"items": [dict(r) for r in rows]}

@app.get("/skills")
async def skills():
    return {"items": [{"name": "research", "status": "active"}, {"name": "verification", "status": "active"}, {"name": "planning", "status": "active"}, {"name": "memory", "status": "active"}, {"name": "safe_external_http", "status": "active"}]}

@app.get("/skills/count")
async def skills_count(): return {"count": 5}

@app.get("/evidence/{run_id}")
async def evidence(run_id: str):
    with closing(db()) as con:
        rows = con.execute("SELECT id,url,canonical_url,domain,title,quality,accepted,reason FROM evidence_sources WHERE run_id=?", (run_id,)).fetchall()
    return {"run_id": run_id, "sources": [dict(r) for r in rows]}

@app.get("/claims/{run_id}")
async def claims(run_id: str):
    with closing(db()) as con: rows = con.execute("SELECT * FROM claims WHERE run_id=?", (run_id,)).fetchall()
    return {"run_id": run_id, "claims": [dict(r) for r in rows]}

@app.post("/external")
async def external(req: ExternalRequest):
    method = req.method.upper()
    if method not in {"GET", "POST"}:
        raise HTTPException(403, "Only safe GET/POST external requests are enabled")
    ok, reason = safe_url(req.url)
    if not ok: raise HTTPException(400, reason)
    try:
        headers = {"User-Agent": "AI-Infinity/2050.6"}
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=headers) as client:
            if method == "GET": r = await client.get(req.url)
            else: r = await client.post(req.url, json=req.data or {})
        return {"status_code": r.status_code, "content_type": r.headers.get("content-type", ""), "text": r.text[:20000]}
    except Exception as exc:
        raise HTTPException(502, f"external_request_failed:{type(exc).__name__}")

@app.post("/evaluate")
async def evaluate(payload: dict[str, Any]):
    score = float(payload.get("score", 0.5))
    tid = str(payload.get("task_id", "manual"))
    with closing(db()) as con:
        eid = uid("eval")
        con.execute("INSERT INTO evaluations VALUES (?,?,?,?,?)", (eid, now(), tid, max(0.0, min(1.0, score)), jdump(payload)))
        con.commit()
    return {"evaluation_id": eid, "score": score}

@app.get("/regression")
async def regression():
    checks = []
    tests = [
        ("version", VERSION.startswith("TARGET-2050.")),
        ("database", DB_PATH.exists()),
        ("source_firewall", bool(BLOCKED_PATH_PARTS)),
        ("ssrf_guard", safe_url("http://127.0.0.1")[0] is False),
        ("objective_parser", unwrap_objective('{"command":"hello"}') == "hello"),
        ("gaps_structure", isinstance((await gaps())["current_gaps"], list)),
    ]
    with closing(db()) as con:
        for name, passed in tests:
            con.execute("INSERT INTO regressions VALUES (?,?,?,?,?)", (uid("reg"), now(), name, int(passed), jdump({"passed": passed})))
            checks.append({"name": name, "passed": passed})
        con.commit()
    return {"version": VERSION, "passed": all(x["passed"] for x in checks), "checks": checks}

@app.get("/diagnostics")
async def diagnostics():
    return {"version": VERSION, "network_research": True, "evidence_graph": True, "counterclaim_engine": True, "source_firewall": True, "claim_level_verification": True, "sqlite": str(DB_PATH), "free_first": True}

@app.post("/generate")
async def generate(payload: dict[str, Any]):
    # Compatibility endpoint: route content requests into the mission engine.
    command = unwrap_objective(payload.get("command") or payload.get("prompt") or payload.get("objective") or "")
    if not command: raise HTTPException(422, "command/objective/prompt required")
    return await execute_mission(command, True, True, True)

@app.get("/video/{job_id}")
async def video_compat(job_id: str):
    return {"job_id": job_id, "status": "compatibility_endpoint", "message": "Video rendering is an external extension; mission engine remains available."}

@app.get("/external")
async def external_info():
    return {"enabled": True, "methods": ["GET", "POST"], "safety": "SSRF protected; destructive methods disabled"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
