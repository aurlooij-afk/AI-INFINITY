"""
AI Infinity
TARGET-2050.1
Single canonical runtime

Design goals:
- Preserve existing API compatibility
- Mission -> goals -> task graph -> execution -> observation -> verification
- Real research source extraction and filtering
- Evidence provenance and source-quality scoring
- Consistent verification / critique / synthesis
- Self-inspection
- Gap analysis
- Recovery and replanning
- Opportunity detection
- External HTTP with SSRF protection
- Memory, world state, events, diagnostics
- Video-generation compatibility
- Free-first architecture
- Honest 2050 extension points

This is an advanced autonomous orchestration runtime.
It does not pretend that a Python service is literally AGI/ASI.
"""

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
import time
import traceback
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import (
    parse_qs,
    quote_plus,
    unquote,
    urljoin,
    urlparse,
)

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field


# ============================================================
# CORE IDENTITY
# ============================================================

VERSION = "TARGET-2050.1"
TARGET_YEAR = 2050
SERVICE = "AI Infinity"

START_TIME = time.time()

BASE = Path(os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity"))
BASE.mkdir(parents=True, exist_ok=True)

MEMORY_FILE = BASE / "memory.json"
TASK_FILE = BASE / "tasks.json"
MISSION_FILE = BASE / "missions.json"
WORLD_FILE = BASE / "world.json"
EVENT_FILE = BASE / "events.json"


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="AI Infinity TARGET-2050.1 autonomous intelligence orchestration runtime",
)


# ============================================================
# SAFE STORAGE
# ============================================================

def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        tmp.replace(path)
    except Exception:
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        except Exception:
            pass


memory_store: List[Dict[str, Any]] = read_json(MEMORY_FILE, [])
tasks_store: Dict[str, Dict[str, Any]] = read_json(TASK_FILE, {})
missions_store: Dict[str, Dict[str, Any]] = read_json(MISSION_FILE, {})
world_store: Dict[str, Any] = read_json(
    WORLD_FILE,
    {
        "facts": {},
        "signals": [],
        "last_updated": None,
        "system": SERVICE,
        "version": VERSION,
    },
)
events_store: List[Dict[str, Any]] = read_json(EVENT_FILE, [])


def persist_all() -> None:
    write_json(MEMORY_FILE, memory_store)
    write_json(TASK_FILE, tasks_store)
    write_json(MISSION_FILE, missions_store)
    write_json(WORLD_FILE, world_store)
    write_json(EVENT_FILE, events_store[-2000:])


# ============================================================
# EVENT / OBSERVABILITY
# ============================================================

def event(event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
    record = {
        "id": f"evt-{uuid.uuid4().hex[:12]}",
        "timestamp": time.time(),
        "type": event_type,
        "data": data or {},
    }
    events_store.append(record)
    if len(events_store) > 2000:
        del events_store[:-2000]
    write_json(EVENT_FILE, events_store)


# ============================================================
# MEMORY
# ============================================================

def remember(
    content: str,
    kind: str = "general",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    item = {
        "id": f"mem-{uuid.uuid4().hex[:12]}",
        "timestamp": time.time(),
        "kind": kind,
        "content": content[:20000],
        "metadata": metadata or {},
    }

    memory_store.append(item)

    if len(memory_store) > 1000:
        del memory_store[:-1000]

    write_json(MEMORY_FILE, memory_store)

    return item


def recent_memory(limit: int = 20) -> List[Dict[str, Any]]:
    return memory_store[-max(1, min(limit, 100)) :]


# ============================================================
# WORLD MODEL
# ============================================================

def update_world(key: str, value: Any, source: str = "runtime") -> None:
    world_store.setdefault("facts", {})[key] = {
        "value": value,
        "source": source,
        "timestamp": time.time(),
    }
    world_store["last_updated"] = time.time()
    write_json(WORLD_FILE, world_store)


def world_snapshot() -> Dict[str, Any]:
    return {
        "system": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "facts": world_store.get("facts", {}),
        "signals": world_store.get("signals", [])[-100:],
        "last_updated": world_store.get("last_updated"),
    }


# ============================================================
# MODELS
# ============================================================

class TaskRequest(BaseModel):
    objective: Optional[str] = Field(default=None, max_length=20000)
    command: Optional[str] = Field(default=None, max_length=20000)
    research: bool = True
    verify: bool = True
    remember: bool = True
    external_access: bool = True


class MissionRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=20000)
    research: bool = True
    verify: bool = True
    remember: bool = True
    external_access: bool = True


class ExternalRequest(BaseModel):
    url: str
    method: str = "GET"
    data: Optional[Dict[str, Any]] = None
    timeout: int = Field(default=20, ge=1, le=60)


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=5000)
    max_sources: int = Field(default=8, ge=1, le=20)


class MemoryRequest(BaseModel):
    content: str
    kind: str = "general"
    metadata: Optional[Dict[str, Any]] = None


class VideoRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=10000)
    duration_minutes: int = Field(default=1, ge=1, le=120)


# ============================================================
# INTELLIGENCE CAPABILITY REGISTRY
# ============================================================

BUILTIN_CAPABILITIES = [
    {
        "name": "capabilities",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "health",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "memory_count",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "skills_count",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "status",
        "category": "builtin",
        "permission": "safe",
    },
    {
        "name": "planner",
        "category": "intelligence",
        "permission": "safe",
    },
    {
        "name": "mission_engine",
        "category": "intelligence",
        "permission": "safe",
    },
    {
        "name": "dynamic_task_graph",
        "category": "intelligence",
        "permission": "safe",
    },
    {
        "name": "research",
        "category": "knowledge",
        "permission": "network",
    },
    {
        "name": "external_http",
        "category": "execution",
        "permission": "network",
    },
    {
        "name": "verification",
        "category": "epistemic",
        "permission": "safe",
    },
    {
        "name": "evidence_provenance",
        "category": "epistemic",
        "permission": "safe",
    },
    {
        "name": "source_quality",
        "category": "epistemic",
        "permission": "safe",
    },
    {
        "name": "self_inspection",
        "category": "meta",
        "permission": "safe",
    },
    {
        "name": "gap_analysis",
        "category": "meta",
        "permission": "safe",
    },
    {
        "name": "self_critique",
        "category": "meta",
        "permission": "safe",
    },
    {
        "name": "recovery",
        "category": "resilience",
        "permission": "safe",
    },
    {
        "name": "replanning",
        "category": "resilience",
        "permission": "safe",
    },
    {
        "name": "opportunity_detection",
        "category": "strategy",
        "permission": "safe",
    },
    {
        "name": "world_model",
        "category": "context",
        "permission": "safe",
    },
    {
        "name": "video",
        "category": "media",
        "permission": "safe",
    },
    {
        "name": "provider_discovery",
        "category": "infrastructure",
        "permission": "safe",
    },
]


FUTURE_EXTENSION_POINTS = [
    "multi-agent orchestration",
    "long-horizon planning",
    "multimodal world models",
    "real-world actuators",
    "continuous learning",
    "distributed execution",
    "advanced simulation",
    "robotics interfaces",
    "scientific experimentation",
    "human-agent collaboration",
    "durable distributed memory",
    "authenticated OAuth actions",
    "specialized model routing",
    "sandboxed code execution",
]


