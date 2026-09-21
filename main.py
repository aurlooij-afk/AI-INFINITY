"""
AI Infinity
TARGET-2050.73
BUILD: SELF-CONSISTENT-DISCOVERY-AND-MISSION-STATE-RECOVERY-CORE

Preserves:
- typed /run contract
- Swagger request body
- transport/WAF classification
- SSRF/public-network protection
- SQLite persistence
- workflow events
- evidence validation
- truthful completion gate
- recovery
- inspection endpoints

Fixes:
- DDG redirect discovery
- free public discovery fallbacks
- mission state reconciliation
- stale queued mission state
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
)
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request as URLRequest,
    build_opener,
)

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


# ============================================================
# IDENTITY
# ============================================================

VERSION = "TARGET-2050.73"
BUILD = "SELF-CONSISTENT-DISCOVERY-AND-MISSION-STATE-RECOVERY-CORE"

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

DB_LOCK = threading.RLock()


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "AI Infinity autonomous research, evidence, "
        "verification, transport and recovery core."
    ),
)


# ============================================================
# REQUEST CONTRACTS
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
        default_factory=dict
    )


class MissionCreateRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )


# ============================================================
# BASIC HELPERS
# ============================================================

def now() -> float:
    return time.time()


def make_id(prefix: str) -> str:
    return (
        f"{prefix}-{uuid.uuid4().hex[:12]}"
    )


def sha256(value: Any) -> str:
    if isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode(
            "utf-8",
            "ignore",
        )

    return hashlib.sha256(raw).hexdigest()


def json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
    )


# ============================================================
# DATABASE
# ============================================================

def get_db() -> sqlite3.Connection:
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
    with DB_LOCK, get_db() as connection:

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

            CREATE INDEX IF NOT EXISTS idx_missions_updated
            ON missions(updated_at);

            CREATE INDEX IF NOT EXISTS idx_events_mission
            ON workflow_events(mission_id);

            CREATE INDEX IF NOT EXISTS idx_transport_mission
            ON transport_events(mission_id);

            CREATE INDEX IF NOT EXISTS idx_evidence_mission
            ON evidence(mission_id);
            """
        )


# ============================================================
# EVENTS
# ============================================================

def write_event(
    mission_id: str,
    event_name: str,
    phase: str,
    detail: Any = None,
) -> None:

    detail_json = (
        json_text(detail)
        if detail is not None
        else None
    )

    with DB_LOCK, get_db() as connection:

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
                event_name,
                phase,
                detail_json,
                now(),
            ),
        )


def write_transport_event(
    mission_id: Optional[str],
    url: str,
    result: dict[str, Any],
) -> None:

    with DB_LOCK, get_db() as connection:

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
                json_text(result),
                now(),
            ),
        )


# ============================================================
# MISSION STORAGE
# ============================================================

def create_mission(
    objective: str,
) -> str:

    mission_id = make_id("mission")
    timestamp = now()

    with DB_LOCK, get_db() as connection:

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

    write_event(
        mission_id,
        "mission_created",
        "queued",
        {
            "objective_length": len(
                objective
            )
        },
    )

    return mission_id


def update_mission(
    mission_id: str,
    status: str,
    phase: str,
    result: Any = None,
) -> None:

    result_json = (
        json_text(result)
        if result is not None
        else None
    )

    with DB_LOCK, get_db() as connection:

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


# ============================================================
# MISSION STATE RECONCILIATION
#
# This is the important 2050.72 repair.
# Workflow events are the audit trail.
# The mission row is synchronized from the latest
# authoritative lifecycle event when necessary.
# ============================================================

TERMINAL_EVENT_MAP = {
    "mission_completed": (
        "completed",
        "closed",
    ),
    "mission_recovery_required": (
        "needs_recovery",
        "recovery",
    ),
    "mission_failed": (
        "failed",
        "recovery",
    ),
}


