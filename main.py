"""
AI Infinity
TARGET-2050.75
BUILD: EVIDENCE-CONSISTENCY-AND-CLOSURE-VERIFICATION-CORE

Preserves TARGET-2050.74:
- typed /run contract
- Swagger request body
- SQLite persistence
- workflow events
- mission state reconciliation
- public-network/SSRF protection
- transport/WAF classification
- evidence storage
- recovery/retry
- diagnostics/inspection endpoints
- search-result integrity firewall
- provider-aware discovery
- canonical URL handling
- OpenAlex/Crossref fallback
- provider fallback
- research-source validation

Adds:
- evidence consistency verification
- independent-domain verification
- contradiction signal detection
- persistent verification events
- truthful verification gate
- closure only after verification passes

Important:
The verification layer is an integrity/consistency gate.
It is NOT semantic proof that a claim is factually true.
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
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request as URLRequest,
    build_opener,
)

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


# ============================================================
# VERSION
# ============================================================

VERSION = "TARGET-2050.75"
BUILD = "EVIDENCE-CONSISTENCY-AND-CLOSURE-VERIFICATION-CORE"


# ============================================================
# RUNTIME
# ============================================================

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
MAX_REDIRECTS = 4
MAX_SOURCES = 12
MIN_TEXT = 250

MIN_INDEPENDENT_SOURCES = 2
MIN_SOURCE_CONSISTENCY = 0.25

DB_LOCK = threading.RLock()


app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description=(
        "Autonomous research, evidence, transport, "
        "verification and recovery core."
    ),
)


# ============================================================
# REQUEST MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=20000,
        description="Mission objective",
    )


class MissionCreateRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=20000,
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


# ============================================================
# BASIC HELPERS
# ============================================================

def now() -> float:
    return time.time()


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def digest(value: Any) -> str:
    raw = (
        value
        if isinstance(value, bytes)
        else str(value).encode("utf-8", "ignore")
    )
    return hashlib.sha256(raw).hexdigest()


def jdump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
    )


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_db() -> None:
    with DB_LOCK, db() as connection:
        connection.executescript(
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

            CREATE TABLE IF NOT EXISTS verification_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                status TEXT NOT NULL,
                score REAL NOT NULL,
                detail_json TEXT,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS
                idx_events_mission
                ON workflow_events(mission_id);

            CREATE INDEX IF NOT EXISTS
                idx_transport_mission
                ON transport_events(mission_id);

            CREATE INDEX IF NOT EXISTS
                idx_evidence_mission
                ON evidence(mission_id);

            CREATE INDEX IF NOT EXISTS
                idx_verification_mission
                ON verification_events(mission_id);
            """
        )

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(evidence)"
            ).fetchall()
        }

        if "provider" not in columns:
            connection.execute(
                "ALTER TABLE evidence ADD COLUMN provider TEXT"
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
    with DB_LOCK, db() as connection:
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
            VALUES(?,?,?,?,?)
            """,
            (
                mission_id,
                name,
                phase,
                (
                    jdump(detail)
                    if detail is not None
                    else None
                ),
                now(),
            ),
        )


def transport_event(
    mission_id: Optional[str],
    url: str,
    result: dict[str, Any],
) -> None:
    with DB_LOCK, db() as connection:
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
            VALUES(?,?,?,?,?)
            """,
            (
                mission_id,
                url,
                result["classification"],
                jdump(result),
                now(),
            ),
        )


# ============================================================
# MISSION STATE
# ============================================================

def create_mission(objective: str) -> str:
    mission_id = make_id("mission")
    timestamp = now()

    with DB_LOCK, db() as connection:
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
            VALUES(?,?,?,?,?,?,?)
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
    with DB_LOCK, db() as connection:
        connection.execute(
            """
            UPDATE missions
            SET
                status=?,
                phase=?,
                result_json=?,
                updated_at=?
            WHERE id=?
            """,
            (
                status,
                phase,
                (
                    jdump(result)
                    if result is not None
                    else None
                ),
                now(),
                mission_id,
            ),
        )


