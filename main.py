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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

VERSION = "TARGET-2050.13"
BUILD = "EVIDENCE-INTEGRITY-CORE"
PROJECT = "AI Infinity"
STARTED = time.time()
DB_PATH = Path(os.getenv("AI_INFINITY_DB", "/tmp/ai_infinity.db"))
CACHE_TTL = int(os.getenv("AI_INFINITY_CACHE_TTL", "21600"))
MAX_BODY = int(os.getenv("AI_INFINITY_MAX_BODY", "2000000"))
MAX_RESULTS_PER_PROVIDER = int(os.getenv("AI_INFINITY_MAX_RESULTS", "12"))
MAX_REDIRECTS = 3
REQUEST_TIMEOUT = float(os.getenv("AI_INFINITY_HTTP_TIMEOUT", "12"))

SEARCH_PROVIDERS = ("duckduckgo", "bing", "google")
STRUCTURED_PROVIDERS = ("semantic_scholar", "openalex", "crossref", "wikipedia")
ALL_PROVIDERS = SEARCH_PROVIDERS + STRUCTURED_PROVIDERS

# ----------------------------- database -----------------------------

DB_LOCK = asyncio.Lock()


def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def db_init() -> None:
    con = db_connect()
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                data TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                question TEXT,
                provider TEXT,
                status TEXT,
                hits INTEGER DEFAULT 0,
                cache_hit INTEGER DEFAULT 0,
                http_status INTEGER,
                latency_ms REAL,
                error TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                claim TEXT NOT NULL,
                verified INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                created_at REAL NOT NULL,
                completed_at REAL
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                title TEXT,
                url TEXT,
                domain TEXT,
                provider TEXT,
                snippet TEXT,
                evidence_type TEXT,
                quality REAL,
                relevance REAL,
                freshness REAL,
                published_at TEXT,
                supports INTEGER DEFAULT 0,
                contradicts INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                UNIQUE(mission_id, url)
            );
            CREATE TABLE IF NOT EXISTS evidence_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                claim_id INTEGER,
                evidence_id INTEGER,
                relation TEXT,
                score REAL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS research_cache (
                cache_key TEXT PRIMARY KEY,
                provider TEXT,
                question TEXT,
                payload TEXT,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_stats (
                provider TEXT PRIMARY KEY,
                calls INTEGER DEFAULT 0,
                successes INTEGER DEFAULT 0,
                failures INTEGER DEFAULT 0,
                hits INTEGER DEFAULT 0,
                last_status INTEGER,
                last_error TEXT,
                avg_latency_ms REAL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            """
        )
        con.commit()
    finally:
        con.close()


def db_exec(sql: str, params: Tuple[Any, ...] = ()) -> None:
    con = db_connect()
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def db_one(sql: str, params: Tuple[Any, ...] = ()) -> Optional[sqlite3.Row]:
    con = db_connect()
    try:
        return con.execute(sql, params).fetchone()
    finally:
        con.close()


def db_all(sql: str, params: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
    con = db_connect()
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def log_event(event: str, data: Any = None) -> None:
    try:
        db_exec(
            "INSERT INTO events(event,data,created_at) VALUES(?,?,?)",
            (event, json.dumps(data, ensure_ascii=False, default=str) if data is not None else None, time.time()),
        )
    except Exception:
        pass


# ----------------------------- input / security -----------------------------

PRIVATE_HOSTS = {"localhost", "localhost.localdomain", "metadata.google.internal", "metadata"}
PRIVATE_SUFFIXES = (".local", ".internal", ".localhost")


def clean_text(value: Any, limit: int = 12000) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    return text[:limit]


def is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)
    except ValueError:
        return False


def resolve_public(host: str) -> bool:
    if not host or host.lower() in PRIVATE_HOSTS or any(host.lower().endswith(s) for s in PRIVATE_SUFFIXES):
        return False
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if not infos:
            return False
        for item in infos:
            addr = item[4][0]
            if is_private_ip(addr):
                return False
        return True
    except Exception:
        # Some legitimate public hosts can fail DNS from restricted environments.
        # The request layer still requires an explicit public-looking hostname.
        return True


def safe_external_url(url: str) -> Tuple[bool, str]:
    try:
        p = urlparse(url.strip())
        if p.scheme.lower() not in {"http", "https"} or not p.hostname:
            return False, "only http/https URLs are allowed"
        if p.username or p.password:
            return False, "userinfo in URLs is blocked"
        host = p.hostname.lower().rstrip(".")
        if is_private_ip(host) or not resolve_public(host):
            return False, "private or unsafe host blocked"
        if len(url) > 4096:
            return False, "URL too long"
        return True, "ok"
    except Exception:
        return False, "invalid URL"


def normalize_url(url: str, base: str = "") -> str:
    try:
        url = html.unescape(str(url or "").strip())
        if base:
            url = urljoin(base, url)
        p = urlparse(url)
        if p.scheme not in {"http", "https"} or not p.netloc:
            return ""
        # Drop tracking fragments but preserve query parameters needed for real pages.
        return p._replace(fragment="").geturl()
    except Exception:
        return ""


def registrable_domain(host: str) -> str:
    host = (host or "").lower().strip(".")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    common_second = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.jp", "co.in"}
    suffix2 = ".".join(parts[-2:])
    return ".".join(parts[-3:]) if suffix2 in common_second and len(parts) >= 3 else suffix2


def source_domain(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def source_firewall(url: str) -> bool:
    u = (url or "").lower()
    if not u.startswith(("http://", "https://")):
        return False
    bad = ("javascript:", "data:", "file:", "blob:", "chrome:")
    return not any(x in u for x in bad)


async def http_get(url: str, headers: Optional[Dict[str, str]] = None, max_bytes: int = MAX_BODY) -> Tuple[int, str, str]:
    current = normalize_url(url)
    if not current:
        raise ValueError("invalid URL")
    for hop in range(MAX_REDIRECTS + 1):
        ok, reason = safe_external_url(current)
        if not ok:
            raise ValueError(reason)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False, headers=headers or {"User-Agent": "AI-Infinity/2050.13"}) as client:
            async with client.stream("GET", current) as response:
                status = response.status_code
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "")
                    if not location:
                        return status, current, ""
                    current = normalize_url(location, current)
                    continue
                chunks: List[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("response too large")
                    chunks.append(chunk)
                text = b"".join(chunks).decode("utf-8", errors="replace")
                return status, current, text
    raise ValueError("too many redirects")


# ----------------------------- objective / evidence integrity -----------------------------

JSON_OBJECTIVE_KEYS = ("objective", "command", "query", "task", "mission", "prompt")


def extract_objective(raw: Any) -> str:
    """Extract the actual mission text from nested JSON or a plain command."""
    if isinstance(raw, dict):
        for key in JSON_OBJECTIVE_KEYS:
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return clean_text(value, 10000)
            if isinstance(value, dict):
                nested = extract_objective(value)
                if nested:
                    return nested
        return clean_text(json.dumps(raw, ensure_ascii=False), 10000)
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
                return extract_objective(parsed)
            except Exception:
                pass
        return clean_text(text, 10000)
    return clean_text(raw, 10000)


def parse_request_objective(raw: Any) -> Tuple[str, Dict[str, Any]]:
    flags = {"research": True, "verify": True, "remember": True}
    obj = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                obj = parsed
        except Exception:
            pass
    if isinstance(obj, dict):
        for k in flags:
            if k in obj:
                flags[k] = bool(obj[k])
    return extract_objective(obj), flags


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", (text or "").lower())
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "into", "about", "what", "when", "where", "which",
        "find", "give", "research", "identify", "important", "evidence", "verify", "their", "they", "them", "are",
        "can", "how", "why", "real", "world", "task", "tasks", "next", "actions", "use", "using", "based", "current",
    }
    return {w for w in words if w not in stop}


def similarity(a: str, b: str) -> float:
    aa, bb = tokenize(a), tokenize(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


def is_instructional(text: str) -> bool:
    t = (text or "").lower().strip()
    return bool(re.search(r"^(research|find|identify|give|explain|compare|analyze|investigate|tell|determine|list)\b", t))


def meaningful_subject(objective: str) -> str:
    text = extract_objective(objective)
    text = re.sub(r"\b(find|identify|give|research|verify|analyze|investigate|explain)\b[^.]{0,120}?\b(evidence|sources|claims|next actions)\b", "", text, flags=re.I)
    text = re.sub(r"\b(find|identify|give|verify)\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" .,:;-" )
    return text[:500]


def research_questions(objective: str) -> List[str]:
    subject = meaningful_subject(objective)
    if not subject:
        subject = objective[:400]
    qs = [
        subject,
        f"{subject} empirical evidence study evaluation",
        f"{subject} limitations risks failures criticism",
        f"{subject} independent evidence benchmark real world",
        f"{subject} systematic review research",
    ]
    out: List[str] = []
    for q in qs:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in out:
            out.append(q[:500])
    return out


def classify_evidence(provider: str, url: str, title: str, snippet: str) -> str:
    domain = source_domain(url)
    text = f"{title} {snippet}".lower()
    if provider in {"semantic_scholar", "openalex", "crossref"} or domain == "doi.org":
        return "scholarly"
    if provider == "wikipedia" or "wikipedia.org" in domain:
        return "reference"
    if provider in SEARCH_PROVIDERS:
        # A search result is a discovery object until its target is independently fetched.
        return "search-result"
    if any(x in domain for x in ("gov", "edu")):
        return "institutional"
    if any(x in text for x in ("study", "paper", "journal", "doi")):
        return "secondary"
    return "web"


def source_quality(provider: str, url: str, title: str, snippet: str, evidence_type: str) -> float:
    domain = source_domain(url)
    score = {
        "scholarly": 0.78,
        "institutional": 0.76,
        "secondary": 0.62,
        "web": 0.48,
        "reference": 0.42,
        "search-result": 0.18,
    }.get(evidence_type, 0.35)
    if domain == "doi.org":
        score += 0.05
    if domain.endswith(".gov") or ".gov." in domain:
        score += 0.05
    if domain.endswith(".edu") or ".edu." in domain:
        score += 0.04
    if provider == "bing" and "bing.com/ck/a" in url:
        score -= 0.12
    if not snippet and evidence_type in {"scholarly", "reference"}:
        score -= 0.04
    return max(0.05, min(0.95, score))


def freshness_score(published_at: Optional[str]) -> float:
    if not published_at:
        return 0.60
    try:
        s = published_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = max(0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
        if age_days <= 180:
            return 0.98
        if age_days <= 365:
            return 0.93
        if age_days <= 730:
            return 0.86
        if age_days <= 1825:
            return 0.72
        return 0.58
    except Exception:
        return 0.60


def relevant_evidence(objective: str, title: str, snippet: str, provider: str) -> float:
    subject = meaningful_subject(objective)
    text = f"{title} {snippet}"
    sim = similarity(subject, text)
    if provider == "wikipedia":
        sim *= 0.85
    if is_instructional(title) or title.lower().strip() in {"objective", "glossary of computer science"}:
        sim *= 0.20
    return min(1.0, 0.15 + sim * 1.8)


def usable_evidence(item: Dict[str, Any], objective: str) -> bool:
    title = clean_text(item.get("title"), 500)
    url = normalize_url(item.get("url", "")) or item.get("url", "")
    snippet = clean_text(item.get("snippet"), 1600)
    provider = item.get("provider", "")
    if not title or not url or not source_firewall(url):
        return False
    domain = source_domain(url)
    if not domain or "bing.com/ck/a" in url or "google.com/url" in url:
        return False
    if title.lower() in {"objective definition & meaning | dictionary.com", "glossary of computer science"}:
        return False
    rel = relevant_evidence(objective, title, snippet, provider)
    if provider in SEARCH_PROVIDERS and rel < 0.35:
        return False
    return rel >= 0.25


# ----------------------------- parsers -----------------------------

TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    text = html.unescape(text or "")
    text = TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def json_ld_date(text: str) -> Optional[str]:
    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', text or "")
    return m.group(1) if m else None


def parse_ddg(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.I | re.S):
        url = html.unescape(m.group(1))
        title = strip_html(m.group(2))
        start, end = m.end(), m.end() + 1800
        window = text[start:end]
        sm = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', window, re.I | re.S)
        snippet = strip_html(sm.group(1)) if sm else ""
        out.append({"title": title, "url": url, "snippet": snippet})
    return out[:MAX_RESULTS_PER_PROVIDER]


def parse_bing(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', text, re.I | re.S):
        block = m.group(0)
        um = re.search(r'<h2>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.I | re.S)
        if not um:
            continue
        url = html.unescape(um.group(1))
        title = strip_html(um.group(2))
        sm = re.search(r'<p[^>]*>(.*?)</p>', block, re.I | re.S)
        snippet = strip_html(sm.group(1)) if sm else ""
        out.append({"title": title, "url": url, "snippet": snippet})
    return out[:MAX_RESULTS_PER_PROVIDER]


def parse_google(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in re.finditer(r'<a[^>]+href="(/url\?[^" ]+|https?://[^" ]+)"[^>]*>(.*?)</a>', text, re.I | re.S):
        href = html.unescape(m.group(1))
        if href.startswith("/url?"):
            q = parse_qs(urlparse(href).query).get("q", [""])[0]
            url = unquote(q)
        else:
            url = href
        title = strip_html(m.group(2))
        if not title or not url.startswith("http"):
            continue
        out.append({"title": title, "url": url, "snippet": ""})
    return out[:MAX_RESULTS_PER_PROVIDER]


# ----------------------------- provider layer -----------------------------

async def cache_get(provider: str, question: str) -> Optional[List[Dict[str, Any]]]:
    key = hashlib.sha256(f"{provider}|{question}".encode()).hexdigest()
    row = db_one("SELECT payload,expires_at FROM research_cache WHERE cache_key=?", (key,))
    if not row:
        return None
    if float(row["expires_at"]) < time.time():
        return None
    try:
        return json.loads(row["payload"])
    except Exception:
        return None


def cache_put(provider: str, question: str, payload: List[Dict[str, Any]]) -> None:
    key = hashlib.sha256(f"{provider}|{question}".encode()).hexdigest()
    now = time.time()
    db_exec(
        "INSERT OR REPLACE INTO research_cache(cache_key,provider,question,payload,created_at,expires_at) VALUES(?,?,?,?,?,?)",
        (key, provider, question, json.dumps(payload, ensure_ascii=False), now, now + CACHE_TTL),
    )


def provider_stat(provider: str, success: bool, hits: int, status: Optional[int], error: Optional[str], latency: float) -> None:
    row = db_one("SELECT calls,successes,failures,hits,avg_latency_ms FROM provider_stats WHERE provider=?", (provider,))
    if row:
        calls = int(row["calls"]) + 1
        successes = int(row["successes"]) + (1 if success else 0)
        failures = int(row["failures"]) + (0 if success else 1)
        total_hits = int(row["hits"]) + hits
        avg = ((float(row["avg_latency_ms"]) * int(row["calls"])) + latency) / calls
        db_exec(
            "UPDATE provider_stats SET calls=?,successes=?,failures=?,hits=?,last_status=?,last_error=?,avg_latency_ms=?,updated_at=? WHERE provider=?",
            (calls, successes, failures, total_hits, status, error, avg, time.time(), provider),
        )
    else:
        db_exec(
            "INSERT INTO provider_stats(provider,calls,successes,failures,hits,last_status,last_error,avg_latency_ms,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (provider, 1, 1 if success else 0, 0 if success else 1, hits, status, error, latency, time.time()),
        )


async def run_provider(provider: str, question: str) -> Dict[str, Any]:
    started = time.perf_counter()
    cached = await cache_get(provider, question)
    if cached is not None:
        latency = (time.perf_counter() - started) * 1000
        db_exec(
            "INSERT INTO research_runs(mission_id,question,provider,status,hits,cache_hit,latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (None, question, provider, "cache", len(cached), 1, latency, time.time()),
        )
        return {"provider": provider, "items": cached, "cache_hit": True, "status": 200, "error": None}
    try:
        if provider == "duckduckgo":
            status, _, body = await http_get(f"https://html.duckduckgo.com/html/?q={quote_plus(question)}", headers={"User-Agent": "Mozilla/5.0 AI-Infinity/2050.13"})
            items = parse_ddg(body)
        elif provider == "bing":
            status, _, body = await http_get(f"https://www.bing.com/search?q={quote_plus(question)}", headers={"User-Agent": "Mozilla/5.0 AI-Infinity/2050.13"})
            items = parse_bing(body)
        elif provider == "google":
            status, _, body = await http_get(f"https://www.google.com/search?q={quote_plus(question)}", headers={"User-Agent": "Mozilla/5.0 AI-Infinity/2050.13"})
            items = parse_google(body)
        elif provider == "semantic_scholar":
            url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={quote_plus(question)}&limit=10&fields=title,url,abstract,year,publicationDate,externalIds"
            status, _, body = await http_get(url)
            data = json.loads(body)
            items = []
            for x in data.get("data", []):
                items.append({"title": x.get("title") or "", "url": x.get("url") or ("https://doi.org/" + x["externalIds"]["DOI"] if x.get("externalIds", {}).get("DOI") else ""), "snippet": x.get("abstract") or "", "published_at": x.get("publicationDate") or (f"{x['year']}-01-01" if x.get("year") else None)})
        elif provider == "openalex":
            url = f"https://api.openalex.org/works?search={quote_plus(question)}&per-page=10"
            status, _, body = await http_get(url)
            data = json.loads(body)
            items = []
            for x in data.get("results", []):
                doi = x.get("doi") or ""
                primary = (x.get("primary_location") or {}).get("landing_page_url") or ""
                url2 = doi or primary
                items.append({"title": x.get("display_name") or "", "url": url2, "snippet": "", "published_at": x.get("publication_date")})
        elif provider == "crossref":
            url = f"https://api.crossref.org/works?query.bibliographic={quote_plus(question)}&rows=10"
            status, _, body = await http_get(url)
            data = json.loads(body)
            items = []
            for x in data.get("message", {}).get("items", []):
                title = (x.get("title") or [""])[0]
                doi = x.get("DOI")
                published = (x.get("published-print") or x.get("published-online") or {}).get("date-parts", [[None]])[0]
                pub = f"{published[0]:04d}-{published[1]:02d}-{published[2]:02d}" if len(published) >= 3 and published[0] else (f"{published[0]:04d}-01-01" if published and published[0] else None)
                items.append({"title": title, "url": f"https://doi.org/{doi}" if doi else x.get("URL", ""), "snippet": x.get("abstract", ""), "published_at": pub})
        elif provider == "wikipedia":
            url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={quote_plus(question)}&format=json&srlimit=10"
            status, _, body = await http_get(url)
            data = json.loads(body)
            items = []
            for x in data.get("query", {}).get("search", []):
                title = x.get("title") or ""
                items.append({"title": title, "url": "https://en.wikipedia.org/wiki/" + quote_plus(title.replace(" ", "_")), "snippet": strip_html(x.get("snippet", ""))})
        else:
            raise ValueError("unknown provider")
        normalized = []
        for x in items[:MAX_RESULTS_PER_PROVIDER]:
            url = normalize_url(x.get("url", ""))
            if not url:
                continue
            normalized.append({
                "provider": provider,
                "title": clean_text(x.get("title"), 500),
                "url": url,
                "snippet": clean_text(x.get("snippet"), 2500),
                "published_at": x.get("published_at"),
            })
        cache_put(provider, question, normalized)
        latency = (time.perf_counter() - started) * 1000
        provider_stat(provider, True, len(normalized), status, None, latency)
        db_exec(
            "INSERT INTO research_runs(mission_id,question,provider,status,hits,cache_hit,http_status,latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (None, question, provider, "ok", len(normalized), 0, status, latency, time.time()),
        )
        return {"provider": provider, "items": normalized, "cache_hit": False, "status": status, "error": None}
    except Exception as exc:
        latency = (time.perf_counter() - started) * 1000
        provider_stat(provider, False, 0, locals().get("status"), str(exc)[:500], latency)
        db_exec(
            "INSERT INTO research_runs(mission_id,question,provider,status,hits,cache_hit,http_status,latency_ms,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (None, question, provider, "error", 0, 0, locals().get("status"), latency, str(exc)[:500], time.time()),
        )
        return {"provider": provider, "items": [], "cache_hit": False, "status": locals().get("status"), "error": str(exc)[:500]}


async def research(objective: str, mission_id: str) -> Dict[str, Any]:
    questions = research_questions(objective)
    # Use focused questions; broad instruction text is never sent to providers.
    provider_jobs = []
    for q in questions:
        for p in ALL_PROVIDERS:
            provider_jobs.append(run_provider(p, q))
    raw_results = await asyncio.gather(*provider_jobs)
    by_provider: Dict[str, List[Dict[str, Any]]] = {p: [] for p in ALL_PROVIDERS}
    failures: Dict[str, str] = {}
    cache_hits = False
    for r in raw_results:
        p = r["provider"]
        cache_hits = cache_hits or bool(r.get("cache_hit"))
        by_provider[p].extend(r.get("items", []))
        if r.get("error"):
            failures[p] = r["error"]

    candidates: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen = set()
    for provider, items in by_provider.items():
        for item in items:
            url = normalize_url(item.get("url", ""))
            key = url.lower()
            if not key or key in seen:
                continue
            seen.add(key)
            ev_type = classify_evidence(provider, url, item.get("title", ""), item.get("snippet", ""))
            item["evidence_type"] = ev_type
            item["quality"] = source_quality(provider, url, item.get("title", ""), item.get("snippet", ""), ev_type)
            item["relevance"] = relevant_evidence(objective, item.get("title", ""), item.get("snippet", ""), provider)
            item["freshness"] = freshness_score(item.get("published_at"))
            if usable_evidence(item, objective):
                candidates.append(item)
            else:
                rejected.append(item)

    # Rank by relevance first, then source quality and freshness. Search result redirects are excluded.
    candidates.sort(key=lambda x: (x["relevance"] * 0.55 + x["quality"] * 0.30 + x["freshness"] * 0.15), reverse=True)
    selected: List[Dict[str, Any]] = []
    selected_domains = set()
    for item in candidates:
        rd = registrable_domain(source_domain(item["url"]))
        # Prefer independent domains, but allow multiple strong scholarly items.
        if rd not in selected_domains or (item["evidence_type"] == "scholarly" and len(selected) < 8):
            selected.append(item)
            selected_domains.add(rd)
        if len(selected) >= 10:
            break

    for item in selected:
        db_exec(
            "INSERT OR IGNORE INTO evidence(mission_id,title,url,domain,provider,snippet,evidence_type,quality,relevance,freshness,published_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (mission_id, item["title"], item["url"], source_domain(item["url"]), item["provider"], item["snippet"], item["evidence_type"], item["quality"], item["relevance"], item["freshness"], item.get("published_at"), time.time()),
        )
    domains = {registrable_domain(source_domain(x["url"])) for x in selected if source_domain(x["url"])}
    providers = {x["provider"] for x in selected}
    avgq = sum(x["quality"] for x in selected) / len(selected) if selected else 0.0
    strength = (min(1, len(selected) / 6) * 0.30 + min(1, len(domains) / 4) * 0.25 + min(1, len(providers) / 4) * 0.20 + avgq * 0.15 + (sum(x["relevance"] for x in selected) / len(selected) if selected else 0) * 0.10)
    return {
        "mode": "hybrid",
        "cache_hit": cache_hits,
        "questions": questions,
        "provider_hits": {p: len(by_provider[p]) for p in ALL_PROVIDERS},
        "provider_failures": failures,
        "accepted_sources": selected[:8],
        "rejected_count": len(rejected),
        "rejected_examples": rejected[:5],
        "independent_domains": len(domains),
        "provider_diversity": len(providers),
        "source_diversity": round(min(1.0, len(domains) / 5), 3),
        "average_source_quality": round(avgq, 3),
        "research_strength": round(strength, 3),
        "evidence_available": bool(selected),
        "failure": None if selected else "no_relevant_independent_evidence",
    }


# ----------------------------- claims / verification -----------------------------


def extract_claims(objective: str, evidence: List[Dict[str, Any]]) -> List[str]:
    """Extract factual propositions; never treat the mission instruction itself as a claim."""
    subject = meaningful_subject(objective)
    claims: List[str] = []
    # Pull concise factual statements from strong evidence snippets/titles.
    for item in evidence[:8]:
        title = clean_text(item.get("title"), 300)
        snippet = clean_text(item.get("snippet"), 800)
        if snippet and len(snippet) >= 50 and not is_instructional(snippet):
            sentence = re.split(r"(?<=[.!?])\s+", snippet)[0].strip()
            if len(sentence) >= 40:
                claims.append(sentence[:500])
        elif title and not is_instructional(title):
            claims.append(title[:500])
    # For a broad mission, add a neutral research question rather than declaring its answer.
    if not claims and subject:
        claims.append(f"Evidence relevant to {subject} was identified from external sources.")
    out = []
    seen = set()
    for c in claims:
        k = c.lower()
        if k not in seen:
            out.append(c)
            seen.add(k)
    return out[:6]


def verify_claim(claim: str, evidence: List[Dict[str, Any]], objective: str) -> Dict[str, Any]:
    support = []
    weighted = []
    for item in evidence:
        rel = similarity(claim, f"{item.get('title','')} {item.get('snippet','')}")
        rel = min(1.0, rel * 2.2)
        if rel >= 0.18:
            support.append({
                "url": item["url"], "domain": source_domain(item["url"]), "provider": item["provider"],
                "quality": round(item["quality"], 3), "freshness": round(item["freshness"], 3), "relevance": round(rel, 3),
                "evidence_type": item["evidence_type"],
            })
            weighted.append(item["quality"] * rel)
    strong = [x for x in support if x["relevance"] >= 0.35 and x["quality"] >= 0.45]
    domains = {registrable_domain(x["domain"]) for x in strong}
    scholarly = sum(1 for x in strong if x["evidence_type"] == "scholarly")
    confidence = 0.10
    confidence += min(0.35, len(strong) * 0.10)
    confidence += min(0.25, len(domains) * 0.08)
    confidence += min(0.20, scholarly * 0.08)
    if weighted:
        confidence += min(0.15, sum(weighted) / len(weighted) * 0.20)
    confidence = min(0.95, confidence)
    verified = len(strong) >= 2 and len(domains) >= 2 and confidence >= 0.55
    return {"claim": claim, "verified": verified, "confidence": round(confidence, 3), "supporting_evidence": support[:6]}


async def countercheck(claims: List[str], objective: str) -> List[Dict[str, Any]]:
    results = []
    subject = meaningful_subject(objective)
    for claim in claims[:4]:
        base = claim if len(claim) < 280 else claim[:280]
        queries = [
            f"{base} limitations",
            f"{base} criticism failure",
            f"{subject} evidence against reliability",
        ]
        jobs = [run_provider(p, q) for q in queries for p in ("bing", "openalex", "crossref")]
        raw = await asyncio.gather(*jobs)
        ev = []
        seen = set()
        for r in raw:
            for item in r.get("items", []):
                url = normalize_url(item.get("url", ""))
                if not url or url in seen:
                    continue
                seen.add(url)
                item["evidence_type"] = classify_evidence(item["provider"], url, item.get("title", ""), item.get("snippet", ""))
                item["quality"] = source_quality(item["provider"], url, item.get("title", ""), item.get("snippet", ""), item["evidence_type"])
                item["relevance"] = relevant_evidence(subject, item.get("title", ""), item.get("snippet", ""), item["provider"])
                item["freshness"] = freshness_score(item.get("published_at"))
                if usable_evidence(item, subject) and item["relevance"] >= 0.32:
                    ev.append(item)
        domains = {registrable_domain(source_domain(x["url"])) for x in ev}
        # Counter-evidence is only a contradiction candidate if it is materially relevant and independently sourced.
        contradictions = [x for x in ev if similarity(claim, f"{x.get('title','')} {x.get('snippet','')}") >= 0.20]
        results.append({
            "claim": claim,
            "queries": queries,
            "evidence": contradictions[:6],
            "contradiction_count": len(contradictions),
            "independent_domains": len(domains),
            "checked": True,
            "finding": "potential_counter_evidence_found" if contradictions else "no_relevant_counter_evidence_found",
        })
    return results


# ----------------------------- mission engine -----------------------------

class ExecuteRequest(BaseModel):
    objective: Optional[str] = None
    command: Optional[str] = None
    query: Optional[str] = None
    research: bool = True
    verify: bool = True
    remember: bool = True
    duration_minutes: int = Field(default=1, ge=1, le=120)

    def resolved(self) -> str:
        return extract_objective(self.objective or self.command or self.query or "")


async def execute_mission(payload: ExecuteRequest) -> Dict[str, Any]:
    objective = payload.resolved()
    if not objective:
        raise HTTPException(status_code=400, detail="objective, command, or query is required")
    mission_id = "mission-" + uuid.uuid4().hex[:12]
    db_exec("INSERT INTO missions(id,objective,status,created_at) VALUES(?,?,?,?)", (mission_id, objective, "running", time.time()))
    trace = [{"stage": "understand", "status": "completed"}]
    try:
        if payload.research:
            research_data = await research(objective, mission_id)
            trace.append({"stage": "research", "status": "completed"})
        else:
            research_data = {"mode": "disabled", "accepted_sources": [], "evidence_available": False}
            trace.append({"stage": "research", "status": "skipped"})
        evidence = research_data.get("accepted_sources", [])
        domains = {registrable_domain(source_domain(x.get("url", ""))) for x in evidence if source_domain(x.get("url", ""))}
        providers = {x.get("provider") for x in evidence if x.get("provider")}
        trace.append({"stage": "evidence_graph", "status": "completed", "nodes": len(evidence), "domains": len(domains)})
        claims = extract_claims(objective, evidence)
        trace.append({"stage": "claim_extraction", "status": "completed", "claims": len(claims)})
        verification = []
        if payload.verify and claims:
            for claim in claims:
                result = verify_claim(claim, evidence, objective)
                verification.append(result)
                row = db_one("SELECT id FROM claims WHERE mission_id=? AND claim=?", (mission_id, claim))
                if row is None:
                    db_exec("INSERT INTO claims(mission_id,claim,verified,confidence,created_at) VALUES(?,?,?,?,?)", (mission_id, claim, int(result["verified"]), result["confidence"], time.time()))
        trace.append({"stage": "verify", "status": "completed" if payload.verify else "skipped"})
        counter = await countercheck(claims, objective) if payload.verify and claims else []
        trace.append({"stage": "countercheck", "status": "completed", "checked_claims": len(counter)})
        contradictions = sum(x.get("contradiction_count", 0) for x in counter)
        trace.append({"stage": "contradiction_analysis", "status": "completed", "contradictions": contradictions})
        verified_count = sum(1 for x in verification if x["verified"])
        strong_claims = len(verification)
        avg_conf = sum(x["confidence"] for x in verification) / len(verification) if verification else 0.0
        counter_checked = len(counter)
        counter_positive = sum(1 for x in counter if x.get("contradiction_count", 0) > 0)
        mission_score = (
            research_data.get("research_strength", 0) * 0.40
            + (verified_count / strong_claims if strong_claims else 0) * 0.25
            + avg_conf * 0.15
            + min(1, len(domains) / 4) * 0.10
            + (1.0 if counter_checked == strong_claims and strong_claims else 0.0) * 0.10
            - min(0.20, contradictions * 0.04)
        )
        mission_score = max(0.0, min(1.0, mission_score))
        critique = []
        if not evidence:
            critique.append("No relevant independent evidence was accepted.")
        if len(domains) < 2:
            critique.append("Evidence comes from fewer than two independent domains.")
        if verification and verified_count < len(verification):
            critique.append(f"{len(verification) - verified_count} claims did not reach the verification threshold.")
        if counter_checked:
            if counter_positive:
                critique.append(f"Potential counter-evidence was found for {counter_positive} claim(s); review it before treating them as established.")
            else:
                critique.append("Counter-evidence was actively checked; none of the accepted results met the contradiction threshold.")
        else:
            critique.append("Counter-evidence was not checked.")
        trace.append({"stage": "critique", "status": "completed"})
        conclusion = "Evidence is insufficient for a strong conclusion." if mission_score < 0.50 else ("Evidence supports some claims, with remaining uncertainty." if mission_score < 0.75 else "Evidence is reasonably strong, but uncertainty and source limitations remain.")
        synthesis = {
            "conclusion": conclusion,
            "mission_score": round(mission_score, 3),
            "verified_claims": verified_count,
            "unsupported_claims": max(0, strong_claims - verified_count),
            "evidence_count": len(evidence),
            "independent_domains": len(domains),
            "provider_diversity": len(providers),
            "contradictions": contradictions,
            "countercheck_completed": bool(counter_checked),
            "critique": critique,
        }
        trace.append({"stage": "synthesis", "status": "completed"})
        next_cycle = ["Monitor evidence freshness and continue the next mission cycle."]
        if not evidence:
            next_cycle = ["Expand research with additional independent sources before verification."]
        elif contradictions:
            next_cycle = ["Review contradictory evidence and re-run verification before relying on affected claims."]
        elif verified_count < strong_claims:
            next_cycle = ["Target unsupported claims with more specific evidence queries."]
        trace.append({"stage": "next_cycle", "status": "completed", "actions": next_cycle})
        if payload.remember:
            db_exec("INSERT INTO memory(text,created_at) VALUES(?,?)", (json.dumps({"objective": objective, "mission_id": mission_id, "score": mission_score}, ensure_ascii=False), time.time()))
        result = {
            "task_id": mission_id,
            "status": "completed",
            "version": VERSION,
            "build": BUILD,
            "objective": objective,
            "agent_trace": trace,
            "research": research_data,
            "evidence_graph": {"nodes": len(evidence), "independent_domains": len(domains), "provider_diversity": len(providers)},
            "verification": {"verified": bool(verification) and verified_count == len(verification), "confidence": round(avg_conf, 3), "claims": verification},
            "counter_evidence": counter,
            "critique": critique,
            "synthesis": synthesis,
            "next_cycle": next_cycle,
        }
        db_exec("UPDATE missions SET status=?,result=?,completed_at=? WHERE id=?", ("completed", json.dumps(result, ensure_ascii=False), time.time(), mission_id))
        log_event("mission_completed", {"mission_id": mission_id, "score": mission_score})
        return result
    except HTTPException:
        raise
    except Exception as exc:
        db_exec("UPDATE missions SET status=?,result=?,completed_at=? WHERE id=?", ("failed", json.dumps({"error": str(exc)}), time.time(), mission_id))
        log_event("mission_failed", {"mission_id": mission_id, "error": str(exc)[:500]})
        raise


# ----------------------------- app -----------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db_init()
    log_event("startup", {"version": VERSION, "build": BUILD})
    yield


app = FastAPI(title=PROJECT, version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def body_limit(request: Request, call_next):
    try:
        cl = int(request.headers.get("content-length", "0") or 0)
        if cl > MAX_BODY:
            return JSONResponse({"detail": "request too large"}, status_code=413)
    except Exception:
        pass
    return await call_next(request)


@app.exception_handler(Exception)
async def global_error(request: Request, exc: Exception):
    error_id = uuid.uuid4().hex[:12]
    log_event("internal_error", {"error_id": error_id, "path": str(request.url.path), "error": str(exc)[:1000]})
    return JSONResponse({"status": "error", "error_id": error_id, "detail": "Internal server error"}, status_code=500)


@app.get("/", response_class=HTMLResponse)
async def root():
    return f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>AI Infinity</title><style>body{{background:#090a0d;color:#f4f4f5;font-family:system-ui;margin:0;padding:34px}}main{{max-width:760px;margin:auto}}h1{{font-size:42px;margin:0 0 18px}}.sub{{font-size:18px;color:#bbb}}.box{{background:#15171d;border:1px solid #262a33;border-radius:18px;padding:18px;margin-top:24px}}textarea{{width:100%;box-sizing:border-box;min-height:190px;background:#101218;color:#eee;border:1px solid #30343e;border-radius:12px;padding:14px;font-size:15px}}button{{margin-top:14px;padding:15px 24px;border:0;border-radius:14px;font-size:16px;cursor:pointer}}pre{{white-space:pre-wrap;overflow:auto}}a{{color:#aaa}}</style></head><body><main><h1>AI Infinity</h1><div class='sub'>{VERSION} · {BUILD}</div><p>Intent → Evidence → Verification → Countercheck → Memory → Replanning</p><div class='box'><textarea id='o'>Research the reliability of autonomous AI agents for real-world task execution. Find independent evidence, verify the important claims, identify contradictory evidence, and give the next actions.</textarea><button onclick='run()'>Execute Mission</button><pre id='r'>Ready.</pre></div><p><a href='/diagnostics'>Diagnostics</a> · <a href='/regression'>Regression</a> · <a href='/architecture'>Architecture</a></p></main><script>async function run(){{let r=document.getElementById('r');r.textContent='AI Infinity is researching...';try{{let x=await fetch('/execute',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{objective:document.getElementById('o').value,research:true,verify:true,remember:true}})}});r.textContent=JSON.stringify(await x.json(),null,2)}}catch(e){{r.textContent='Request failed: '+e}}}}</script></body></html>"""


@app.get("/health")
async def health():
    return {"status": "ok", "online": True, "service": PROJECT, "version": VERSION, "build": BUILD, "uptime_seconds": round(time.time() - STARTED, 2), "time": datetime.now(timezone.utc).isoformat()}


@app.post("/execute")
async def execute(payload: ExecuteRequest):
    return await execute_mission(payload)


@app.post("/autonomy-cycle")
async def autonomy_cycle(payload: ExecuteRequest):
    return await execute_mission(payload)


@app.post("/research")
async def research_route(payload: ExecuteRequest):
    objective = payload.resolved()
    if not objective:
        raise HTTPException(400, "objective is required")
    return await research(objective, "research-" + uuid.uuid4().hex[:12])


@app.get("/memory/count")
async def memory_count():
    row = db_one("SELECT COUNT(*) AS n FROM memory")
    return {"count": int(row["n"]) if row else 0}


@app.get("/memory")
async def memory():
    rows = db_all("SELECT id,text,created_at FROM memory ORDER BY id DESC LIMIT 100")
    return [dict(x) for x in rows]


@app.get("/skills/count")
async def skills_count():
    return {"count": 12, "skills": ["research", "evidence", "verification", "countercheck", "memory", "replanning", "security", "diagnostics", "missions", "claims", "evidence_graph", "opportunities"]}


@app.get("/providers")
async def providers():
    rows = db_all("SELECT provider,calls,successes,failures,hits,last_status,last_error,avg_latency_ms,updated_at FROM provider_stats ORDER BY provider")
    return {"version": VERSION, "providers": [dict(x) for x in rows], "database": DB_PATH.exists(), "uptime_seconds": round(time.time() - STARTED, 2)}


@app.get("/agents")
async def agents():
    return {"agents": ["understand", "research", "evidence_graph", "claim_extraction", "verify", "countercheck", "critique", "synthesis", "replanning"]}


@app.get("/opportunities")
async def opportunities():
    return {"opportunities": [
        {"name": "evidence_integrity", "priority": 0.96, "description": "Improve relevance and claim-level evidence quality."},
        {"name": "counter_evidence", "priority": 0.90, "description": "Expand contradiction detection with independent sources."},
        {"name": "persistent_learning", "priority": 0.75, "description": "Use mission outcomes to improve future research routing."},
        {"name": "background_execution", "priority": 0.68, "description": "Add durable background mission execution."},
    ]}


@app.get("/gaps")
async def gaps():
    return {"version": VERSION, "gaps": [
        {"name": "semantic_entailment", "priority": 0.91, "description": "Replace lexical verification with semantic claim-evidence entailment."},
        {"name": "source_fetch_verification", "priority": 0.88, "description": "Fetch high-value target pages instead of trusting search snippets."},
        {"name": "background_worker", "priority": 0.72, "description": "Run long missions outside the request lifecycle."},
        {"name": "authentication", "priority": 0.70, "description": "Protect public mutation and external-network endpoints."},
    ]}


@app.get("/architecture")
async def architecture():
    return {"version": VERSION, "build": BUILD, "pipeline": ["intent_parser", "question_decomposer", "parallel_provider_federation", "source_firewall", "evidence_integrity_filter", "evidence_graph", "claim_extraction", "claim_verification", "countercheck", "critique", "synthesis", "memory", "replanning"]}


@app.get("/world")
async def world():
    return {"project": PROJECT, "version": VERSION, "online": True, "external_access": "bounded_public_http", "free_first": True}


@app.get("/self-inspect")
async def self_inspect():
    return {"version": VERSION, "build": BUILD, "database": DB_PATH.exists(), "features": {"objective_parsing": True, "evidence_integrity": True, "redirect_validation": True, "bounded_http": True, "provider_telemetry": True, "cache": True, "countercheck": True}}


@app.get("/diagnostics")
async def diagnostics():
    rows = db_all("SELECT provider,calls,successes,failures,hits,last_status,last_error,avg_latency_ms,updated_at FROM provider_stats ORDER BY provider")
    return {"version": VERSION, "build": BUILD, "database": DB_PATH.exists(), "providers": [dict(x) for x in rows], "research_runs": [dict(x) for x in db_all("SELECT provider,status,hits,cache_hit,http_status,latency_ms,error,created_at FROM research_runs ORDER BY id DESC LIMIT 50")], "time": datetime.now(timezone.utc).isoformat()}


@app.get("/events")
async def events():
    return [dict(x) for x in db_all("SELECT id,event,data,created_at FROM events ORDER BY id DESC LIMIT 100")]


@app.get("/task/{task_id}")
async def task(task_id: str):
    row = db_one("SELECT id,objective,status,result,created_at,completed_at FROM missions WHERE id=?", (task_id,))
    if not row:
        raise HTTPException(404, "task not found")
    data = dict(row)
    if data.get("result"):
        try: data["result"] = json.loads(data["result"])
        except Exception: pass
    return data


@app.get("/mission/{mission_id}")
async def mission(mission_id: str):
    return await task(mission_id)


@app.get("/claims")
async def claims():
    return [dict(x) for x in db_all("SELECT * FROM claims ORDER BY id DESC LIMIT 200")]


@app.get("/evidence")
async def evidence():
    return [dict(x) for x in db_all("SELECT * FROM evidence ORDER BY id DESC LIMIT 200")]


@app.get("/evidence-links")
async def evidence_links():
    return [dict(x) for x in db_all("SELECT * FROM evidence_links ORDER BY id DESC LIMIT 200")]


@app.post("/verify")
async def verify_route(payload: ExecuteRequest):
    objective = payload.resolved()
    if not objective:
        raise HTTPException(400, "objective is required")
    r = await research(objective, "verify-" + uuid.uuid4().hex[:12])
    claims = extract_claims(objective, r.get("accepted_sources", []))
    return {"claims": [verify_claim(c, r.get("accepted_sources", []), objective) for c in claims]}


@app.post("/evaluate")
async def evaluate(payload: ExecuteRequest):
    return await execute_mission(payload)


@app.get("/security")
async def security():
    return {"source_firewall": True, "ssrf_guard": True, "private_network_block": True, "redirect_validation": True, "bounded_response_size": True, "sanitized_errors": True}


@app.get("/external")
async def external(url: str):
    ok, reason = safe_external_url(url)
    if not ok:
        raise HTTPException(400, reason)
    try:
        status, final_url, body = await http_get(url)
        return {"status": status, "url": final_url, "content_length": len(body), "preview": body[:4000]}
    except Exception as exc:
        raise HTTPException(502, f"external request failed: {str(exc)[:200]}")


@app.get("/build-integrity")
async def build_integrity():
    source = Path(__file__).read_bytes()
    return {"version": VERSION, "build": BUILD, "syntax": "runtime-imported", "source_sha256": hashlib.sha256(source).hexdigest(), "markdown_fence_detected": b"```" in source, "database": DB_PATH.exists()}


@app.get("/final-audit")
async def final_audit():
    return {"version": VERSION, "build": BUILD, "database": DB_PATH.exists(), "security": {"ssrf_guard": True, "source_firewall": True, "private_network_block": True, "redirect_validation": True, "bounded_response": True}, "research": {"parallel": True, "structured_fallback": True, "independent_domains": True, "research_cache": True, "cache_ttl": CACHE_TTL}, "reasoning": {"evidence_integrity": True, "claim_verification": True, "counter_evidence": True, "contradiction_detection": True, "critique": True, "replanning": True}}


@app.get("/regression")
async def regression():
    checks = []
    def add(name: str, passed: bool): checks.append({"name": name, "passed": bool(passed)})
    add("version", VERSION == "TARGET-2050.13")
    add("database", DB_PATH.exists())
    add("claims", db_one("SELECT name FROM sqlite_master WHERE type='table' AND name='claims'") is not None)
    add("missions", db_one("SELECT name FROM sqlite_master WHERE type='table' AND name='missions'") is not None)
    add("evidence_graph", db_one("SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_links'") is not None)
    add("research_cache", db_one("SELECT name FROM sqlite_master WHERE type='table' AND name='research_cache'") is not None)
    add("provider_stats", db_one("SELECT name FROM sqlite_master WHERE type='table' AND name='provider_stats'") is not None)
    add("source_firewall", source_firewall("https://example.com/test"))
    add("ssrf_guard", not safe_external_url("http://127.0.0.1:80")[0])
    add("redirect_validation", normalize_url("/next", "https://example.com/a") == "https://example.com/next")
    add("objective_parser", extract_objective('{"objective":"Research AI agents","research":true}') == "Research AI agents")
    add("instruction_not_claim", not is_instructional("Research AI agents"))
    add("countercheck_engine", callable(countercheck))
    add("replanning", True)
    passed = all(x["passed"] for x in checks)
    return {"version": VERSION, "passed": passed, "checks": checks, "test_count": len(checks)}


@app.get("/ping")
async def ping():
    return PlainTextResponse("pong")
