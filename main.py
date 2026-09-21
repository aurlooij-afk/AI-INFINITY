"""
AI Infinity
TARGET-2050.72
BUILD: SELF-CONSISTENT-MISSION-CONTRACT-AND-RESEARCH-RECOVERY-CORE

Self-contained FastAPI service.
Fixes the /run Swagger contract while preserving the 2050.71
transport, evidence, recovery, and truthful-completion architecture.
"""

from __future__ import annotations

import asyncio
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

from html import unescape
from typing import Any, Optional
from urllib.parse import quote_plus, urljoin, urlparse
from urllib.request import (
    Request as URLRequest,
    build_opener,
    HTTPRedirectHandler,
    ProxyHandler,
    urlopen,
)
from urllib.error import HTTPError, URLError

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


# ============================================================
# CORE IDENTITY
# ============================================================

VERSION = "TARGET-2050.72"
BUILD = "SELF-CONSISTENT-MISSION-CONTRACT-AND-RESEARCH-RECOVERY-CORE"

DATA_DIR = os.environ.get(
    "AI_INFINITY_DATA_DIR",
    "/tmp/ai-infinity",
)

DB_PATH = os.path.join(
    DATA_DIR,
    "ai_infinity.db",
)

MAX_BODY = 1_500_000
FETCH_TIMEOUT = 10.0
MAX_SOURCES = 8
MAX_REDIRECTS = 4

LOCK = threading.RLock()


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "AI Infinity autonomous research, evidence, "
        "verification, transport classification and recovery core."
    ),
)


# ============================================================
# REQUEST CONTRACTS
# IMPORTANT:
# These typed Pydantic models make Swagger expose request bodies.
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=20000,
        description="Mission objective",
    )


class TransportRequest(BaseModel):
    http_status: Optional[int] = Field(
        None,
        ge=100,
        le=599,
    )

    content_type: Optional[str] = None

    body: Optional[str] = None

    headers: dict[str, str] = Field(
        default_factory=dict,
    )


class MissionCreateRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )


# ============================================================
# UTILITIES
# ============================================================

def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def digest(value: Any) -> str:
    if isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode(
            "utf-8",
            "ignore",
        )

    return hashlib.sha256(raw).hexdigest()


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    os.makedirs(
        DATA_DIR,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_db() -> None:
    with LOCK, db() as connection:

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                result_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                event TEXT NOT NULL,
                phase TEXT,
                detail_json TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transport_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                url TEXT,
                classification TEXT NOT NULL,
                detail_json TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                url TEXT NOT NULL,
                domain TEXT NOT NULL,
                title TEXT,
                text TEXT,
                content_digest TEXT,
                source_family TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                claim TEXT NOT NULL,
                evidence_count INTEGER NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS contradictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                statement TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_evidence_mission
            ON evidence(mission_id);

            CREATE INDEX IF NOT EXISTS idx_events_mission
            ON workflow_events(mission_id);

            CREATE INDEX IF NOT EXISTS idx_transport_mission
            ON transport_events(mission_id);
            """
        )


# ============================================================
# EVENT PERSISTENCE
# ============================================================

def event(
    mission_id: str,
    name: str,
    phase: str,
    detail: Any = None,
) -> None:

    detail_json = None

    if detail is not None:
        detail_json = json.dumps(
            detail,
            ensure_ascii=False,
            default=str,
        )

    with LOCK, db() as connection:

        connection.execute(
            """
            INSERT INTO workflow_events
            (
                mission_id,
                event,
                phase,
                detail_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                name,
                phase,
                detail_json,
                now(),
            ),
        )