TERMINAL_MAP = {
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


def reconcile(mission_id: str) -> None:
    with DB_LOCK, db() as connection:
        mission = connection.execute(
            """
            SELECT *
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        latest = connection.execute(
            """
            SELECT
                event,
                phase,
                detail_json
            FROM workflow_events
            WHERE mission_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

    if (
        not mission
        or not latest
        or latest["event"] not in TERMINAL_MAP
    ):
        return

    status, phase = TERMINAL_MAP[
        latest["event"]
    ]

    if mission["status"] == status:
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
    reconcile(mission_id)

    with DB_LOCK, db() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

    if not row:
        return None

    result = dict(row)

    raw = result.pop(
        "result_json",
        None,
    )

    try:
        result["result"] = (
            json.loads(raw)
            if raw
            else None
        )
    except Exception:
        result["result"] = raw

    return result


# ============================================================
# NETWORK SAFETY
# ============================================================

def public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)

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

        if parsed.username or parsed.password:
            return (
                False,
                "Credential-bearing URL blocked.",
            )

        host = (
            parsed.hostname
            .rstrip(".")
            .lower()
        )

        if (
            host == "localhost"
            or host.endswith(".local")
        ):
            return (
                False,
                "Local hostname blocked.",
            )

        port = parsed.port or (
            443
            if parsed.scheme == "https"
            else 80
        )

        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        }

        if not addresses:
            return (
                False,
                "No DNS address.",
            )

        if any(
            not public_ip(address)
            for address in addresses
        ):
            return (
                False,
                "Non-public address blocked.",
            )

        return True, "ok"

    except Exception as exc:
        return False, str(exc)


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


# ============================================================
# SAFE FETCH
# ============================================================

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
                    "AI-Infinity/2050.75 research"
                ),
                "Accept": (
                    "text/html,"
                    "text/plain,"
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
                    "http_status": getattr(
                        response,
                        "status",
                        200,
                    ),
                    "headers": headers,
                    "body": body.decode(
                        "utf-8",
                        "replace",
                    ),
                    "error": None,
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

            try:
                body = exc.read(
                    MAX_BODY + 1
                ).decode(
                    "utf-8",
                    "replace",
                )
            except Exception:
                body = ""

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
# TRANSPORT CLASSIFICATION
# ============================================================

WAF_MARKERS = (
    "waf",
    "request blocked",
    "access denied",
    "forbidden",
    "cloudflare",
    "captcha",
    "web application firewall",
    "blocked",
)


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
        for key, value in headers.items()
    }

    content_type_value = (
        content_type
        or normalized_headers.get(
            "content-type",
            "",
        )
    ).lower()

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
            for marker in WAF_MARKERS
        )
    ):
        classification = (
            "EDGE_WAF_BLOCK"
        )
        reason = (
            "HTTP edge/WAF block page "
            "detected; response is not "
            "research evidence."
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
        "text/html"
        in content_type_value
        and any(
            marker in lowered
            for marker in WAF_MARKERS
        )
    ):
        classification = (
            "BLOCKED_HTML"
        )
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
        classification = (
            "OPAQUE_ASSET"
        )
        reason = (
            "Opaque binary asset is not "
            "research evidence."
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
            "Public response contains "
            "usable textual content."
        )

    else:
        classification = (
            "UNVERIFIED_RESPONSE"
        )
        reason = (
            "Response could not be safely "
            "classified as research evidence."
        )

    usable = (
        classification
        == "VALID_PUBLIC_CONTENT"
    )

    return {
        "classification": classification,
        "edge_failure": (
            classification
            in {
                "EDGE_WAF_BLOCK",
                "BLOCKED_HTML",
            }
        ),
        "application_failure": (
            classification
            == "UPSTREAM_5XX"
        ),
        "is_evidence": usable,
        "usable_for_research": usable,
        "http_status": http_status,
        "content_type": content_type_value,
        "reason": reason,
        "digest": digest(text),
    }


# ============================================================
# TEXT PROCESSING
# ============================================================