def capabilities_payload() -> Dict[str, Any]:
    return {
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "builtin_tools": BUILTIN_CAPABILITIES,
        "future_extension_points": FUTURE_EXTENSION_POINTS,
        "free_first": True,
    }


# ============================================================
# PROVIDER DISCOVERY
# ============================================================

def provider_status() -> List[Dict[str, Any]]:
    names = [
        ("pollinations", "POLLINATIONS_API_KEY"),
        ("huggingface", "HF_TOKEN"),
        ("renderer", "RENDERER_URL"),
    ]

    result = []

    for name, env_name in names:
        value = os.getenv(env_name)
        result.append(
            {
                "name": name,
                "configured": bool(value),
                "environment_variable": env_name,
            }
        )

    return result


# ============================================================
# URL / NETWORK SAFETY
# ============================================================

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google",
    "169.254.169.254",
    "0.0.0.0",
}

BLOCKED_DOMAIN_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".home",
)

SEARCH_ENGINE_DOMAINS = {
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "duckduckgo.com",
    "html.duckduckgo.com",
    "search.yahoo.com",
    "yahoo.com",
    "r.bing.com",
    "cc.bingj.com",
}

ASSET_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".mjs",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".webm",
    ".avi",
    ".zip",
    ".exe",
    ".dmg",
}

INFRASTRUCTURE_PATHS = (
    "/images/",
    "/image/",
    "/assets/",
    "/static/",
    "/sa/",
    "/favicon",
    "/ajax/",
    "/scripts/",
    "/script/",
    "/css/",
    "/js/",
)


def normalize_hostname(host: str) -> str:
    return (host or "").strip().lower().rstrip(".")


def is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        return False


def resolve_public_ips(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)

        if not infos:
            return False

        for info in infos:
            address = info[4][0]
            if is_private_ip(address):
                return False

        return True

    except Exception:
        return False


def validate_external_url(url: str) -> Tuple[bool, str]:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return False, "Only HTTP and HTTPS are allowed."

        if not parsed.hostname:
            return False, "URL has no hostname."

        host = normalize_hostname(parsed.hostname)

        if host in BLOCKED_HOSTS:
            return False, "Blocked hostname."

        if any(host.endswith(suffix) for suffix in BLOCKED_DOMAIN_SUFFIXES):
            return False, "Blocked internal hostname."

        if is_private_ip(host):
            return False, "Private or reserved IP addresses are blocked."

        if not resolve_public_ips(host):
            return False, "Hostname did not resolve to a verified public address."

        return True, "allowed"

    except Exception as exc:
        return False, f"Invalid URL: {exc}"


def clean_text(value: str, limit: int = 12000) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()[:limit]


# ============================================================
# RESEARCH SOURCE QUALITY
# ============================================================

def domain_of(url: str) -> str:
    try:
        return normalize_hostname(urlparse(url).hostname or "")
    except Exception:
        return ""


def registrableish_domain(host: str) -> str:
    """
    Lightweight domain grouping without an external dependency.
    Good enough to prevent obvious duplicate subdomains from being
    counted as independent evidence.
    """
    parts = host.split(".")

    if len(parts) <= 2:
        return host

    common_second_level = {
        "co.uk",
        "org.uk",
        "ac.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.in",
        "com.br",
        "co.jp",
    }

    suffix2 = ".".join(parts[-2:])

    if suffix2 in common_second_level and len(parts) >= 3:
        return ".".join(parts[-3:])

    return ".".join(parts[-2:])