def reconcile_mission(
    mission_id: str,
) -> None:

    with DB_LOCK, get_db() as connection:

        mission = connection.execute(
            """
            SELECT *
            FROM missions
            WHERE id = ?
            """,
            (mission_id,),
        ).fetchone()

        if not mission:
            return

        latest = connection.execute(
            """
            SELECT
                event,
                phase,
                detail_json,
                created_at
            FROM workflow_events
            WHERE mission_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

    if not latest:
        return

    event_name = latest["event"]

    if event_name not in TERMINAL_EVENT_MAP:
        return

    status, phase = TERMINAL_EVENT_MAP[
        event_name
    ]

    current_status = mission["status"]

    if current_status == status:
        return

    detail = None

    if latest["detail_json"]:
        try:
            detail = json.loads(
                latest["detail_json"]
            )
        except Exception:
            detail = None

    update_mission(
        mission_id,
        status,
        phase,
        detail,
    )


def get_mission(
    mission_id: str,
) -> Optional[dict[str, Any]]:

    reconcile_mission(
        mission_id
    )

    with DB_LOCK, get_db() as connection:

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

    raw_result = result.pop(
        "result_json",
        None,
    )

    if raw_result:
        try:
            result["result"] = json.loads(
                raw_result
            )
        except Exception:
            result["result"] = raw_result
    else:
        result["result"] = None

    return result


# ============================================================
# PUBLIC NETWORK POLICY
# ============================================================

def public_ip(
    value: str,
) -> bool:

    try:
        address = ipaddress.ip_address(
            value
        )

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


def validate_public_url(
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
                "Only HTTP/HTTPS is allowed.",
            )

        if not parsed.hostname:
            return (
                False,
                "Missing hostname.",
            )

        if (
            parsed.username
            or parsed.password
        ):
            return (
                False,
                "Credential-bearing URL blocked.",
            )

        hostname = (
            parsed.hostname
            .rstrip(".")
            .lower()
        )

        if (
            hostname == "localhost"
            or hostname.endswith(".local")
        ):
            return (
                False,
                "Local hostname blocked.",
            )

        port = (
            parsed.port
            or (
                443
                if parsed.scheme == "https"
                else 80
            )
        )

        infos = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )

        addresses = {
            item[4][0]
            for item in infos
        }

        if not addresses:
            return (
                False,
                "No DNS address.",
            )

        for address in addresses:

            if not public_ip(address):
                return (
                    False,
                    "Non-public address blocked.",
                )

        return True, "ok"

    except Exception as exc:

        return (
            False,
            str(exc),
        )


# ============================================================
# SAFE FETCH
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


def fetch(
    url: str,
) -> dict[str, Any]:

    current = url

    opener = build_opener(
        ProxyHandler({}),
        NoRedirect(),
    )

    for _ in range(
        MAX_REDIRECTS + 1
    ):

        valid, reason = (
            validate_public_url(
                current
            )
        )

        if not valid:

            return {
                "ok": False,
                "url": current,
                "http_status": None,
                "headers": {},
                "body": "",
                "error": reason,
            }

        request = URLRequest(
            current,
            headers={
                "User-Agent": (
                    "AI-Infinity/2050.73 "
                    "research"
                ),
                "Accept": (
                    "text/html,text/plain,"
                    "application/json,"
                    "application/xhtml+xml"
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
                    "ok": True,
                    "url": current,
                    "http_status": status,
                    "headers": headers,
                    "body": body.decode(
                        "utf-8",
                        "replace",
                    ),
                    "error": None,
                }

        except HTTPError as exc:

            headers = {
                key.lower(): value
                for key, value
                in (
                    exc.headers.items()
                    if exc.headers
                    else []
                )
            }

            body = ""

            try:
                body = exc.read(
                    MAX_BODY + 1
                ).decode(
                    "utf-8",
                    "replace",
                )
            except Exception:
                pass

            location = headers.get(
                "location"
            )

            if (
                exc.code
                in (
                    301,
                    302,
                    303,
                    307,
                    308,
                )
                and location
            ):

                current = urljoin(
                    current,
                    location,
                )

                continue

            return {
                "ok": False,
                "url": current,
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
                "ok": False,
                "url": current,
                "http_status": None,
                "headers": {},
                "body": "",
                "error": str(exc),
            }

        except Exception as exc:

            return {
                "ok": False,
                "url": current,
                "http_status": None,
                "headers": {},
                "body": "",
                "error": str(exc),
            }

    return {
        "ok": False,
        "url": current,
        "http_status": None,
        "headers": {},
        "body": "",
        "error": "redirect limit exceeded",
    }


# ============================================================
# TRANSPORT CLASSIFIER
# ============================================================

def classify_transport(
    http_status: Optional[int],
    content_type: Optional[str],
    body: Optional[str],
    headers: dict[str, str],
) -> dict[str, Any]:

    text = (
        body or ""
    )[:MAX_BODY]

    lowered = text.lower()

    normalized_headers = {
        str(key).lower(): str(value)
        for key, value
        in headers.items()
    }

    ctype = (
        content_type
        or normalized_headers.get(
            "content-type",
            "",
        )
    ).lower()

    waf_markers = (
        "waf",
        "request blocked",
        "access denied",
        "forbidden",
        "cloudflare",
        "captcha",
        "web application firewall",
        "blocked",
    )

    if (
        http_status
        in (
            401,
            403,
            406,
            429,
        )
        and any(
            marker in lowered
            for marker in waf_markers
        )
    ):

        classification = (
            "EDGE_WAF_BLOCK"
        )

        reason = (
            "HTTP edge/WAF block page detected; "
            "response is not research evidence."
        )

    elif (
        http_status is not None
        and http_status >= 500
    ):

        classification = (
            "UPSTREAM_5XX"
        )

        reason = (
            "Upstream server failure; "
            "response is not research evidence."
        )

    elif not text.strip():

        classification = (
            "EMPTY_RESPONSE"
        )

        reason = (
            "Empty response is not research evidence."
        )

    elif (
        "text/html" in ctype
        and any(
            marker in lowered
            for marker in waf_markers
        )
    ):

        classification = (
            "BLOCKED_HTML"
        )

        reason = (
            "HTML block/interstitial detected; "
            "response is not research evidence."
        )

    elif ctype.startswith(
        (
            "image/",
            "audio/",
            "video/",
            "application/octet-stream",
        )
    ):

        classification = (
            "OPAQUE_ASSET"
        )

        reason = (
            "Opaque binary asset is not research evidence."
        )

    elif (
        http_status is not None
        and 200 <= http_status < 400
        and text.strip()
    ):

        classification = (
            "VALID_PUBLIC_CONTENT"
        )

        reason = (
            "Public response contains usable textual content."
        )

    else:

        classification = (
            "UNVERIFIED_RESPONSE"
        )

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
        "content_type": ctype,
        "reason": reason,
        "digest": sha256(text),
    }


# ============================================================
# HTML
# ============================================================

def clean_html(
    value: str,
) -> str:

    value = re.sub(
        r"(?is)"
        r"<script[^>]*>.*?</script>"
        r"|<style[^>]*>.*?</style>"
        r"|<noscript[^>]*>.*?</noscript>",
        " ",
        value,
    )

    value = re.sub(
        r"(?s)<[^>]+>",
        " ",
        value,
    )

    return re.sub(
        r"\s+",
        " ",
        html.unescape(value),
    ).strip()


def title_from_html(
    value: str,
) -> str:

    match = re.search(
        r"(?is)<title[^>]*>(.*?)</title>",
        value,
    )

    if not match:
        return ""

    return clean_html(
        match.group(1)
    )[:500]


# ============================================================
# DISCOVERY
#
# 2050.72 problem:
# DDG results can be wrapped in:
# /l/?uddg=<encoded-real-url>
#
# 2050.73 decodes those links.
# ============================================================

def normalize_candidate_url(
    value: str,
) -> Optional[str]:

    value = html.unescape(
        value.strip()
    )

    if value.startswith("//"):
        value = "https:" + value

    parsed = urlparse(value)

    if (
        parsed.scheme
        not in (
            "http",
            "https",
        )
    ):
        return None

    host = (
        parsed.hostname
        or ""
    ).lower()

    if not host:
        return None

    if (
        host.endswith(
            (
                "duckduckgo.com",
                "bing.com",
            )
        )
        and (
            "uddg" in parse_qs(
                parsed.query
            )
        )
    ):

        encoded = parse_qs(
            parsed.query
        ).get(
            "uddg",
            [""],
        )[0]

        decoded = unquote(
            encoded
        )

        return normalize_candidate_url(
            decoded
        )

    return value


def extract_urls(
    html_text: str,
) -> list[str]:

    candidates: list[str] = []

    patterns = [
        r'href=["\']([^"\']+)["\']',
        r'href=([^ >]+)',
    ]

    for pattern in patterns:

        for raw in re.findall(
            pattern,
            html_text,
            re.I,
        ):

            candidate = (
                normalize_candidate_url(
                    raw
                )
            )

            if (
                candidate
                and candidate not in candidates
            ):
                candidates.append(
                    candidate
                )

            if len(candidates) >= 20:
                return candidates

    return candidates


def discovery_query(
    objective: str,
) -> str:

    compact = re.sub(
        r"\s+",
        " ",
        objective,
    ).strip()

    return compact[:900]


def discover_ddg(
    query: str,
) -> list[str]:

    search_url = (
        "https://html.duckduckgo.com/html/?q="
        + quote_plus(query)
    )

    response = fetch(
        search_url
    )

    if not response.get("ok"):
        return []

    return extract_urls(
        response.get(
            "body",
            "",
        )
    )


def discover_bing(
    query: str,
) -> list[str]:

    search_url = (
        "https://www.bing.com/search?q="
        + quote_plus(query)
    )

    response = fetch(
        search_url
    )

    if not response.get("ok"):
        return []

    return extract_urls(
        response.get(
            "body",
            "",
        )
    )


def discover_openalex(
    query: str,
) -> list[str]:

    url = (
        "https://api.openalex.org/works"
        "?search="
        + quote_plus(query)
        + "&per-page=8"
    )

    response = fetch(
        url
    )

    if not response.get("ok"):
        return []

    try:
        payload = json.loads(
            response.get(
                "body",
                "",
            )
        )
    except Exception:
        return []

    urls: list[str] = []

    for item in payload.get(
        "results",
        [],
    ):

        primary = item.get(
            "primary_location"
        ) or {}

        landing = primary.get(
            "landing_page_url"
        )

        pdf = primary.get(
            "pdf_url"
        )

        for candidate in (
            landing,
            pdf,
        ):

            normalized = (
                normalize_candidate_url(
                    candidate
                )
                if candidate
                else None
            )

            if (
                normalized
                and normalized not in urls
            ):
                urls.append(
                    normalized
                )

    return urls[:MAX_SOURCES]


def discover_crossref(
    query: str,
) -> list[str]:

    url = (
        "https://api.crossref.org/works"
        "?query="
        + quote_plus(query)
        + "&rows=8"
    )

    response = fetch(
        url
    )

    if not response.get("ok"):
        return []

    try:
        payload = json.loads(
            response.get(
                "body",
                "",
            )
        )
    except Exception:
        return []

    urls: list[str] = []

    for item in (
        payload
        .get("message", {})
        .get("items", [])
    ):

        candidate = (
            item.get("URL")
        )

        normalized = (
            normalize_candidate_url(
                candidate
            )
            if candidate
            else None
        )

        if (
            normalized
            and normalized not in urls
        ):
            urls.append(
                normalized
            )

    return urls[:MAX_SOURCES]


def direct_urls(
    objective: str,
) -> list[str]:

    values = re.findall(
        r"https?://[^\s<>\"']+",
        objective,
    )

    return list(
        dict.fromkeys(
            value.rstrip(
                ".,);]"
            )
            for value in values
        )
    )[:MAX_SOURCES]


def discover_sources(
    objective: str,
) -> tuple[list[str], dict[str, int]]:

    direct = direct_urls(
        objective
    )

    if direct:
        return direct, {
            "direct": len(direct),
            "ddg": 0,
            "bing": 0,
            "openalex": 0,
            "crossref": 0,
        }

    query = discovery_query(
        objective
    )

    discovered: list[str] = []

    counts = {
        "direct": 0,
        "ddg": 0,
        "bing": 0,
        "openalex": 0,
        "crossref": 0,
    }

    providers = [
        (
            "ddg",
            discover_ddg,
        ),
        (
            "bing",
            discover_bing,
        ),
        (
            "openalex",
            discover_openalex,
        ),
        (
            "crossref",
            discover_crossref,
        ),
    ]

    for name, provider in providers:

        try:
            results = provider(
                query
            )
        except Exception:
            results = []

        added = 0

        for candidate in results:

            if candidate in discovered:
                continue

            valid, _ = (
                validate_public_url(
                    candidate
                )
            )

            if not valid:
                continue

            discovered.append(
                candidate
            )

            added += 1

            if len(discovered) >= MAX_SOURCES:
                break

        counts[name] = added

        if len(discovered) >= MAX_SOURCES:
            break

    return (
        discovered[:MAX_SOURCES],
        counts,
    )


# ============================================================
# EVIDENCE
# ============================================================

def store_evidence(
    mission_id: str,
    url: str,
    title: str,
    text: str,
    source_family: str,
) -> None:

    domain = (
        urlparse(url).hostname
        or ""
    ).lower()

    with DB_LOCK, get_db() as connection:

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
                sha256(text),
                source_family,
                now(),
            ),
        )


# ============================================================
# RESEARCH
# ============================================================

def run_research(
    mission_id: str,
    objective: str,
) -> dict[str, Any]:

    write_event(
        mission_id,
        "research_started",
        "research",
    )

    urls, discovery = (
        discover_sources(
            objective
        )
    )

    write_event(
        mission_id,
        "research_discovery_completed",
        "research",
        {
            "candidate_count": len(urls),
            "providers": discovery,
        },
    )

    usable: list[
        dict[str, Any]
    ] = []

    blocked: list[
        dict[str, Any]
    ] = []

    for url in urls:

        response = fetch(
            url
        )

        headers = response.get(
            "headers",
            {},
        )

        transport = classify_transport(
            response.get(
                "http_status"
            ),
            headers.get(
                "content-type"
            ),
            response.get(
                "body",
                "",
            ),
            headers,
        )

        write_transport_event(
            mission_id,
            url,
            transport,
        )

        if not transport[
            "usable_for_research"
        ]:

            blocked.append(
                {
                    "url": url,
                    "reason": transport[
                        "reason"
                    ],
                    "transport": transport,
                }
            )

            continue

        body = response.get(
            "body",
            "",
        )

        text = clean_html(
            body
        )

        if len(text) < 200:

            blocked.append(
                {
                    "url": url,
                    "reason": (
                        "insufficient textual content"
                    ),
                    "transport": transport,
                }
            )

            continue

        title = title_from_html(
            body
        )

        hostname = (
            urlparse(url).hostname
            or ""
        ).lower()

        parts = hostname.split(
            "."
        )

        family = (
            ".".join(
                parts[-2:]
            )
            if len(parts) >= 2
            else hostname
        )

        store_evidence(
            mission_id,
            url,
            title,
            text,
            family,
        )

        usable.append(
            {
                "url": url,
                "domain": hostname,
                "title": title,
                "digest": sha256(text),
            }
        )

    with DB_LOCK, get_db() as connection:

        rows = connection.execute(
            """
            SELECT DISTINCT domain
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
            f"Research produced "
            f"{len(usable)} usable public source(s) "
            f"across {len(domains)} independent domain(s)."
        )

        with DB_LOCK, get_db() as connection:

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

    if closure:

        next_actions: list[str] = []

    else:

        next_actions = [
            "Retry using additional independent discovery providers.",
            "Do not treat blocked, WAF, empty, or opaque responses as evidence.",
            "Require at least two usable sources from two independent domains before completion.",
        ]

    result = {
        "mode": "research",
        "closure": closure,
        "evidence_count": len(usable),
        "independent_domains": len(domains),
        "domains": domains,
        "sources": usable,
        "blocked_sources": blocked,
        "claims": claims,
        "discovery": discovery,
        "candidate_count": len(urls),
        "next_actions": next_actions,
    }

    write_event(
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

        write_event(
            mission_id,
            "mission_started",
            "planning",
        )

        result = run_research(
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

            write_event(
                mission_id,
                "mission_completed",
                "closed",
                {
                    "closure": True,
                    "evidence_count": result[
                        "evidence_count"
                    ],
                    "independent_domains": result[
                        "independent_domains"
                    ],
                },
            )

        else:

            update_mission(
                mission_id,
                "needs_recovery",
                "recovery",
                result,
            )

            write_event(
                mission_id,
                "mission_recovery_required",
                "recovery",
                {
                    "closure": False,
                    "evidence_count": result[
                        "evidence_count"
                    ],
                    "independent_domains": result[
                        "independent_domains"
                    ],
                },
            )

    except Exception as exc:

        failure = {
            "closure": False,
            "error": type(exc).__name__,
            "message": str(exc),
            "next_actions": [
                "Inspect workflow events.",
                "Retry the mission after diagnosis.",
            ],
        }

        update_mission(
            mission_id,
            "failed",
            "recovery",
            failure,
        )

        write_event(
            mission_id,
            "mission_failed",
            "recovery",
            failure,
        )


async def background_mission(
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

    with DB_LOCK, get_db() as connection:

        rows = connection.execute(
            """
            SELECT id
            FROM missions
            WHERE status IN ('queued', 'running')
            """
        ).fetchall()

    for row in rows:

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

        write_event(
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
        background_mission(
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
async def missions_create(
    request: MissionCreateRequest,
) -> dict[str, Any]:

    objective = request.objective.strip()

    mission_id = create_mission(
        objective
    )

    asyncio.create_task(
        background_mission(
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

    if result is None:

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return result


@app.get("/missions")
def mission_list(
    limit: int = Query(
        20,
        ge=1,
        le=100,
    ),
) -> dict[str, Any]:

    with DB_LOCK, get_db() as connection:

        rows = connection.execute(
            """
            SELECT id
            FROM missions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    results = []

    for row in rows:

        item = get_mission(
            row["id"]
        )

        if item:
            results.append(item)

    return {
        "missions": results
    }


@app.post("/mission/{mission_id}/retry")
async def mission_retry(
    mission_id: str,
) -> dict[str, Any]:

    item = get_mission(
        mission_id
    )

    if item is None:

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

    write_event(
        mission_id,
        "manual_retry",
        "queued",
    )

    asyncio.create_task(
        background_mission(
            mission_id,
            item["objective"],
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

    if get_mission(
        mission_id
    ) is None:

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    with DB_LOCK, get_db() as connection:

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

    if get_mission(
        mission_id
    ) is None:

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    with DB_LOCK, get_db() as connection:

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

    if get_mission(
        mission_id
    ) is None:

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    with DB_LOCK, get_db() as connection:

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
# ============================================================

@app.post("/transport/classify")
def transport_classify(
    request: TransportRequest,
) -> dict[str, Any]:

    headers = {
        key.lower(): value
        for key, value
        in request.headers.items()
    }

    return classify_transport(
        request.http_status,
        request.content_type,
        request.body,
        headers,
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

    with DB_LOCK, get_db() as connection:

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

        raw = item.pop(
            "detail_json",
            None,
        )

        try:
            item["detail"] = (
                json.loads(raw)
                if raw
                else None
            )
        except Exception:
            item["detail"] = raw

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

    with DB_LOCK, get_db() as connection:

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

        raw = item.pop(
            "detail_json",
            None,
        )

        try:
            item["detail"] = (
                json.loads(raw)
                if raw
                else None
            )
        except Exception:
            item["detail"] = raw

        output.append(item)

    return {
        "events": output
    }


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/diagnostics")
def diagnostics() -> dict[str, Any]:

    with DB_LOCK, get_db() as connection:

        mission_count = connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM missions
            """
        ).fetchone()["n"]

        transport_count = connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM transport_events
            """
        ).fetchone()["n"]

        evidence_count = connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM evidence
            """
        ).fetchone()["n"]

        event_count = connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM workflow_events
            """
        ).fetchone()["n"]

    return {
        "version": VERSION,
        "build": BUILD,
        "missions": {
            "count": mission_count
        },
        "workflow_events": {
            "count": event_count
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

        "typed_run_request": True,
        "swagger_request_body": True,

        "transport_boundary": True,
        "edge_failure_separation": True,
        "application_failure_separation": True,
        "html_waf_detection": True,

        "public_network_policy": True,
        "ssrf_protection": True,

        "multi_provider_discovery": True,
        "ddg_redirect_decoding": True,
        "openalex_discovery": True,
        "crossref_discovery": True,

        "workflow_persistence": True,
        "mission_state_reconciliation": True,

        "truthful_completion_gate": True,
        "recovery_integrity": True,
    }


# ============================================================
# INITIALIZE
# ============================================================

init_db()
