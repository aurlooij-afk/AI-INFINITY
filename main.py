"""
AI Infinity
TARGET-2050.74
BUILD: RESEARCH-DISCOVERY-INTEGRITY-AND-PROVIDER-FALLBACK-CORE

Drop-in single-file FastAPI build.
Preserves:
- typed /run contract
- Swagger request body
- SQLite persistence
- workflow events
- mission state reconciliation
- public-network/SSRF protection
- transport/WAF classification
- evidence storage
- truthful completion gate
- recovery/retry
- diagnostics/inspection endpoints

Adds:
- search-result URL integrity firewall
- Bing/DDG infrastructure-asset rejection
- provider-aware discovery
- canonical URL handling
- OpenAlex/Crossref fallback
- provider fallback when a provider yields invalid infrastructure
- research-source validation separate from transport validation
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import (
    parse_qs,
    quote_plus,
    unquote,
    urljoin,
    urlparse,
    urlunparse,
)
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request as URLRequest, build_opener

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


VERSION = "TARGET-2050.74"
BUILD = "RESEARCH-DISCOVERY-INTEGRITY-AND-PROVIDER-FALLBACK-CORE"

DATA_DIR = os.environ.get("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity")
DB_PATH = os.path.join(DATA_DIR, "ai_infinity.db")

MAX_BODY = 1_500_000
FETCH_TIMEOUT = 10.0
MAX_REDIRECTS = 4
MAX_SOURCES = 12
MIN_TEXT = 250

DB_LOCK = threading.RLock()

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Autonomous research, evidence, transport and recovery core.",
)


class RunRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=20000, description="Mission objective")


class MissionCreateRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=20000)


class TransportRequest(BaseModel):
    http_status: Optional[int] = Field(None, ge=100, le=599)
    content_type: Optional[str] = None
    body: Optional[str] = None
    headers: dict[str, str] = Field(default_factory=dict)


def now() -> float:
    return time.time()


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest()


def jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with DB_LOCK, db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions(
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                result_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflow_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                event TEXT NOT NULL,
                phase TEXT,
                detail_json TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transport_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                url TEXT,
                classification TEXT NOT NULL,
                detail_json TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                url TEXT NOT NULL,
                domain TEXT NOT NULL,
                title TEXT,
                text TEXT,
                content_digest TEXT,
                source_family TEXT,
                provider TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claims(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                claim TEXT NOT NULL,
                evidence_count INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_mission ON workflow_events(mission_id);
            CREATE INDEX IF NOT EXISTS idx_transport_mission ON transport_events(mission_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_mission ON evidence(mission_id);
            """
        )
        # Upgrade older 2050.73 databases without destroying data.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(evidence)").fetchall()}
        if "provider" not in cols:
            c.execute("ALTER TABLE evidence ADD COLUMN provider TEXT")


def event(mission_id: str, name: str, phase: str, detail: Any = None) -> None:
    with DB_LOCK, db() as c:
        c.execute(
            """INSERT INTO workflow_events
               (mission_id,event,phase,detail_json,created_at)
               VALUES(?,?,?,?,?)""",
            (mission_id, name, phase, jdump(detail) if detail is not None else None, now()),
        )


def transport_event(mission_id: Optional[str], url: str, result: dict[str, Any]) -> None:
    with DB_LOCK, db() as c:
        c.execute(
            """INSERT INTO transport_events
               (mission_id,url,classification,detail_json,created_at)
               VALUES(?,?,?,?,?)""",
            (mission_id, url, result["classification"], jdump(result), now()),
        )