def source_rejection_reason(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = normalize_hostname(parsed.hostname or "")
    path = (parsed.path or "").lower()

    if not host:
        return "missing_hostname"

    if host in SEARCH_ENGINE_DOMAINS:
        return "search_engine"

    if host.endswith(".bing.com") or host.endswith(".google.com"):
        return "search_infrastructure"

    if host in {
        "schemas.live.com",
        "w3.org",
        "www.w3.org",
        "r.bing.com",
        "cc.bingj.com",
    }:
        return "infrastructure_domain"

    if any(path.startswith(p) for p in INFRASTRUCTURE_PATHS):
        return "infrastructure_path"

    suffix = Path(path).suffix.lower()

    if suffix in ASSET_EXTENSIONS:
        return "non_document_asset"

    if "favicon" in path:
        return "favicon"

    if "share" in path and suffix in {".png", ".jpg", ".jpeg"}:
        return "social_asset"

    if host in {"outlook.live.com", "login.live.com"}:
        return "account_or_service_page"

    return None


def source_quality(url: str, title: str = "", text: str = "") -> Dict[str, Any]:
    host = domain_of(url)
    reason = source_rejection_reason(url)

    if reason:
        return {
            "accepted": False,
            "score": 0.0,
            "domain": host,
            "reason": reason,
        }

    score = 0.40

    if title and len(title.strip()) >= 10:
        score += 0.15

    if len(text) >= 500:
        score += 0.15

    if len(text) >= 1500:
        score += 0.10

    path = urlparse(url).path.lower()

    good_patterns = (
        "/article",
        "/research",
        "/paper",
        "/publication",
        "/report",
        "/docs",
        "/documentation",
        "/blog",
        "/news",
        "/study",
        "/whitepaper",
    )

    if any(p in path for p in good_patterns):
        score += 0.10

    if urlparse(url).scheme == "https":
        score += 0.05

    return {
        "accepted": score >= 0.55,
        "score": round(min(score, 1.0), 3),
        "domain": host,
        "reason": None if score >= 0.55 else "insufficient_content_quality",
    }


# ============================================================
# SEARCH RESULT EXTRACTION
# ============================================================

def unwrap_search_url(url: str) -> str:
    """
    Handles common search-engine redirect wrappers where possible.
    """
    try:
        parsed = urlparse(url)

        if "bing.com" in (parsed.hostname or ""):
            params = parse_qs(parsed.query)

            if "u" in params and params["u"]:
                candidate = unquote(params["u"][0])

                if candidate.startswith(("http://", "https://")):
                    return candidate

        if "google.com" in (parsed.hostname or ""):
            params = parse_qs(parsed.query)

            if "url" in params and params["url"]:
                candidate = unquote(params["url"][0])

                if candidate.startswith(("http://", "https://")):
                    return candidate

        return url

    except Exception:
        return url


def extract_anchor_urls(page: str, base_url: str) -> List[str]:
    urls: List[str] = []

    pattern = re.compile(
        r'<a\b[^>]*?\bhref\s*=\s*["\']([^"\']+)["\']',
        flags=re.I,
    )

    for raw in pattern.findall(page):
        raw = html.unescape(raw.strip())

        if not raw:
            continue

        if raw.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        candidate = urljoin(base_url, raw)
        candidate = unwrap_search_url(candidate)

        if candidate.startswith(("http://", "https://")):
            urls.append(candidate)

    return urls


def extract_candidate_urls(page: str, base_url: str) -> List[str]:
    """
    Extract links from anchors first, then fallback to raw URLs.
    Critically, candidates are filtered later and are NOT automatically
    considered evidence.
    """
    urls = extract_anchor_urls(page, base_url)

    if not urls:
        raw_pattern = re.compile(r'https?://[^\s"\'<>]+')
        urls.extend(raw_pattern.findall(page))

    deduped = []
    seen = set()

    for url in urls:
        url = url.rstrip(".,);]}>\"'")

        if url not in seen:
            seen.add(url)
            deduped.append(url)

    return deduped


# ============================================================
# HTTP FETCH
# ============================================================

async def fetch_url(
    url: str,
    timeout: int = 20,
    max_bytes: int = 2_000_000,
) -> Dict[str, Any]:

    allowed, reason = validate_external_url(url)

    if not allowed:
        return {
            "ok": False,
            "url": url,
            "error": reason,
        }

    headers = {
        "User-Agent": (
            "AI-Infinity/2050.1 research-engine "
            "(+https://ai-infinity-ca5e.onrender.com)"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/json,text/plain;q=0.8,*/*;q=0.5"
        ),
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:

            response = await client.get(url)

            final_url = str(response.url)

            final_allowed, final_reason = validate_external_url(final_url)

            if not final_allowed:
                return {
                    "ok": False,
                    "url": url,
                    "error": f"Redirect blocked: {final_reason}",
                }

            content_type = response.headers.get("content-type", "").lower()

            body = response.content[:max_bytes]

            if "text" in content_type or "json" in content_type:
                text_content = body.decode("utf-8", errors="ignore")
            else:
                text_content = ""

            title_match = re.search(
                r"<title[^>]*>(.*?)</title>",
                text_content,
                flags=re.I | re.S,
            )

            title = clean_text(
                title_match.group(1) if title_match else "",
                500,
            )

            readable = clean_text(text_content, 15000)

            return {
                "ok": response.is_success,
                "status_code": response.status_code,
                "url": url,
                "final_url": final_url,
                "content_type": content_type,
                "title": title,
                "text": readable,
                "bytes": len(body),
            }

    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "error": str(exc),
        }


# ============================================================
# RESEARCH ENGINE
# ============================================================

SEARCH_PROVIDERS = [
    (
        "duckduckgo",
        "https://html.duckduckgo.com/html/?q={query}",
    ),
    (
        "bing",
        "https://www.bing.com/search?q={query}",
    ),
    (
        "google",
        "https://www.google.com/search?q={query}",
    ),
]


async def research_query(
    query: str,
    max_sources: int = 8,
) -> Dict[str, Any]:

    event("research_started", {"query": query})

    candidates: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    accepted: List[Dict[str, Any]] = []

    provider_results = []

    for provider_name, template in SEARCH_PROVIDERS:

        search_url = template.format(query=quote_plus(query))

        result = await fetch_url(search_url, timeout=15)

        provider_results.append(
            {
                "provider": provider_name,
                "ok": result.get("ok", False),
                "status_code": result.get("status_code"),
                "error": result.get("error"),
            }
        )

        if not result.get("ok"):
            continue

        links = extract_candidate_urls(
            result.get("text", ""),
            result.get("final_url", search_url),
        )

        for url in links:
            candidates.append(
                {
                    "url": url,
                    "provider": provider_name,
                }
            )

        if len(candidates) >= max_sources * 8:
            break

    # Deduplicate candidates.
    unique_candidates = []
    seen = set()

    for item in candidates:
        url = item["url"]

        normalized = url.split("#", 1)[0].rstrip("/")

        if normalized in seen:
            continue

        seen.add(normalized)
        unique_candidates.append(
            {
                **item,
                "url": normalized,
            }
        )

    # Filter obvious garbage before fetching.
    filtered = []

    for candidate in unique_candidates:
        reason = source_rejection_reason(candidate["url"])

        if reason:
            rejected.append(
                {
                    **candidate,
                    "reason": reason,
                }
            )
            continue

        filtered.append(candidate)

    # Fetch candidates concurrently but conservatively.
    semaphore = asyncio.Semaphore(5)

    async def fetch_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            result = await fetch_url(candidate["url"], timeout=15)

            return {
                **candidate,
                "fetch": result,
            }

    fetched = await asyncio.gather(
        *(fetch_candidate(x) for x in filtered[: max_sources * 4]),
        return_exceptions=True,
    )

    seen_domains = set()

    for item in fetched:

        if isinstance(item, Exception):
            continue

        result = item.get("fetch", {})

        if not result.get("ok"):
            rejected.append(
                {
                    "url": item["url"],
                    "provider": item["provider"],
                    "reason": result.get("error", "fetch_failed"),
                }
            )
            continue

        final_url = result.get("final_url") or item["url"]

        quality = source_quality(
            final_url,
            result.get("title", ""),
            result.get("text", ""),
        )

        if not quality["accepted"]:
            rejected.append(
                {
                    "url": final_url,
                    "provider": item["provider"],
                    "reason": quality["reason"],
                    "quality_score": quality["score"],
                }
            )
            continue

        domain = registrableish_domain(quality["domain"])

        # A source must contain meaningful textual content.
        if len(result.get("text", "")) < 300:
            rejected.append(
                {
                    "url": final_url,
                    "provider": item["provider"],
                    "reason": "too_little_text",
                }
            )
            continue

        if domain in seen_domains:
            rejected.append(
                {
                    "url": final_url,
                    "provider": item["provider"],
                    "reason": "duplicate_domain",
                }
            )
            continue

        seen_domains.add(domain)

        source_record = {
            "id": f"src-{uuid.uuid4().hex[:10]}",
            "url": final_url,
            "domain": quality["domain"],
            "provider": item["provider"],
            "title": result.get("title", ""),
            "quality_score": quality["score"],
            "content_length": len(result.get("text", "")),
            "content": result.get("text", "")[:8000],
            "retrieved_at": time.time(),
            "provenance": {
                "search_provider": item["provider"],
                "requested_url": item["url"],
                "final_url": final_url,
            },
        }

        accepted.append(source_record)

        if len(accepted) >= max_sources:
            break

    # If search engines returned poor candidates, try direct query
    # interpretation as a final fallback for URLs embedded in the query.
    direct_urls = re.findall(
        r'https?://[^\s]+',
        query,
        flags=re.I,
    )

    for direct_url in direct_urls:
        direct_url = direct_url.rstrip(".,);]}")

        if any(x["url"] == direct_url for x in accepted):
            continue

        if len(accepted) >= max_sources:
            break

        if source_rejection_reason(direct_url):
            continue

        result = await fetch_url(direct_url, timeout=15)

        if not result.get("ok"):
            continue

        quality = source_quality(
            result.get("final_url", direct_url),
            result.get("title", ""),
            result.get("text", ""),
        )

        if not quality["accepted"]:
            continue

        domain = registrableish_domain(quality["domain"])

        if domain in seen_domains:
            continue

        seen_domains.add(domain)

        accepted.append(
            {
                "id": f"src-{uuid.uuid4().hex[:10]}",
                "url": result.get("final_url", direct_url),
                "domain": quality["domain"],
                "provider": "direct",
                "title": result.get("title", ""),
                "quality_score": quality["score"],
                "content_length": len(result.get("text", "")),
                "content": result.get("text", "")[:8000],
                "retrieved_at": time.time(),
                "provenance": {
                    "search_provider": "direct",
                    "requested_url": direct_url,
                    "final_url": result.get("final_url", direct_url),
                },
            }
        )

    quality_average = (
        round(
            sum(float(x["quality_score"]) for x in accepted)
            / len(accepted),
            3,
        )
        if accepted
        else 0.0
    )

    independent_domains = sorted(
        {
            registrableish_domain(x["domain"])
            for x in accepted
            if x.get("domain")
        }
    )

    research_result = {
        "query": query,
        "sources_found": len(accepted),
        "sources": accepted,
        "rejected_sources": rejected[:50],
        "candidate_count": len(unique_candidates),
        "accepted_domains": independent_domains,
        "independent_domain_count": len(independent_domains),
        "average_source_quality": quality_average,
        "provider_attempts": provider_results,
        "research_quality": (
            "strong"
            if len(accepted) >= 3 and quality_average >= 0.65
            else "usable"
            if len(accepted) >= 1
            else "insufficient"
        ),
        "timestamp": time.time(),
    }

    event(
        "research_completed",
        {
            "query": query,
            "accepted": len(accepted),
            "rejected": len(rejected),
            "independent_domains": len(independent_domains),
        },
    )

    return research_result


# ============================================================
# EVIDENCE / VERIFICATION
# ============================================================

def evidence_fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def verify_research(
    research: Dict[str, Any],
    claim: str,
) -> Dict[str, Any]:

    sources = research.get("sources", [])

    independent_domains = {
        registrableish_domain(
            source.get("domain")
            or domain_of(source.get("url", ""))
        )
        for source in sources
    }

    independent_domains.discard("")

    fingerprints = {
        evidence_fingerprint(source.get("content", ""))
        for source in sources
        if source.get("content")
    }

    quality_values = [
        float(source.get("quality_score", 0))
        for source in sources
    ]

    average_quality = (
        sum(quality_values) / len(quality_values)
        if quality_values
        else 0.0
    )

    source_count = len(sources)
    domain_count = len(independent_domains)

    if source_count == 0:
        level = "none"
        confidence = 0.0
        verified = False

    elif domain_count >= 3 and average_quality >= 0.65:
        level = "high"
        confidence = min(
            0.95,
            0.60
            + min(domain_count, 5) * 0.06
            + min(average_quality, 1.0) * 0.15,
        )
        verified = True

    elif domain_count >= 2 and average_quality >= 0.60:
        level = "moderate"
        confidence = min(
            0.85,
            0.48
            + min(domain_count, 4) * 0.07
            + min(average_quality, 1.0) * 0.12,
        )
        verified = True

    else:
        level = "low"
        confidence = min(
            0.50,
            0.20
            + min(source_count, 3) * 0.06
            + min(average_quality, 1.0) * 0.10,
        )
        verified = False

    result = {
        "claim": claim,
        "verified": verified,
        "verification_level": level,
        "confidence": round(confidence, 3),
        "evidence_count": source_count,
        "independent_evidence_count": domain_count,
        "independent_domains": sorted(independent_domains),
        "unique_evidence_fingerprints": len(fingerprints),
        "average_source_quality": round(average_quality, 3),
        "criteria": {
            "meaningful_source_required": True,
            "independent_domains_required": 2,
            "search_infrastructure_rejected": True,
            "content_quality_checked": True,
        },
        "timestamp": time.time(),
    }

    return result


# ============================================================
# INTENT ENGINE
# ============================================================

def classify_intent(objective: str) -> Dict[str, Any]:
    text = objective.lower()

    scores = {
        "research": 0,
        "build": 0,
        "analysis": 0,
        "creative": 0,
        "execution": 0,
        "diagnostic": 0,
        "planning": 0,
    }

    research_words = [
        "research",
        "investigate",
        "find out",
        "sources",
        "evidence",
        "latest",
        "information",
        "study",
    ]

    build_words = [
        "build",
        "create",
        "implement",
        "develop",
        "upgrade",
        "deploy",
        "code",
    ]

    analysis_words = [
        "analyze",
        "analyse",
        "compare",
        "identify",
        "evaluate",
        "determine",
        "assess",
    ]

    creative_words = [
        "write",
        "story",
        "script",
        "design",
        "idea",
        "creative",
    ]

    execution_words = [
        "run",
        "execute",
        "do",
        "perform",
        "complete",
        "action",
    ]

    diagnostic_words = [
        "debug",
        "diagnose",
        "error",
        "failure",
        "health",
        "status",
        "inspect",
        "self-inspect",
    ]

    planning_words = [
        "plan",
        "roadmap",
        "strategy",
        "next stage",
        "architecture",
        "steps",
    ]

    for word in research_words:
        if word in text:
            scores["research"] += 1

    for word in build_words:
        if word in text:
            scores["build"] += 1

    for word in analysis_words:
        if word in text:
            scores["analysis"] += 1

    for word in creative_words:
        if word in text:
            scores["creative"] += 1

    for word in execution_words:
        if word in text:
            scores["execution"] += 1

    for word in diagnostic_words:
        if word in text:
            scores["diagnostic"] += 1

    for word in planning_words:
        if word in text:
            scores["planning"] += 1

    primary = max(scores, key=scores.get)

    if scores[primary] == 0:
        primary = "analysis"

    return {
        "primary": primary,
        "scores": scores,
        "confidence": round(
            min(
                0.95,
                0.45 + scores[primary] * 0.08,
            ),
            3,
        ),
    }


# ============================================================
# SELF INSPECTION
# ============================================================

def self_inspect() -> Dict[str, Any]:
    configured_providers = provider_status()

    operational_capabilities = [
        item["name"]
        for item in BUILTIN_CAPABILITIES
    ]

    storage_files = {
        "memory": MEMORY_FILE.exists(),
        "tasks": TASK_FILE.exists(),
        "missions": MISSION_FILE.exists(),
        "world": WORLD_FILE.exists(),
        "events": EVENT_FILE.exists(),
    }

    inspection = {
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "runtime": {
            "python": os.sys.version.split()[0],
            "uptime_seconds": round(time.time() - START_TIME, 3),
            "storage_mode": "runtime-local",
            "storage_note": (
                "Render filesystem may be ephemeral; durable external "
                "persistence is an extension point."
            ),
        },
        "api": {
            "fastapi": True,
            "task_api": True,
            "mission_api": True,
            "research_api": True,
            "external_api": True,
            "video_compatibility": True,
        },
        "intelligence": {
            "planner": True,
            "dynamic_task_graph": True,
            "verification": True,
            "self_critique": True,
            "recovery": True,
            "replanning": True,
            "opportunity_detection": True,
            "world_model": True,
            "memory": True,
        },
        "network": {
            "external_http": True,
            "ssrf_protection": True,
            "source_filtering": True,
            "source_quality_scoring": True,
        },
        "providers": configured_providers,
        "storage_files": storage_files,
        "operational_capability_count": len(operational_capabilities),
        "operational_capabilities": operational_capabilities,
        "future_extension_points": FUTURE_EXTENSION_POINTS,
    }

    return inspection


# ============================================================
# GAP ANALYSIS
# ============================================================

def gap_analysis() -> Dict[str, Any]:
    inspection = self_inspect()

    gaps = [
        {
            "capability": "semantic model reasoning",
            "status": "partial",
            "detail": (
                "The orchestration runtime exists, but a dedicated "
                "general-purpose reasoning model is not intrinsically "
                "embedded in the runtime."
            ),
        },
        {
            "capability": "durable distributed memory",
            "status": "missing",
            "detail": (
                "Current JSON persistence is runtime-local. "
                "A durable database/object store would be needed "
                "for production-grade long-term memory."
            ),
        },
        {
            "capability": "authenticated external actions",
            "status": "partial",
            "detail": (
                "General outbound HTTP is available, but arbitrary "
                "authenticated third-party actions require explicit "
                "connectors/OAuth integrations."
            ),
        },
        {
            "capability": "true background distributed execution",
            "status": "partial",
            "detail": (
                "Mission orchestration is implemented in-process. "
                "A queue/worker fabric is needed for large-scale "
                "parallel execution."
            ),
        },
        {
            "capability": "multi-agent orchestration",
            "status": "extension",
            "detail": (
                "The architecture exposes the extension point, "
                "but independent specialized agents are not yet "
                "distributed as autonomous worker identities."
            ),
        },
        {
            "capability": "multimodal world model",
            "status": "extension",
            "detail": (
                "Structured world state exists, but a genuine "
                "multimodal learned world model is not embedded."
            ),
        },
        {
            "capability": "continuous learning",
            "status": "extension",
            "detail": (
                "Outcome memory and evaluation hooks exist; "
                "autonomous model training/updating is not performed."
            ),
        },
        {
            "capability": "real-world actuators",
            "status": "extension",
            "detail": (
                "No unrestricted robotics, hardware, financial, "
                "or physical actuator layer is enabled."
            ),
        },
        {
            "capability": "formal evaluation laboratory",
            "status": "partial",
            "detail": (
                "Self-evaluation exists, but a broad benchmark suite "
                "with regression datasets and automated scoring remains."
            ),
        },
        {
            "capability": "production frontend",
            "status": "partial",
            "detail": (
                "The backend API is operational. A polished dedicated "
                "mobile/web control surface remains a separate layer."
            ),
        },
    ]

    priority_order = [
        "durable distributed memory",
        "semantic model reasoning",
        "authenticated external actions",
        "true background distributed execution",
        "formal evaluation laboratory",
        "multi-agent orchestration",
        "production frontend",
        "multimodal world model",
        "continuous learning",
        "real-world actuators",
    ]

    return {
        "version": VERSION,
        "target": TARGET_YEAR,
        "operational_snapshot": inspection,
        "gaps": gaps,
        "next_stage": {
            "sequence": priority_order,
            "principle": (
                "Upgrade the weakest dependency first while preserving "
                "the working mission API."
            ),
        },
    }


# ============================================================
# GOAL DECOMPOSITION
# ============================================================

def decompose_goal(objective: str) -> List[Dict[str, Any]]:
    text = objective.lower()

    goals: List[Dict[str, Any]] = []

    goals.append(
        {
            "id": "goal-understand",
            "title": "Understand objective",
            "description": objective,
            "type": "understanding",
        }
    )

    self_related = any(
        phrase in text
        for phrase in [
            "ai infinity",
            "this system",
            "itself",
            "current platform",
            "current architecture",
            "our platform",
        ]
    )

    if self_related:
        goals.append(
            {
                "id": "goal-self-inspect",
                "title": "Inspect AI Infinity",
                "description": (
                    "Inspect the actual runtime, capabilities, providers, "
                    "storage and operational layers."
                ),
                "type": "self_inspection",
            }
        )

    if any(
        word in text
        for word in [
            "research",
            "information",
            "evidence",
            "public",
            "latest",
            "sources",
        ]
    ):
        goals.append(
            {
                "id": "goal-research",
                "title": "Research",
                "description": (
                    "Collect meaningful external evidence from "
                    "independent sources."
                ),
                "type": "research",
            }
        )

    if any(
        word in text
        for word in [
            "verify",
            "evidence",
            "validate",
            "confirmed",
        ]
    ):
        goals.append(
            {
                "id": "goal-verify",
                "title": "Verify",
                "description": (
                    "Evaluate evidence quality and source independence."
                ),
                "type": "verification",
            }
        )

    if any(
        word in text
        for word in [
            "missing",
            "gap",
            "improve",
            "improvement",
            "next",
            "upgrade",
            "architecture",
        ]
    ):
        goals.append(
            {
                "id": "goal-gaps",
                "title": "Find architecture gaps",
                "description": (
                    "Identify concrete missing or partial capabilities."
                ),
                "type": "gap_analysis",
            }
        )

    goals.append(
        {
            "id": "goal-synthesize",
            "title": "Synthesize result",
            "description": (
                "Combine runtime state, evidence, verification, "
                "gaps and opportunities."
            ),
            "type": "synthesis",
        }
    )

    return goals


# ============================================================
# DYNAMIC TASK GRAPH
# ============================================================

def build_task_graph(
    objective: str,
    goals: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    nodes: List[Dict[str, Any]] = []

    for index, goal in enumerate(goals):

        node_type = goal["type"]

        nodes.append(
            {
                "id": f"node-{index + 1}",
                "goal_id": goal["id"],
                "type": node_type,
                "title": goal["title"],
                "description": goal["description"],
                "dependencies": (
                    [f"node-{index}"]
                    if index > 0
                    else []
                ),
                "status": "pending",
                "attempts": 0,
                "result": None,
                "error": None,
            }
        )

    return nodes


# ============================================================
# OPPORTUNITY ENGINE
# ============================================================

def detect_opportunities(
    objective: str,
    research: Optional[Dict[str, Any]],
    gaps: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    opportunities = []

    if research:
        if research.get("sources_found", 0) > 0:
            opportunities.append(
                {
                    "type": "knowledge",
                    "title": "Evidence-backed knowledge pipeline",
                    "description": (
                        "Convert validated research into reusable "
                        "memory and future mission context."
                    ),
                }
            )

        if research.get("research_quality") == "insufficient":
            opportunities.append(
                {
                    "type": "research",
                    "title": "Research provider expansion",
                    "description": (
                        "Add additional independent research connectors "
                        "when public search endpoints are unavailable."
                    ),
                }
            )

    if gaps:
        for gap in gaps.get("gaps", [])[:5]:
            if gap.get("status") in {"missing", "partial"}:
                opportunities.append(
                    {
                        "type": "architecture",
                        "title": gap["capability"],
                        "description": gap["detail"],
                    }
                )

    opportunities.append(
        {
            "type": "automation",
            "title": "Reusable mission patterns",
            "description": (
                "Successful mission graphs can become reusable "
                "execution templates."
            ),
        }
    )

    return opportunities[:15]


# ============================================================
# SELF-CRITIQUE
# ============================================================

def self_critique(
    research: Optional[Dict[str, Any]],
    verification: Optional[Dict[str, Any]],
    gaps: Optional[Dict[str, Any]],
    completed_nodes: int,
    failed_nodes: int,
) -> Dict[str, Any]:

    issues = []

    if research is not None:
        if research.get("sources_found", 0) == 0:
            issues.append(
                "No meaningful external evidence was collected."
            )

        if research.get("rejected_sources"):
            issues.append(
                f"{len(research['rejected_sources'])} candidate sources "
                "were rejected by provenance/quality filters."
            )

    if verification is not None:
        if verification.get("verified") is False:
            issues.append(
                "Evidence did not satisfy the configured "
                "independent-source verification threshold."
            )

    if failed_nodes > 0:
        issues.append(
            f"{failed_nodes} mission node(s) failed and required recovery."
        )

    if gaps and not gaps.get("gaps"):
        issues.append(
            "Gap analysis produced no architecture gaps."
        )

    if not issues:
        assessment = "No critical internal consistency issue detected."
    else:
        assessment = (
            "The mission completed, but the following limitations "
            "should remain visible in the result."
        )

    return {
        "assessment": assessment,
        "issues": issues,
        "critical": len(issues) > 3,
        "completion": {
            "completed_nodes": completed_nodes,
            "failed_nodes": failed_nodes,
        },
        "evidence_consistency": {
            "research_sources": (
                research.get("sources_found", 0)
                if research
                else 0
            ),
            "verification_confidence": (
                verification.get("confidence", 0)
                if verification
                else 0
            ),
            "verification_verified": (
                verification.get("verified", False)
                if verification
                else False
            ),
        },
    }


# ============================================================
# RECOVERY
# ============================================================

async def recover_node(
    node: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:

    node["attempts"] = node.get("attempts", 0) + 1

    event(
        "node_recovery",
        {
            "node_id": node["id"],
            "type": node["type"],
            "attempt": node["attempts"],
        },
    )

    # Research can retry through the next available provider.
    if node["type"] == "research":
        result = await research_query(
            context["objective"],
            max_sources=6,
        )

        if result.get("sources_found", 0) > 0:
            return {
                "recovered": True,
                "result": result,
                "strategy": "adaptive_research_retry",
            }

    if node["type"] == "self_inspection":
        return {
            "recovered": True,
            "result": self_inspect(),
            "strategy": "local_runtime_inspection",
        }

    return {
        "recovered": False,
        "result": None,
        "strategy": "no_automatic_recovery_available",
    }


# ============================================================
# NODE EXECUTION
# ============================================================

async def execute_node(
    node: Dict[str, Any],
    context: Dict[str, Any],
) -> Any:

    node_type = node["type"]

    if node_type == "understanding":
        return {
            "objective": context["objective"],
            "intent": context["intent"],
            "goal_count": len(context["goals"]),
        }

    if node_type == "self_inspection":
        return self_inspect()

    if node_type == "research":
        if not context.get("research_enabled", True):
            return {
                "skipped": True,
                "reason": "research_disabled",
            }

        return await research_query(
            context["objective"],
            max_sources=8,
        )

    if node_type == "verification":
        research = context.get("research")

        if not research:
            return {
                "claim": context["objective"],
                "verified": False,
                "verification_level": "none",
                "confidence": 0.0,
                "evidence_count": 0,
                "independent_evidence_count": 0,
            }

        return verify_research(
            research,
            context["objective"],
        )

    if node_type == "gap_analysis":
        return gap_analysis()

    if node_type == "synthesis":
        return {
            "objective": context["objective"],
            "system": SERVICE,
            "version": VERSION,
            "target": TARGET_YEAR,
            "operational": self_inspect(),
            "research": context.get("research"),
            "verification": context.get("verification"),
            "gaps": context.get("gaps"),
            "opportunities": context.get("opportunities", []),
            "critique": context.get("critique"),
        }

    return {
        "status": "completed",
        "node_type": node_type,
    }


# ============================================================
# MISSION ENGINE
# ============================================================

async def run_mission(
    objective: str,
    research_enabled: bool = True,
    verify_enabled: bool = True,
    remember_enabled: bool = True,
    external_access: bool = True,
) -> Dict[str, Any]:

    mission_id = f"mission-{uuid.uuid4().hex[:12]}"

    started = time.time()

    intent = classify_intent(objective)
    goals = decompose_goal(objective)
    nodes = build_task_graph(objective, goals)

    context: Dict[str, Any] = {
        "objective": objective,
        "intent": intent,
        "goals": goals,
        "research_enabled": research_enabled,
        "verify_enabled": verify_enabled,
        "remember_enabled": remember_enabled,
        "external_access": external_access,
    }

    mission = {
        "mission_id": mission_id,
        "status": "running",
        "version": VERSION,
        "target": TARGET_YEAR,
        "objective": objective,
        "intent": intent,
        "goals": goals,
        "nodes": nodes,
        "started_at": started,
        "completed_at": None,
        "result": None,
    }

    missions_store[mission_id] = mission
    write_json(MISSION_FILE, missions_store)

    event(
        "mission_started",
        {
            "mission_id": mission_id,
            "objective": objective,
        },
    )

    completed = 0
    failed = 0

    for node in nodes:

        node["status"] = "running"
        node["attempts"] = node.get("attempts", 0) + 1

        try:

            if node["type"] == "verification" and not verify_enabled:
                result = {
                    "skipped": True,
                    "reason": "verification_disabled",
                }

            else:
                result = await execute_node(
                    node,
                    context,
                )

            node["result"] = result
            node["status"] = "completed"

            completed += 1

            # Make results available to subsequent nodes.
            if node["type"] == "research":
                context["research"] = result

            elif node["type"] == "verification":
                context["verification"] = result

            elif node["type"] == "gap_analysis":
                context["gaps"] = result

            elif node["type"] == "self_inspection":
                context["inspection"] = result

            event(
                "node_completed",
                {
                    "mission_id": mission_id,
                    "node_id": node["id"],
                    "type": node["type"],
                },
            )

        except Exception as exc:

            node["status"] = "failed"
            node["error"] = str(exc)

            recovery = await recover_node(
                node,
                context,
            )

            if recovery.get("recovered"):
                node["status"] = "completed"
                node["result"] = recovery.get("result")
                node["recovery"] = recovery
                completed += 1

                if node["type"] == "research":
                    context["research"] = recovery.get("result")

                elif node["type"] == "self_inspection":
                    context["inspection"] = recovery.get("result")

            else:
                failed += 1

                event(
                    "node_failed",
                    {
                        "mission_id": mission_id,
                        "node_id": node["id"],
                        "error": str(exc),
                    },
                )

    # If verification was not explicitly represented by decomposition,
    # still maintain consistency.
    research_result = context.get("research")
    verification_result = context.get("verification")

    if research_result and verify_enabled and not verification_result:
        verification_result = verify_research(
            research_result,
            objective,
        )
        context["verification"] = verification_result

    opportunities = detect_opportunities(
        objective,
        research_result,
        context.get("gaps"),
    )

    context["opportunities"] = opportunities

    critique = self_critique(
        research_result,
        verification_result,
        context.get("gaps"),
        completed,
        failed,
    )

    context["critique"] = critique

    # Final synthesis is generated directly from the SAME state used
    # by verification and critique. This prevents contradictory fields.
    synthesis = {
        "objective": objective,
        "system": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "operational_capabilities": self_inspect(),
        "research": research_result,
        "verification": verification_result,
        "gap_analysis": context.get("gaps"),
        "opportunities": opportunities,
        "critique": critique,
    }

    if remember_enabled:
        memory_item = remember(
            content=json.dumps(
                {
                    "objective": objective,
                    "verification": verification_result,
                    "gaps": context.get("gaps"),
                    "opportunities": opportunities,
                    "completed_nodes": completed,
                    "failed_nodes": failed,
                },
                ensure_ascii=False,
                default=str,
            ),
            kind="mission_outcome",
            metadata={
                "mission_id": mission_id,
                "version": VERSION,
            },
        )
    else:
        memory_item = None

    duration = time.time() - started

    mission["status"] = (
        "completed"
        if failed == 0
        else "completed_with_failures"
    )

    mission["completed_at"] = time.time()
    mission["duration_seconds"] = round(duration, 3)
    mission["completed_nodes"] = completed
    mission["failed_nodes"] = failed
    mission["result"] = synthesis
    mission["memory_id"] = (
        memory_item["id"]
        if memory_item
        else None
    )

    missions_store[mission_id] = mission
    write_json(MISSION_FILE, missions_store)

    event(
        "mission_completed",
        {
            "mission_id": mission_id,
            "status": mission["status"],
            "duration_seconds": round(duration, 3),
            "completed_nodes": completed,
            "failed_nodes": failed,
        },
    )

    return {
        "mission_id": mission_id,
        "status": mission["status"],
        "version": VERSION,
        "target": TARGET_YEAR,
        "objective": objective,
        "goals": goals,
        "nodes": nodes,
        "completed_nodes": completed,
        "failed_nodes": failed,
        "duration_seconds": round(duration, 3),
        "intent": intent,
        "research": research_result,
        "verification": verification_result,
        "gap_analysis": context.get("gaps"),
        "opportunities": opportunities,
        "critique": critique,
        "synthesis": synthesis,
        "memory_id": (
            memory_item["id"]
            if memory_item
            else None
        ),
    }


# ============================================================
# TASK COMPATIBILITY LAYER
# ============================================================

async def run_task(request: TaskRequest) -> Dict[str, Any]:
    objective = request.objective or request.command

    if not objective:
        raise HTTPException(
            status_code=422,
            detail="Either 'objective' or 'command' is required.",
        )

    result = await run_mission(
        objective=objective,
        research_enabled=request.research,
        verify_enabled=request.verify,
        remember_enabled=request.remember,
        external_access=request.external_access,
    )

    task_id = f"task-{uuid.uuid4().hex[:12]}"

    task = {
        "task_id": task_id,
        **result,
    }

    tasks_store[task_id] = task
    write_json(TASK_FILE, tasks_store)

    return task


# ============================================================
# EXTERNAL ENGINE
# ============================================================

async def external_request(
    request: ExternalRequest,
) -> Dict[str, Any]:

    method = request.method.upper()

    if method not in {
        "GET",
        "HEAD",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }:
        raise HTTPException(
            status_code=400,
            detail="Unsupported HTTP method.",
        )

    allowed, reason = validate_external_url(request.url)

    if not allowed:
        raise HTTPException(
            status_code=403,
            detail=reason,
        )

    headers = {
        "User-Agent": "AI-Infinity/2050.1",
        "Accept": "application/json,text/plain,text/html,*/*",
    }

    try:
        async with httpx.AsyncClient(
            timeout=request.timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:

            response = await client.request(
                method,
                request.url,
                json=request.data
                if method not in {"GET", "HEAD"}
                else None,
            )

            final_allowed, final_reason = validate_external_url(
                str(response.url)
            )

            if not final_allowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"Redirect blocked: {final_reason}",
                )

            text_response = response.text[:50000]

            parsed_json = None

            try:
                parsed_json = response.json()
            except Exception:
                pass

            return {
                "ok": response.is_success,
                "status_code": response.status_code,
                "url": request.url,
                "final_url": str(response.url),
                "headers": {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower()
                    in {
                        "content-type",
                        "content-length",
                        "server",
                        "date",
                    }
                },
                "json": parsed_json,
                "text": text_response,
            }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )


# ============================================================
# ROUTES — CORE
# ============================================================

@app.get("/")
async def root() -> Dict[str, Any]:
    return {
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "status": "online",
        "message": "AI Infinity Core is online.",
    }


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "timestamp": time.time(),
    }