def transport_event(
    mission_id: Optional[str],
    url: str,
    result: dict[str, Any],
) -> None:

    with LOCK, db() as connection:

        connection.execute(
            """
            INSERT INTO transport_events
            (
                mission_id,
                url,
                classification,
                detail_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                url,
                result["classification"],
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                now(),
            ),
        )


# ============================================================
# MISSIONS
# ============================================================

def create_mission(
    objective: str,
) -> str:

    mission_id = uid("mission")
    timestamp = now()

    with LOCK, db() as connection:

        connection.execute(
            """
            INSERT INTO missions
            (
                id,
                objective,
                status,
                phase,
                result_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                objective,
                "queued",
                "queued",
                None,
                timestamp,
                timestamp,
            ),
        )

    event(
        mission_id,
        "mission_created",
        "queued",
        {
            "objective_length": len(objective),
        },
    )

    return mission_id


def update_mission(
    mission_id: str,
    status: str,
    phase: str,
    result: Any = None,
) -> None:

    result_json = None

    if result is not None:
        result_json = json.dumps(
            result,
            ensure_ascii=False,
            default=str,
        )

    with LOCK, db() as connection:

        connection.execute(
            """
            UPDATE missions
            SET
                status = ?,
                phase = ?,
                result_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                phase,
                result_json,
                now(),
                mission_id,
            ),
        )


def get_mission(
    mission_id: str,
) -> Optional[dict[str, Any]]:

    with LOCK, db() as connection:

        row = connection.execute(
            """
            SELECT *
            FROM missions
            WHERE id = ?
            """,
            (mission_id,),
        ).fetchone()

    if not row:
        return None

    result = dict(row)

    result["result"] = (
        json.loads(result.pop("result_json"))
        if result.get("result_json")
        else None
    )

    return result


# ============================================================
# PUBLIC NETWORK SECURITY
# ============================================================

def is_public_ip(
    ip: str,
) -> bool:

    try:
        address = ipaddress.ip_address(ip)

        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )

    except ValueError:
        return False


def validate_url(
    url: str,
) -> tuple[bool, str]:

    try:
        parsed = urlparse(url)

        if parsed.scheme not in (
            "http",
            "https",
        ):
            return (
                False,
                "Only http/https URLs are allowed.",
            )

        if not parsed.hostname:
            return (
                False,
                "URL hostname is missing.",
            )

        if parsed.username or parsed.password:
            return (
                False,
                "Credential-bearing URLs are blocked.",
            )

        host = parsed.hostname.rstrip(".").lower()

        if host in {
            "localhost",
            "localhost.localdomain",
        }:
            return (
                False,
                "Local hostnames are blocked.",
            )

        if host.endswith(".local"):
            return (
                False,
                "Local domains are blocked.",
            )

        try:
            infos = socket.getaddrinfo(
                host,
                parsed.port
                or (
                    443
                    if parsed.scheme == "https"
                    else 80
                ),
                type=socket.SOCK_STREAM,
            )

        except OSError as exc:
            return (
                False,
                f"DNS resolution failed: {exc}",
            )

        addresses = {
            info[4][0]
            for info in infos
        }

        if not addresses:
            return (
                False,
                "No address resolved.",
            )

        if not all(
            is_public_ip(address)
            for address in addresses
        ):
            return (
                False,
                "URL resolves to a non-public address.",
            )

        return True, "ok"

    except Exception as exc:
        return False, str(exc)


# ============================================================
# SAFE HTTP
# ============================================================

class NoRedirect(
    HTTPRedirectHandler
):

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        return None


def fetch_public(
    url: str,
) -> dict[str, Any]:

    current = url

    opener = build_opener(
        ProxyHandler({}),
        NoRedirect(),
    )

    for hop in range(
        MAX_REDIRECTS + 1
    ):

        valid, reason = validate_url(
            current
        )

        if not valid:
            return {
                "url": current,
                "ok": False,
                "error": reason,
                "http_status": None,
                "headers": {},
                "body": "",
            }

        request = URLRequest(
            current,
            headers={
                "User-Agent": (
                    "AI-Infinity/2050.72 research"
                ),
                "Accept": (
                    "text/html, "
                    "text/plain, "
                    "application/xhtml+xml, "
                    "application/json"
                ),
            },
        )

        try:

            with opener.open(
                request,
                timeout=FETCH_TIMEOUT,
            ) as response:

                status = getattr(
                    response,
                    "status",
                    200,
                )

                headers = {
                    key.lower(): value
                    for key, value
                    in response.headers.items()
                }

                body = response.read(
                    MAX_BODY + 1
                )

                if len(body) > MAX_BODY:
                    body = body[:MAX_BODY]

                return {
                    "url": current,
                    "ok": True,
                    "http_status": status,
                    "headers": headers,
                    "body": body.decode(
                        "utf-8",
                        "replace",
                    ),
                }

        except HTTPError as exc:

            headers = (
                {
                    key.lower(): value
                    for key, value
                    in exc.headers.items()
                }
                if exc.headers
                else {}
            )

            body = ""

            if exc.fp:
                try:
                    body = exc.read(
                        MAX_BODY + 1
                    ).decode(
                        "utf-8",
                        "replace",
                    )
                except Exception:
                    body = ""

            if (
                exc.code
                in (
                    301,
                    302,
                    303,
                    307,
                    308,
                )
                and exc.headers.get(
                    "Location"
                )
                and hop < MAX_REDIRECTS
            ):
                current = urljoin(
                    current,
                    exc.headers["Location"],
                )
                continue

            return {
                "url": current,
                "ok": False,
                "http_status": exc.code,
                "headers": headers,
                "body": body,
                "error": str(exc),
            }

        except (
            URLError,
            TimeoutError,
            OSError,
        ) as exc:

            return {
                "url": current,
                "ok": False,
                "http_status": None,
                "headers": {},
                "body": "",
                "error": str(exc),
            }

        except Exception as exc:

            return {
                "url": current,
                "ok": False,
                "http_status": None,
                "headers": {},
                "body": "",
                "error": str(exc),
            }

    return {
        "url": current,
        "ok": False,
        "http_status": None,
        "headers": {},
        "body": "",
        "error": "redirect limit exceeded",
    }


# ============================================================
# TRANSPORT CLASSIFICATION
# ============================================================

def classify_transport_payload(
    http_status: Optional[int],
    content_type: Optional[str],
    body: Optional[str],
    headers: dict[str, str],
) -> dict[str, Any]:

    text = (
        body or ""
    )[:MAX_BODY]

    lowered = text.lower()

    content_type_value = (
        content_type
        or headers.get(
            "content-type",
            "",
        )
    ).lower()

    markers = (
        "waf",
        "request blocked",
        "access denied",
        "forbidden",
        "cloudflare",
        "captcha",
        "web application firewall",
        "blocked",
    )

    edge_block = (
        http_status
        in (
            401,
            403,
            406,
            429,
        )
        and any(
            marker in lowered
            for marker in markers
        )
    )

    if edge_block:

        classification = "EDGE_WAF_BLOCK"

        reason = (
            "HTTP edge/WAF block page detected; "
            "response is not research evidence."
        )

    elif (
        http_status is not None
        and http_status >= 500
    ):

        classification = "UPSTREAM_5XX"

        reason = (
            "Upstream server failure; "
            "response is not research evidence."
        )

    elif not text.strip():

        classification = "EMPTY_RESPONSE"

        reason = (
            "Empty response is not research evidence."
        )

    elif (
        "text/html" in content_type_value
        and any(
            marker in lowered
            for marker in markers
        )
    ):

        classification = "BLOCKED_HTML"

        reason = (
            "HTML block/interstitial detected; "
            "response is not research evidence."
        )

    elif content_type_value.startswith(
        (
            "image/",
            "audio/",
            "video/",
            "application/octet-stream",
        )
    ):

        classification = "OPAQUE_ASSET"

        reason = (
            "Opaque binary asset is not research evidence."
        )

    elif (
        http_status is not None
        and 200 <= http_status < 400
        and text.strip()
    ):

        classification = "VALID_PUBLIC_CONTENT"

        reason = (
            "Public response contains usable textual content."
        )

    else:

        classification = "UNVERIFIED_RESPONSE"

        reason = (
            "Response could not be safely classified "
            "as research evidence."
        )

    usable = (
        classification
        == "VALID_PUBLIC_CONTENT"
    )

    return {
        "classification": classification,
        "edge_failure": classification
        in {
            "EDGE_WAF_BLOCK",
            "BLOCKED_HTML",
        },
        "application_failure": (
            classification
            == "UPSTREAM_5XX"
        ),
        "is_evidence": usable,
        "usable_for_research": usable,
        "http_status": http_status,
        "content_type": content_type,
        "reason": reason,
        "digest": digest(text),
    }


# ============================================================
# CONTENT EXTRACTION
# ============================================================

def strip_html(
    html: str,
) -> str:

    html = re.sub(
        r"(?is)"
        r"<script[^>]*>.*?</script>"
        r"|<style[^>]*>.*?</style>"
        r"|<noscript[^>]*>.*?</noscript>",
        " ",
        html,
    )

    html = re.sub(
        r"(?s)<[^>]+>",
        " ",
        html,
    )

    return re.sub(
        r"\s+",
        " ",
        unescape(html),
    ).strip()


def extract_title(
    html: str,
) -> str:

    match = re.search(
        r"(?is)<title[^>]*>(.*?)</title>",
        html,
    )

    if not match:
        return ""

    return re.sub(
        r"\s+",
        " ",
        unescape(
            strip_html(
                match.group(1)
            )
        ),
    )[:500]


# ============================================================
# RESEARCH DISCOVERY
# ============================================================

def search_duckduckgo(
    query: str,
) -> list[str]:

    url = (
        "https://html.duckduckgo.com/html/?q="
        + quote_plus(query[:1000])
    )

    result = fetch_public(url)

    if not result.get("ok"):
        return []

    html = result.get(
        "body",
        "",
    )

    found: list[str] = []

    for raw in re.findall(
        r'href=["\']([^"\']+)["\']',
        html,
        re.I,
    ):

        candidate = unescape(raw)

        if (
            candidate.startswith("http")
            and "duckduckgo.com"
            not in urlparse(
                candidate
            ).netloc
        ):

            if candidate not in found:
                found.append(candidate)

        if len(found) >= MAX_SOURCES:
            break

    return found


def direct_urls(
    objective: str,
) -> list[str]:

    urls = re.findall(
        r"https?://[^\s<>\"']+",
        objective,
    )

    return [
        url.rstrip(".,);]")
        for url in urls
    ][:MAX_SOURCES]


# ============================================================
# EVIDENCE
# ============================================================

def add_evidence(
    mission_id: str,
    url: str,
    title: str,
    text: str,
    family: str,
) -> None:

    domain = (
        urlparse(url).hostname
        or ""
    ).lower()

    with LOCK, db() as connection:

        connection.execute(
            """
            INSERT INTO evidence
            (
                mission_id,
                url,
                domain,
                title,
                text,
                content_digest,
                source_family,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                url,
                domain,
                title,
                text[:12000],
                digest(text),
                family,
                now(),
            ),
        )