def create_mission(objective: str) -> str:
    mid = make_id("mission")
    t = now()
    with DB_LOCK, db() as c:
        c.execute(
            """INSERT INTO missions
               (id,objective,status,phase,result_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (mid, objective, "queued", "queued", None, t, t),
        )
    event(mid, "mission_created", "queued", {"objective_length": len(objective)})
    return mid


def update_mission(mid: str, status: str, phase: str, result: Any = None) -> None:
    with DB_LOCK, db() as c:
        c.execute(
            """UPDATE missions
               SET status=?,phase=?,result_json=?,updated_at=?
               WHERE id=?""",
            (status, phase, jdump(result) if result is not None else None, now(), mid),
        )


TERMINAL_MAP = {
    "mission_completed": ("completed", "closed"),
    "mission_recovery_required": ("needs_recovery", "recovery"),
    "mission_failed": ("failed", "recovery"),
}


def reconcile(mid: str) -> None:
    with DB_LOCK, db() as c:
        mission = c.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
        latest = c.execute(
            """SELECT event,phase,detail_json FROM workflow_events
               WHERE mission_id=? ORDER BY id DESC LIMIT 1""",
            (mid,),
        ).fetchone()
    if not mission or not latest or latest["event"] not in TERMINAL_MAP:
        return
    status, phase = TERMINAL_MAP[latest["event"]]
    if mission["status"] == status:
        return
    detail = None
    if latest["detail_json"]:
        try:
            detail = json.loads(latest["detail_json"])
        except Exception:
            detail = None
    update_mission(mid, status, phase, detail)


def get_mission(mid: str) -> Optional[dict[str, Any]]:
    reconcile(mid)
    with DB_LOCK, db() as c:
        row = c.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
    if not row:
        return None
    out = dict(row)
    raw = out.pop("result_json", None)
    try:
        out["result"] = json.loads(raw) if raw else None
    except Exception:
        out["result"] = raw
    return out


def public_ip(value: str) -> bool:
    try:
        a = ipaddress.ip_address(value)
        return not (
            a.is_private or a.is_loopback or a.is_link_local or
            a.is_multicast or a.is_reserved or a.is_unspecified
        )
    except ValueError:
        return False


def validate_public_url(url: str) -> tuple[bool, str]:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, "Only HTTP/HTTPS is allowed."
        if not p.hostname:
            return False, "Missing hostname."
        if p.username or p.password:
            return False, "Credential-bearing URL blocked."
        host = p.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(".local"):
            return False, "Local hostname blocked."
        port = p.port or (443 if p.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = {x[4][0] for x in infos}
        if not addresses:
            return False, "No DNS address."
        if any(not public_ip(a) for a in addresses):
            return False, "Non-public address blocked."
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url: str) -> dict[str, Any]:
    current = url
    opener = build_opener(ProxyHandler({}), NoRedirect())

    for _ in range(MAX_REDIRECTS + 1):
        valid, reason = validate_public_url(current)
        if not valid:
            return {"ok": False, "url": current, "http_status": None, "headers": {}, "body": "", "error": reason}

        req = URLRequest(
            current,
            headers={
                "User-Agent": "AI-Infinity/2050.74 research",
                "Accept": "text/html,text/plain,application/json,application/xhtml+xml",
            },
        )

        try:
            with opener.open(req, timeout=FETCH_TIMEOUT) as response:
                headers = {k.lower(): v for k, v in response.headers.items()}
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    body = body[:MAX_BODY]
                return {
                    "ok": True,
                    "url": current,
                    "http_status": getattr(response, "status", 200),
                    "headers": headers,
                    "body": body.decode("utf-8", "replace"),
                    "error": None,
                }
        except HTTPError as exc:
            headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            try:
                body = exc.read(MAX_BODY + 1).decode("utf-8", "replace")
            except Exception:
                body = ""
            location = headers.get("location")
            if exc.code in (301, 302, 303, 307, 308) and location:
                current = urljoin(current, location)
                continue
            return {"ok": False, "url": current, "http_status": exc.code, "headers": headers, "body": body, "error": str(exc)}
        except (URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "url": current, "http_status": None, "headers": {}, "body": "", "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "url": current, "http_status": None, "headers": {}, "body": "", "error": str(exc)}

    return {"ok": False, "url": current, "http_status": None, "headers": {}, "body": "", "error": "redirect limit exceeded"}


WAF_MARKERS = (
    "waf", "request blocked", "access denied", "forbidden",
    "cloudflare", "captcha", "web application firewall", "blocked",
)


def classify_transport(
    http_status: Optional[int],
    content_type: Optional[str],
    body: Optional[str],
    headers: dict[str, str],
) -> dict[str, Any]:
    text = (body or "")[:MAX_BODY]
    lowered = text.lower()
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    ctype = (content_type or h.get("content-type", "")).lower()

    if http_status in (401, 403, 406, 429) and any(x in lowered for x in WAF_MARKERS):
        classification, reason = "EDGE_WAF_BLOCK", "HTTP edge/WAF block page detected; response is not research evidence."
    elif http_status is not None and http_status >= 500:
        classification, reason = "UPSTREAM_5XX", "Upstream server failure; response is not research evidence."
    elif not text.strip():
        classification, reason = "EMPTY_RESPONSE", "Empty response is not research evidence."
    elif "text/html" in ctype and any(x in lowered for x in WAF_MARKERS):
        classification, reason = "BLOCKED_HTML", "HTML block/interstitial detected; response is not research evidence."
    elif ctype.startswith(("image/", "audio/", "video/", "application/octet-stream")):
        classification, reason = "OPAQUE_ASSET", "Opaque binary asset is not research evidence."
    elif http_status is not None and 200 <= http_status < 400 and text.strip():
        classification, reason = "VALID_PUBLIC_CONTENT", "Public response contains usable textual content."
    else:
        classification, reason = "UNVERIFIED_RESPONSE", "Response could not be safely classified as research evidence."

    usable = classification == "VALID_PUBLIC_CONTENT"
    return {
        "classification": classification,
        "edge_failure": classification in {"EDGE_WAF_BLOCK", "BLOCKED_HTML"},
        "application_failure": classification == "UPSTREAM_5XX",
        "is_evidence": usable,
        "usable_for_research": usable,
        "http_status": http_status,
        "content_type": ctype,
        "reason": reason,
        "digest": digest(text),
    }


def clean_html(value: str) -> str:
    value = re.sub(r"(?is)<script[^>]*>.*?</script>|<style[^>]*>.*?</style>|<noscript[^>]*>.*?</noscript>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def title_from_html(value: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", value)
    return clean_html(m.group(1))[:500] if m else ""


# -----------------------------
# DISCOVERY INTEGRITY FIREWALL
# -----------------------------

ASSET_EXTENSIONS = (
    ".css", ".js", ".mjs", ".map", ".png", ".jpg", ".jpeg", ".gif",
    ".svg", ".ico", ".webp", ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".webm", ".avi", ".zip", ".gz", ".bin",
)

INFRASTRUCTURE_HOSTS = {
    "r.bing.com",
    "th.bing.com",
    "cc.bingj.com",
    "bat.bing.com",
    "c.bing.com",
    "bing.com",
    "www.bing.com",
    "duckduckgo.com",
    "html.duckduckgo.com",
    "links.duckduckgo.com",
}

TRACKING_HOST_PARTS = (
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
)


def registrable_family(host: str) -> str:
    host = host.lower().rstrip(".")
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def canonicalize_url(value: str) -> Optional[str]:
    if not value:
        return None
    value = html.unescape(value.strip())
    if value.startswith("//"):
        value = "https:" + value
    p = urlparse(value)
    if p.scheme not in ("http", "https") or not p.hostname:
        return None

    # Decode common search-provider redirect wrappers.
    qs = parse_qs(p.query)
    for key in ("uddg", "url", "target", "dest", "destination"):
        if key in qs and qs[key]:
            nested = unquote(qs[key][0])
            nested_url = canonicalize_url(nested)
            if nested_url:
                return nested_url

    host = p.hostname.lower().rstrip(".")
    path = p.path or "/"

    # Strip fragments and default ports.
    netloc = host
    if p.port and not ((p.scheme == "http" and p.port == 80) or (p.scheme == "https" and p.port == 443)):
        netloc = f"{host}:{p.port}"

    return urlunparse((p.scheme, netloc, path, "", p.query, ""))


def research_candidate_check(url: str, provider: str) -> tuple[bool, str]:
    normalized = canonicalize_url(url)
    if not normalized:
        return False, "invalid_url"

    p = urlparse(normalized)
    host = (p.hostname or "").lower().rstrip(".")
    path = (p.path or "").lower()

    if path.endswith(ASSET_EXTENSIONS):
        return False, "asset_extension"

    if host in INFRASTRUCTURE_HOSTS:
        return False, "search_infrastructure_host"

    if any(part in host for part in TRACKING_HOST_PARTS):
        return False, "tracking_host"

    if provider in {"bing", "ddg"}:
        # Search providers are discovery sources, not evidence domains.
        if host.endswith(".bing.com") or host.endswith(".duckduckgo.com"):
            return False, "search_provider_host"

    if "/rb/" in path or "/rp/" in path or "/th?id=" in path:
        return False, "search_asset_path"

    if any(x in path for x in ("/images/", "/imgres", "/favicon", "/static/")) and provider in {"bing", "ddg"}:
        return False, "search_asset_path"

    if p.query:
        q = p.query.lower()
        if any(k in q for k in ("click=", "u=a1", "adurl=", "msclkid=", "gclid=")):
            # Tracking is not automatically fatal, but it is suspicious for discovery.
            if provider in {"bing", "ddg"}:
                return False, "tracking_redirect"

    ok, reason = validate_public_url(normalized)
    if not ok:
        return False, f"network:{reason}"

    return True, normalized


def extract_anchor_urls(html_text: str) -> list[str]:
    out = []
    for raw in re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html_text, re.I):
        n = canonicalize_url(raw)
        if n and n not in out:
            out.append(n)
    return out


def extract_bing_result_urls(html_text: str) -> list[str]:
    out = []
    # Prefer result blocks over every anchor on the page.
    blocks = re.findall(
        r'(?is)<li[^>]+class=["\'][^"\']*\bb_algo\b[^"\']*["\'][^>]*>(.*?)</li>',
        html_text,
    )
    for block in blocks:
        for raw in re.findall(r'href\s*=\s*["\']([^"\']+)["\']', block, re.I):
            n = canonicalize_url(raw)
            if n:
                ok, _ = research_candidate_check(n, "bing")
                if ok and n not in out:
                    out.append(n)
                    break

    if out:
        return out

    # Fallback only to anchors that are already non-Bing/non-asset URLs.
    for raw in re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html_text, re.I):
        n = canonicalize_url(raw)
        if not n:
            continue
        ok, _ = research_candidate_check(n, "bing")
        if ok and n not in out:
            out.append(n)
    return out


def extract_ddg_result_urls(html_text: str) -> list[str]:
    out = []
    blocks = re.findall(
        r'(?is)<a[^>]+class=["\'][^"\']*\bresult__a\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\']',
        html_text,
    )
    for raw in blocks:
        n = canonicalize_url(raw)
        if n:
            ok, _ = research_candidate_check(n, "ddg")
            if ok and n not in out:
                out.append(n)

    if out:
        return out

    for raw in extract_anchor_urls(html_text):
        ok, _ = research_candidate_check(raw, "ddg")
        if ok and raw not in out:
            out.append(raw)
    return out


def discovery_query(objective: str) -> str:
    q = re.sub(r"\s+", " ", objective).strip()
    return q[:900]


def discover_ddg(query: str) -> list[str]:
    r = fetch("https://html.duckduckgo.com/html/?q=" + quote_plus(query))
    return extract_ddg_result_urls(r.get("body", "")) if r.get("ok") else []


def discover_bing(query: str) -> list[str]:
    r = fetch("https://www.bing.com/search?q=" + quote_plus(query))
    return extract_bing_result_urls(r.get("body", "")) if r.get("ok") else []


def discover_openalex(query: str) -> list[str]:
    r = fetch("https://api.openalex.org/works?search=" + quote_plus(query) + "&per-page=10")
    if not r.get("ok"):
        return []
    try:
        payload = json.loads(r.get("body", ""))
    except Exception:
        return []

    out = []
    for item in payload.get("results", []):
        loc = item.get("primary_location") or {}
        for candidate in (loc.get("landing_page_url"), loc.get("pdf_url")):
            if not candidate:
                continue
            n = canonicalize_url(candidate)
            if n:
                ok, _ = research_candidate_check(n, "openalex")
                if ok and n not in out:
                    out.append(n)
    return out[:MAX_SOURCES]


def discover_crossref(query: str) -> list[str]:
    r = fetch("https://api.crossref.org/works?query=" + quote_plus(query) + "&rows=10")
    if not r.get("ok"):
        return []
    try:
        payload = json.loads(r.get("body", ""))
    except Exception:
        return []

    out = []
    for item in payload.get("message", {}).get("items", []):
        n = canonicalize_url(item.get("URL", ""))
        if n:
            ok, _ = research_candidate_check(n, "crossref")
            if ok and n not in out:
                out.append(n)
    return out[:MAX_SOURCES]


def direct_urls(objective: str) -> list[str]:
    out = []
    for raw in re.findall(r"https?://[^\s<>\"']+", objective):
        n = canonicalize_url(raw.rstrip(".,);]"))
        if n and n not in out:
            out.append(n)
    return out[:MAX_SOURCES]


def discover_sources(objective: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    direct = direct_urls(objective)
    if direct:
        return (
            [{"url": x, "provider": "direct"} for x in direct],
            {"direct": len(direct), "ddg": 0, "bing": 0, "openalex": 0, "crossref": 0,
             "rejected": 0, "rejected_reasons": {}},
        )

    q = discovery_query(objective)
    candidates: list[dict[str, str]] = []
    seen = set()
    counts = {"direct": 0, "ddg": 0, "bing": 0, "openalex": 0, "crossref": 0,
              "rejected": 0, "rejected_reasons": {}}

    providers = (
        ("ddg", discover_ddg),
        ("bing", discover_bing),
        ("openalex", discover_openalex),
        ("crossref", discover_crossref),
    )

    # Provider fallback is deliberate: a provider that returns only assets
    # cannot consume the entire discovery budget.
    for name, fn in providers:
        try:
            results = fn(q)
        except Exception:
            results = []

        accepted = 0
        for raw in results:
            normalized = canonicalize_url(raw)
            ok, reason = research_candidate_check(normalized or raw, name)
            if not ok:
                counts["rejected"] += 1
                counts["rejected_reasons"][reason] = counts["rejected_reasons"].get(reason, 0) + 1
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append({"url": normalized, "provider": name})
            accepted += 1
            if len(candidates) >= MAX_SOURCES:
                break

        counts[name] = accepted
        if len(candidates) >= MAX_SOURCES:
            break

    return candidates, counts


def store_evidence(mid: str, url: str, title: str, text: str, provider: str) -> None:
    domain = (urlparse(url).hostname or "").lower()
    family = registrable_family(domain)
    with DB_LOCK, db() as c:
        c.execute(
            """INSERT INTO evidence
               (mission_id,url,domain,title,text,content_digest,source_family,provider,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (mid, url, domain, title, text[:12000], digest(text), family, provider, now()),
        )


def run_research(mid: str, objective: str) -> dict[str, Any]:
    event(mid, "research_started", "research")

    candidates, discovery = discover_sources(objective)
    event(
        mid,
        "research_discovery_completed",
        "research",
        {"candidate_count": len(candidates), "providers": discovery},
    )

    usable = []
    blocked = []
    seen_domains = set()

    for item in candidates:
        url = item["url"]
        provider = item["provider"]

        # Final source firewall check before any network fetch.
        ok, reason = research_candidate_check(url, provider)
        if not ok:
            blocked.append({"url": url, "provider": provider, "reason": reason})
            continue

        response = fetch(url)
        headers = response.get("headers", {})
        transport = classify_transport(
            response.get("http_status"),
            headers.get("content-type"),
            response.get("body", ""),
            headers,
        )
        transport_event(mid, url, transport)

        if not transport["usable_for_research"]:
            blocked.append({
                "url": url,
                "provider": provider,
                "reason": transport["reason"],
                "transport": transport,
            })
            continue

        raw = response.get("body", "")
        text = clean_html(raw)

        if len(text) < MIN_TEXT:
            blocked.append({
                "url": url,
                "provider": provider,
                "reason": "insufficient textual content",
                "transport": transport,
            })
            continue

        # Search-engine pages, CDN pages, and obvious interstitials cannot
        # become evidence even when transport returned 200.
        host = (urlparse(url).hostname or "").lower()
        if host in INFRASTRUCTURE_HOSTS or host.endswith(".bing.com") or host.endswith(".duckduckgo.com"):
            blocked.append({"url": url, "provider": provider, "reason": "search_infrastructure_host"})
            continue

        title = title_from_html(raw)
        store_evidence(mid, url, title, text, provider)

        domain = host
        seen_domains.add(domain)
        usable.append({
            "url": url,
            "domain": domain,
            "title": title,
            "provider": provider,
            "digest": digest(text),
        })

    closure = len(usable) >= 2 and len(seen_domains) >= 2

    claims = []
    if usable:
        claim = f"Research produced {len(usable)} usable public source(s) across {len(seen_domains)} independent domain(s)."
        with DB_LOCK, db() as c:
            c.execute(
                "INSERT INTO claims(mission_id,claim,evidence_count,created_at) VALUES(?,?,?,?)",
                (mid, claim, len(usable), now()),
            )
        claims.append({"claim": claim, "evidence_count": len(usable)})

    if closure:
        next_actions = [
            "Proceed to claim-level verification using the validated evidence set.",
            "Check contradictory evidence before final synthesis.",
        ]
    else:
        next_actions = [
            "Retry with provider fallback and broader independent sources.",
            "Do not treat search-engine infrastructure, assets, WAF, empty, or opaque responses as evidence.",
            "Require at least two usable sources from two independent domains before completion.",
        ]

    result = {
        "mode": "research",
        "closure": closure,
        "evidence_count": len(usable),
        "independent_domains": len(seen_domains),
        "domains": sorted(seen_domains),
        "sources": usable,
        "blocked_sources": blocked,
        "claims": claims,
        "discovery": discovery,
        "candidate_count": len(candidates),
        "next_actions": next_actions,
    }

    event(
        mid,
        "research_closed" if closure else "research_needs_recovery",
        "closed" if closure else "recovery",
        result,
    )
    return result


def execute_mission(mid: str, objective: str) -> None:
    try:
        update_mission(mid, "running", "planning")
        event(mid, "mission_started", "planning")

        result = run_research(mid, objective)

        if result["closure"]:
            update_mission(mid, "completed", "closed", result)
            event(mid, "mission_completed", "closed", {
                "closure": True,
                "evidence_count": result["evidence_count"],
                "independent_domains": result["independent_domains"],
            })
        else:
            update_mission(mid, "needs_recovery", "recovery", result)
            event(mid, "mission_recovery_required", "recovery", {
                "closure": False,
                "evidence_count": result["evidence_count"],
                "independent_domains": result["independent_domains"],
            })
    except Exception as exc:
        failure = {
            "closure": False,
            "error": type(exc).__name__,
            "message": str(exc),
            "next_actions": ["Inspect workflow events.", "Retry the mission after diagnosis."],
        }
        update_mission(mid, "failed", "recovery", failure)
        event(mid, "mission_failed", "recovery", failure)


async def background_mission(mid: str, objective: str) -> None:
    await asyncio.to_thread(execute_mission, mid, objective)


@app.on_event("startup")
def startup() -> None:
    init_db()
    # A process restart must never leave work pretending to be active.
    with DB_LOCK, db() as c:
        rows = c.execute(
            "SELECT id FROM missions WHERE status IN ('queued','running')"
        ).fetchall()
    for row in rows:
        update_mission(row["id"], "needs_recovery", "recovery", {
            "reason": "Recovered after service restart."
        })
        event(row["id"], "startup_recovery", "recovery")


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "name": "AI Infinity",
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "docs": "/docs",
        "health": "/health",
        "run": "/run",
        "architecture": "/architecture",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": "AI Infinity",
        "version": VERSION,
        "build": BUILD,
        "status": "healthy",
        "policy": {
            "valid": True,
            "network_policy_enforced": True,
            "controlled_public_web_access": True,
            "arbitrary_code_execution": False,
            "unrestricted_private_network_access": False,
            "permission_bypass": False,
            "external_content_untrusted": True,
            "evidence_requires_transport_validation": True,
            "research_source_requires_integrity_validation": True,
            "completion_requires_closure": True,
        },
    }