def clean_html(
    value: str,
) -> str:

    value = re.sub(
        r"(?is)<script[^>]*>.*?</script>"
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

    return (
        clean_html(match.group(1))[:500]
        if match
        else ""
    )


# ============================================================
# DISCOVERY FIREWALL
# ============================================================

ASSET_EXTENSIONS = (
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".webm",
    ".avi",
    ".zip",
    ".gz",
    ".bin",
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
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
)


def registrable_family(
    host: str,
) -> str:

    host = (
        host
        .lower()
        .rstrip(".")
    )

    parts = host.split(".")

    return (
        ".".join(parts[-2:])
        if len(parts) >= 2
        else host
    )


def canonicalize_url(
    value: str,
) -> Optional[str]:

    if not value:
        return None

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
        or not parsed.hostname
    ):
        return None

    query = parse_qs(
        parsed.query
    )

    for key in (
        "uddg",
        "url",
        "target",
        "dest",
        "destination",
    ):

        if key in query and query[key]:

            nested = unquote(
                query[key][0]
            )

            nested_url = (
                canonicalize_url(
                    nested
                )
            )

            if nested_url:
                return nested_url

    host = (
        parsed.hostname
        .lower()
        .rstrip(".")
    )

    path = parsed.path or "/"

    netloc = host

    if parsed.port and not (
        (
            parsed.scheme == "http"
            and parsed.port == 80
        )
        or (
            parsed.scheme == "https"
            and parsed.port == 443
        )
    ):
        netloc = (
            f"{host}:{parsed.port}"
        )

    return urlunparse(
        (
            parsed.scheme,
            netloc,
            path,
            "",
            parsed.query,
            "",
        )
    )


def research_candidate_check(
    url: str,
    provider: str,
) -> tuple[bool, str]:

    normalized = canonicalize_url(
        url
    )

    if not normalized:
        return False, "invalid_url"

    parsed = urlparse(
        normalized
    )

    host = (
        parsed.hostname
        or ""
    ).lower().rstrip(".")

    path = (
        parsed.path
        or ""
    ).lower()

    if path.endswith(
        ASSET_EXTENSIONS
    ):
        return False, "asset_extension"

    if host in INFRASTRUCTURE_HOSTS:
        return False, (
            "search_infrastructure_host"
        )

    if any(
        part in host
        for part in TRACKING_HOST_PARTS
    ):
        return False, "tracking_host"

    if provider in {
        "bing",
        "ddg",
    }:
        if (
            host.endswith(".bing.com")
            or host.endswith(
                ".duckduckgo.com"
            )
        ):
            return False, (
                "search_provider_host"
            )

    if (
        "/rb/" in path
        or "/rp/" in path
        or "/th?id=" in path
    ):
        return False, (
            "search_asset_path"
        )

    if (
        any(
            value in path
            for value in (
                "/images/",
                "/imgres",
                "/favicon",
                "/static/",
            )
        )
        and provider in {
            "bing",
            "ddg",
        }
    ):
        return False, (
            "search_asset_path"
        )

    if parsed.query:
        query = (
            parsed.query.lower()
        )

        if any(
            key in query
            for key in (
                "click=",
                "u=a1",
                "adurl=",
                "msclkid=",
                "gclid=",
            )
        ):
            if provider in {
                "bing",
                "ddg",
            }:
                return False, (
                    "tracking_redirect"
                )

    valid, reason = (
        validate_public_url(
            normalized
        )
    )

    if not valid:
        return False, (
            f"network:{reason}"
        )

    return True, normalized


# ============================================================
# SEARCH RESULT EXTRACTION
# ============================================================

def extract_anchor_urls(
    html_text: str,
) -> list[str]:

    output = []

    for raw in re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        html_text,
        re.I,
    ):

        normalized = (
            canonicalize_url(
                raw
            )
        )

        if (
            normalized
            and normalized not in output
        ):
            output.append(
                normalized
            )

    return output


def extract_bing_result_urls(
    html_text: str,
) -> list[str]:

    output = []

    blocks = re.findall(
        r'(?is)<li[^>]+class=["\'][^"\']*'
        r'\bb_algo\b[^"\']*["\'][^>]*>'
        r'(.*?)</li>',
        html_text,
    )

    for block in blocks:

        for raw in re.findall(
            r'href\s*=\s*["\']([^"\']+)["\']',
            block,
            re.I,
        ):

            normalized = (
                canonicalize_url(
                    raw
                )
            )

            if normalized:

                accepted, _ = (
                    research_candidate_check(
                        normalized,
                        "bing",
                    )
                )

                if (
                    accepted
                    and normalized not in output
                ):
                    output.append(
                        normalized
                    )
                    break

    if output:
        return output

    for raw in re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        html_text,
        re.I,
    ):

        normalized = (
            canonicalize_url(
                raw
            )
        )

        if not normalized:
            continue

        accepted, _ = (
            research_candidate_check(
                normalized,
                "bing",
            )
        )

        if (
            accepted
            and normalized not in output
        ):
            output.append(
                normalized
            )

    return output