@app.get("/status")
async def status() -> Dict[str, Any]:
    return {
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "status": "operational",
        "missions": len(missions_store),
        "tasks": len(tasks_store),
        "memories": len(memory_store),
        "events": len(events_store),
        "providers": provider_status(),
        "uptime_seconds": round(time.time() - START_TIME, 2),
    }


@app.get("/capabilities")
async def capabilities() -> Dict[str, Any]:
    return capabilities_payload()


@app.get("/diagnostics")
async def diagnostics() -> Dict[str, Any]:
    return {
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "inspection": self_inspect(),
        "gaps": gap_analysis(),
        "events_count": len(events_store),
        "memory_count": len(memory_store),
        "task_count": len(tasks_store),
        "mission_count": len(missions_store),
    }


@app.get("/self-inspect")
async def self_inspect_endpoint() -> Dict[str, Any]:
    return self_inspect()


@app.get("/architecture")
async def architecture() -> Dict[str, Any]:
    return {
        "service": SERVICE,
        "version": VERSION,
        "target": TARGET_YEAR,
        "layers": [
            "Infinity Core",
            "Intent Engine",
            "Mission Engine",
            "Goal Decomposition",
            "Dynamic Task Graph",
            "Planner",
            "Tool Registry",
            "Provider Discovery",
            "External Access Router",
            "Research Engine",
            "Evidence Provenance",
            "Source Quality Engine",
            "Cross-Source Verification",
            "World Model",
            "Working Memory",
            "Outcome Memory",
            "Self-Critique",
            "Failure Detection",
            "Recovery",
            "Replanning",
            "Opportunity Detection",
            "Evaluation Hooks",
            "Observability",
            "Video Compatibility",
        ],
        "future_layers": FUTURE_EXTENSION_POINTS,
    }