@app.get("/ready")
def ready() -> dict[str, Any]:
    init_db()
    return {"ready": True, "version": VERSION, "build": BUILD}


@app.post("/run")
async def run(request: RunRequest) -> dict[str, Any]:
    objective = request.objective.strip()
    if not objective:
        raise HTTPException(status_code=422, detail="objective cannot be empty")
    mid = create_mission(objective)
    asyncio.create_task(background_mission(mid, objective))
    return {"mission_id": mid, "objective": objective, "status": "queued"}


@app.post("/missions")
async def missions_create(request: MissionCreateRequest) -> dict[str, Any]:
    objective = request.objective.strip()
    mid = create_mission(objective)
    asyncio.create_task(background_mission(mid, objective))
    return {"mission_id": mid, "status": "queued"}


@app.get("/mission/{mission_id}")
def mission(mission_id: str) -> dict[str, Any]:
    item = get_mission(mission_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return item


@app.get("/missions")
def missions(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    with DB_LOCK, db() as c:
        rows = c.execute(
            "SELECT id FROM missions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"missions": [x for r in rows if (x := get_mission(r["id"]))]}


@app.post("/mission/{mission_id}/retry")
async def retry(mission_id: str) -> dict[str, Any]:
    item = get_mission(mission_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    update_mission(mission_id, "queued", "queued", None)
    event(mission_id, "manual_retry", "queued")
    asyncio.create_task(background_mission(mission_id, item["objective"]))
    return {"mission_id": mission_id, "status": "queued"}


@app.get("/workflow/events")
def workflow_events(
    mission_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    with DB_LOCK, db() as c:
        if mission_id:
            rows = c.execute(
                "SELECT * FROM workflow_events WHERE mission_id=? ORDER BY id DESC LIMIT ?",
                (mission_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM workflow_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    out = []
    for r in rows:
        x = dict(r)
        raw = x.pop("detail_json", None)
        try:
            x["detail"] = json.loads(raw) if raw else None
        except Exception:
            x["detail"] = raw
        out.append(x)
    return {"events": out}


@app.get("/transport/events")
def transport_events(
    mission_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    with DB_LOCK, db() as c:
        if mission_id:
            rows = c.execute(
                "SELECT * FROM transport_events WHERE mission_id=? ORDER BY id DESC LIMIT ?",
                (mission_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM transport_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    out = []
    for r in rows:
        x = dict(r)
        raw = x.pop("detail_json", None)
        try:
            x["detail"] = json.loads(raw) if raw else None
        except Exception:
            x["detail"] = raw
        out.append(x)
    return {"events": out}


@app.get("/evidence/{mission_id}")
def evidence(mission_id: str) -> dict[str, Any]:
    if get_mission(mission_id) is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    with DB_LOCK, db() as c:
        rows = c.execute(
            """SELECT url,domain,title,text,content_digest,source_family,provider,created_at
               FROM evidence WHERE mission_id=? ORDER BY id""",
            (mission_id,),
        ).fetchall()
    return {"mission_id": mission_id, "evidence": [dict(x) for x in rows]}


@app.get("/sources/{mission_id}")
def sources(mission_id: str) -> dict[str, Any]:
    if get_mission(mission_id) is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    with DB_LOCK, db() as c:
        rows = c.execute(
            """SELECT url,domain,title,content_digest,source_family,provider,created_at
               FROM evidence WHERE mission_id=? ORDER BY id""",
            (mission_id,),
        ).fetchall()
    return {"mission_id": mission_id, "sources": [dict(x) for x in rows]}


@app.get("/claims/{mission_id}")
def claims(mission_id: str) -> dict[str, Any]:
    if get_mission(mission_id) is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    with DB_LOCK, db() as c:
        rows = c.execute(
            "SELECT claim,evidence_count,created_at FROM claims WHERE mission_id=? ORDER BY id",
            (mission_id,),
        ).fetchall()
    return {"mission_id": mission_id, "claims": [dict(x) for x in rows]}


@app.post("/transport/classify")
def transport_classify(request: TransportRequest) -> dict[str, Any]:
    return classify_transport(
        request.http_status,
        request.content_type,
        request.body,
        request.headers,
    )


@app.get("/policy")
def policy() -> dict[str, Any]:
    return health()["policy"]


@app.get("/diagnostics")
def diagnostics() -> dict[str, Any]:
    with DB_LOCK, db() as c:
        mc = c.execute("SELECT COUNT(*) n FROM missions").fetchone()["n"]
        ec = c.execute("SELECT COUNT(*) n FROM workflow_events").fetchone()["n"]
        tc = c.execute("SELECT COUNT(*) n FROM transport_events").fetchone()["n"]
        vc = c.execute("SELECT COUNT(*) n FROM evidence").fetchone()["n"]
    return {
        "version": VERSION,
        "build": BUILD,
        "missions": {"count": mc},
        "workflow_events": {"count": ec},
        "transport_classifications": {"count": tc},
        "evidence": {"count": vc},
        "limits": {
            "max_body": MAX_BODY,
            "fetch_timeout": FETCH_TIMEOUT,
            "max_sources": MAX_SOURCES,
            "min_text": MIN_TEXT,
        },
    }


@app.get("/architecture")
def architecture() -> dict[str, Any]:
    return {
        "version": VERSION,
        "build": BUILD,
        "typed_run_request": True,
        "swagger_request_body": True,
        "transport_boundary": True,
        "edge_failure_separation": True,
        "application_failure_separation": True,
        "html_waf_detection": True,
        "public_network_policy": True,
        "ssrf_protection": True,
        "research_source_integrity_firewall": True,
        "search_asset_rejection": True,
        "search_infrastructure_rejection": True,
        "provider_aware_extraction": True,
        "provider_fallback": True,
        "ddg_redirect_decoding": True,
        "bing_result_block_extraction": True,
        "openalex_discovery": True,
        "crossref_discovery": True,
        "workflow_persistence": True,
        "mission_state_reconciliation": True,
        "truthful_completion_gate": True,
        "recovery_integrity": True,
    }


init_db()