def extract_ddg_result_urls(
    html_text: str,
) -> list[str]:

    output = []

    blocks = re.findall(
        r'(?is)<a[^>]+class=["\'][^"\']*'
        r'\bresult__a\b[^"\']*["\']'
        r'[^>]*href=["\']([^"\']+)["\']',
        html_text,
    )

    for raw in blocks:

        normalized = (
            canonicalize_url(
                raw
            )
        )

        if normalized:

            accepted, _ = (
                research_candidate_check(
                    normalized,
                    "ddg",
                )
            )

            if (
                accepted
                and normalized not in output
            ):
                output.append(
                    normalized
                )

    if output:
        return output

    for raw in extract_anchor_urls(
        html_text
    ):

        accepted, _ = (
            research_candidate_check(
                raw,
                "ddg",
            )
        )

        if (
            accepted
            and raw not in output
        ):
            output.append(raw)

    return output


# ============================================================
# DISCOVERY PROVIDERS
# ============================================================

def discovery_query(
    objective: str,
) -> str:

    query = re.sub(
        r"\s+",
        " ",
        objective,
    ).strip()

    return query[:900]


def discover_ddg(
    query: str,
) -> list[str]:

    response = fetch(
        "https://html.duckduckgo.com/html/?q="
        + quote_plus(query)
    )

    if not response.get("ok"):
        return []

    return extract_ddg_result_urls(
        response.get(
            "body",
            "",
        )
    )


def discover_bing(
    query: str,
) -> list[str]:

    response = fetch(
        "https://www.bing.com/search?q="
        + quote_plus(query)
    )

    if not response.get("ok"):
        return []

    return extract_bing_result_urls(
        response.get(
            "body",
            "",
        )
    )