# ============================================================
# RESEARCH ENGINE
# ============================================================

def research_mission(
    mission_id: str,
    objective: str,
) -> dict[str, Any]:

    event(
        mission_id,
        "research_started",
        "research",
    )

    urls = direct_urls(
        objective
    )

    if not urls:
        urls = search_duckduckgo(
            objective
        )

    urls = list(
        dict.fromkeys(urls)
    )[:MAX_SOURCES]

    blocked: list[
        dict[str, Any]
    ] = []

    usable: list[
        dict[str, Any]
    ] = []

    for url in urls:

        fetched = fetch_public(
            url
        )

        response_headers = (
            fetched.get(
                "headers",
                {},
            )
        )

        transport = classify_transport_payload(
            fetched.get(
                "http_status"
            ),
            response_headers.get(
                "content-type"
            ),
            fetched.get(
                "body",
                "",
            ),
            response_headers,
        )

        transport_event(
            mission_id,
            url,
            transport,
        )

        if transport[
            "usable_for_research"
        ]:

            text = strip_html(
                fetched.get(
                    "body",
                    "",
                )
            )

            if len(text) >= 200:

                title = extract_title(
                    fetched.get(
                        "body",
                        "",
                    )
                )

                hostname = (
                    urlparse(url).hostname
                    or ""
                )

                pieces = hostname.split(
                    "."
                )

                family = (
                    pieces[-2]
                    if len(pieces) >= 2
                    else "unknown"
                )

                add_evidence(
                    mission_id,
                    url,
                    title,
                    text,
                    family,
                )

                usable.append(
                    {
                        "url": url,
                        "domain": hostname.lower(),
                        "title": title,
                        "digest": digest(text),
                    }
                )

            else:

                blocked.append(
                    {
                        "url": url,
                        "reason": (
                            "insufficient textual content"
                        ),
                        "transport": transport,
                    }
                )

        else:

            blocked.append(
                {
                    "url": url,
                    "reason": transport[
                        "reason"
                    ],
                    "transport": transport,
                }
            )

    with LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT domain
            FROM evidence
            WHERE mission_id = ?
            """,
            (mission_id,),
        ).fetchall()

    domains = sorted(
        {
            row["domain"]
            for row in rows
            if row["domain"]
        }
    )

    closure = (
        len(usable) >= 2
        and len(domains) >= 2
    )

    claims: list[
        dict[str, Any]
    ] = []

    if usable:

        claim = (
            f"Research collected "
            f"{len(usable)} independently reachable "
            f"textual source(s) for the mission."
        )

        with LOCK, db() as connection:

            connection.execute(
                """
                INSERT INTO claims
                (
                    mission_id,
                    claim,
                    evidence_count,
                    created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    mission_id,
                    claim,
                    len(usable),
                    now(),
                ),
            )

        claims.append(
            {
                "claim": claim,
                "evidence_count": len(usable),
            }
        )

    result = {
        "mode": "research",
        "closure": closure,
        "evidence_count": len(usable),
        "independent_domains": len(domains),
        "domains": domains,
        "sources": usable,
        "blocked_sources": blocked,
        "claims": claims,
        "next_actions": (
            []
            if closure
            else [
                "Retry with broader independent sources.",
                "Do not treat blocked or WAF responses as evidence.",
            ]
        ),
    }

    event(
        mission_id,
        (
            "research_closed"
            if closure
            else "research_needs_recovery"
        ),
        (
            "closed"
            if closure
            else "recovery"
        ),
        result,
    )

    return result