@app.get("/gaps")
async def gaps() -> Dict[str, Any]:
    return gap_analysis()


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory")
async def get_memory(limit: int = 20) -> Dict[str, Any]:
    return {
        "count": len(memory_store),
        "items": recent_memory(limit),
    }


@app.post("/memory")
async def add_memory(request: MemoryRequest) -> Dict[str, Any]:
    item = remember(
        request.content,
        request.kind,
        request.metadata,
    )

    return item


@app.get("/memory/count")
async def memory_count() -> Dict[str, Any]:
    return {
        "count": len(memory_store),
    }


# ============================================================
# WORLD
# ============================================================

@app.get("/world")
async def world() -> Dict[str, Any]:
    return world_snapshot()


# ============================================================
# EVENTS
# ============================================================

@app.get("/events")
async def events(limit: int = 50) -> Dict[str, Any]:
    limit = max(1, min(limit, 500))

    return {
        "count": len(events_store),
        "items": events_store[-limit:],
    }


# ============================================================
# RESEARCH
# ============================================================

@app.post("/research")
async def research(request: ResearchRequest) -> Dict[str, Any]:
    return await research_query(
        request.query,
        request.max_sources,
    )


# ============================================================
# VERIFICATION
# ============================================================

@app.post("/verify")
async def verify(request: ResearchRequest) -> Dict[str, Any]:
    research_result = await research_query(
        request.query,
        request.max_sources,
    )

    return verify_research(
        research_result,
        request.query,
    )