def discover_openalex(
    query: str,
) -> list[str]:

    response = fetch(
        "https://api.openalex.org/works?search="
        + quote_plus(query)
        + "&per-page=10"
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

    output = []

    for item in payload.get(
        "results",
        [],
    ):

        location = (
            item.get(
                "primary_location"
            )
            or {}
        )

        for candidate in (
            location.get(
                "landing_page_url"
            ),
            location.get(
                "pdf_url"
            ),
        ):

            if not candidate:
                continue

            normalized = (
                canonicalize_url(
                    candidate
                )
            )

            if normalized:

                accepted, _ = (
                    research_candidate_check(
                        normalized,
                        "openalex",
                    )
                )

                if (
                    accepted
                    and normalized not in output
                ):
                    output.append(
                        normalized
                    )

    return output[:MAX_SOURCES]


def discover_crossref(
    query: str,
) -> list[str]:

    response = fetch(
        "https://api.crossref.org/works?query="
        + quote_plus(query)
        + "&rows=10"
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

    output = []

    for item in (
        payload
        .get(
            "message",
            {},
        )
        .get(
            "items",
            [],
        )
    ):

        normalized = (
            canonicalize_url(
                item.get(
                    "URL",
                    "",
                )
            )
        )

        if normalized:

            accepted, _ = (
                research_candidate_check(
                    normalized,
                    "crossref",
                )
            )

            if (
                accepted
                and normalized not in output
            ):
                output.append(
                    normalized
                )

    return output[:MAX_SOURCES]


def direct_urls(
    objective: str,
) -> list[str]:

    output = []

    for raw in re.findall(
        r"https?://[^\s<>\"']+",
        objective,
    ):

        normalized = canonicalize_url(
            raw.rstrip(
                ".,);]"
            )
        )

        if (
            normalized
            and normalized not in output
        ):
            output.append(
                normalized
            )

    return output[:MAX_SOURCES]


def discover_sources(
    objective: str,
) -> tuple[
    list[dict[str, str]],
    dict[str, Any],
]:

    direct = direct_urls(
        objective
    )

    if direct:
        return (
            [
                {
                    "url": value,
                    "provider": "direct",
                }
                for value in direct
            ],
            {
                "direct": len(direct),
                "ddg": 0,
                "bing": 0,
                "openalex": 0,
                "crossref": 0,
                "rejected": 0,
                "rejected_reasons": {},
            },
        )

    query = discovery_query(
        objective
    )

    candidates = []
    seen = set()

    counts = {
        "direct": 0,
        "ddg": 0,
        "bing": 0,
        "openalex": 0,
        "crossref": 0,
        "rejected": 0,
        "rejected_reasons": {},
    }

    providers = (
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
    )

    for name, provider_function in providers:

        try:
            results = provider_function(
                query
            )
        except Exception:
            results = []

        accepted_count = 0

        for raw in results:

            normalized = (
                canonicalize_url(
                    raw
                )
            )

            accepted, reason = (
                research_candidate_check(
                    normalized or raw,
                    name,
                )
            )

            if not accepted:

                counts["rejected"] += 1

                counts[
                    "rejected_reasons"
                ][reason] = (
                    counts[
                        "rejected_reasons"
                    ].get(
                        reason,
                        0,
                    )
                    + 1
                )

                continue

            if normalized in seen:
                continue

            seen.add(normalized)

            candidates.append(
                {
                    "url": normalized,
                    "provider": name,
                }
            )

            accepted_count += 1

            if (
                len(candidates)
                >= MAX_SOURCES
            ):
                break

        counts[name] = (
            accepted_count
        )

        if (
            len(candidates)
            >= MAX_SOURCES
        ):
            break

    return candidates, counts


# ============================================================
# EVIDENCE STORAGE
# ============================================================

def store_evidence(
    mission_id: str,
    url: str,
    title: str,
    text: str,
    provider: str,
) -> None:

    domain = (
        urlparse(url)
        .hostname
        or ""
    ).lower()

    family = registrable_family(
        domain
    )

    with DB_LOCK, db() as connection:
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
                provider,
                created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                mission_id,
                url,
                domain,
                title,
                text[:12000],
                digest(text),
                family,
                provider,
                now(),
            ),
        )


# ============================================================
# 2050.75 VERIFICATION ENGINE
# ============================================================

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "about",
    "find",
    "research",
    "evidence",
    "independent",
    "high",
    "quality",
    "empirical",
    "important",
    "claims",
    "identify",
    "contradictory",
    "next",
    "actions",
    "real",
    "world",
    "task",
    "execution",
    "autonomous",
    "agents",
    "agent",
    "their",
    "they",
    "them",
    "are",
    "was",
    "were",
    "has",
    "have",
    "been",
    "can",
    "may",
    "will",
    "using",
    "use",
    "based",
    "more",
    "than",
    "such",
    "between",
    "through",
    "across",
    "what",
    "how",
    "why",
    "which",
    "where",
    "when",
    "who",
    "does",
    "did",
    "not",
    "but",
    "also",
    "all",
    "our",
    "its",
    "reliability",
}


CONTRADICTION_MARKERS = (
    "contradict",
    "however",
    "in contrast",
    "on the other hand",
    "no evidence",
    "did not",
    "failed to",
    "failure",
    "ineffective",
    "lower than",
    "worse than",
    "decreased",
    "declined",
    "unable to",
)


def verification_tokens(
    text: str,
) -> set[str]:

    words = re.findall(
        r"[a-zA-Z][a-zA-Z0-9-]{2,}",
        (text or "").lower(),
    )

    return {
        word
        for word in words
        if word not in STOPWORDS
    }


def source_consistency_score(
    objective: str,
    sources: list[dict[str, Any]],
) -> tuple[
    float,
    dict[str, Any],
]:

    target = verification_tokens(
        objective
    )

    if (
        not target
        or not sources
    ):
        return (
            0.0,
            {
                "shared_terms": [],
                "target_term_count": len(
                    target
                ),
                "independent_term_coverage": 0.0,
            },
        )

    source_sets = []

    for source in sources:

        source_text = (
            f"{source.get('title', '')} "
            f"{source.get('url', '')} "
            f"{source.get('text', '')}"
        )

        source_sets.append(
            verification_tokens(
                source_text
            )
        )

    shared_terms = []

    for token in target:

        appearances = sum(
            1
            for terms in source_sets
            if token in terms
        )

        if appearances >= 2:
            shared_terms.append(
                token
            )

    score = (
        len(shared_terms)
        / max(
            1,
            len(target),
        )
    )

    return (
        score,
        {
            "shared_terms": sorted(
                shared_terms
            )[:100],
            "target_term_count": len(
                target
            ),
            "independent_term_coverage": score,
        },
    )