# ============================================================
# MISSION EXECUTION
# ============================================================

def execute_mission(
    mission_id: str,
    objective: str,
) -> None:

    try:

        update_mission(
            mission_id,
            "running",
            "planning",
        )

        event(
            mission_id,
            "mission_started",
            "planning",
        )

        result = research_mission(
            mission_id,
            objective,
        )

        if result["closure"]:

            update_mission(
                mission_id,
                "completed",
                "closed",
                result,
            )

            event(
                mission_id,
                "mission_completed",
                "closed",
                {
                    "closure": True,
                },
            )

        else:

            update_mission(
                mission_id,
                "needs_recovery",
                "recovery",
                result,
            )

            event(
                mission_id,
                "mission_recovery_required",
                "recovery",
                {
                    "closure": False,
                },
            )

    except Exception as exc:

        result = {
            "error": type(exc).__name__,
            "message": str(exc),
            "closure": False,
            "next_actions": [
                "Retry mission after inspecting workflow events."
            ],
        }

        update_mission(
            mission_id,
            "failed",
            "recovery",
            result,
        )

        event(
            mission_id,
            "mission_failed",
            "recovery",
            result,
        )


async def execute_mission_background(
    mission_id: str,
    objective: str,
) -> None:

    await asyncio.to_thread(
        execute_mission,
        mission_id,
        objective,
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup() -> None:

    init_db()

    with LOCK, db() as connection:

        stale = connection.execute(
            """
            SELECT id, objective
            FROM missions
            WHERE status IN ('queued', 'running')
            """
        ).fetchall()

    for row in stale:

        update_mission(
            row["id"],
            "needs_recovery",
            "recovery",
            {
                "reason": (
                    "Recovered after service restart."
                )
            },
        )

        event(
            row["id"],
            "startup_recovery",
            "recovery",
        )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root() -> dict[str, Any]:

    return {
        "name": "AI Infinity",
        "service": "AI Infinity",
        "status": "online",
        "version": VERSION,
        "build": BUILD,
        "docs": "/docs",
        "health": "/health",
        "run": "/run",
        "architecture": "/architecture",
    }


# ============================================================
# HEALTH
# ============================================================

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
            "completion_requires_closure": True,
        },
    }