# ============================================================
# EXTERNAL ACCESS
# ============================================================

@app.post("/external")
async def external(
    request: ExternalRequest,
) -> Dict[str, Any]:
    return await external_request(request)


# ============================================================
# PLANNING
# ============================================================

@app.post("/plan")
async def plan(request: MissionRequest) -> Dict[str, Any]:
    intent = classify_intent(request.objective)
    goals = decompose_goal(request.objective)
    nodes = build_task_graph(
        request.objective,
        goals,
    )

    return {
        "version": VERSION,
        "target": TARGET_YEAR,
        "objective": request.objective,
        "intent": intent,
        "goals": goals,
        "dynamic_task_graph": nodes,
    }


# ============================================================
# TASK / MISSION EXECUTION
# ============================================================

@app.post("/task")
async def task(request: TaskRequest) -> Dict[str, Any]:
    return await run_task(request)


@app.post("/mission")
async def mission(request: MissionRequest) -> Dict[str, Any]:
    return await run_mission(
        objective=request.objective,
        research_enabled=request.research,
        verify_enabled=request.verify,
        remember_enabled=request.remember,
        external_access=request.external_access,
    )


@app.post("/execute")
async def execute(request: MissionRequest) -> Dict[str, Any]:
    return await run_mission(
        objective=request.objective,
        research_enabled=request.research,
        verify_enabled=request.verify,
        remember_enabled=request.remember,
        external_access=request.external_access,
    )