def verify_evidence_set(
    mission_id: str,
    objective: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:

    domains = sorted(
        {
            (
                urlparse(
                    source["url"]
                ).hostname
                or ""
            ).lower()
            for source in sources
        }
    )

    families = sorted(
        {
            registrable_family(
                (
                    urlparse(
                        source["url"]
                    ).hostname
                    or ""
                )
            )
            for source in sources
        }
    )

    score, consistency = (
        source_consistency_score(
            objective,
            sources,
        )
    )

    contradiction_hits = []

    for source in sources:

        text = (
            source.get(
                "text",
                "",
            )
            or ""
        ).lower()

        hits = [
            marker
            for marker
            in CONTRADICTION_MARKERS
            if marker in text
        ]

        if hits:

            contradiction_hits.append(
                {
                    "url": source["url"],
                    "domain": source["domain"],
                    "markers": sorted(
                        set(hits)
                    ),
                }
            )

    enough_sources = (
        len(sources)
        >= MIN_INDEPENDENT_SOURCES
    )

    enough_domains = (
        len(domains)
        >= MIN_INDEPENDENT_SOURCES
    )

    consistent = (
        score
        >= MIN_SOURCE_CONSISTENCY
    )

    contradiction_review_required = (
        bool(contradiction_hits)
    )

    if (
        enough_sources
        and enough_domains
        and consistent
        and not contradiction_review_required
    ):

        status = "verified"

        reason = (
            "Evidence passed independent-domain "
            "and consistency checks; no "
            "contradiction markers were detected."
        )

    elif (
        enough_sources
        and enough_domains
        and consistent
    ):

        status = (
            "supported_pending_contradiction_review"
        )

        reason = (
            "Evidence is independently supported, "
            "but contradiction markers require review."
        )

    else:

        status = "insufficient"

        reason = (
            "Evidence did not meet the "
            "independent-source/domain or "
            "consistency threshold."
        )

    detail = {
        "status": status,
        "reason": reason,
        "score": round(
            score,
            4,
        ),
        "threshold": (
            MIN_SOURCE_CONSISTENCY
        ),
        "evidence_count": len(
            sources
        ),
        "independent_domains": len(
            domains
        ),
        "source_families": families,
        "consistency": consistency,
        "contradiction_review_required": (
            contradiction_review_required
        ),
        "contradiction_signals": (
            contradiction_hits[:20]
        ),
        "method": (
            "lexical-independent-"
            "evidence-consistency-v1"
        ),
        "warning": (
            "This is an integrity/consistency "
            "check, not semantic proof of factual truth."
        ),
    }

    with DB_LOCK, db() as connection:

        connection.execute(
            """
            INSERT INTO verification_events
            (
                mission_id,
                status,
                score,
                detail_json,
                created_at
            )
            VALUES(?,?,?,?,?)
            """,
            (
                mission_id,
                status,
                float(score),
                jdump(detail),
                now(),
            ),
        )

    event(
        mission_id,
        "evidence_verification_completed",
        "verification",
        detail,
    )

    return detail


# ============================================================
# RESEARCH
# ============================================================

def run_research(
    mission_id: str,
    objective: str,
) -> dict[str, Any]:

    event(
        mission_id,
        "research_started",
        "research",
    )

    candidates, discovery = (
        discover_sources(
            objective
        )
    )

    event(
        mission_id,
        "research_discovery_completed",
        "research",
        {
            "candidate_count": len(
                candidates
            ),
            "providers": discovery,
        },
    )

    usable = []
    blocked = []
    seen_domains = set()

    for item in candidates:

        url = item["url"]
        provider = item["provider"]

        accepted, reason = (
            research_candidate_check(
                url,
                provider,
            )
        )

        if not accepted:

            blocked.append(
                {
                    "url": url,
                    "provider": provider,
                    "reason": reason,
                }
            )

            continue

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

        transport_event(
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
                    "provider": provider,
                    "reason": transport[
                        "reason"
                    ],
                    "transport": transport,
                }
            )

            continue

        raw = response.get(
            "body",
            "",
        )

        text = clean_html(
            raw
        )

        if len(text) < MIN_TEXT:

            blocked.append(
                {
                    "url": url,
                    "provider": provider,
                    "reason": (
                        "insufficient textual content"
                    ),
                    "transport": transport,
                }
            )

            continue

        host = (
            urlparse(url)
            .hostname
            or ""
        ).lower()

        if (
            host in INFRASTRUCTURE_HOSTS
            or host.endswith(
                ".bing.com"
            )
            or host.endswith(
                ".duckduckgo.com"
            )
        ):

            blocked.append(
                {
                    "url": url,
                    "provider": provider,
                    "reason": (
                        "search_infrastructure_host"
                    ),
                }
            )

            continue

        title = title_from_html(
            raw
        )

        store_evidence(
            mission_id,
            url,
            title,
            text,
            provider,
        )

        seen_domains.add(
            host
        )

        usable.append(
            {
                "url": url,
                "domain": host,
                "title": title,
                "provider": provider,
                "digest": digest(text),
                "text": text[:12000],
            }
        )

    verification = (
        verify_evidence_set(
            mission_id,
            objective,
            usable,
        )
    )

    closure = (
        verification["status"]
        == "verified"
    )

    claims = []

    if usable:

        claim = (
            f"Research produced "
            f"{len(usable)} usable public "
            f"source(s) across "
            f"{len(seen_domains)} "
            f"independent domain(s)."
        )

        with DB_LOCK, db() as connection:

            connection.execute(
                """
                INSERT INTO claims
                (
                    mission_id,
                    claim,
                    evidence_count,
                    created_at
                )
                VALUES(?,?,?,?)
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
                "evidence_count": len(
                    usable
                ),
            }
        )

    if closure:

        next_actions = [
            (
                "Evidence integrity and "
                "consistency checks passed."
            ),
            (
                "Proceed to final synthesis "
                "while preserving source provenance."
            ),
        ]

    else:

        next_actions = [
            (
                "Retry with provider fallback "
                "and broader independent sources."
            ),
            (
                "Do not treat search-engine "
                "infrastructure, assets, WAF, "
                "empty, or opaque responses "
                "as evidence."
            ),
            (
                "Require at least two usable "
                "sources from two independent "
                "domains plus consistency "
                "verification before completion."
            ),
        ]

    result = {
        "mode": "research",
        "closure": closure,
        "verification": verification,
        "evidence_count": len(
            usable
        ),
        "independent_domains": len(
            seen_domains
        ),
        "domains": sorted(
            seen_domains
        ),
        "sources": usable,
        "blocked_sources": blocked,
        "claims": claims,
        "discovery": discovery,
        "candidate_count": len(
            candidates
        ),
        "next_actions": next_actions,
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

            event(
                mission_id,
                "mission_completed",
                "closed",
                {
                    "closure": True,
                    "verification_status": (
                        result[
                            "verification"
                        ][
                            "status"
                        ]
                    ),
                    "evidence_count": (
                        result[
                            "evidence_count"
                        ]
                    ),
                    "independent_domains": (
                        result[
                            "independent_domains"
                        ]
                    ),
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
                    "verification_status": (
                        result[
                            "verification"
                        ][
                            "status"
                        ]
                    ),
                    "evidence_count": (
                        result[
                            "evidence_count"
                        ]
                    ),
                    "independent_domains": (
                        result[
                            "independent_domains"
                        ]
                    ),
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

        event(
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

    with DB_LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT id
            FROM missions
            WHERE status IN (
                'queued',
                'running'
            )
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
            "research_source_requires_integrity_validation": True,
            "evidence_consistency_verification": True,
            "contradiction_review_enabled": True,
            "completion_requires_closure": True,
            "completion_requires_verification": True,
        },
    }


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
            detail=(
                "objective cannot be empty"
            ),
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


@app.post("/missions")
async def missions_create(
    request: MissionCreateRequest,
) -> dict[str, Any]:

    objective = (
        request.objective.strip()
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
        "status": "queued",
    }


# ============================================================
# MISSION
# ============================================================

@app.get("/mission/{mission_id}")
def mission(
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

    return item


@app.get("/missions")
def missions(
    limit: int = Query(
        20,
        ge=1,
        le=100,
    ),
) -> dict[str, Any]:

    with DB_LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT id
            FROM missions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {
        "missions": [
            item
            for row in rows
            if (
                item := get_mission(
                    row["id"]
                )
            )
        ]
    }


@app.post("/mission/{mission_id}/retry")
async def retry(
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

    event(
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

    with DB_LOCK, db() as connection:

        if mission_id:

            rows = connection.execute(
                """
                SELECT *
                FROM workflow_events
                WHERE mission_id=?
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
# TRANSPORT EVENTS
# ============================================================

@app.get("/transport/events")
def transport_events(
    mission_id: Optional[str] = None,
    limit: int = Query(
        100,
        ge=1,
        le=500,
    ),
) -> dict[str, Any]:

    with DB_LOCK, db() as connection:

        if mission_id:

            rows = connection.execute(
                """
                SELECT *
                FROM transport_events
                WHERE mission_id=?
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

    with DB_LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT
                url,
                domain,
                title,
                text,
                content_digest,
                source_family,
                provider,
                created_at
            FROM evidence
            WHERE mission_id=?
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

    with DB_LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT
                url,
                domain,
                title,
                content_digest,
                source_family,
                provider,
                created_at
            FROM evidence
            WHERE mission_id=?
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

    with DB_LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT
                claim,
                evidence_count,
                created_at
            FROM claims
            WHERE mission_id=?
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
# VERIFICATION INSPECTION
# ============================================================

@app.get("/verification/{mission_id}")
def verification(
    mission_id: str,
) -> dict[str, Any]:

    if get_mission(
        mission_id
    ) is None:

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    with DB_LOCK, db() as connection:

        rows = connection.execute(
            """
            SELECT
                id,
                mission_id,
                status,
                score,
                detail_json,
                created_at
            FROM verification_events
            WHERE mission_id=?
            ORDER BY id DESC
            """,
            (mission_id,),
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
        "mission_id": mission_id,
        "verification_events": output,
    }


# ============================================================
# TRANSPORT TEST
# ============================================================

@app.post("/transport/classify")
def transport_classify(
    request: TransportRequest,
) -> dict[str, Any]:

    return classify_transport(
        request.http_status,
        request.content_type,
        request.body,
        request.headers,
    )


# ============================================================
# POLICY
# ============================================================

@app.get("/policy")
def policy() -> dict[str, Any]:

    return health()["policy"]


# ============================================================
# DIAGNOSTICS
# ============================================================

@app.get("/diagnostics")
def diagnostics() -> dict[str, Any]:

    with DB_LOCK, db() as connection:

        mission_count = connection.execute(
            "SELECT COUNT(*) n FROM missions"
        ).fetchone()["n"]

        event_count = connection.execute(
            "SELECT COUNT(*) n FROM workflow_events"
        ).fetchone()["n"]

        transport_count = connection.execute(
            "SELECT COUNT(*) n FROM transport_events"
        ).fetchone()["n"]

        evidence_count = connection.execute(
            "SELECT COUNT(*) n FROM evidence"
        ).fetchone()["n"]

        verification_count = connection.execute(
            "SELECT COUNT(*) n FROM verification_events"
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
        "verification_events": {
            "count": verification_count
        },
        "limits": {
            "max_body": MAX_BODY,
            "fetch_timeout": FETCH_TIMEOUT,
            "max_sources": MAX_SOURCES,
            "min_text": MIN_TEXT,
            "min_independent_sources": (
                MIN_INDEPENDENT_SOURCES
            ),
            "min_source_consistency": (
                MIN_SOURCE_CONSISTENCY
            ),
        },
    }


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

        "evidence_consistency_verification": True,
        "independent_domain_verification": True,
        "contradiction_marker_detection": True,
        "truthful_verification_gate": True,

        "truthful_completion_gate": True,
        "recovery_integrity": True,
    }


# ============================================================
# INITIALIZE
# ============================================================

init_db()