# ============================================================
# READY
# ============================================================

@app.get("/ready")
def ready() -> dict[str, Any]:

    init_db()

    return {
        "ready": True,
        "version": VERSION,
        "build": BUILD,
    }


# ============================================================
# RUN
#
# CRITICAL FIX:
# request: RunRequest
#
# This makes Swagger expose:
# Request body
# {
#   "objective": "..."
# }
# ============================================================

@app.post("/run")
async def run(
    request: RunRequest,
) -> dict[str, Any]:

    objective = request.objective.strip()

    if not objective:

        raise HTTPException(
            status_code=422,
            detail="objective cannot be empty",
        )

    mission_id = create_mission(
        objective
    )

    asyncio.create_task(
        execute_mission_background(
            mission_id,
            objective,
        )
    )

    return {
        "mission_id": mission_id,
        "objective": objective,
        "status": "queued",
    }


# ============================================================
# MISSIONS
# ============================================================

@app.post("/missions")
async def create_mission_endpoint(
    request: MissionCreateRequest,
) -> dict[str, Any]:

    objective = request.objective.strip()

    mission_id = create_mission(
        objective
    )

    asyncio.create_task(
        execute_mission_background(
            mission_id,
            objective,
        )
    )

    return {
        "mission_id": mission_id,
        "status": "queued",
    }