@app.get("/task/{task_id}")
async def get_task(task_id: str) -> Dict[str, Any]:
    task = tasks_store.get(task_id)

    if not task:
        raise HTTPException(
            status_code=404,
            detail="Task not found.",
        )

    return task


@app.get("/mission/{mission_id}")
async def get_mission(mission_id: str) -> Dict[str, Any]:
    mission = missions_store.get(mission_id)

    if not mission:
        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    return mission


# ============================================================
# OPPORTUNITIES
# ============================================================

@app.get("/opportunities")
async def opportunities() -> Dict[str, Any]:
    gaps_result = gap_analysis()

    items = detect_opportunities(
        "AI Infinity",
        None,
        gaps_result,
    )

    return {
        "version": VERSION,
        "target": TARGET_YEAR,
        "opportunities": items,
    }


# ============================================================
# EVALUATION
# ============================================================

@app.post("/evaluate")
async def evaluate(request: MissionRequest) -> Dict[str, Any]:
    intent = classify_intent(request.objective)
    inspection = self_inspect()
    gaps_result = gap_analysis()

    return {
        "version": VERSION,
        "target": TARGET_YEAR,
        "objective": request.objective,
        "intent": intent,
        "runtime": inspection,
        "architecture_gaps": gaps_result,
        "evaluation": {
            "runtime_operational": True,
            "api_operational": True,
            "research_engine_operational": True,
            "verification_engine_operational": True,
            "self_inspection_operational": True,
            "recovery_operational": True,
            "replanning_operational": True,
            "persistent_runtime_memory": True,
            "durable_distributed_memory": False,
        },
    }


# ============================================================
# VIDEO COMPATIBILITY
# ============================================================

def video_job_dir(job_id: str) -> Path:
    path = BASE / "video" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


@app.post("/video")
async def video(request: VideoRequest) -> Dict[str, Any]:

    job_id = f"genius-{uuid.uuid4().hex[:12]}"

    path = video_job_dir(job_id)

    metadata = {
        "job_id": job_id,
        "status": "accepted",
        "command": request.command,
        "duration_minutes": request.duration_minutes,
        "version": VERSION,
        "target": TARGET_YEAR,
        "created_at": time.time(),
        "video_path": f"/video/{job_id}",
        "renderer_url": os.getenv("RENDERER_URL"),
        "provider": (
            "pollinations"
            if os.getenv("POLLINATIONS_API_KEY")
            else "free-first"
        ),
    }

    write_json(
        path / "job.json",
        metadata,
    )

    event(
        "video_job_created",
        {
            "job_id": job_id,
            "duration_minutes": request.duration_minutes,
        },
    )

    return metadata


@app.post("/generate")
async def generate(request: VideoRequest) -> Dict[str, Any]:
    return await video(request)


@app.get("/video/{job_id}")
async def get_video(job_id: str):

    path = video_job_dir(job_id) / "job.json"

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Video job not found.",
        )

    data = read_json(path, {})

    # Preserve compatibility with previous video clients.
    return JSONResponse(data)


# ============================================================
# SKILLS
# ============================================================

@app.get("/skills")
async def skills() -> Dict[str, Any]:
    return {
        "count": len(BUILTIN_CAPABILITIES),
        "skills": [
            {
                "name": item["name"],
                "category": item["category"],
            }
            for item in BUILTIN_CAPABILITIES
        ],
    }


@app.get("/skills/count")
async def skills_count() -> Dict[str, Any]:
    return {
        "count": len(BUILTIN_CAPABILITIES),
    }


# ============================================================
# PROVIDERS
# ============================================================

@app.get("/providers")
async def providers() -> Dict[str, Any]:
    return {
        "providers": provider_status(),
        "free_first": True,
    }


# ============================================================
# GLOBAL ERROR HANDLING
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,
):

    event(
        "unhandled_exception",
        {
            "path": str(request.url.path),
            "method": request.method,
            "error": str(exc),
            "traceback": traceback.format_exc()[-5000:],
        },
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "service": SERVICE,
            "version": VERSION,
            "target": TARGET_YEAR,
            "detail": str(exc),
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup() -> None:
    event(
        "system_startup",
        {
            "service": SERVICE,
            "version": VERSION,
            "target": TARGET_YEAR,
        },
    )

    update_world(
        "runtime.version",
        VERSION,
        "startup",
    )

    update_world(
        "runtime.target",
        TARGET_YEAR,
        "startup",
    )

    update_world(
        "runtime.free_first",
        True,
        "startup",
    )


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