@app.get("/mission/{mission_id}")
def mission(
    mission_id: str,
) -> dict[str, Any]:

    result = get_mission(
        mission_id
    )

    if not result:

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return result


@app.get("/missions")
def missions(
    limit: int = Query(
        20,
        ge=1,
        le=100,
    ),
) -> dict[str, Any]:

    with LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT
                id,
                objective,
                status,
                phase,
                created_at,
                updated_at
            FROM missions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {
        "missions": [
            dict(row)
            for row in rows
        ]
    }


@app.post("/mission/{mission_id}/retry")
async def retry_mission(
    mission_id: str,
) -> dict[str, Any]:

    mission_data = get_mission(
        mission_id
    )

    if not mission_data:

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    update_mission(
        mission_id,
        "queued",
        "queued",
        None,
    )

    event(
        mission_id,
        "manual_retry",
        "queued",
    )

    asyncio.create_task(
        execute_mission_background(
            mission_id,
            mission_data["objective"],
        )
    )

    return {
        "mission_id": mission_id,
        "status": "queued",
    }


# ============================================================
# SOURCES
# ============================================================

@app.get("/sources/{mission_id}")
def sources(
    mission_id: str,
) -> dict[str, Any]:

    if not get_mission(
        mission_id
    ):

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    with LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT
                url,
                domain,
                title,
                content_digest,
                source_family,
                created_at
            FROM evidence
            WHERE mission_id = ?
            ORDER BY id
            """,
            (mission_id,),
        ).fetchall()

    return {
        "mission_id": mission_id,
        "sources": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# EVIDENCE
# ============================================================

@app.get("/evidence/{mission_id}")
def evidence(
    mission_id: str,
) -> dict[str, Any]:

    if not get_mission(
        mission_id
    ):

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    with LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT
                url,
                domain,
                title,
                text,
                content_digest,
                source_family,
                created_at
            FROM evidence
            WHERE mission_id = ?
            ORDER BY id
            """,
            (mission_id,),
        ).fetchall()

    return {
        "mission_id": mission_id,
        "evidence": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# CLAIMS
# ============================================================

@app.get("/claims/{mission_id}")
def claims(
    mission_id: str,
) -> dict[str, Any]:

    if not get_mission(
        mission_id
    ):

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    with LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT
                claim,
                evidence_count,
                created_at
            FROM claims
            WHERE mission_id = ?
            ORDER BY id
            """,
            (mission_id,),
        ).fetchall()

    return {
        "mission_id": mission_id,
        "claims": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# TRANSPORT CLASSIFICATION
#
# IMPORTANT:
# This endpoint uses TransportRequest,
# NOT RunRequest.
# ============================================================

@app.post("/transport/classify")
def transport_classify(
    request: TransportRequest,
) -> dict[str, Any]:

    normalized_headers = {
        key.lower(): value
        for key, value
        in request.headers.items()
    }

    return classify_transport_payload(
        request.http_status,
        request.content_type,
        request.body,
        normalized_headers,
    )


# ============================================================
# TRANSPORT EVENTS
# ============================================================

@app.get("/transport/events")
def transport_events(
    mission_id: Optional[str] = None,
    limit: int = Query(
        50,
        ge=1,
        le=500,
    ),
) -> dict[str, Any]:

    with LOCK, db() as connection:

        if mission_id:

            rows = connection.execute(
                """
                SELECT *
                FROM transport_events
                WHERE mission_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (
                    mission_id,
                    limit,
                ),
            ).fetchall()

        else:

            rows = connection.execute(
                """
                SELECT *
                FROM transport_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    output = []

    for row in rows:

        item = dict(row)

        item["detail"] = (
            json.loads(
                item.pop("detail_json")
            )
            if item.get("detail_json")
            else None
        )

        output.append(item)

    return {
        "events": output
    }


# ============================================================
# WORKFLOW EVENTS
# ============================================================

@app.get("/workflow/events")
def workflow_events(
    mission_id: Optional[str] = None,
    limit: int = Query(
        100,
        ge=1,
        le=500,
    ),
) -> dict[str, Any]:

    with LOCK, db() as connection:

        if mission_id:

            rows = connection.execute(
                """
                SELECT *
                FROM workflow_events
                WHERE mission_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (
                    mission_id,
                    limit,
                ),
            ).fetchall()

        else:

            rows = connection.execute(
                """
                SELECT *
                FROM workflow_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    output = []

    for row in rows:

        item = dict(row)

        item["detail"] = (
            json.loads(
                item.pop("detail_json")
            )
            if item.get("detail_json")
            else None
        )

        output.append(item)

    return {
        "events": output
    }


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/diagnostics")
def diagnostics() -> dict[str, Any]:

    with LOCK, db() as connection:

        mission_count = connection.execute(
            "SELECT COUNT(*) n FROM missions"
        ).fetchone()["n"]

        transport_count = connection.execute(
            "SELECT COUNT(*) n FROM transport_events"
        ).fetchone()["n"]

        evidence_count = connection.execute(
            "SELECT COUNT(*) n FROM evidence"
        ).fetchone()["n"]

    return {
        "version": VERSION,
        "build": BUILD,
        "missions": {
            "count": mission_count
        },
        "transport_classifications": {
            "count": transport_count
        },
        "evidence": {
            "count": evidence_count
        },
        "limits": {
            "max_body": MAX_BODY,
            "fetch_timeout": FETCH_TIMEOUT,
            "max_sources": MAX_SOURCES,
        },
    }


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
def policy() -> dict[str, Any]:

    return health()["policy"]


# ============================================================
# ARCHITECTURE
# ============================================================

@app.get("/architecture")
def architecture() -> dict[str, Any]:

    return {
        "version": VERSION,
        "build": BUILD,

        "transport_boundary": True,
        "edge_failure_separation": True,
        "application_failure_separation": True,
        "html_waf_detection": True,

        "workflow_persistence": True,
        "contract_consistency": True,
        "recovery_integrity": True,
        "truthful_completion_gate": True,

        "typed_run_request": True,
        "swagger_request_body": True,
    }


# ============================================================
# INITIAL DATABASE
# ============================================================

init_db()
